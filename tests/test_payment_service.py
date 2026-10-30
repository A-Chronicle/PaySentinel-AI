import json

import pytest

from paysentinel.payment_ledger import (
    DuplicatePaymentError,
    PaymentLedger,
)
from paysentinel.payment_service import PaymentService


@pytest.fixture
def records_path():
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / "data" / "records.json"


@pytest.fixture
def service(tmp_path, records_path):
    ledger = PaymentLedger(tmp_path / "payments.sqlite3")
    return PaymentService(records_path=records_path, ledger=ledger)


def test_valid_invoice_is_recorded_for_review(service):
    result = service.evaluate_and_record("INV-2026-001")

    assert result.decision.outcome == "ELIGIBLE_FOR_REVIEW"
    assert result.payment["status"] == "REVIEW_REQUIRED"
    assert result.payment["invoice_id"] == "INV-2026-001"


def test_bank_change_invoice_enters_approval_queue(service):
    result = service.evaluate_and_record("INV-2026-002")

    assert result.decision.outcome == "REQUIRES_APPROVAL"
    assert result.payment["status"] == "PENDING_APPROVAL"

    events = service.ledger.get_events(result.payment["payment_id"])
    assert [event["new_status"] for event in events] == [
        "REVIEW_REQUIRED",
        "PENDING_APPROVAL",
    ]


def test_unallowlisted_supplier_is_blocked(service):
    result = service.evaluate_and_record("INV-2026-003")

    assert result.decision.outcome == "BLOCK"
    assert result.payment["status"] == "BLOCKED"


def test_missing_invoice_is_blocked_and_recorded(service):
    result = service.evaluate_and_record("INV-DOES-NOT-EXIST")

    assert result.decision.outcome == "BLOCK"
    assert result.payment["status"] == "BLOCKED"


def test_duplicate_processing_is_rejected(service):
    service.evaluate_and_record("INV-2026-001")

    with pytest.raises(DuplicatePaymentError):
        service.evaluate_and_record("INV-2026-001")


def test_invalid_invoice_id_is_rejected(service):
    with pytest.raises(ValueError):
        service.evaluate_and_record("   ")


def test_service_never_authorizes_a_payment(service):
    result = service.evaluate_and_record("INV-2026-001")

    assert result.payment["status"] not in {
        "AUTHORIZED",
        "SUBMITTED",
        "UNKNOWN",
    }


def test_changed_amount_is_blocked_and_recorded(tmp_path, records_path):
    records = json.loads(records_path.read_text(encoding="utf-8"))
    records["invoices"][0]["amount"] = "481.00"

    modified_records = tmp_path / "modified_records.json"
    modified_records.write_text(json.dumps(records), encoding="utf-8")

    ledger = PaymentLedger(tmp_path / "modified_payments.sqlite3")
    service = PaymentService(
        records_path=modified_records,
        ledger=ledger,
    )

    result = service.evaluate_and_record("INV-2026-001")

    assert result.decision.outcome == "BLOCK"
    assert result.payment["status"] == "BLOCKED"