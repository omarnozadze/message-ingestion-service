from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WhatsAppInboundText:
    external_message_id: str
    sender_id: str
    sender_name: str | None
    text: str


def verify_meta_signature(*, body: bytes, signature: str | None, app_secret: str) -> bool:
    """Verify Meta's X-Hub-Signature-256 HMAC signature.

    When ``app_secret`` is configured, callers should reject missing or invalid
    signatures. Keeping this helper pure makes it straightforward to unit test.
    """
    if not app_secret or not signature:
        return False

    prefix = "sha256="
    if not signature.startswith(prefix):
        return False

    expected = hmac.new(
        app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    supplied = signature[len(prefix) :]
    return hmac.compare_digest(expected, supplied)


def parse_meta_whatsapp_text_messages(payload: dict[str, Any]) -> list[WhatsAppInboundText]:
    """Extract inbound text messages from a Meta WhatsApp webhook payload.

    Delivery/status callbacks and unsupported message types are intentionally
    ignored. The WhatsApp message id is preserved as the external id so the
    existing database uniqueness/idempotency guard handles webhook retries.
    """
    if payload.get("object") != "whatsapp_business_account":
        return []

    result: list[WhatsAppInboundText] = []

    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue

        for change in entry.get("changes") or []:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue

            value = change.get("value")
            if not isinstance(value, dict):
                continue

            contact_names: dict[str, str] = {}
            for contact in value.get("contacts") or []:
                if not isinstance(contact, dict):
                    continue
                wa_id = str(contact.get("wa_id") or "").strip()
                profile = contact.get("profile")
                name = None
                if isinstance(profile, dict):
                    candidate = profile.get("name")
                    if candidate is not None:
                        name = str(candidate).strip() or None
                if wa_id and name:
                    contact_names[wa_id] = name

            for message in value.get("messages") or []:
                if not isinstance(message, dict) or message.get("type") != "text":
                    continue

                message_id = str(message.get("id") or "").strip()
                sender_id = str(message.get("from") or "").strip()
                text_obj = message.get("text")
                text = None
                if isinstance(text_obj, dict):
                    body = text_obj.get("body")
                    if body is not None:
                        text = str(body).strip()

                if not message_id or not sender_id or not text:
                    continue

                result.append(
                    WhatsAppInboundText(
                        external_message_id=message_id,
                        sender_id=sender_id,
                        sender_name=contact_names.get(sender_id),
                        text=text,
                    )
                )

    return result
