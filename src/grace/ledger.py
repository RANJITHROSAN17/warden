"""SQLite ledger. Payments and every transition, each transition carrying a signed receipt.

The ledger answers two questions a payment dashboard usually cannot:
  * where is the money right now - committed, taken, or handed back?
  * what did the agent decide in the gap, and on what grounds?
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from grace import receipts
from grace.models import (
    OPEN_STATES,
    Action,
    Authority,
    Payee,
    Payment,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS authorities (
    id TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    authority_id TEXT NOT NULL,
    payee_id TEXT NOT NULL,
    payee_name TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    purpose TEXT NOT NULL,
    state TEXT NOT NULL,
    captured REAL NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    order_id TEXT,
    authorization_id TEXT,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transitions (
    id TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    payment_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    amount REAL,
    allowed INTEGER NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    counterfactual TEXT,
    receipt TEXT NOT NULL
);
"""


class Ledger:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ── authorities ─────────────────────────────────────────────────────────────────────────
    def save_authority(self, authority: Authority, at: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO authorities (id, at, body) VALUES (?, ?, ?)",
            (authority.id, at, json.dumps(authority.public(), sort_keys=True)),
        )
        self._conn.commit()

    def get_authority(self, authority_id: str) -> Authority | None:
        row = self._conn.execute(
            "SELECT body FROM authorities WHERE id = ?", (authority_id,)
        ).fetchone()
        return Authority(**json.loads(row["body"])) if row else None

    # ── payments ────────────────────────────────────────────────────────────────────────────
    def save_payment(self, payment: Payment) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO payments
               (id, at, authority_id, payee_id, payee_name, amount, currency, purpose, state,
                captured, attempts, order_id, authorization_id, expires_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                payment.id,
                payment.created_at,
                payment.authority_id,
                payment.payee.id,
                payment.payee.name,
                payment.amount,
                payment.currency,
                payment.purpose,
                payment.state,
                payment.captured,
                payment.attempts,
                payment.order_id,
                payment.authorization_id,
                payment.expires_at,
            ),
        )
        self._conn.commit()

    def get_payment(self, payment_id: str) -> Payment | None:
        row = self._conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        if row is None:
            return None
        return Payment(
            id=row["id"],
            authority_id=row["authority_id"],
            payee=Payee(id=row["payee_id"], name=row["payee_name"]),
            amount=row["amount"],
            currency=row["currency"],
            purpose=row["purpose"],
            created_at=row["at"],
            expires_at=row["expires_at"],
            state=row["state"],
            captured=row["captured"],
            attempts=row["attempts"],
            order_id=row["order_id"],
            authorization_id=row["authorization_id"],
        )

    def payments(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM payments ORDER BY at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "id": row["id"],
                    "at": row["at"],
                    "authority_id": row["authority_id"],
                    "payee": row["payee_name"],
                    "amount": row["amount"],
                    "captured": row["captured"],
                    "remaining": round(row["amount"] - row["captured"], 2),
                    "currency": row["currency"],
                    "purpose": row["purpose"],
                    "state": row["state"],
                    "attempts": row["attempts"],
                    "expires_at": row["expires_at"],
                }
            )
        return out

    # ── transitions ─────────────────────────────────────────────────────────────────────────
    def add_transition(self, action: Action) -> str:
        transition_id = f"tr_{uuid.uuid4().hex[:12]}"
        self._conn.execute(
            """INSERT INTO transitions
               (id, at, payment_id, kind, amount, allowed, actor, reason, counterfactual, receipt)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                transition_id,
                action.at,
                action.payment_id,
                action.kind,
                action.amount,
                1 if action.allowed else 0,
                action.actor,
                action.reason,
                action.counterfactual,
                json.dumps(action.receipt, sort_keys=True),
            ),
        )
        self._conn.commit()
        return transition_id

    def transitions(self, payment_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM transitions WHERE payment_id = ? ORDER BY at, rowid", (payment_id,)
        ).fetchall()
        out = []
        for row in rows:
            receipt = json.loads(row["receipt"])
            out.append(
                {
                    "id": row["id"],
                    "at": row["at"],
                    "kind": row["kind"],
                    "amount": row["amount"],
                    "allowed": bool(row["allowed"]),
                    "actor": row["actor"],
                    "reason": row["reason"],
                    "counterfactual": row["counterfactual"],
                    "checks": receipt.get("payload", {}).get("checks", []),
                }
            )
        return out

    def receipt(self, transition_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT receipt FROM transitions WHERE id = ?", (transition_id,)
        ).fetchone()
        return json.loads(row["receipt"]) if row else None

    def verify(self, transition_id: str) -> tuple[bool, str]:
        receipt = self.receipt(transition_id)
        if receipt is None:
            return False, "no such transition"
        return receipts.verify(receipt)

    # ── the state of the book ──────────────────────────────────────────────────────────────
    def summary(self) -> dict[str, Any]:
        """Where the money is. `open` is the amber window; `released` never moved at all."""
        rows = self._conn.execute(
            """SELECT state, currency, COUNT(*) AS n,
                      ROUND(SUM(amount), 2) AS authorised,
                      ROUND(SUM(captured), 2) AS captured
               FROM payments GROUP BY state, currency"""
        ).fetchall()
        buckets: dict[str, dict[str, Any]] = {}
        for row in rows:
            bucket = buckets.setdefault(
                row["state"],
                {"state": row["state"], "count": 0, "authorised": 0.0, "captured": 0.0},
            )
            bucket["count"] += row["n"]
            bucket["authorised"] = round(bucket["authorised"] + row["authorised"], 2)
            bucket["captured"] = round(bucket["captured"] + (row["captured"] or 0.0), 2)
            bucket["currency"] = row["currency"]

        open_rows = [b for state, b in buckets.items() if state in OPEN_STATES]
        closed_rows = [b for state, b in buckets.items() if state not in OPEN_STATES]
        return {
            "by_state": sorted(buckets.values(), key=lambda b: b["state"]),
            # committed and not yet settled: the money actually at risk in the gap
            "open": round(sum(b["authorised"] - b["captured"] for b in open_rows), 2),
            "captured": round(sum(b["captured"] for b in buckets.values()), 2),
            # authorised money that was handed back, whether or not the rest was taken
            "released": round(sum(b["authorised"] - b["captured"] for b in closed_rows), 2),
            "payments": sum(b["count"] for b in buckets.values()),
        }
