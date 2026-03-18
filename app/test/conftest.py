import hashlib
import hmac
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
import httpx
from fastapi.testclient import TestClient

# Set env vars before importing app modules
os.environ.setdefault("WHATSAPP_API_TOKEN", "test_token")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "123456789")
os.environ.setdefault("WHATSAPP_VERIFY_TOKEN", "test_verify_token")
os.environ.setdefault("WHATSAPP_APP_SECRET", "test_app_secret")

from app.config import get_settings, Settings
from app.main import app


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Clear the lru_cache on get_settings between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def settings() -> Settings:
    return get_settings()


@pytest.fixture()
def client():
    """Synchronous FastAPI test client with a mocked http_client on app state."""
    with TestClient(app) as c:
        # Replace the real httpx client with a mock for outbound calls
        app.state.http_client = AsyncMock(spec=httpx.AsyncClient)
        # Default response for POST calls to WhatsApp API
        # Use MagicMock because httpx Response.json() and raise_for_status() are sync
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "messaging_product": "whatsapp",
            "contacts": [{"input": "1234567890", "wa_id": "1234567890"}],
            "messages": [{"id": "wamid.test123"}],
        }
        app.state.http_client.post.return_value = mock_response
        yield c


# ── Sample payloads ──────────────────────────────────────────────

SAMPLE_WEBHOOK_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
            "changes": [
                {
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "15550000000",
                            "phone_number_id": "123456789",
                        },
                        "contacts": [
                            {
                                "profile": {"name": "Test User"},
                                "wa_id": "919999999999",
                            }
                        ],
                        "messages": [
                            {
                                "from": "919999999999",
                                "id": "wamid.HBgLMTIzNDU2Nzg5MBUCABIYFjNF",
                                "timestamp": "1709712000",
                                "text": {"body": "Hello from test"},
                                "type": "text",
                            }
                        ],
                    },
                    "field": "messages",
                }
            ],
        }
    ],
}

SAMPLE_STATUS_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
            "changes": [
                {
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "15550000000",
                            "phone_number_id": "123456789",
                        },
                        "statuses": [
                            {
                                "id": "wamid.HBgLMTIzNDU2Nzg5MBUCABIYFjNF",
                                "status": "delivered",
                                "timestamp": "1709712001",
                                "recipient_id": "919999999999",
                            }
                        ],
                    },
                    "field": "messages",
                }
            ],
        }
    ],
}


def make_signature(payload_bytes: bytes, secret: str = "test_app_secret") -> str:
    """Generate a valid X-Hub-Signature-256 for test payloads."""
    sig = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={sig}"
