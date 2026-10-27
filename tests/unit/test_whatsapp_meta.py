import hashlib
import hmac

from app.integrations.whatsapp.meta import (
    parse_meta_whatsapp_text_messages,
    verify_meta_signature,
)


def test_parse_meta_whatsapp_text_message():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "contacts": [
                                {
                                    "wa_id": "995555000111",
                                    "profile": {"name": "111"},
                                }
                            ],
                            "messages": [
                                {
                                    "from": "995555000111",
                                    "id": "wamid.TEST123",
                                    "type": "text",
                                    "text": {"body": "#223 ნიკორა ჩასვი პარასკევზე"},
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }

    messages = parse_meta_whatsapp_text_messages(payload)

    assert len(messages) == 1
    message = messages[0]
    assert message.external_message_id == "wamid.TEST123"
    assert message.sender_id == "995555000111"
    assert message.sender_name == "111"
    assert message.text == "#223 ნიკორა ჩასვი პარასკევზე"


def test_parse_ignores_statuses_and_non_text_messages():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [{"id": "wamid.STATUS", "status": "delivered"}],
                            "messages": [
                                {
                                    "from": "995555000111",
                                    "id": "wamid.IMAGE",
                                    "type": "image",
                                    "image": {"id": "media-id"},
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }

    assert parse_meta_whatsapp_text_messages(payload) == []


def test_parse_returns_empty_for_other_object():
    assert parse_meta_whatsapp_text_messages({"object": "something_else"}) == []


def test_verify_meta_signature():
    body = b'{"hello":"world"}'
    secret = "test-secret"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert verify_meta_signature(
        body=body,
        signature=f"sha256={digest}",
        app_secret=secret,
    )
    assert not verify_meta_signature(
        body=body,
        signature="sha256=bad",
        app_secret=secret,
    )
    assert not verify_meta_signature(body=body, signature=None, app_secret=secret)
