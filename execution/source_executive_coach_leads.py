#!/usr/bin/env python3
"""Source executive coach leads and write them to CSV + Google Sheets."""

from __future__ import annotations

import argparse
import base64
import csv
from dataclasses import dataclass
from datetime import datetime
import html
from html.parser import HTMLParser
import logging
import os
import re
import sys
import time
from typing import List, Optional, Sequence
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request, urlopen

try:
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    Credentials = None  # type: ignore


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0"
DEFAULT_LIMIT = 3
REQUEST_DELAY = 1.0  # seconds between requests to avoid rate limiting
MAX_RETRIES = 3
RETRY_DELAY = 2.0  # seconds between retries

EAST_COAST_STATES = [
    ("Maine", "ME"),
    ("New Hampshire", "NH"),
    ("Vermont", "VT"),
    ("Massachusetts", "MA"),
    ("Rhode Island", "RI"),
    ("Connecticut", "CT"),
    ("New York", "NY"),
    ("New Jersey", "NJ"),
    ("Pennsylvania", "PA"),
    ("Delaware", "DE"),
    ("Maryland", "MD"),
    ("District of Columbia", "DC"),
    ("Virginia", "VA"),
    ("North Carolina", "NC"),
    ("South Carolina", "SC"),
    ("Georgia", "GA"),
    ("Florida", "FL"),
]

SEARCH_SEEDS = [
    "executive coach",
    "leadership coach",
    "executive coaching",
]

EAST_COAST_CITIES = [
    "New York, NY",
    "Boston, MA",
    "Philadelphia, PA",
    "Washington, DC",
    "Baltimore, MD",
    "Richmond, VA",
    "Raleigh, NC",
    "Charlotte, NC",
    "Atlanta, GA",
    "Miami, FL",
    "Orlando, FL",
    "Tampa, FL",
]

EXCLUDE_KEYWORDS = [
    "software",
    "engineering",
    "technical",
    "developer",
    "devops",
    "product manager",
    "cto",
    "cio",
    "it",
]

SPECIALTY_KEYWORDS = [
    "leadership",
    "c-suite",
    "ceo",
    "founder",
    "team",
    "organizational",
    "career",
    "communication",
    "strategy",
    "performance",
    "women leaders",
    "executive presence",
    "succession",
]


def load_env() -> None:
    if os.path.exists(".env"):
        try:
            from dotenv import load_dotenv  # type: ignore
        except ImportError:
            return
        load_dotenv(".env")


@dataclass
class Lead:
    name: str
    role: str
    website_url: str
    linkedin_url: str
    location: str
    specialty: str
    evidence: str


class BingResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: List[str] = []
        self._in_result = False

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "li" and "class" in attrs_dict and "b_algo" in (attrs_dict.get("class") or ""):
            self._in_result = True
            return
        if tag == "a" and self._in_result:
            href = attrs_dict.get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self._in_result:
            self._in_result = False

class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = " ".join(data.split())
        if cleaned:
            self.parts.append(cleaned)

    def text(self) -> str:
        return " ".join(self.parts)


def fetch_url(url: str, timeout: int = 20, retries: int = MAX_RETRIES) -> str:
    """Fetch URL with retry logic."""
    last_error = None
    for attempt in range(retries):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                charset_match = re.search(r"charset=([\\w-]+)", content_type)
                encoding = charset_match.group(1) if charset_match else "utf-8"
                data = response.read()
                return data.decode(encoding, errors="replace")
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                logging.warning(f"Fetch failed (attempt {attempt + 1}/{retries}): {url} - {exc}")
                time.sleep(RETRY_DELAY)
            else:
                logging.error(f"Fetch failed after {retries} attempts: {url} - {exc}")
    raise last_error if last_error else Exception("Failed to fetch URL")


def extract_title(html_text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return html.unescape(match.group(1)).strip()


def extract_h1(html_text: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    text = re.sub(r"<[^>]+>", " ", match.group(1))
    return html.unescape(" ".join(text.split())).strip()

def normalize_bing_link(link: str) -> str:
    if "bing.com/ck/a" not in link:
        return link
    parsed = urlparse(link)
    query = parse_qs(parsed.query)
    encoded = query.get("u", [""])[0]
    if not encoded:
        return link
    if encoded.startswith("a1"):
        encoded = encoded[2:]
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8", errors="replace")
        if decoded.startswith("http"):
            return decoded
    except Exception:
        return link
    return link


def bing_search(query: str, max_results: int = 10) -> List[str]:
    """Search Bing with rate limiting."""
    time.sleep(REQUEST_DELAY)  # Rate limit
    url = f"https://www.bing.com/search?q={quote_plus(query)}"
    html_text = fetch_url(url)
    parser = BingResultParser()
    parser.feed(html_text)
    results = []
    seen = set()
    for link in parser.links:
        normalized = normalize_bing_link(link)
        if not normalized.startswith("http"):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        results.append(normalized)
        if len(results) >= max_results:
            break
    logging.info(f"Found {len(results)} results for query: {query}")
    return results


def contains_excluded_keywords(text: str) -> bool:
    lower = text.lower()
    return any(keyword in lower for keyword in EXCLUDE_KEYWORDS)


def looks_like_executive_coach(text: str) -> bool:
    lower = text.lower()
    return "executive coach" in lower or "executive coaching" in lower or "leadership coach" in lower


def extract_linkedin(html_text: str) -> str:
    match = re.search(r"https?://(?:www\\.)?linkedin\\.com/[\\w\\-/%?=&#.]+", html_text, re.IGNORECASE)
    if match:
        return match.group(0).rstrip(").,")
    return ""


def extract_location(text: str) -> str:
    for state_name, state_abbr in EAST_COAST_STATES:
        pattern = rf"\\b([A-Z][a-z]+(?:\\s+[A-Z][a-z]+)?),\\s*{state_abbr}\\b"
        match = re.search(pattern, text)
        if match:
            return f"{match.group(1)}, {state_abbr}"
        if state_name.lower() in text.lower():
            return state_name
    return ""


def extract_specialty(text: str) -> str:
    lower = text.lower()
    found = []
    for keyword in SPECIALTY_KEYWORDS:
        if keyword in lower:
            found.append(keyword)
    if not found:
        return ""
    return ", ".join(found[:3])


def extract_evidence(text: str) -> str:
    lowered = text.lower()
    for phrase in ["executive coach", "executive coaching", "leadership coach"]:
        idx = lowered.find(phrase)
        if idx != -1:
            start = max(0, idx - 60)
            end = min(len(text), idx + 120)
            snippet = text[start:end]
            return " ".join(snippet.split())
    return ""


def guess_name(title: str, h1: str) -> str:
    candidate = h1 or title
    if not candidate:
        return ""
    for sep in ["|", "-", "—", ":"]:
        if sep in candidate:
            candidate = candidate.split(sep)[0]
            break
    return " ".join(candidate.split()).strip()


def search_linkedin(name: str) -> str:
    """Search for LinkedIn profile with rate limiting."""
    if not name:
        return ""
    query = f'"{name}" executive coach LinkedIn'
    try:
        results = bing_search(query, max_results=5)
        for link in results:
            if "linkedin.com/in" in link.lower():
                return link
    except Exception as exc:
        logging.warning(f"LinkedIn search failed for {name}: {exc}")
    return ""


def build_candidates(limit: int) -> List[Lead]:
    """Build candidate list with improved error handling and logging."""
    leads: List[Lead] = []
    seen_domains = set()

    search_targets = EAST_COAST_CITIES + [state for state, _ in EAST_COAST_STATES]
    logging.info(f"Starting search for {limit} leads across {len(search_targets)} locations")

    for target in search_targets:
        for seed in SEARCH_SEEDS:
            if len(leads) >= limit:
                logging.info(f"Reached target of {limit} leads")
                return leads
            query = f'"{seed}" "{target}"'
            try:
                results = bing_search(query, max_results=10)
            except Exception as exc:
                logging.error(f"Search failed for query '{query}': {exc}")
                continue
            for link in results:
                if len(leads) >= limit:
                    return leads
                parsed = urlparse(link)
                domain = parsed.netloc.lower()
                if not domain or domain in seen_domains:
                    continue
                if "linkedin.com" in domain:
                    continue
                seen_domains.add(domain)
                try:
                    html_text = fetch_url(link)
                    time.sleep(REQUEST_DELAY)  # Rate limit between page fetches
                except Exception as exc:
                    logging.warning(f"Failed to fetch {link}: {exc}")
                    continue
                title = extract_title(html_text)
                h1 = extract_h1(html_text)
                text_extractor = TextExtractor()
                text_extractor.feed(html_text)
                text = text_extractor.text()
                if not looks_like_executive_coach(text):
                    logging.debug(f"Skipping {link}: doesn't look like executive coach")
                    continue
                if contains_excluded_keywords(text):
                    logging.debug(f"Skipping {link}: contains excluded keywords")
                    continue
                name = guess_name(title, h1)
                location = extract_location(text)
                if not location and "," in target:
                    location = target
                elif not location:
                    location = target
                linkedin_url = extract_linkedin(html_text)
                if not linkedin_url:
                    linkedin_url = search_linkedin(name)
                specialty = extract_specialty(text)
                evidence = extract_evidence(text)
                lead = Lead(
                    name=name or "Unknown",
                    role="Executive Coach",
                    website_url=link,
                    linkedin_url=linkedin_url,
                    location=location,
                    specialty=specialty,
                    evidence=evidence,
                )
                leads.append(lead)
                logging.info(f"Added lead #{len(leads)}: {lead.name} from {location}")
    return leads


def write_csv(leads: Sequence[Lead], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "role", "website_url", "linkedin_url", "location", "specialty", "evidence"])
        for lead in leads:
            writer.writerow([
                lead.name,
                lead.role,
                lead.website_url,
                lead.linkedin_url,
                lead.location,
                lead.specialty,
                lead.evidence,
            ])


def get_credentials(credentials_path: str, token_path: str) -> "Credentials":
    if Credentials is None:
        raise SystemExit(
            "Missing Google API dependencies. Install: pip install google-api-python-client google-auth-httplib2 "
            "google-auth-oauthlib"
        )
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w", encoding="utf-8") as handle:
            handle.write(creds.to_json())
    return creds


def write_google_sheet(leads: Sequence[Lead], credentials_path: str, token_path: str, title: str) -> str:
    creds = get_credentials(credentials_path, token_path)
    service = build("sheets", "v4", credentials=creds)
    sheet_body = {"properties": {"title": title}}
    spreadsheet = service.spreadsheets().create(body=sheet_body, fields="spreadsheetId").execute()
    spreadsheet_id = spreadsheet["spreadsheetId"]
    values = [["name", "role", "website_url", "linkedin_url", "location", "specialty", "evidence"]]
    for lead in leads:
        values.append([
            lead.name,
            lead.role,
            lead.website_url,
            lead.linkedin_url,
            lead.location,
            lead.specialty,
            lead.evidence,
        ])
    body = {"values": values}
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="Sheet1!A1",
        valueInputOption="RAW",
        body=body,
    ).execute()
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"


def main() -> int:
    load_env()

    # Setup logging
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Source executive coach leads.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Number of leads to collect")
    parser.add_argument("--csv-path", default=".tmp/lead_candidates.csv", help="CSV output path")
    parser.add_argument(
        "--credentials",
        default=os.getenv("SHEETS_CREDENTIALS_PATH", "credentials.json"),
        help="Path to Google OAuth client credentials JSON",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("SHEETS_TOKEN_PATH", "token_sheets.json"),
        help="Path to Google OAuth token JSON",
    )
    args = parser.parse_args()

    logging.info(f"Starting lead sourcing: target={args.limit} leads")
    leads = build_candidates(args.limit)
    if not leads:
        logging.error("No leads found")
        print("No leads found.")
        return 1

    write_csv(leads, args.csv_path)
    logging.info(f"Wrote {len(leads)} leads to CSV: {args.csv_path}")
    print(f"Wrote CSV to {args.csv_path}")

    if not os.path.exists(args.credentials):
        print(f"Missing credentials file: {args.credentials}")
        print("Skipping Google Sheet creation.")
        return 0

    sheet_title = f"Executive Coach Leads - {datetime.now().strftime('%Y-%m-%d')}"
    try:
        sheet_url = write_google_sheet(leads, args.credentials, args.token, sheet_title)
    except Exception as exc:
        print(f"Google Sheet creation failed: {exc}")
        return 0
    print(f"Google Sheet: {sheet_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
