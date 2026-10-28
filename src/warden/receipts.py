"""Signed receipts.

A ledger entry is a claim. A receipt is a claim someone can check without trusting the database
that produced it. Every decision is serialised canonically, signed with HMAC-SHA256, and stored
with the signature; `/receipts/{id}/verify` recomputes it.

This is not a replacement for a proper KMS or asymmetric signing in production - it is the shape
of the thing, small enough to audit in one sitting. The README says so plainly.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from warden.models import canonical_json

DEFAULT_KEY = "dev-only-signing-key-change-me"
ALGORITHM = "HMAC-SHA256"


def _key() -> bytes:
    return os.environ.get("WARDEN_SIGNING_KEY", DEFAULT_KEY).encode("utf-8")


def sign(payload: dict[str, Any], *, key_id: str | None = None) -> dict[str, Any]:
    """Return `{alg, key_id, payload, sig}`. The signature covers the payload only."""
    body = canonical_json(payload).encode("utf-8")
    signature = hmac.new(_key(), body, hashlib.sha256).hexdigest()
    return {
        "alg": ALGORITHM,
        "key_id": key_id or os.environ.get("WARDEN_SIGNING_KEY_ID", "local-dev"),
        "payload": payload,
        "sig": signature,
    }


def verify(receipt: dict[str, Any]) -> tuple[bool, str]:
    """Recompute the signature. Returns `(ok, explanation)` - never raises on bad input."""
    if not isinstance(receipt, dict) or "payload" not in receipt or "sig" not in receipt:
        return False, "receipt is missing payload or signature"
    if receipt.get("alg") != ALGORITHM:
        return False, f"unsupported algorithm {receipt.get('alg')!r}"
    body = canonical_json(receipt["payload"]).encode("utf-8")
    expected = hmac.new(_key(), body, hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected, str(receipt["sig"])):
        return True, "signature valid"
    return False, "signature does not match payload - receipt was modified or the key is wrong"
