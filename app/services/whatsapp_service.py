import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class IncomingMessage:
    """Parsed incoming WhatsApp message."""
    from_number: str
    message_id: str
    timestamp: str
    text: str
    name: str | None = None


class WhatsAppService:
    def __init__(self, http_client: httpx.AsyncClient):
        self.settings = get_settings()
        self.http_client = http_client
        self.base_url = (
            f"{self.settings.whatsapp_api_url}"
            f"/{self.settings.whatsapp_phone_number_id}"
        )
        self.headers = {
            "Authorization": f"Bearer {self.settings.whatsapp_api_token}",
            "Content-Type": "application/json",
        }

    # ── Sending ──────────────────────────────────────────────

    async def send_text_message(self, to: str, body: str) -> dict:
        """Send a text message to a WhatsApp number."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        response = await self.http_client.post(
            f"{self.base_url}/messages",
            headers=self.headers,
            json=payload,
        )
        if response.status_code != 200:
            logger.error(
                "WhatsApp API error %s: %s", response.status_code, response.text
            )
            response.raise_for_status()
        data = response.json()
        logger.info("Message sent to %s, id=%s", to, data.get("messages", [{}])[0].get("id"))
        return data

    async def mark_as_read(self, message_id: str) -> None:
        """Mark an incoming message as read (blue ticks)."""
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        response = await self.http_client.post(
            f"{self.base_url}/messages",
            headers=self.headers,
            json=payload,
        )
        response.raise_for_status()

    async def send_document(self, to: str, file_path: str,
                           filename: str | None = None,
                           caption: str = "") -> dict:
        """
        Upload a document to WhatsApp media and send it.
        1. Upload file to media API
        2. Send document message with media_id
        """
        import os
        if not filename:
            filename = os.path.basename(file_path)

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"PDF not found: {file_path}")

        file_size = os.path.getsize(file_path)
        logger.info("Uploading document %s (%d KB) to WhatsApp…", filename, file_size // 1024)

        # Step 1: Upload media
        with open(file_path, "rb") as f:
            upload_resp = await self.http_client.post(
                f"{self.base_url}/media",
                headers={"Authorization": f"Bearer {self.settings.whatsapp_api_token}"},
                files={"file": (filename, f, "application/pdf")},
                data={"messaging_product": "whatsapp", "type": "application/pdf"},
            )
        if upload_resp.status_code != 200:
            logger.error("Media upload failed %s: %s",
                         upload_resp.status_code, upload_resp.text)
            upload_resp.raise_for_status()

        media_id = upload_resp.json().get("id")
        if not media_id:
            raise ValueError(f"WhatsApp media upload returned no ID: {upload_resp.text}")
        logger.info("Media uploaded, id=%s", media_id)

        # Step 2: Send document message
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "document",
            "document": {
                "id": media_id,
                "filename": filename,
            },
        }
        if caption:
            payload["document"]["caption"] = caption

        response = await self.http_client.post(
            f"{self.base_url}/messages",
            headers=self.headers,
            json=payload,
        )
        if response.status_code != 200:
            logger.error("WhatsApp document send error %s: %s",
                         response.status_code, response.text)
            response.raise_for_status()
        data = response.json()
        logger.info("Document sent to %s, id=%s", to, data.get("messages", [{}])[0].get("id"))
        return data

    # ── Receiving / parsing ──────────────────────────────────

    @staticmethod
    def parse_webhook_payload(payload: dict) -> list[IncomingMessage]:
        """
        Extract messages from the webhook payload sent by Meta.
        Returns a list because a single webhook can carry multiple messages.
        """
        messages: list[IncomingMessage] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                contacts = {
                    c["wa_id"]: c.get("profile", {}).get("name")
                    for c in value.get("contacts", [])
                }
                for msg in value.get("messages", []):
                    if msg.get("type") != "text":
                        logger.debug("Skipping non-text message type: %s", msg.get("type"))
                        continue
                    messages.append(
                        IncomingMessage(
                            from_number=msg["from"],
                            message_id=msg["id"],
                            timestamp=msg["timestamp"],
                            text=msg["text"]["body"],
                            name=contacts.get(msg["from"]),
                        )
                    )
        return messages
