"""Application service connecting policy evaluation to the payment ledger.

This service records policy decisions. It does not authorize payments,
verify human approvals, or execute PayPal payouts.
"""

from dataclasses import dataclass
from pathlib import Path

from paysentinel.payment_ledger import (
    DuplicatePaymentError,
    PaymentLedger,
)
from paysentinel.policy_gateway import PolicyDecision, evaluate_invoice


POLICY_TO_LEDGER_STATE = {
    "BLOCK": "BLOCKED",
    "ELIGIBLE_FOR_REVIEW": "REVIEW_REQUIRED",
    "REQUIRES_APPROVAL": "PENDING_APPROVAL",
}


@dataclass(frozen=True)
class PaymentEvaluation:
    """Result of evaluating an invoice and recording its payment state."""

    decision: PolicyDecision
    payment: dict


class PaymentService:
    """Coordinate policy evaluation and persistent payment tracking."""

    def __init__(
        self,
        *,
        records_path: str | Path,
        ledger: PaymentLedger,
    ):
        self.records_path = Path(records_path)
        self.ledger = ledger

    def evaluate_and_record(self, invoice_id: str) -> PaymentEvaluation:
        """Evaluate an invoice and persist the resulting non-authorized state.

        Repeated calls for an invoice already in the ledger are rejected.
        This avoids silently reusing an old record after policy inputs change.
        """
        if not isinstance(invoice_id, str) or not invoice_id.strip():
            raise ValueError("invoice_id must be a non-empty string")

        decision = evaluate_invoice(self.records_path, invoice_id)

        try:
            initial_state = decision.outcome
            if initial_state not in POLICY_TO_LEDGER_STATE:
                raise ValueError(
                    f"Unsupported policy outcome: {initial_state}"
                )

            ledger_state = POLICY_TO_LEDGER_STATE[initial_state]

            # The ledger only permits non-privileged initial states.
            # Bank-change cases are created for review, then explicitly
            # transitioned into the approval queue.
            create_state = (
                "REVIEW_REQUIRED"
                if ledger_state == "PENDING_APPROVAL"
                else ledger_state
            )

            payment_id = f"PAY-{invoice_id}"
            idempotency_key = f"payout-{payment_id}"

            payment = self.ledger.create_payment(
                payment_id=payment_id,
                invoice_id=invoice_id,
                idempotency_key=idempotency_key,
                initial_status=create_state,
                reason=(
                    f"Policy outcome: {decision.outcome}. "
                    + " ".join(decision.reasons)
                ),
            )

            if ledger_state == "PENDING_APPROVAL":
                payment = self.ledger.transition(
                    payment_id,
                    "PENDING_APPROVAL",
                    "Policy requires human approval. "
                    + " ".join(decision.reasons),
                )

        except DuplicatePaymentError:
            # Do not silently reuse an old result. The caller must inspect
            # the existing record and deliberately handle reevaluation.
            raise

        return PaymentEvaluation(decision=decision, payment=payment)