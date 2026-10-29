
import json
from pathlib import Path

from paysentinel.policy_gateway import evaluate_invoice


RECORDS_PATH = Path(__file__).resolve().parents[1] / "data" / "records.json"


def test_valid_invoice_is_eligible_for_review():
    decision = evaluate_invoice(RECORDS_PATH, "INV-2026-001")

    assert decision.outcome == "ELIGIBLE_FOR_REVIEW"
    assert decision.invoice_id == "INV-2026-001"


def test_bank_detail_change_requires_approval():
    decision = evaluate_invoice(RECORDS_PATH, "INV-2026-002")

    assert decision.outcome == "REQUIRES_APPROVAL"
    assert any(
        "bank details" in reason.lower()
        for reason in decision.reasons
    )


def test_unallowlisted_supplier_is_blocked():
    decision = evaluate_invoice(RECORDS_PATH, "INV-2026-003")

    assert decision.outcome == "BLOCK"
    assert any(
        "not allowlisted" in reason.lower()
        for reason in decision.reasons
    )


def test_missing_invoice_is_blocked():
    decision = evaluate_invoice(RECORDS_PATH, "INV-DOES-NOT-EXIST")

    assert decision.outcome == "BLOCK"
    assert any(
        "exactly once" in reason.lower()
        for reason in decision.reasons
    )


def test_eligible_decision_does_not_claim_payment_is_authorized():
    decision = evaluate_invoice(RECORDS_PATH, "INV-2026-001")

    assert decision.outcome == "ELIGIBLE_FOR_REVIEW"
    assert any(
        "not authorized" in reason.lower()
        for reason in decision.reasons
    )


def load_modified_records(tmp_path, modification):
    """Create isolated test records without changing the real seed file."""
    with RECORDS_PATH.open("r", encoding="utf-8") as file:
        records = json.load(file)

    modification(records)

    test_records_path = tmp_path / "records.json"
    test_records_path.write_text(
        json.dumps(records),
        encoding="utf-8",
    )

    return test_records_path


def test_invoice_amount_mismatch_is_blocked(tmp_path):
    def modify(records):
        records["invoices"][0]["amount"] = "481.00"

    path = load_modified_records(tmp_path, modify)
    decision = evaluate_invoice(path, "INV-2026-001")

    assert decision.outcome == "BLOCK"
    assert any(
        "amount does not match" in reason.lower()
        for reason in decision.reasons
    )


def test_duplicate_invoice_number_is_blocked(tmp_path):
    def modify(records):
        duplicate = records["invoices"][0].copy()
        duplicate["id"] = "INV-DUPLICATE-001"
        records["invoices"].append(duplicate)

    path = load_modified_records(tmp_path, modify)
    decision = evaluate_invoice(path, "INV-2026-001")

    assert decision.outcome == "BLOCK"
    assert any(
        "duplicate invoice" in reason.lower()
        for reason in decision.reasons
    )


def test_unreceived_goods_are_blocked(tmp_path):
    def modify(records):
        records["purchase_orders"][0]["goods_received"] = False

    path = load_modified_records(tmp_path, modify)
    decision = evaluate_invoice(path, "INV-2026-001")

    assert decision.outcome == "BLOCK"
    assert any(
        "received" in reason.lower()
        for reason in decision.reasons
    )


def test_inactive_supplier_is_blocked(tmp_path):
    def modify(records):
        records["suppliers"][0]["status"] = "inactive"

    path = load_modified_records(tmp_path, modify)
    decision = evaluate_invoice(path, "INV-2026-001")

    assert decision.outcome == "BLOCK"
    assert any(
        "not active" in reason.lower()
        for reason in decision.reasons
    )