import hmac
import hashlib
import os
from typing import Optional

# Load at call-time, not import-time, to avoid dotenv race condition
def _get_secret() -> Optional[str]:
    return os.getenv("GITHUB_WEBHOOK_SECRET")


def verify_github_signature(payload: bytes, signature: Optional[str]) -> None:
    if not signature:
        raise ValueError("Missing GitHub signature header")

    secret = _get_secret()
    if not secret:
        raise ValueError("GITHUB_WEBHOOK_SECRET is not configured")

    # maxsplit=1 handles edge case where signature value itself contains "="
    parts = signature.split("=", 1)
    if len(parts) != 2:
        raise ValueError(f"Malformed signature header: {signature}")

    sha_name, received_sig = parts

    if sha_name != "sha256":
        raise ValueError(f"Unsupported signature algorithm: {sha_name}")

    mac = hmac.new(
        secret.encode(),
        msg=payload,
        digestmod=hashlib.sha256,
    )

    if not hmac.compare_digest(mac.hexdigest(), received_sig):
        raise ValueError("Signature mismatch — payload may be tampered or secret is wrong")