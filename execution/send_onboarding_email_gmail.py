#!/usr/bin/env python3
"""Send an onboarding email via Gmail API and log the send."""

import argparse
import base64
import csv
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
import logging
import os
import sys

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError as exc:
    raise SystemExit(
        "Missing Google API dependencies. Install: pip install google-api-python-client google-auth-httplib2 "
        "google-auth-oauthlib"
    ) from exc

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def load_env() -> None:
    if os.path.exists(".env"):
        try:
            from dotenv import load_dotenv  # type: ignore
        except ImportError:
            return
        load_dotenv(".env")


def get_credentials(credentials_path: str, token_path: str) -> Credentials:
    """Get Google OAuth credentials with logging."""
    creds = None
    if os.path.exists(token_path):
        logging.debug(f"Loading existing token from {token_path}")
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logging.info("Refreshing expired credentials")
            creds.refresh(Request())
        else:
            logging.info("Starting OAuth flow")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w", encoding="utf-8") as handle:
            handle.write(creds.to_json())
        logging.info(f"Saved credentials to {token_path}")
    return creds


def send_email(creds: Credentials, sender: str, recipient: str, subject: str, body: str) -> str:
    """Send email via Gmail API with logging."""
    logging.info(f"Sending email to {recipient} with subject: {subject}")
    service = build("gmail", "v1", credentials=creds)
    message = EmailMessage()
    message["To"] = recipient
    message["From"] = sender
    message["Subject"] = subject
    message.set_content(body)
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    response = service.users().messages().send(userId="me", body={"raw": raw_message}).execute()
    message_id = response.get("id", "")
    logging.info(f"Email sent successfully, message ID: {message_id}")
    return message_id


def log_send(log_path: str, recipient: str, subject: str, sender: str, message_id: str, template_version: str) -> None:
    """Log email send to CSV."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    file_exists = os.path.exists(log_path)
    with open(log_path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if not file_exists:
            writer.writerow(["timestamp_utc", "recipient", "subject", "sender", "message_id", "template_version"])
        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            recipient,
            subject,
            sender,
            message_id,
            template_version,
        ])
    logging.debug(f"Logged send to {log_path}")


def should_skip_send(
    log_path: str,
    recipient: str,
    subject: str,
    sender: str,
    template_version: str,
    window_hours: int,
) -> bool:
    """Check if duplicate email was recently sent."""
    if not os.path.exists(log_path):
        logging.debug(f"No log file found at {log_path}, proceeding with send")
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    logging.debug(f"Checking for duplicates within {window_hours} hours (since {cutoff.isoformat()})")
    with open(log_path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                sent_at = datetime.fromisoformat(row.get("timestamp_utc", ""))
            except ValueError:
                continue
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)
            if sent_at < cutoff:
                continue
            if (
                row.get("recipient") == recipient
                and row.get("subject") == subject
                and row.get("sender") == sender
                and row.get("template_version") == template_version
            ):
                logging.warning(f"Duplicate found: email to {recipient} sent at {sent_at.isoformat()}")
                return True
    return False


def build_onboarding_body(first_name: str, scheduling_link: str, sender_name: str, template_path: str = "") -> str:
    """Build email body from template file or default template."""
    if template_path and os.path.exists(template_path):
        logging.debug(f"Loading template from {template_path}")
        with open(template_path, "r", encoding="utf-8") as handle:
            template = handle.read()
        # Replace placeholders
        scheduling_line = f"We'll schedule our kickoff call ({scheduling_link})" if scheduling_link else "I'll follow up with a kickoff time"
        body = template.replace("{first_name}", first_name)
        body = body.replace("{sender_name}", sender_name)
        body = body.replace("{scheduling_line}", scheduling_line)
        return body
    else:
        # Fallback to hardcoded template
        lines = [
            f"Hi {first_name},",
            "",
            "Thanks for signing up to work together — I'm excited to get started.",
            "",
            "Here's what happens next:",
            "- I'll review your intake details and draft an initial plan",
        ]
        if scheduling_link:
            lines.append(f"- We'll schedule our kickoff call ({scheduling_link})")
        else:
            lines.append("- I'll follow up with a kickoff time")
        lines.extend([
            "- I'll share a shared workspace and any prep materials",
            "",
            "If you have any immediate questions, just reply here.",
            "",
            f"Best,\n{sender_name}",
        ])
        return "\n".join(lines)


def main() -> int:
    load_env()

    # Setup logging
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Send onboarding email via Gmail API.")
    parser.add_argument("--to", required=True, help="Recipient email address")
    parser.add_argument("--from", dest="sender", required=True, help="Sender email address")
    parser.add_argument("--first-name", required=True, help="Recipient first name")
    parser.add_argument("--sender-name", required=True, help="Sender name for signature")
    parser.add_argument("--scheduling-link", default="", help="Scheduling link to include")
    parser.add_argument("--subject", default="Welcome — next steps for our work together", help="Email subject")
    parser.add_argument("--body", help="Raw email body to send instead of the onboarding template")
    parser.add_argument("--body-file", help="Path to a text file containing the email body")
    parser.add_argument(
        "--template-file",
        default=os.getenv("ONBOARDING_TEMPLATE_PATH", ".tmp/onboarding_email_template.txt"),
        help="Path to email template file with {first_name}, {sender_name}, {scheduling_line} placeholders",
    )
    parser.add_argument(
        "--credentials",
        default=os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json"),
        help="Path to OAuth client credentials JSON",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("GMAIL_TOKEN_PATH", "token.json"),
        help="Path to OAuth token JSON",
    )
    parser.add_argument(
        "--log-path",
        default=os.getenv("ONBOARDING_LOG_PATH", ".tmp/onboarding_sends.csv"),
        help="CSV log path",
    )
    parser.add_argument(
        "--template-version",
        default=os.getenv("ONBOARDING_TEMPLATE_VERSION", "default-v1"),
        help="Template version label",
    )
    parser.add_argument(
        "--duplicate-window-hours",
        type=int,
        default=int(os.getenv("ONBOARDING_DUPLICATE_WINDOW_HOURS", "24")),
        help="Skip send if a matching email was sent within this window",
    )
    parser.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="Send even if a matching email was recently logged",
    )
    args = parser.parse_args()

    logging.info(f"Starting onboarding email send to {args.to}")

    if not os.path.exists(args.credentials):
        logging.error(f"Missing credentials file: {args.credentials}")
        raise SystemExit(f"Missing credentials file: {args.credentials}")

    if args.body_file:
        logging.debug(f"Loading email body from {args.body_file}")
        with open(args.body_file, "r", encoding="utf-8") as handle:
            body = handle.read()
    elif args.body:
        logging.debug("Using provided email body")
        body = args.body
    else:
        logging.debug("Building email from template")
        body = build_onboarding_body(args.first_name, args.scheduling_link, args.sender_name, args.template_file)

    if not args.allow_duplicate and should_skip_send(
        args.log_path,
        args.to,
        args.subject,
        args.sender,
        args.template_version,
        args.duplicate_window_hours,
    ):
        logging.info("Skipped: recent matching send found in log")
        print("Skipped: recent matching send found in log.")
        return 0

    creds = get_credentials(args.credentials, args.token)
    message_id = send_email(creds, args.sender, args.to, args.subject, body)
    log_send(args.log_path, args.to, args.subject, args.sender, message_id, args.template_version)
    logging.info(f"Completed successfully, message ID: {message_id}")
    print(f"Sent message id: {message_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
