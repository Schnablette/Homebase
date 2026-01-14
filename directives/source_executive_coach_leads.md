## Directive: Source executive coach leads

### Goal
Find executive coach leads (non-technical) in the Eastern time zone, capture key details, and deliver them in a Google Sheet. Save any intermediate data to `.tmp/`.

### Inputs
- Lead count (default: 3)
- Geography: Eastern time zone (US East Coast states/cities)
- Exclusions: technical/engineering/IT-focused coaches

### Tools / Scripts
- `execution/source_executive_coach_leads.py`

### Outputs
- `.tmp/lead_candidates.csv` with columns: name, role, website_url, linkedin_url, location, specialty, evidence
- A new Google Sheet containing the same rows (URL printed to stdout)

### Process
1. Use the execution script to search public web sources for executive coaches with personal websites.
2. Extract name, role, website URL, LinkedIn URL, location, specialty, and evidence.
3. Filter to non-technical executive coaching.
4. Write candidates to `.tmp/lead_candidates.csv`.
5. Create a Google Sheet and load the rows.

### Edge Cases
- If a LinkedIn URL is not present on the personal website, perform a follow-up search using the person's name.
- If location is ambiguous, infer from site text; otherwise leave blank.
- If Google API credentials are missing, exit with a clear message and still write the CSV.

### Technical Details
- The script uses `token_sheets.json` (not `token.json`) for Google Sheets authentication by default.
- Rate limiting: 1 second delay between web requests to avoid being blocked.
- Retry logic: Up to 3 retries with 2 second delays for failed network requests.
- Logging: Set `LOG_LEVEL=DEBUG` in `.env` for verbose output during troubleshooting.
