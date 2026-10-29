"""PayPal Sandbox client.

Only the Policy Gateway imports this module. The agent service must never
have these credentials or a network route to PayPal.
"""

import os
import time
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
SANDBOX_HOSTS = {"api-m.sandbox.paypal.com"}
TOKEN_REFRESH_MARGIN_SECONDS = 60


class UnsafeEndpointError(RuntimeError):
    """Raised when the client is pointed at anything other than the PayPal Sandbox."""


@dataclass(frozen=True)
class PayoutResult:
    payout_batch_id: str
    batch_status: str
    idempotency_key: str


class PayPalSandboxClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = SANDBOX_BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ):
        if urlparse(base_url).hostname not in SANDBOX_HOSTS:
            raise UnsafeEndpointError(f"refusing non-sandbox PayPal endpoint: {base_url}")
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = httpx.Client(base_url=base_url, transport=transport, timeout=30.0)
        self._token: str | None = None
        self._token_expires_at = 0.0

    @classmethod
    def from_env(cls) -> "PayPalSandboxClient":
        load_dotenv()
        try:
            client_id = os.environ["PAYPAL_CLIENT_ID"]
            client_secret = os.environ["PAYPAL_CLIENT_SECRET"]
        except KeyError as missing:
            raise RuntimeError(f"missing environment variable: {missing.args[0]}") from None
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            base_url=os.environ.get("PAYPAL_BASE_URL", SANDBOX_BASE_URL),
        )

    def close(self) -> None:
        self._http.close()

    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
            return self._token
        response = self._http.post(
            "/v1/oauth2/token",
            auth=(self._client_id, self._client_secret),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        body = response.json()
        self._token = body["access_token"]
        self._token_expires_at = time.monotonic() + body["expires_in"]
        return self._token

    def create_payout(
        self,
        *,
        idempotency_key: str,
        receiver_email: str,
        amount: Decimal,
        currency: str,
        note: str,
    ) -> PayoutResult:
        """Send a payout to a supplier in the Sandbox.

        idempotency_key must be derived from the payment record (for example
        "payout-<payment_id>"), so a retry of the same payment reuses the same
        key and PayPal will not pay twice.
        """
        body = {
            "sender_batch_header": {
                "sender_batch_id": idempotency_key,
                "email_subject": "PaySentinel payment",
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "amount": {"value": f"{amount:.2f}", "currency": currency},
                    "receiver": receiver_email,
                    "note": note,
                    "sender_item_id": idempotency_key,
                }
            ],
        }
        response = self._http.post(
            "/v1/payments/payouts",
            json=body,
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json",
                "PayPal-Request-Id": idempotency_key,
            },
        )
        response.raise_for_status()
        header = response.json()["batch_header"]
        return PayoutResult(
            payout_batch_id=header["payout_batch_id"],
            batch_status=header["batch_status"],
            idempotency_key=idempotency_key,
        )
