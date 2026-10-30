"""Persistent payment ledger for PaySentinel AI.

The ledger records payment state and state-transition events.

It does not authorize payments, verify human approvals, or call PayPal.
Privileged payment operations must be implemented behind a separate
authorization and execution boundary.
"""

import sqlite3
from pathlib import Path
from typing import Final


PAYMENT_STATES: Final[frozenset[str]] = frozenset(
    {
        "BLOCKED",
        "REVIEW_REQUIRED",
        "PENDING_APPROVAL",
        "AUTHORIZED",
        "SUBMITTED",
        "UNKNOWN",
    }
)

# Generic transitions are intentionally restricted.
# AUTHORIZED, SUBMITTED, and UNKNOWN cannot be entered through transition().
ALLOWED_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "BLOCKED": frozenset(),
    "REVIEW_REQUIRED": frozenset({"PENDING_APPROVAL", "BLOCKED"}),
    "PENDING_APPROVAL": frozenset({"BLOCKED"}),
    "AUTHORIZED": frozenset(),
    "SUBMITTED": frozenset(),
    "UNKNOWN": frozenset(),
}

INITIAL_STATES: Final[frozenset[str]] = frozenset(
    {"REVIEW_REQUIRED", "BLOCKED"}
)


class DuplicatePaymentError(RuntimeError):
    """Raised when a payment ID, invoice ID, or idempotency key already exists."""


class PaymentNotFoundError(LookupError):
    """Raised when a payment ID does not exist."""


class InvalidPaymentStateError(ValueError):
    """Raised when an invalid state or transition is requested."""


class PaymentLedger:
    """SQLite-backed ledger with atomic state transitions and event history."""

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)

        if self.database_path == ":memory:":
            raise ValueError(
                "Use a file-backed SQLite database. This ledger opens a "
                "separate connection for each operation."
            )

        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    payment_id TEXT PRIMARY KEY,
                    invoice_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'BLOCKED',
                            'REVIEW_REQUIRED',
                            'PENDING_APPROVAL',
                            'AUTHORIZED',
                            'SUBMITTED',
                            'UNKNOWN'
                        )
                    ),
                    created_at TEXT NOT NULL DEFAULT (
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    ),
                    updated_at TEXT NOT NULL DEFAULT (
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS payment_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_id TEXT NOT NULL,
                    previous_status TEXT,
                    new_status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (
                        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    ),
                    FOREIGN KEY (payment_id)
                        REFERENCES payments(payment_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_payment_events_payment_id
                ON payment_events(payment_id, event_id)
                """
            )

    @staticmethod
    def _require_text(value: str, field_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")

    def create_payment(
        self,
        *,
        payment_id: str,
        invoice_id: str,
        idempotency_key: str,
        initial_status: str,
        reason: str,
    ) -> dict:
        """Create a payment record in a non-privileged initial state."""
        self._require_text(payment_id, "payment_id")
        self._require_text(invoice_id, "invoice_id")
        self._require_text(idempotency_key, "idempotency_key")
        self._require_text(reason, "reason")

        if initial_status not in INITIAL_STATES:
            raise InvalidPaymentStateError(
                "Initial state must be REVIEW_REQUIRED or BLOCKED. "
                "Privileged states require a separate authorization path."
            )

        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO payments (
                        payment_id, invoice_id, idempotency_key, status
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (payment_id, invoice_id, idempotency_key, initial_status),
                )
                connection.execute(
                    """
                    INSERT INTO payment_events (
                        payment_id, previous_status, new_status, reason
                    )
                    VALUES (?, NULL, ?, ?)
                    """,
                    (payment_id, initial_status, reason),
                )
        except sqlite3.IntegrityError as error:
            raise DuplicatePaymentError(
                "Payment ID, invoice ID, or idempotency key already exists."
            ) from error

        return self.get_payment(payment_id)

    def get_payment(self, payment_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()

        if row is None:
            raise PaymentNotFoundError(f"Payment not found: {payment_id}")

        return dict(row)

    def transition(
        self,
        payment_id: str,
        new_status: str,
        reason: str,
    ) -> dict:
        """Perform an allowed non-privileged transition atomically.

        This method intentionally cannot authorize, submit, or reconcile a
        payout. Those operations require a dedicated trusted service.
        """
        self._require_text(payment_id, "payment_id")
        self._require_text(reason, "reason")

        if new_status not in PAYMENT_STATES:
            raise InvalidPaymentStateError(
                f"Unsupported payment state: {new_status}"
            )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()

            if row is None:
                raise PaymentNotFoundError(
                    f"Payment not found: {payment_id}"
                )

            previous_status = row["status"]

            if new_status not in ALLOWED_TRANSITIONS[previous_status]:
                raise InvalidPaymentStateError(
                    f"Transition not allowed: {previous_status} -> {new_status}"
                )

            connection.execute(
                """
                UPDATE payments
                SET status = ?,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE payment_id = ?
                """,
                (new_status, payment_id),
            )
            connection.execute(
                """
                INSERT INTO payment_events (
                    payment_id, previous_status, new_status, reason
                )
                VALUES (?, ?, ?, ?)
                """,
                (payment_id, previous_status, new_status, reason),
            )

        return self.get_payment(payment_id)

    def get_events(self, payment_id: str) -> list[dict]:
        """Return the event history in insertion order."""
        with self._connect() as connection:
            payment_exists = connection.execute(
                "SELECT 1 FROM payments WHERE payment_id = ?",
                (payment_id,),
            ).fetchone()

            if payment_exists is None:
                raise PaymentNotFoundError(
                    f"Payment not found: {payment_id}"
                )

            rows = connection.execute(
                """
                SELECT *
                FROM payment_events
                WHERE payment_id = ?
                ORDER BY event_id
                """,
                (payment_id,),
            ).fetchall()

        return [dict(row) for row in rows]