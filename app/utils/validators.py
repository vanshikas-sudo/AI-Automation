import hashlib
import hmac


def verify_webhook_signature(payload: bytes, signature: str, app_secret: str) -> bool:
    """
    Verify the X-Hub-Signature-256 header sent by Meta.
    Returns True when the HMAC-SHA256 of the raw body matches the header.
    """
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    received = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)
