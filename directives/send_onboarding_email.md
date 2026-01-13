# Directive: Send onboarding email to new consulting client

## Goal
Send a consistent onboarding email to each consulting client as soon as they sign up, using a standard template and any client-specific details provided at signup.

## Triggers
- A new client signs a consulting agreement, pays an invoice, or completes a signup form.

## Inputs
- Client full name
- Client email address
- Company name (optional)
- Engagement start date (optional)
- Time zone (optional)
- Primary goals or scope (optional)
- Preferred scheduling link (optional)
- Any required attachments (optional)

## Outputs
- A sent onboarding email to the client
- A record of the send (timestamp, subject, recipient, and template version)

## Tools / Scripts
- Check `execution/` for an existing email sender script.
- If none exists, create a deterministic script in `execution/` to send email via the chosen provider (Gmail API, SendGrid, or SMTP).
- Prefer environment variables in `.env` for credentials and logging paths:
  - `GMAIL_CREDENTIALS_PATH`
  - `GMAIL_TOKEN_PATH`
  - `ONBOARDING_LOG_PATH`
  - `ONBOARDING_TEMPLATE_VERSION`

## Process
1. Verify required inputs (name and email). If missing, request them.
2. Select the onboarding template and fill in any provided client-specific fields.
3. Check the send log for recent duplicates and skip if a matching email was sent within the configured window.
4. Only override duplicate protection when explicitly requested.
5. Send the email.
6. Log the send in a local record (e.g., `.tmp/onboarding_sends.csv`) or a cloud sheet if configured.
7. If the send fails, capture the error, retry once, and if it still fails, report back with the error details.

## Email Template (Default)
Subject: Welcome — next steps for our work together

Hi {first_name},

Thanks for signing up to work together — I’m excited to get started.

Here’s what happens next:
- I’ll review your intake details and draft an initial plan
- We’ll schedule our kickoff call ({scheduling_link})
- I’ll share a shared workspace and any prep materials

If you have any immediate questions, just reply here.

Best,
{your_name}

## Edge Cases
- Missing scheduling link: remove the scheduling line or replace with “I’ll follow up with a kickoff time.”
- Multiple contacts: send individually and log each recipient.
- Attachments required: confirm file paths before sending.

## Notes
- Update this directive when email provider limits, rate limits, or template changes are discovered.
- If `token.json` is a placeholder, delete it before first OAuth run so the script can generate a real token.
- Duplicate protection is controlled via `ONBOARDING_DUPLICATE_WINDOW_HOURS` or `--duplicate-window-hours`.
