"""PayPal sandbox client, plus a simulator that speaks the same interface.

Two modes, never silently interchangeable:

* `sandbox`  - real HTTP against api-m.sandbox.paypal.com. Creating an order returns PayPal's
               own approval URL; the buyer approves it in a browser, then the authorization is
               captured. That approval step is the point of the demo, not an obstacle to it.
* `simulated`- an in-process stand-in with deterministic ids, used by tests, CI and the offline
               demo. Every receipt produced in this mode says `simulated`, in the payload, in the
               ledger and on screen.

Faking a payment call without labelling it would be the exact failure this project is about.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Protocol

SANDBOX_API = "https://api-m.sandbox.paypal.com"


class PayPalError(RuntimeError):
    pass


class PaymentRail(Protocol):
    mode: str

    def create_order(self, amount: float, currency: str, reference: str) -> dict[str, Any]: ...

    def authorize(self, order_id: str) -> dict[str, Any]: ...

    def capture(self, authorization_id: str) -> dict[str, Any]: ...

    def void(self, authorization_id: str) -> dict[str, Any]: ...


class SandboxPayPal:
    """Real PayPal sandbox calls. Requires WARDEN_PAYPAL_CLIENT_ID / _SECRET."""

    mode = "sandbox"

    def __init__(self, client_id: str | None = None, secret: str | None = None) -> None:
        self.client_id = client_id or os.environ.get("WARDEN_PAYPAL_CLIENT_ID", "")
        self.secret = secret or os.environ.get("WARDEN_PAYPAL_CLIENT_SECRET", "")
        if not self.client_id or not self.secret:
            raise PayPalError(
                "sandbox mode needs WARDEN_PAYPAL_CLIENT_ID and WARDEN_PAYPAL_CLIENT_SECRET"
            )

    def _token(self) -> str:
        credentials = base64.b64encode(f"{self.client_id}:{self.secret}".encode()).decode("ascii")
        request = urllib.request.Request(
            f"{SANDBOX_API}/v1/oauth2/token",
            data=b"grant_type=client_credentials",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return str(json.loads(response.read().decode("utf-8"))["access_token"])

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": str(uuid.uuid4()),
        }
        request = urllib.request.Request(
            f"{SANDBOX_API}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                return dict(json.loads(raw)) if raw else {}
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            raise PayPalError(f"{exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc

    def create_order(self, amount: float, currency: str, reference: str) -> dict[str, Any]:
        order = self._call(
            "POST",
            "/v2/checkout/orders",
            {
                "intent": "AUTHORIZE",
                "purchase_units": [
                    {
                        "reference_id": reference,
                        "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
                    }
                ],
            },
        )
        approval = next(
            (
                link["href"]
                for link in order.get("links", [])
                if link.get("rel") in {"approve", "payer-action"}
            ),
            None,
        )
        return {
            "mode": self.mode,
            "order_id": order.get("id"),
            "status": order.get("status"),
            "approval_url": approval,
        }

    def authorize(self, order_id: str) -> dict[str, Any]:
        result = self._call("POST", f"/v2/checkout/orders/{order_id}/authorize", {})
        authorization = (result.get("purchase_units") or [{}])[0].get("payments", {})
        authorization_id = (authorization.get("authorizations") or [{}])[0].get("id")
        return {
            "mode": self.mode,
            "authorization_id": authorization_id,
            "status": result.get("status"),
        }

    def capture(self, authorization_id: str) -> dict[str, Any]:
        result = self._call("POST", f"/v2/payments/authorizations/{authorization_id}/capture", {})
        return {"mode": self.mode, "capture_id": result.get("id"), "status": result.get("status")}

    def void(self, authorization_id: str) -> dict[str, Any]:
        self._call("POST", f"/v2/payments/authorizations/{authorization_id}/void", {})
        return {"mode": self.mode, "voided": authorization_id}


class SimulatedPayPal:
    """Deterministic stand-in. Same interface, obviously-labelled output, no network."""

    mode = "simulated"

    def __init__(self) -> None:
        self._orders: dict[str, dict[str, Any]] = {}

    def create_order(self, amount: float, currency: str, reference: str) -> dict[str, Any]:
        order_id = f"SIM-ORDER-{uuid.uuid5(uuid.NAMESPACE_URL, reference).hex[:12].upper()}"
        self._orders[order_id] = {
            "amount": round(amount, 2),
            "currency": currency,
            "reference": reference,
            "status": "CREATED",
        }
        return {
            "mode": self.mode,
            "order_id": order_id,
            "status": "CREATED",
            "approval_url": None,
        }

    def authorize(self, order_id: str) -> dict[str, Any]:
        order = self._orders.setdefault(order_id, {"status": "CREATED"})
        if order.get("status") == "CREATED":
            order["status"] = "APPROVED"
        authorization_id = f"SIM-AUTH-{uuid.uuid5(uuid.NAMESPACE_URL, order_id).hex[:12].upper()}"
        order["authorization_id"] = authorization_id
        return {"mode": self.mode, "authorization_id": authorization_id, "status": "AUTHORIZED"}

    def capture(self, authorization_id: str) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "capture_id": f"SIM-CAP-{authorization_id[-8:]}",
            "status": "COMPLETED",
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def void(self, authorization_id: str) -> dict[str, Any]:
        return {"mode": self.mode, "voided": authorization_id, "status": "VOIDED"}


def make_rail() -> PaymentRail:
    """Sandbox when credentials exist, simulator otherwise - and the mode travels with the call."""
    mode = os.environ.get("WARDEN_PAYPAL_MODE", "").lower()
    if mode == "sandbox" or (
        not mode
        and os.environ.get("WARDEN_PAYPAL_CLIENT_ID")
        and os.environ.get("WARDEN_PAYPAL_CLIENT_SECRET")
    ):
        return SandboxPayPal()
    return SimulatedPayPal()
