import os
import httpx
from dotenv import load_dotenv

load_dotenv()

client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
base_url = os.getenv(
    "PAYPAL_BASE_URL",
    "https://api-m.sandbox.paypal.com",
).strip().rstrip("/")

if not client_id or not client_secret:
    print("ERROR: PayPal credentials are missing from .env")
    raise SystemExit(1)

if base_url != "https://api-m.sandbox.paypal.com":
    print("ERROR: Expected the PayPal Sandbox endpoint.")
    raise SystemExit(1)

try:
    response = httpx.post(
        f"{base_url}/v1/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
        timeout=20.0,
    )

    print("HTTP status:", response.status_code)

    if response.is_success:
        data = response.json()
        print("SUCCESS: PayPal Sandbox authentication works.")
        print("Token received:", bool(data.get("access_token")))
        print("Expires in:", data.get("expires_in"), "seconds")
    else:
        print("PayPal error response:")

        try:
            data = response.json()
            print("Error:", data.get("error", "Not provided"))
            print(
                "Description:",
                data.get("error_description", "Not provided"),
            )
            print("Debug ID:", data.get("debug_id", "Not provided"))
        except ValueError:
            print("Response was not JSON.")
            print(response.text[:1000])

except httpx.RequestError as exc:
    print("NETWORK ERROR:", type(exc).__name__)
    print(str(exc))

finally:
    client_id = None
    client_secret = None