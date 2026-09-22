"""
Handles Gmail OAuth and pulling course-related emails.

First run will open a browser window asking you to log into the Google
account you want to read email from, then caches a token so you don't
have to log in again.
"""

import base64
import os
from email.utils import parsedate_to_datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")


def get_service():
    """Returns an authenticated Gmail API service object, handling the
    OAuth dance and token refresh/caching."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(
                    "credentials.json not found next to this script. "
                    "Download it from Google Cloud Console (OAuth client "
                    "for a Desktop app) and place it here."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_PATH, SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def build_query(senders: dict, days_back: int, extra: str = "") -> str:
    """Builds a Gmail search query that matches any of the configured
    sender addresses/domains, restricted to the last N days."""
    clauses = []
    for addr in senders.get("emails", []):
        clauses.append(f"from:{addr}")
    for domain in senders.get("domains", []):
        clauses.append(f"from:@{domain}")

    if not clauses:
        raise ValueError("config.json has no sender emails or domains set")

    sender_part = "(" + " OR ".join(clauses) + ")"
    query = f"{sender_part} newer_than:{days_back}d"
    if extra:
        query += f" {extra}"
    return query


def _extract_plain_text(payload) -> str:
    """Walks a Gmail message payload to find the plain-text body."""
    if payload.get("mimeType") == "text/plain" and "data" in payload.get(
        "body", {}
    ):
        data = payload["body"]["data"]
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    for part in payload.get("parts", []) or []:
        text = _extract_plain_text(part)
        if text:
            return text
    return ""


def _fetch_full(service, msg_id: str) -> dict:
    """Fetches and parses a single message into our email dict shape."""
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format="full")
        .execute()
    )
    headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    subject = headers.get("Subject", "(no subject)")
    sender = headers.get("From", "(unknown sender)")
    date_raw = headers.get("Date", "")
    try:
        date = parsedate_to_datetime(date_raw).strftime("%Y-%m-%d")
    except Exception:
        date = date_raw

    body = _extract_plain_text(msg["payload"]) or msg.get("snippet", "")
    body = body.strip()[:3000]  # keep prompts a reasonable size

    return {
        "id": msg_id,
        "sender": sender,
        "subject": subject,
        "date": date,
        "body": body,
    }


def _list_message_ids(service, senders: dict, days_back: int, max_results: int):
    query = build_query(senders, days_back)
    results = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    return [m["id"] for m in results.get("messages", [])]


def fetch_course_emails(service, senders: dict, days_back: int, max_emails: int):
    """Returns full dicts (id, sender, subject, date, body) for every
    email matching the sender list within the lookback window. Used
    for the full weekly rebuild."""
    ids = _list_message_ids(service, senders, days_back, max_emails)
    return [_fetch_full(service, msg_id) for msg_id in ids]


def fetch_new_emails(
    service, senders: dict, days_back: int, max_emails: int, processed_ids: set
):
    """Same search as fetch_course_emails, but only fetches full
    content for messages not already in processed_ids. Used for
    incremental checks so we don't re-download/re-process mail
    already folded into the summary."""
    ids = _list_message_ids(service, senders, days_back, max_emails)
    new_ids = [i for i in ids if i not in processed_ids]
    return [_fetch_full(service, msg_id) for msg_id in new_ids]