import json

from app.test.conftest import SAMPLE_WEBHOOK_PAYLOAD, SAMPLE_STATUS_PAYLOAD, make_signature


class TestWebhookVerification:
    """Tests for GET /webhook (Meta's verification handshake)."""

    def test_valid_verification(self, client):
        resp = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "test_verify_token",
                "hub.challenge": "987654321",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == 987654321

    def test_wrong_verify_token(self, client):
        resp = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong_token",
                "hub.challenge": "123",
            },
        )
        assert resp.status_code == 403

    def test_wrong_mode(self, client):
        resp = client.get(
            "/webhook",
            params={
                "hub.mode": "unsubscribe",
                "hub.verify_token": "test_verify_token",
                "hub.challenge": "123",
            },
        )
        assert resp.status_code == 403

    def test_missing_params(self, client):
        resp = client.get("/webhook")
        assert resp.status_code == 422  # validation error


class TestWebhookReceiveMessage:
    """Tests for POST /webhook (incoming messages from Meta)."""

    def test_valid_text_message(self, client):
        body = json.dumps(SAMPLE_WEBHOOK_PAYLOAD).encode()
        sig = make_signature(body)
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_invalid_signature_rejected(self, client):
        body = json.dumps(SAMPLE_WEBHOOK_PAYLOAD).encode()
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=invalid",
            },
        )
        assert resp.status_code == 403

    def test_status_update_returns_ok(self, client):
        """Status webhooks (delivered/read) should be accepted with 200."""
        body = json.dumps(SAMPLE_STATUS_PAYLOAD).encode()
        sig = make_signature(body)
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_empty_entry_returns_ok(self, client):
        payload = {"object": "whatsapp_business_account", "entry": []}
        body = json.dumps(payload).encode()
        sig = make_signature(body)
        resp = client.post(
            "/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
            },
        )
        assert resp.status_code == 200


class TestSendMessageEndpoint:
    """Tests for POST /messages/send."""

    def test_send_message_success(self, client):
        resp = client.post(
            "/messages/send",
            json={"to": "919999999999", "message": "Hello!"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "sent"

    def test_send_message_missing_fields(self, client):
        resp = client.post("/messages/send", json={"to": "919999999999"})
        assert resp.status_code == 422

    def test_send_message_empty_body(self, client):
        resp = client.post("/messages/send", json={})
        assert resp.status_code == 422
