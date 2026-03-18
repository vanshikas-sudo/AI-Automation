import hashlib
import hmac

from app.utils.validators import verify_webhook_signature


class TestVerifyWebhookSignature:
    """Tests for the HMAC-SHA256 webhook signature validator."""

    SECRET = "my_app_secret"
    PAYLOAD = b'{"test": "data"}'

    def _sign(self, payload: bytes = None, secret: str = None) -> str:
        p = payload or self.PAYLOAD
        s = secret or self.SECRET
        sig = hmac.new(s.encode(), p, hashlib.sha256).hexdigest()
        return f"sha256={sig}"

    def test_valid_signature(self):
        sig = self._sign()
        assert verify_webhook_signature(self.PAYLOAD, sig, self.SECRET) is True

    def test_invalid_signature(self):
        assert verify_webhook_signature(self.PAYLOAD, "sha256=bad", self.SECRET) is False

    def test_missing_sha256_prefix(self):
        sig_hex = hmac.new(
            self.SECRET.encode(), self.PAYLOAD, hashlib.sha256
        ).hexdigest()
        # No "sha256=" prefix → should be rejected
        assert verify_webhook_signature(self.PAYLOAD, sig_hex, self.SECRET) is False

    def test_empty_signature(self):
        assert verify_webhook_signature(self.PAYLOAD, "", self.SECRET) is False

    def test_wrong_secret(self):
        sig = self._sign(secret="wrong_secret")
        assert verify_webhook_signature(self.PAYLOAD, sig, self.SECRET) is False

    def test_tampered_payload(self):
        sig = self._sign()
        tampered = b'{"test": "tampered"}'
        assert verify_webhook_signature(tampered, sig, self.SECRET) is False
