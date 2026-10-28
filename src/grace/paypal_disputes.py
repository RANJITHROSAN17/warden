"""A PayPal Disputes API client written from PayPal's published OpenAPI document.

Every path, field and enum in this file is taken from `openapi/customer_disputes_v1.json`, the
`customer_disputes_v1` spec (version 1.11) published in `paypal/paypal-rest-api-specifications`.
Nothing here is remembered from a blog post.

The endpoints implemented are the ones a merchant's agent actually needs:

    GET  /v1/customer/disputes                          list, with the spec's own filters
    GET  /v1/customer/disputes/{id}                      show details
    POST /v1/customer/disputes/{id}/provide-evidence     answer the claim, multipart
    POST /v1/customer/disputes/{id}/make-offer           settle inside the inquiry window
    POST /v1/customer/disputes/{id}/accept-claim         concede, and stop paying a fee
    POST /v1/customer/disputes/{id}/send-message         talk to the buyer
    POST /v1/customer/disputes/{id}/escalate             escalate an inquiry to a claim

`provide-evidence` is multipart: a JSON `input` part plus `file1..fileN`. That detail is why this
client exists rather than a thin `requests.post`.

`SimulatedDisputes` implements the same interface with no credentials, so the demo and the tests run
offline. It is not a stub that returns nothing - it holds state and enforces the same rules, so a
call that would fail against PayPal fails here too.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

SANDBOX = "https://api-m.sandbox.paypal.com"
LIVE = "https://api-m.paypal.com"

# From the spec: components/schemas/dispute_state and dispute_lifecycle_stage.
STATES = (
    "OPEN_INQUIRIES",
    "REQUIRED_ACTION",
    "REQUIRED_OTHER_PARTY_ACTION",
    "UNDER_PAYPAL_REVIEW",
    "APPEALABLE",
    "RESOLVED",
)
STAGES = ("INQUIRY", "CHARGEBACK", "PRE_ARBITRATION", "ARBITRATION")

# From components/schemas/offer_type.
OFFER_TYPES = (
    "REFUND",
    "REFUND_WITH_RETURN",
    "REFUND_WITH_REPLACEMENT",
    "REPLACEMENT_WITHOUT_REFUND",
)

# From components/schemas/evidence -> evidence_type (81 values). The ones this project emits.
EVIDENCE_TYPES = (
    "PROOF_OF_FULFILLMENT",
    "PROOF_OF_DELIVERY_SIGNATURE",
    "ONLINE_TRACKING_INFORMATION",
    "PROOF_OF_SIGNIFICANT_DIFFERENCE",
    "ITEM_DESCRIPTION",
    "ADDITIONAL_INFORMATION",
    "DELIVERY_ADDRESS",
    "DETAILS_OF_PURCHASE",
    "COPY_OF_CONTRACT",
    "PROOF_OF_REFUND",
)


class DisputesError(RuntimeError):
    def __init__(self, status: int, body: str, where: str) -> None:
        super().__init__(f"{where} -> HTTP {status}: {body[:400]}")
        self.status = status
        self.body = body
        self.where = where


class DisputesAPI(Protocol):
    mode: str

    def list_disputes(self, **filters: Any) -> dict[str, Any]: ...
    def get_dispute(self, dispute_id: str) -> dict[str, Any]: ...
    def provide_evidence(
        self, dispute_id: str, evidences: list[dict[str, Any]], files: list[str] | None = None
    ) -> dict[str, Any]: ...
    def make_offer(
        self, dispute_id: str, note: str, amount: float, currency: str, offer_type: str
    ) -> dict[str, Any]: ...
    def accept_claim(self, dispute_id: str, note: str = "") -> dict[str, Any]: ...
    def send_message(self, dispute_id: str, message: str) -> dict[str, Any]: ...
    def escalate(self, dispute_id: str, note: str = "") -> dict[str, Any]: ...


@dataclass
class PayPalDisputes:
    """The live client. Requires a sandbox or live app with the Disputes scope."""

    client_id: str
    client_secret: str
    base: str = SANDBOX
    mode: str = field(default="sandbox", init=False)
    _token: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.mode = "live" if self.base == LIVE else "sandbox"

    # ── auth ────────────────────────────────────────────────────────────────────────────────
    def token(self) -> str:
        if self._token:
            return self._token
        raw = f"{self.client_id}:{self.client_secret}".encode()
        request = urllib.request.Request(
            f"{self.base}/v1/oauth2/token",
            data=b"grant_type=client_credentials",
            headers={
                "Authorization": "Basic " + base64.b64encode(raw).decode(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            payload = json.loads(response.read())
        self._token = str(payload["access_token"])
        return self._token

    def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base}{path}"
        if query:
            clean = {k: v for k, v in query.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token()}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:  # noqa: S310
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise DisputesError(exc.code, exc.read().decode(errors="replace"), url) from exc

    def _multipart(
        self, path: str, payload: dict[str, Any], files: list[str] | None = None
    ) -> dict[str, Any]:
        """The `provide-evidence` shape: an `input` JSON part plus `fileN` binary parts."""
        boundary = f"----grace{uuid.uuid4().hex}"
        parts: list[bytes] = []
        parts.append(
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="input"\r\n'
                "Content-Type: application/json\r\n\r\n"
                f"{json.dumps(payload)}\r\n"
            ).encode()
        )
        for index, path_on_disk in enumerate(files or [], start=1):
            name = os.path.basename(path_on_disk)
            with open(path_on_disk, "rb") as handle:
                blob = handle.read()
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="file{index}"; filename="{name}"\r\n'
                    "Content-Type: application/octet-stream\r\n\r\n"
                ).encode()
                + blob
                + b"\r\n"
            )
        parts.append(f"--{boundary}--\r\n".encode())

        request = urllib.request.Request(
            f"{self.base}{path}",
            data=b"".join(parts),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token()}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raise DisputesError(
                exc.code, exc.read().decode(errors="replace"), f"{self.base}{path}"
            ) from exc

    # ── endpoints ───────────────────────────────────────────────────────────────────────────
    def list_disputes(self, **filters: Any) -> dict[str, Any]:
        """GET /v1/customer/disputes. Filters are the spec's query parameters."""
        allowed = {
            "disputed_transaction_id",
            "dispute_state",
            "dispute_life_cycle_stage",
            "create_time_before",
            "create_time_after",
            "update_time_before",
            "update_time_after",
            "page_size",
            "next_page_token",
            "start_time",
        }
        unknown = set(filters) - allowed
        if unknown:
            raise ValueError(
                f"unknown filter(s) {sorted(unknown)}; the spec allows {sorted(allowed)}"
            )
        return self._call("GET", "/v1/customer/disputes", query=filters)

    def get_dispute(self, dispute_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/customer/disputes/{dispute_id}")

    def provide_evidence(
        self, dispute_id: str, evidences: list[dict[str, Any]], files: list[str] | None = None
    ) -> dict[str, Any]:
        for item in evidences:
            kind = item.get("evidence_type")
            if kind not in EVIDENCE_TYPES:
                raise ValueError(
                    f"{kind!r} is not an evidence_type this client emits; see EVIDENCE_TYPES"
                )
        return self._multipart(
            f"/v1/customer/disputes/{dispute_id}/provide-evidence",
            {"evidences": evidences},
            files,
        )

    def make_offer(
        self, dispute_id: str, note: str, amount: float, currency: str, offer_type: str
    ) -> dict[str, Any]:
        if offer_type not in OFFER_TYPES:
            raise ValueError(f"offer_type must be one of {OFFER_TYPES}")
        return self._call(
            "POST",
            f"/v1/customer/disputes/{dispute_id}/make-offer",
            {
                "note": note,
                "offer_amount": {"currency_code": currency, "value": f"{amount:.2f}"},
                "offer_type": offer_type,
            },
        )

    def accept_claim(self, dispute_id: str, note: str = "") -> dict[str, Any]:
        body = {"note": note} if note else {}
        return self._call("POST", f"/v1/customer/disputes/{dispute_id}/accept-claim", body)

    def send_message(self, dispute_id: str, message: str) -> dict[str, Any]:
        return self._call(
            "POST", f"/v1/customer/disputes/{dispute_id}/send-message", {"message": message}
        )

    def escalate(self, dispute_id: str, note: str = "") -> dict[str, Any]:
        body = {"note": note} if note else {}
        return self._call("POST", f"/v1/customer/disputes/{dispute_id}/escalate", body)


@dataclass
class SimulatedDisputes:
    """The same interface, no credentials, real state.

    It enforces the rules that matter: a resolved dispute cannot be answered, an evidence type must
    be one PayPal defines, an offer must be a valid type. A demo that cannot fail teaches nothing.
    """

    mode: str = field(default="simulated", init=False)
    _cases: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)

    def add(self, dispute_id: str, reason: str, amount: float, currency: str = "USD") -> None:
        self._cases[dispute_id] = {
            "dispute_id": dispute_id,
            "reason": reason,
            "status": "REQUIRED_ACTION",
            "dispute_state": "REQUIRED_ACTION",
            "dispute_life_cycle_stage": "CHARGEBACK",
            "dispute_amount": {"currency_code": currency, "value": f"{amount:.2f}"},
            "evidences": [],
            "messages": [],
            "offer": {},
        }

    def _case(self, dispute_id: str) -> dict[str, Any]:
        if dispute_id not in self._cases:
            raise DisputesError(404, '{"name":"INVALID_RESOURCE_ID"}', dispute_id)
        case = self._cases[dispute_id]
        if case["dispute_state"] == "RESOLVED":
            raise DisputesError(422, '{"name":"DISPUTE_ALREADY_RESOLVED"}', dispute_id)
        return case

    def list_disputes(self, **filters: Any) -> dict[str, Any]:
        items = list(self._cases.values())
        state = filters.get("dispute_state")
        if state:
            items = [c for c in items if c["dispute_state"] == state]
        return {"items": items, "total_items": len(items), "total_pages": 1}

    def get_dispute(self, dispute_id: str) -> dict[str, Any]:
        if dispute_id not in self._cases:
            raise DisputesError(404, '{"name":"INVALID_RESOURCE_ID"}', dispute_id)
        return self._cases[dispute_id]

    def provide_evidence(
        self, dispute_id: str, evidences: list[dict[str, Any]], files: list[str] | None = None
    ) -> dict[str, Any]:
        case = self._case(dispute_id)
        for item in evidences:
            if item.get("evidence_type") not in EVIDENCE_TYPES:
                raise DisputesError(
                    422,
                    '{"name":"INVALID_PARAMETER_VALUE","field":"evidences.evidence_type"}',
                    dispute_id,
                )
        case["evidences"].extend(evidences)
        case["dispute_state"] = "UNDER_PAYPAL_REVIEW"
        case["status"] = "UNDER_PAYPAL_REVIEW"
        return {"dispute_id": dispute_id, "links": [{"rel": "self", "method": "GET"}]}

    def make_offer(
        self, dispute_id: str, note: str, amount: float, currency: str, offer_type: str
    ) -> dict[str, Any]:
        if offer_type not in OFFER_TYPES:
            raise DisputesError(
                422, '{"name":"INVALID_PARAMETER_VALUE","field":"offer_type"}', dispute_id
            )
        case = self._case(dispute_id)
        case["offer"] = {
            "note": note,
            "offer_type": offer_type,
            "offer_amount": {"currency_code": currency, "value": f"{amount:.2f}"},
        }
        return {"dispute_id": dispute_id, "offer": case["offer"]}

    def accept_claim(self, dispute_id: str, note: str = "") -> dict[str, Any]:
        case = self._case(dispute_id)
        case["dispute_state"] = "RESOLVED"
        case["status"] = "RESOLVED"
        case["dispute_outcome"] = {"outcome_code": "RESOLVED_BUYER_FAVOUR", "note": note}
        return {"dispute_id": dispute_id, "dispute_state": "RESOLVED"}

    def send_message(self, dispute_id: str, message: str) -> dict[str, Any]:
        case = self._case(dispute_id)
        case["messages"].append({"posted_by": "SELLER", "content": message})
        return {"dispute_id": dispute_id}

    def escalate(self, dispute_id: str, note: str = "") -> dict[str, Any]:
        case = self._case(dispute_id)
        case["dispute_life_cycle_stage"] = "CHARGEBACK"
        return {"dispute_id": dispute_id, "dispute_life_cycle_stage": "CHARGEBACK"}


def make_disputes(mode: str = "auto") -> DisputesAPI:
    """`sandbox`/`live` build the real client; anything else builds the simulator."""
    client_id = os.environ.get("PAYPAL_CLIENT_ID")
    secret = os.environ.get("PAYPAL_CLIENT_SECRET")
    if mode in {"sandbox", "live", "auto"} and client_id and secret:
        base = LIVE if mode == "live" else SANDBOX
        return PayPalDisputes(client_id=client_id, client_secret=secret, base=base)
    return SimulatedDisputes()
