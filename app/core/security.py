import hmac
import hashlib
from app.core.config import get_env
from typing import Optional

GITHUB_WEBHOOK_SECRET = get_env("GITHUB_WEBHOOK_SECRET")

def verify_github_signature(payload: bytes, signature: Optional[str]):
    if not signature:
        raise ValueError("Missing GitHub signature")

    if not GITHUB_WEBHOOK_SECRET:
        raise ValueError("Webhook secret not configured")

    sha_name, received_sig = signature.split("=")

    mac = hmac.new(
        GITHUB_WEBHOOK_SECRET.encode(),
        msg=payload,
        digestmod=hashlib.sha256,
    )

    if not hmac.compare_digest(mac.hexdigest(), received_sig):
        raise ValueError("Invalid GitHub signature")