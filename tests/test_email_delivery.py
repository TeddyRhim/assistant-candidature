from __future__ import annotations

import base64
import json
from io import BytesIO
from unittest.mock import patch
from urllib.request import Request

import pytest

from src.services.email_delivery import EmailDeliveryError, send_application_email


def test_mailjet_sends_individual_message_with_optional_pdf_attachment() -> None:
    response = BytesIO(
        json.dumps(
            {
                "Messages": [
                    {
                        "Status": "success",
                        "To": [{"Email": "recruitment@example.com", "MessageID": 456}],
                    }
                ]
            }
        ).encode()
    )
    with patch("src.services.email_delivery.urlopen", return_value=response) as open_url:
        message_id = send_application_email(
            api_key="public",
            api_secret="private",
            sender_email="candidate@example.com",
            sender_name="Candidate",
            recipient_email="recruitment@example.com",
            subject="Candidature développeur",
            text_body="Bonjour,\nVeuillez trouver mon CV.",
            attachment=b"%PDF-test",
            attachment_filename="cv-cible.pdf",
        )

    request = open_url.call_args.args[0]
    assert isinstance(request, Request)
    assert request.full_url == "https://api.mailjet.com/v3.1/send"
    assert request.get_header("Authorization") == (
        "Basic " + base64.b64encode(b"public:private").decode("ascii")
    )
    payload = json.loads(request.data)
    message = payload["Messages"][0]
    assert message["To"] == [{"Email": "recruitment@example.com"}]
    assert message["Attachments"][0]["Base64Content"] == base64.b64encode(
        b"%PDF-test"
    ).decode("ascii")
    assert message_id == "456"


def test_mailjet_rejects_invalid_email_and_empty_message() -> None:
    with pytest.raises(EmailDeliveryError, match="destinataire"):
        send_application_email(
            "public",
            "private",
            "candidate@example.com",
            "Candidate",
            "not-an-email",
            "Subject",
            "Body",
        )
    with pytest.raises(EmailDeliveryError, match="vide"):
        send_application_email(
            "public",
            "private",
            "candidate@example.com",
            "Candidate",
            "recruitment@example.com",
            "Subject",
            "",
        )
