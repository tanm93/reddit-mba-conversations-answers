"""
ISB / MBA India Reddit Profile-Eval Monitor
--------------------------------------------
Scans a set of subreddits for new posts that look like profile evaluation,
application strategy, or admissions consulting questions. Drafts an answer
for each using the Anthropic API, then logs everything to a Google Sheet
for manual review before posting.

This script does NOT post anything to Reddit. It only reads public data
(no Reddit login required) and writes to your Google Sheet.
"""

import os
import re
import json
import time
import requests
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials
from anthropic import Anthropic

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Subreddits to monitor, with per-subreddit strictness.
# "strict": True means we require a stronger keyword match (used for large,
# general subreddits like MBAIndia where most posts are NOT relevant).
SUBREDDITS = [
    {"name": "ISB_Aspirants", "strict": False, "requires_india_signal": False},
    {"name": "ISBapplications", "strict": False, "requires_india_signal": False},
    {"name": "MBAIndia", "strict": True, "requires_india_signal": False},
    # r/MBA is a large, mostly non-India subreddit (MBA abroad in general).
    # We only want posts from Indian applicants asking about MBA abroad, so
    # this one additionally requires an "India signal" term to match.
    {"name": "MBA", "strict": True, "requires_india_signal": True},
]

# How many of the newest posts to look at per subreddit, per run.
POSTS_PER_SUBREDDIT = 25

# Keyword groups. A post is considered a match if its title+body contains
# at least one term from ANY group (loose) or from MULTIPLE groups (strict).
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

# Used only for subreddits where requires_india_signal=True (e.g. r/MBA),
# to narrow a large general subreddit down to Indian-applicant posts.
KEYWORDS_INDIA_SIGNAL = [
    "india", "indian", "nri", " iim ", "iim-", "isb ", "isb,", "isb.",
    "hyderabad", "mohali", "bangalore", "delhi", "mumbai", "pune",
]

STATE_FILE = "seen_posts.json"  # tracks post IDs already processed, committed to repo

REDDIT_HEADERS = {
    "User-Agent": "isb-profile-eval-monitor/1.0 (personal, non-commercial, read-only script)"
}

# ---------------------------------------------------------------------------
# Reddit fetching (public JSON endpoints, no auth needed for read-only)
# ---------------------------------------------------------------------------

def fetch_new_posts(subreddit: str, limit: int = 25):
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit={limit}"
    resp = requests.get(url, headers=REDDIT_HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    posts = []
    for child in data.get("data", {}).get("children", []):
        p = child.get("data", {})
        posts.append({
            "id": p.get("id"),
            "title": p.get("title", ""),
            "selftext": p.get("selftext", ""),
            "url": f"https://www.reddit.com{p.get('permalink', '')}",
            "subreddit": subreddit,
            "created_utc": p.get("created_utc"),
        })
    return posts


def matches_keywords(text: str, strict: bool, requires_india_signal: bool = False) -> bool:
    text_lower = text.lower()

    if requires_india_signal and not any(kw in text_lower for kw in KEYWORDS_INDIA_SIGNAL):
        return False

    group_hits = 0
    for group in ALL_GROUPS:
        if any(kw in text_lower for kw in group):
            group_hits += 1

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
someone already mid-career -- rather than fresh-graduate profile-building advice."""


def draft_answer(client: Anthropic, post: dict) -> str:
    user_content = f"Subreddit: r/{post['subreddit']}\nTitle: {post['title']}\n\nBody:\n{post['selftext'][:3000]}"
    response = client.messages.create(
        model="claude-sonnet-4-6",
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
                 "Drafted Answer", "Status", "Date Posted"]


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


def append_to_sheet(ws, post: dict, answer: str):
    ws.append_row([
        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        post["subreddit"],
        post["title"],
        post["url"],
        answer,
        "draft",   # you change this to "ready to post" once reviewed
        "",
    ])


# ---------------------------------------------------------------------------
# State tracking (avoid re-processing the same post)
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
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    ws = get_sheet()

    new_seen = set(seen)
    matched_count = 0

    for sub in SUBREDDITS:
        try:
            posts = fetch_new_posts(sub["name"], POSTS_PER_SUBREDDIT)
        except Exception as e:
            print(f"[warn] failed to fetch r/{sub['name']}: {e}")
            continue

        for post in posts:
            if post["id"] in seen:
                continue
            new_seen.add(post["id"])

            full_text = f"{post['title']} {post['selftext']}"
            if not matches_keywords(full_text, sub["strict"], sub.get("requires_india_signal", False)):
                continue

            try:
                answer = draft_answer(client, post)
            except Exception as e:
                print(f"[warn] draft failed for {post['url']}: {e}")
                continue

            append_to_sheet(ws, post, answer)
            matched_count += 1
            time.sleep(1)  # gentle pacing, avoid hammering the Anthropic API

    save_seen(new_seen)
    print(f"Done. {matched_count} new matched post(s) added to the sheet.")


if __name__ == "__main__":
    main()
