from __future__ import annotations

import base64
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAILJET_SEND_URL = "https://api.mailjet.com/v3.1/send"
MAILJET_TIMEOUT_SECONDS = 20
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailDeliveryError(RuntimeError):
    """An application email could not be accepted by the configured provider."""


def send_application_email(
    api_key: str,
    api_secret: str,
    sender_email: str,
    sender_name: str,
    recipient_email: str,
    subject: str,
    text_body: str,
    attachment: bytes | None = None,
    attachment_filename: str | None = None,
) -> str:
    for label, email in (
        ("expéditeur", sender_email),
        ("destinataire", recipient_email),
    ):
        if not EMAIL_PATTERN.fullmatch(email.strip()):
            raise EmailDeliveryError(f"L'adresse e-mail de {label} n'est pas valide.")
    if not api_key.strip() or not api_secret.strip():
        raise EmailDeliveryError("Les identifiants API Mailjet ne sont pas configurés.")
    if not sender_name.strip():
        raise EmailDeliveryError("Le nom de l'expéditeur doit être renseigné.")
    if not subject.strip() or "\r" in subject or "\n" in subject:
        raise EmailDeliveryError("L'objet du message est vide ou invalide.")
    if not text_body.strip():
        raise EmailDeliveryError("Le contenu du message ne peut pas être vide.")
    if attachment is not None and not attachment_filename:
        raise EmailDeliveryError("Le nom du fichier joint doit être renseigné.")

    message: dict[str, object] = {
        "From": {"Email": sender_email.strip(), "Name": sender_name.strip()},
        "To": [{"Email": recipient_email.strip()}],
        "Subject": subject.strip(),
        "TextPart": text_body.strip(),
    }
    if attachment is not None:
        message["Attachments"] = [
            {
                "ContentType": "application/pdf",
                "Filename": attachment_filename,
                "Base64Content": base64.b64encode(attachment).decode("ascii"),
            }
        ]
    payload = json.dumps({"Messages": [message]}).encode("utf-8")
    authorization = base64.b64encode(
        f"{api_key}:{api_secret}".encode()
    ).decode("ascii")
    request = Request(
        MAILJET_SEND_URL,
        data=payload,
        headers={
            "Accept": "application/json",
            "Authorization": f"Basic {authorization}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=MAILJET_TIMEOUT_SECONDS) as response:
            response_data = json.loads(response.read())
    except HTTPError as error:
        try:
            error_payload = json.loads(error.read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            error_payload = None
        detail = _mailjet_error_detail(error_payload)
        raise EmailDeliveryError(
            f"Mailjet a refusé l'envoi (HTTP {error.code})"
            + (f" : {detail}" if detail else ".")
        ) from error
    except URLError as error:
        raise EmailDeliveryError(
            f"Connexion à Mailjet impossible : {error.reason}"
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise EmailDeliveryError("Mailjet a renvoyé une réponse JSON invalide.") from error

    if not isinstance(response_data, dict):
        raise EmailDeliveryError("La réponse Mailjet n'a pas le format attendu.")
    messages = response_data.get("Messages")
    if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
        raise EmailDeliveryError("La réponse Mailjet ne contient aucun résultat d'envoi.")
    result = messages[0]
    if result.get("Status") != "success":
        detail = _mailjet_error_detail(result)
        raise EmailDeliveryError(
            "Mailjet n'a pas accepté le message."
            + (f" {detail}" if detail else "")
        )
    recipients = result.get("To")
    if not isinstance(recipients, list) or not recipients or not isinstance(
        recipients[0], dict
    ):
        raise EmailDeliveryError(
            "Mailjet n'a pas confirmé le destinataire ; vérifie l'état du message dans "
            "ton compte avant tout nouvel envoi."
        )
    return str(recipients[0].get("MessageID", "accepté"))


def _mailjet_error_detail(payload: object) -> str:
    if isinstance(payload, dict):
        if isinstance(payload.get("ErrorMessage"), str):
            return payload["ErrorMessage"]
        messages = payload.get("Messages")
        if isinstance(messages, list) and messages and isinstance(messages[0], dict):
            errors = messages[0].get("Errors")
            if isinstance(errors, list):
                return "; ".join(
                    error["ErrorMessage"]
                    for error in errors
                    if isinstance(error, dict)
                    and isinstance(error.get("ErrorMessage"), str)
                )
    return ""
