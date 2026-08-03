"""
ISB / MBA India Reddit Profile-Eval Monitor (v2 - Google Alerts based)
------------------------------------------------------------------------
Reddit now blocks direct/automated fetching of its .json and page endpoints
from server IPs (see README for details). To work around this, this version
reads Google Alert emails delivered to your Gmail instead of hitting Reddit
directly. You set up Google Alerts (in your browser, one-time) for searches
like `site:reddit.com/r/ISB_Aspirants profile OR chances OR GMAT`, and this
script scans your inbox daily for new alert emails, extracts the Reddit
links + title/snippet, drafts an answer for relevant ones, and logs
everything to your Google Sheet.

Because Google's alert emails only include a short snippet (not the full
post body), each drafted answer is based on limited context. The sheet
flags this so you know to open the link and read the full post before
finalizing/posting.

This script does NOT post anything to Reddit and does NOT modify your
Gmail except marking processed alert emails as read.
"""

import os
import re
import json
import time
import email
import imaplib
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

import gspread
from google.oauth2.service_account import Credentials
from anthropic import Anthropic

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Per-subreddit handling. "strict" = require a stronger keyword match.
# "requires_india_signal" = only relevant if an India-related term is present
# (used for r/MBA, which is a large, mostly non-Indian subreddit).
SUBREDDIT_CONFIG = {
    "ISB_Aspirants":   {"strict": False, "requires_india_signal": False},
    "ISBapplications": {"strict": False, "requires_india_signal": False},
    "MBAIndia":        {"strict": True,  "requires_india_signal": False},
    "MBA":             {"strict": True,  "requires_india_signal": True},
}
DEFAULT_SUBREDDIT_SETTINGS = {"strict": False, "requires_india_signal": False}

ALERT_SENDER = "googlealerts-noreply@google.com"

KEYWORDS_PROFILE_EVAL = [
    "profile eval", "profile evaluation", "eval my profile", "rate my profile",
    "my chances", "what are my chances", "shortlist chances", "chances of shortlist",
    "gmat", "gre", "nmat", "cat score",
]
KEYWORDS_APPLICATION = [
    "application strategy", "which round", "round 1", "round 2", "round 3",
    "essay help", "essay review", "sop review", "statement of purpose",
    "recommendation letter", "reco letter", "lor ",
]
KEYWORDS_CONSULTING = [
    "admissions consultant", "interview prep", "interview experience",
    "mock interview", "choosing between", "isb vs", "should i apply",
]
KEYWORDS_EXEC_PROGRAMS = [
    "pgpx", "epgp", "execmba", "exec mba", "executive mba",
    "sp jain", "s.p. jain", "spjain",
]
ALL_GROUPS = [KEYWORDS_PROFILE_EVAL, KEYWORDS_APPLICATION, KEYWORDS_CONSULTING, KEYWORDS_EXEC_PROGRAMS]

KEYWORDS_INDIA_SIGNAL = [
    "india", "indian", "nri", " iim ", "iim-", "isb ", "isb,", "isb.",
    "hyderabad", "mohali", "bangalore", "delhi", "mumbai", "pune",
]

STATE_FILE = "seen_posts.json"

REDDIT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# ---------------------------------------------------------------------------
# Gmail (IMAP) - fetch Google Alert emails
# ---------------------------------------------------------------------------

def fetch_alert_emails():
    """Connects to Gmail via IMAP and returns a list of (uid, html_body) for
    unread Google Alert emails."""
    gmail_user = os.environ["GMAIL_ADDRESS"]
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"]

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(gmail_user, gmail_pass)
    imap.select("INBOX")

    status, data = imap.search(None, f'(UNSEEN FROM "{ALERT_SENDER}")')
    if status != "OK":
        imap.logout()
        return []

    uids = data[0].split()
    results = []
    for uid in uids:
        status, msg_data = imap.fetch(uid, "(RFC822)")
        if status != "OK":
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        html_body = _extract_html(msg)
        if html_body:
            results.append((uid, html_body))

    imap.logout()
    return results


def mark_as_read(uids):
    gmail_user = os.environ["GMAIL_ADDRESS"]
    gmail_pass = os.environ["GMAIL_APP_PASSWORD"]
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(gmail_user, gmail_pass)
    imap.select("INBOX")
    for uid in uids:
        imap.store(uid, "+FLAGS", "\\Seen")
    imap.logout()


def _extract_html(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="ignore")
    else:
        if msg.get_content_type() == "text/html":
            charset = msg.get_content_charset() or "utf-8"
            return msg.get_payload(decode=True).decode(charset, errors="ignore")
    return None


# ---------------------------------------------------------------------------
# Parse Reddit links + snippets out of an alert email
# ---------------------------------------------------------------------------

def unwrap_google_redirect(url: str) -> str:
    """Google Alert links look like https://www.google.com/url?q=<real_url>&...
    Unwraps to the real URL. If it's already a plain URL, returns as-is."""
    parsed = urlparse(url)
    if "google.com" in parsed.netloc and parsed.path == "/url":
        qs = parse_qs(parsed.query)
        if "q" in qs:
            return qs["q"][0]
    return url


def extract_subreddit(url: str) -> str:
    match = re.search(r"reddit\.com/r/([A-Za-z0-9_]+)/", url)
    return match.group(1) if match else ""


def extract_post_id(url: str) -> str:
    match = re.search(r"/comments/([a-z0-9]+)/", url)
    return match.group(1) if match else url


def parse_alert_email(html_body: str):
    """Returns a list of dicts: {url, subreddit, title, snippet}."""
    soup = BeautifulSoup(html_body, "html.parser")
    items = []
    seen_urls_in_email = set()

    for a in soup.find_all("a", href=True):
        real_url = unwrap_google_redirect(a["href"])
        if "reddit.com/r/" not in real_url or "/comments/" not in real_url:
            continue
        if real_url in seen_urls_in_email:
            continue
        seen_urls_in_email.add(real_url)

        title = a.get_text(strip=True)
        if not title:
            continue

        # Best-effort snippet: look at the text in the parent container,
        # minus the title itself.
        snippet = ""
        parent = a.find_parent(["td", "div", "p"])
        if parent:
            parent_text = parent.get_text(" ", strip=True)
            snippet = parent_text.replace(title, "", 1).strip()

        items.append({
            "url": real_url,
            "subreddit": extract_subreddit(real_url),
            "title": title,
            "snippet": snippet[:800],
        })

    return items


# ---------------------------------------------------------------------------
# Best-effort full post fetch (may fail - Reddit blocks a lot of this now)
# ---------------------------------------------------------------------------

def try_fetch_full_post(url: str):
    try:
        resp = requests.get(url.rstrip("/") + ".json", headers=REDDIT_HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        post = data[0]["data"]["children"][0]["data"]
        return post.get("selftext", "")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

def matches_keywords(text: str, strict: bool, requires_india_signal: bool = False) -> bool:
    text_lower = text.lower()

    if requires_india_signal and not any(kw in text_lower for kw in KEYWORDS_INDIA_SIGNAL):
        return False

    group_hits = sum(1 for group in ALL_GROUPS if any(kw in text_lower for kw in group))

    if strict:
        return group_hits >= 2 or any(kw in text_lower for kw in KEYWORDS_PROFILE_EVAL)
    return group_hits >= 1


# ---------------------------------------------------------------------------
# Answer drafting via Claude
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are helping draft a Reddit reply for an ISB (Indian School of Business) \
PGP alum who has served on the ISB admissions interview panel and now advises MBA applicants. \
Write in a casual, warm, direct tone -- like an experienced senior replying to a junior on \
Reddit, not a formal consultant. It should read like a real person typed it quickly between \
other things: contractions, casual phrasing, occasional internet shorthand (tbh, imo, ngl, \
fwiw, lol where it actually fits, etc.), and a bit of informal punctuation are all fine and \
encouraged -- don't sanitize it into polished prose. No corporate language, no "as an AI" \
framing, no excessive hedging, no overly neat structure (avoid bullet-point-perfect answers \
unless the question genuinely calls for a list). Where relevant, mention specifics (typical \
GMAT/GRE bands, work-ex norms, essay/interview expectations) but avoid making up specific \
stats you are not sure of -- speak in ranges and general patterns instead. Keep the answer to \
150-250 words. Do not sign off with a name.

Some questions will be about executive/mid-career programs instead of a fresh full-time MBA \
-- e.g. ISB PGP, IIM Ahmedabad PGPX, IIM Bangalore EPGP, IIM Calcutta Executive MBA, or SP \
Jain programs. For these, weight your answer toward what actually matters at that career \
stage: years of work-ex and seniority fit, sponsorship/self-funding and opportunity cost, \
career pivot vs. acceleration goals, and how the program's peer group and placements suit \
someone already mid-career -- rather than fresh-graduate profile-building advice.

Sometimes you will only have a short snippet/preview of the post, not the full text. If so,
write the best general answer you can based on the title and snippet, but keep it a bit more
general/hedged rather than inventing specifics the post never mentioned."""


def draft_answer(client: Anthropic, item: dict, full_text: str) -> str:
    body = full_text if full_text else f"(Only a short preview was available)\n{item['snippet']}"
    user_content = f"Subreddit: r/{item['subreddit']}\nTitle: {item['title']}\n\nBody:\n{body[:3000]}"
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    parts = [block.text for block in response.content if block.type == "text"]
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Google Sheets logging
# ---------------------------------------------------------------------------

SHEET_HEADERS = ["Date Found", "Subreddit", "Question Title", "Post Link",
                 "Drafted Answer", "Context Used", "Status", "Date Posted"]


def get_sheet():
    creds_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    info = json.loads(creds_json)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    ws = sh.sheet1
    if ws.row_values(1) != SHEET_HEADERS:
        ws.clear()
        ws.append_row(SHEET_HEADERS)
    return ws


def append_to_sheet(ws, item: dict, answer: str, context_used: str):
    ws.append_row([
        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        item["subreddit"],
        item["title"],
        item["url"],
        answer,
        context_used,   # "Full post" or "Alert snippet only - verify before posting"
        "draft",
        "",
    ])


# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------

def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen_ids):
    with open(STATE_FILE, "w") as f:
        json.dump(sorted(seen_ids), f)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    seen = load_seen()
    new_seen = set(seen)
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    ws = get_sheet()

    emails = fetch_alert_emails()
    print(f"Found {len(emails)} new alert email(s).")

    matched_count = 0
    processed_uids = []

    for uid, html_body in emails:
        items = parse_alert_email(html_body)
        email_had_failure = False
        for item in items:
            post_id = extract_post_id(item["url"])
            if post_id in seen:
                continue

            settings = SUBREDDIT_CONFIG.get(item["subreddit"], DEFAULT_SUBREDDIT_SETTINGS)
            full_text_for_matching = f"{item['title']} {item['snippet']}"
            if not matches_keywords(full_text_for_matching, settings["strict"], settings["requires_india_signal"]):
                new_seen.add(post_id)  # not relevant, don't bother re-checking it again
                continue

            full_body = try_fetch_full_post(item["url"])
            context_used = "Full post" if full_body else "Alert snippet only - verify before posting"

            try:
                answer = draft_answer(client, item, full_body)
            except Exception as e:
                print(f"[warn] draft failed for {item['url']}: {e}")
                email_had_failure = True
                continue  # do NOT mark as seen - retry next run

            append_to_sheet(ws, item, answer, context_used)
            new_seen.add(post_id)  # only mark seen after a successful draft
            matched_count += 1
            time.sleep(1)

        if not email_had_failure:
            processed_uids.append(uid)  # only mark read if nothing in it needs a retry

    if processed_uids:
        mark_as_read(processed_uids)

    save_seen(new_seen)
    print(f"Done. {matched_count} new matched post(s) added to the sheet.")


if __name__ == "__main__":
    main()
