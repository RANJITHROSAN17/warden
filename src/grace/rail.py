"""The payment rail: PayPal Orders API, or a deterministic stand-in.

Only four operations exist, and the whole product lives in the space between them:

    create_order -> authorize -> capture(amount?) -> void

`capture` takes an amount because PayPal's capture takes an amount. Partial capture is the reason
this project exists: it is the difference between a payment that half-happens and one that does not
happen at all.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Protocol


class RailError(RuntimeError):
    """A readable failure from the rail. Never swallowed, never downgraded to a simulation."""


class PaymentRail(Protocol):
    mode: str

    def create_order(self, amount: float, currency: str, reference: str) -> dict[str, Any]: ...

    def authorize(self, order_id: str) -> dict[str, Any]: ...

    def capture(
        self, authorization_id: str, amount: float | None = None, currency: str = "USD"
    ) -> dict[str, Any]: ...

    def void(self, authorization_id: str) -> dict[str, Any]: ...


class SandboxPayPal:
    """Real PayPal sandbox calls. Requires GRACE_PAYPAL_CLIENT_ID / _SECRET."""

    mode = "sandbox"

    def __init__(self, client_id: str | None = None, secret: str | None = None) -> None:
        self.client_id = client_id or os.environ.get("GRACE_PAYPAL_CLIENT_ID", "")
        self.secret = secret or os.environ.get("GRACE_PAYPAL_CLIENT_SECRET", "")
        if not self.client_id or not self.secret:
            raise RailError(
                "sandbox rail requested without GRACE_PAYPAL_CLIENT_ID / "
                "GRACE_PAYPAL_CLIENT_SECRET. Refusing to fall back to the simulator silently - "
                "that would hide exactly the failure this project is about."
            )
        self.base = os.environ.get("GRACE_PAYPAL_BASE", "https://api-m.sandbox.paypal.com")
        self._token: str | None = None
        self._token_expires = 0.0

    # ── auth ────────────────────────────────────────────────────────────────────────────────
    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires - 30:
            return self._token
        import base64
        import json
        import urllib.error
        import urllib.request

        basic = base64.b64encode(f"{self.client_id}:{self.secret}".encode()).decode()
        request = urllib.request.Request(
            f"{self.base}/v1/oauth2/token",
            data=b"grant_type=client_credentials",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RailError(
                f"PayPal OAuth failed with {exc.code}. Check the client id and secret."
            ) from exc
        self._token = body["access_token"]
        self._token_expires = time.time() + float(body.get("expires_in", 3600))
        return self._token

    def _call(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        import json
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(body).encode() if method != "GET" else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json",
                # PayPal's answer to a retried capture: this header is how a partial failure is
                # made safe. Same key, same operation, no double charge.
                "PayPal-Request-Id": body.pop("__request_id", str(uuid.uuid4())),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise RailError(f"PayPal {method} {path} failed {exc.code}: {detail}") from exc

    # ── operations ──────────────────────────────────────────────────────────────────────────
    def create_order(self, amount: float, currency: str, reference: str) -> dict[str, Any]:
        result = self._call(
            "POST",
            "/v2/checkout/orders",
            {
                "intent": "AUTHORIZE",
                "__request_id": reference,
                "purchase_units": [
                    {
                        "reference_id": reference,
                        "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
                    }
                ],
            },
        )
        return {"mode": self.mode, "order_id": result.get("id"), "status": result.get("status")}

    def authorize(self, order_id: str) -> dict[str, Any]:
        result = self._call("POST", f"/v2/checkout/orders/{order_id}/authorize", {})
        authorization_id = None
        for unit in result.get("purchase_units", []):
            for payment in unit.get("payments", {}).get("authorizations", []):
                authorization_id = payment.get("id")
        return {
            "mode": self.mode,
            "authorization_id": authorization_id,
            "status": result.get("status"),
        }

    def capture(
        self, authorization_id: str, amount: float | None = None, currency: str = "USD"
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if amount is not None:
            body["amount"] = {"currency_code": currency, "value": f"{amount:.2f}"}
        result = self._call("POST", f"/v2/payments/authorizations/{authorization_id}/capture", body)
        return {
            "mode": self.mode,
            "capture_id": result.get("id"),
            "status": result.get("status"),
            "amount": amount,
        }

    def void(self, authorization_id: str) -> dict[str, Any]:
        self._call("POST", f"/v2/payments/authorizations/{authorization_id}/void", {})
        return {"mode": self.mode, "voided": authorization_id, "status": "VOIDED"}


class SimulatedRail:
    """Deterministic stand-in with the failure modes that matter, so they can be exercised.

    `instrument_limit` models an issuer that will not clear the full amount. `transient_failures`
    models a rail that fails a fixed number of times and then works. Both are real shapes of
    failure; scripting them is how the compensation logic gets tested instead of assumed.
    """

    mode = "simulated"

    def __init__(self) -> None:
        self._orders: dict[str, dict[str, Any]] = {}
        self.instrument_limit: float | None = None
        self.transient_failures = 0

    def create_order(self, amount: float, currency: str, reference: str) -> dict[str, Any]:
        order_id = f"SIM-ORDER-{uuid.uuid5(uuid.NAMESPACE_URL, reference).hex[:12].upper()}"
        self._orders[order_id] = {
            "amount": round(amount, 2),
            "currency": currency,
            "status": "CREATED",
        }
        return {"mode": self.mode, "order_id": order_id, "status": "CREATED", "approval_url": None}

    def authorize(self, order_id: str) -> dict[str, Any]:
        order = self._orders.setdefault(order_id, {"status": "CREATED"})
        order["status"] = "APPROVED"
        authorization_id = f"SIM-AUTH-{uuid.uuid5(uuid.NAMESPACE_URL, order_id).hex[:12].upper()}"
        order["authorization_id"] = authorization_id
        return {"mode": self.mode, "authorization_id": authorization_id, "status": "AUTHORIZED"}

    def capture(
        self, authorization_id: str, amount: float | None = None, currency: str = "USD"
    ) -> dict[str, Any]:
        if self.transient_failures > 0:
            self.transient_failures -= 1
            return {"mode": self.mode, "status": "FAILED", "reason": "RAIL_TIMEOUT"}
        over = (
            self.instrument_limit is not None
            and amount is not None
            and round(amount, 2) > round(self.instrument_limit, 2)
        )
        if over:
            return {
                "mode": self.mode,
                "status": "DECLINED",
                "reason": "INSTRUMENT_DECLINED",
                "declined_amount": round(amount or 0.0, 2),
                "instrument_limit": round(float(self.instrument_limit or 0.0), 2),
            }
        return {
            "mode": self.mode,
            "capture_id": f"SIM-CAP-{authorization_id[-8:]}-{uuid.uuid4().hex[:4].upper()}",
            "status": "COMPLETED",
            "amount": amount,
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def void(self, authorization_id: str) -> dict[str, Any]:
        return {"mode": self.mode, "voided": authorization_id, "status": "VOIDED"}


def make_rail(mode: str = "auto") -> PaymentRail:
    """Choose a rail, and never fall back silently from the real one to the fake one."""
    want = (mode or "auto").lower()
    if want == "sandbox":
        return SandboxPayPal()
    if want == "simulated":
        return SimulatedRail()
    if os.environ.get("GRACE_PAYPAL_CLIENT_ID") and os.environ.get("GRACE_PAYPAL_CLIENT_SECRET"):
        return SandboxPayPal()
    return SimulatedRail()
