import pytest

from paysentinel.payment_ledger import (
    DuplicatePaymentError,
    InvalidPaymentStateError,
    PaymentLedger,
    PaymentNotFoundError,
)


@pytest.fixture
def ledger(tmp_path):
    return PaymentLedger(tmp_path / "payments.sqlite3")


def create_review_payment(
    ledger,
    payment_id="PAY-001",
    invoice_id="INV-001",
    idempotency_key="payout-PAY-001",
):
    return ledger.create_payment(
        payment_id=payment_id,
        invoice_id=invoice_id,
        idempotency_key=idempotency_key,
        initial_status="REVIEW_REQUIRED",
        reason="Awaiting policy review",
    )


def test_create_payment_for_review(ledger):
    payment = create_review_payment(ledger)

    assert payment["payment_id"] == "PAY-001"
    assert payment["invoice_id"] == "INV-001"
    assert payment["status"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize(
    "privileged_state",
    ["AUTHORIZED", "SUBMITTED", "UNKNOWN"],
)
def test_privileged_initial_states_are_rejected(ledger, privileged_state):
    with pytest.raises(InvalidPaymentStateError):
        ledger.create_payment(
            payment_id="PAY-PRIVILEGED",
            invoice_id="INV-PRIVILEGED",
            idempotency_key="payout-PRIVILEGED",
            initial_status=privileged_state,
            reason="Must not bypass authorization",
        )


def test_duplicate_payment_id_is_rejected(ledger):
    create_review_payment(ledger)

    with pytest.raises(DuplicatePaymentError):
        create_review_payment(
            ledger,
            invoice_id="INV-002",
            idempotency_key="payout-PAY-002",
        )


def test_duplicate_invoice_id_is_rejected(ledger):
    create_review_payment(ledger)

    with pytest.raises(DuplicatePaymentError):
        create_review_payment(
            ledger,
            payment_id="PAY-002",
            idempotency_key="payout-PAY-002",
        )


def test_duplicate_idempotency_key_is_rejected(ledger):
    create_review_payment(ledger)

    with pytest.raises(DuplicatePaymentError):
        create_review_payment(
            ledger,
            payment_id="PAY-002",
            invoice_id="INV-002",
        )


def test_review_can_move_to_pending_approval(ledger):
    create_review_payment(ledger)

    payment = ledger.transition(
        "PAY-001",
        "PENDING_APPROVAL",
        "Policy requires human approval",
    )

    assert payment["status"] == "PENDING_APPROVAL"


@pytest.mark.parametrize(
    "target_state",
    ["AUTHORIZED", "SUBMITTED", "UNKNOWN"],
)
def test_generic_transition_cannot_enter_privileged_states(
    ledger,
    target_state,
):
    create_review_payment(ledger)

    with pytest.raises(InvalidPaymentStateError):
        ledger.transition(
            "PAY-001",
            target_state,
            "Attempted privileged transition",
        )

    assert ledger.get_payment("PAY-001")["status"] == "REVIEW_REQUIRED"


def test_blocked_payment_is_terminal(ledger):
    ledger.create_payment(
        payment_id="PAY-BLOCKED",
        invoice_id="INV-BLOCKED",
        idempotency_key="payout-BLOCKED",
        initial_status="BLOCKED",
        reason="Supplier is not allowlisted",
    )

    with pytest.raises(InvalidPaymentStateError):
        ledger.transition(
            "PAY-BLOCKED",
            "REVIEW_REQUIRED",
            "Attempt to reopen blocked payment",
        )

    assert ledger.get_payment("PAY-BLOCKED")["status"] == "BLOCKED"


def test_transition_creates_audit_event(ledger):
    create_review_payment(ledger)

    ledger.transition(
        "PAY-001",
        "PENDING_APPROVAL",
        "Approval required",
    )

    events = ledger.get_events("PAY-001")

    assert len(events) == 2
    assert events[0]["previous_status"] is None
    assert events[0]["new_status"] == "REVIEW_REQUIRED"
    assert events[1]["previous_status"] == "REVIEW_REQUIRED"
    assert events[1]["new_status"] == "PENDING_APPROVAL"
    assert events[1]["reason"] == "Approval required"


def test_missing_payment_raises_error(ledger):
    with pytest.raises(PaymentNotFoundError):
        ledger.get_payment("DOES-NOT-EXIST")


def test_empty_reason_is_rejected(ledger):
    with pytest.raises(ValueError):
        ledger.create_payment(
            payment_id="PAY-EMPTY",
            invoice_id="INV-EMPTY",
            idempotency_key="payout-EMPTY",
            initial_status="REVIEW_REQUIRED",
            reason="   ",
        )