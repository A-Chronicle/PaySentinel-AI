"""Deterministic PaySentinel policy checks.

This module evaluates payment proposals using trusted system records.
It does not call PayPal or execute payments.
"""

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PolicyDecision:
    invoice_id: str
    outcome: str
    reasons: tuple[str, ...]


def _money(value: Any) -> Decimal:
    """Parse a monetary value without using floating-point arithmetic."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid monetary value") from None

    if not amount.is_finite():
        raise ValueError("Monetary value must be finite")

    return amount


def evaluate_invoice(
    records_path: str | Path,
    invoice_id: str,
) -> PolicyDecision:
    """Evaluate an invoice using trusted records only.

    Outcomes:
      BLOCK: at least one mandatory check failed.
      REQUIRES_APPROVAL: checks passed, but a bank-detail change was recorded.
      ELIGIBLE_FOR_REVIEW: basic checks passed; this is not payment authorization.
    """
    path = Path(records_path)

    with path.open("r", encoding="utf-8") as file:
        records = json.load(file)

    invoices = records.get("invoices", [])
    suppliers = records.get("suppliers", [])
    purchase_orders = records.get("purchase_orders", [])

    matches = [
        invoice for invoice in invoices
        if invoice.get("id") == invoice_id
    ]

    if len(matches) != 1:
        return PolicyDecision(
            invoice_id=invoice_id,
            outcome="BLOCK",
            reasons=("Invoice must exist exactly once in trusted records.",),
        )

    invoice = matches[0]
    reasons: list[str] = []

    # Detect duplicate invoice numbers for the same supplier.
    invoice_number = str(invoice.get("number", "")).strip().casefold()
    supplier_id = invoice.get("supplier_id")

    if not invoice_number:
        reasons.append("Invoice number is missing.")
    else:
        duplicates = [
            item for item in invoices
            if item.get("id") != invoice_id
            and item.get("supplier_id") == supplier_id
            and str(item.get("number", "")).strip().casefold()
            == invoice_number
        ]
        if duplicates:
            reasons.append("Duplicate invoice number detected.")

    supplier_matches = [
        supplier for supplier in suppliers
        if supplier.get("id") == supplier_id
    ]

    if len(supplier_matches) != 1:
        reasons.append("Supplier must exist exactly once.")
        supplier = None
    else:
        supplier = supplier_matches[0]

        if supplier.get("status") != "active":
            reasons.append("Supplier is not active.")

        if supplier.get("allowlisted") is not True:
            reasons.append("Supplier is not allowlisted.")

        if not supplier.get("payout_email"):
            reasons.append("Supplier payout email is missing.")

    if invoice.get("status") != "unpaid":
        reasons.append("Invoice is not marked unpaid.")

    try:
        invoice_amount = _money(invoice.get("amount"))
        if invoice_amount <= 0:
            reasons.append("Invoice amount must be positive.")
    except ValueError:
        invoice_amount = None
        reasons.append("Invoice amount is invalid.")

    invoice_currency = invoice.get("currency")
    if not isinstance(invoice_currency, str) or not invoice_currency.strip():
        reasons.append("Invoice currency is missing.")

    po_id = invoice.get("po_id")
    po_matches = [
        po for po in purchase_orders
        if po.get("id") == po_id
    ] if po_id else []

    if len(po_matches) != 1:
        reasons.append("A matching purchase order is required.")
    else:
        purchase_order = po_matches[0]

        if purchase_order.get("supplier_id") != supplier_id:
            reasons.append("Purchase order supplier does not match invoice.")

        if invoice_amount is not None:
            try:
                po_amount = _money(purchase_order.get("amount"))
                if invoice_amount != po_amount:
                    reasons.append("Invoice amount does not match purchase order.")
            except ValueError:
                reasons.append("Purchase order amount is invalid.")

        if purchase_order.get("currency") != invoice_currency:
            reasons.append("Invoice currency does not match purchase order.")

        if purchase_order.get("goods_received") is not True:
            reasons.append("Goods or services are not confirmed as received.")

    if reasons:
        return PolicyDecision(
            invoice_id=invoice_id,
            outcome="BLOCK",
            reasons=tuple(reasons),
        )

    # A recorded bank-detail change requires human review.
    if supplier and supplier.get("bank_changed_at"):
        return PolicyDecision(
            invoice_id=invoice_id,
            outcome="REQUIRES_APPROVAL",
            reasons=("Supplier bank details have a recorded change date.",),
        )

    return PolicyDecision(
        invoice_id=invoice_id,
        outcome="ELIGIBLE_FOR_REVIEW",
        reasons=("Basic invoice and supplier checks passed; payment is not authorized.",),
    )