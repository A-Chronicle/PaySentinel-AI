import json
from decimal import Decimal

import httpx
import pytest

from paysentinel.paypal_client import PayPalSandboxClient, UnsafeEndpointError


def make_client(handler):
    return PayPalSandboxClient(
        client_id="test-id",
        client_secret="test-secret",
        transport=httpx.MockTransport(handler),
    )


def token_response():
    return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 32400})


def payout_response(status="PENDING"):
    return httpx.Response(
        201,
        json={"batch_header": {"payout_batch_id": "BATCH-1", "batch_status": status}},
    )


def test_refuses_production_endpoint():
    with pytest.raises(UnsafeEndpointError):
        PayPalSandboxClient("id", "secret", base_url="https://api-m.paypal.com")


def test_from_env_requires_credentials(monkeypatch):
    monkeypatch.setattr("paysentinel.paypal_client.load_dotenv", lambda: None)
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="PAYPAL_CLIENT_ID"):
        PayPalSandboxClient.from_env()


def test_create_payout_sends_expected_request():
    seen = {}

    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            assert request.headers["Authorization"].startswith("Basic ")
            return token_response()
        seen["request"] = request
        return payout_response()

    client = make_client(handler)
    result = client.create_payout(
        idempotency_key="payout-PAY-0001",
        receiver_email="supplier-17@example.com",
        amount=Decimal("480"),
        currency="USD",
        note="Invoice INV-001",
    )

    request = seen["request"]
    body = json.loads(request.content)
    assert request.url.path == "/v1/payments/payouts"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert request.headers["PayPal-Request-Id"] == "payout-PAY-0001"
    assert body["sender_batch_header"]["sender_batch_id"] == "payout-PAY-0001"
    item = body["items"][0]
    assert item["amount"] == {"value": "480.00", "currency": "USD"}
    assert item["receiver"] == "supplier-17@example.com"
    assert result.payout_batch_id == "BATCH-1"
    assert result.batch_status == "PENDING"


def test_token_is_cached_between_calls():
    token_calls = 0
    payout_calls = 0

    def handler(request):
        nonlocal token_calls, payout_calls
        if request.url.path == "/v1/oauth2/token":
            token_calls += 1
            return token_response()
        payout_calls += 1
        return payout_response()

    client = make_client(handler)
    for _ in range(2):
        client.create_payout(
            idempotency_key="payout-PAY-0002",
            receiver_email="supplier-22@example.com",
            amount=Decimal("10"),
            currency="USD",
            note="test",
        )

    assert token_calls == 1
    assert payout_calls == 2


def test_http_error_is_raised():
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        return httpx.Response(422, json={"name": "VALIDATION_ERROR"})

    client = make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.create_payout(
            idempotency_key="payout-PAY-0003",
            receiver_email="bad",
            amount=Decimal("1"),
            currency="USD",
            note="test",
        )
