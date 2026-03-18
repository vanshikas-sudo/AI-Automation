from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.whatsapp_service import WhatsAppService, IncomingMessage
from app.test.conftest import SAMPLE_WEBHOOK_PAYLOAD, SAMPLE_STATUS_PAYLOAD


# ── parse_webhook_payload ────────────────────────────────────────


class TestParseWebhookPayload:
    """Tests for parsing Meta webhook payloads into IncomingMessage objects."""

    def test_single_text_message(self):
        messages = WhatsAppService.parse_webhook_payload(SAMPLE_WEBHOOK_PAYLOAD)
        assert len(messages) == 1
        msg = messages[0]
        assert msg.from_number == "919999999999"
        assert msg.text == "Hello from test"
        assert msg.name == "Test User"
        assert msg.message_id == "wamid.HBgLMTIzNDU2Nzg5MBUCABIYFjNF"
        assert msg.timestamp == "1709712000"

    def test_status_update_yields_no_messages(self):
        messages = WhatsAppService.parse_webhook_payload(SAMPLE_STATUS_PAYLOAD)
        assert messages == []

    def test_empty_payload(self):
        messages = WhatsAppService.parse_webhook_payload({})
        assert messages == []

    def test_non_text_message_is_skipped(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "123", "profile": {"name": "A"}}],
                                "messages": [
                                    {
                                        "from": "123",
                                        "id": "wamid.img",
                                        "timestamp": "100",
                                        "type": "image",
                                        "image": {"id": "img123"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        messages = WhatsAppService.parse_webhook_payload(payload)
        assert messages == []

    def test_multiple_messages_in_one_payload(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [
                                    {"wa_id": "111", "profile": {"name": "Alice"}},
                                    {"wa_id": "222", "profile": {"name": "Bob"}},
                                ],
                                "messages": [
                                    {"from": "111", "id": "m1", "timestamp": "1", "type": "text", "text": {"body": "Hi"}},
                                    {"from": "222", "id": "m2", "timestamp": "2", "type": "text", "text": {"body": "Hey"}},
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        messages = WhatsAppService.parse_webhook_payload(payload)
        assert len(messages) == 2
        assert messages[0].name == "Alice"
        assert messages[1].name == "Bob"

    def test_contact_without_profile(self):
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "111"}],
                                "messages": [
                                    {"from": "111", "id": "m1", "timestamp": "1", "type": "text", "text": {"body": "Hi"}},
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        messages = WhatsAppService.parse_webhook_payload(payload)
        assert len(messages) == 1
        assert messages[0].name is None


# ── send_text_message / mark_as_read (mocked HTTP) ──────────────


class TestWhatsAppServiceAsync:
    """Tests for outbound WhatsApp API calls with mocked httpx."""

    @pytest.fixture()
    def mock_client(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "messaging_product": "whatsapp",
            "messages": [{"id": "wamid.sent123"}],
        }
        client.post.return_value = resp
        return client

    @pytest.mark.asyncio
    async def test_send_text_message(self, mock_client):
        wa = WhatsAppService(mock_client)
        result = await wa.send_text_message(to="919999999999", body="Hello!")
        assert result["messages"][0]["id"] == "wamid.sent123"
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        sent_json = call_kwargs.kwargs["json"]
        assert sent_json["to"] == "919999999999"
        assert sent_json["text"]["body"] == "Hello!"
        assert sent_json["messaging_product"] == "whatsapp"

    @pytest.mark.asyncio
    async def test_mark_as_read(self, mock_client):
        wa = WhatsAppService(mock_client)
        await wa.mark_as_read("wamid.abc123")
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        sent_json = call_kwargs.kwargs["json"]
        assert sent_json["status"] == "read"
        assert sent_json["message_id"] == "wamid.abc123"

    @pytest.mark.asyncio
    async def test_send_message_raises_on_http_error(self, mock_client):
        resp = MagicMock()
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=MagicMock()
        )
        mock_client.post.return_value = resp
        wa = WhatsAppService(mock_client)
        with pytest.raises(httpx.HTTPStatusError):
            await wa.send_text_message(to="123", body="fail")
