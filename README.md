# ISB / MBA India Profile-Eval Monitor (v2)

Daily automated scan for profile evaluation, application strategy, and
admissions-consulting questions across r/ISB_Aspirants, r/ISBapplications,
r/MBAIndia, and (India-filtered) r/MBA -- including executive/mid-career
programs like ISB PGP, IIM Ahmedabad PGPX, IIM Bangalore EPGP, IIM Calcutta
Executive MBA, and SP Jain.

**Why v2 works differently:** Reddit closed off free/automated access to its
`.json` and page endpoints in 2026, and now requires an approved developer
app for API access, which is a slow, uncertain approval process. To route
around that, this version monitors **Google Alerts** delivered to your Gmail
instead of hitting Reddit directly. Google indexes Reddit posts, so this
still catches new posts, just with a bit of lag (usually a few hours to a
day) and using a short text snippet instead of the full post body in most
cases.

Drafts an answer for each matched post using Claude, and logs everything to
a Google Sheet for you to review. Does not post anything automatically.

## One-time setup

### 1. Create the GitHub repo
Already done if you're continuing from before -- this repo. If starting
fresh: github.com -> New repository -> upload these files (keep the
`.github/workflows/daily_scan.yml` file in that exact nested folder path).
Public or private both work; public avoids GitHub's spending-limit/card
requirement for Actions minutes.

### 2. Set up Google Alerts
1. Go to **google.com/alerts** (log in with your Google account)
2. In the search box, create an alert for each subreddit. Suggested queries:
   - `site:reddit.com/r/ISB_Aspirants`
   - `site:reddit.com/r/ISBapplications`
   - `site:reddit.com/r/MBAIndia (profile OR chances OR GMAT OR GRE OR essay OR PGPX OR EPGP)`
   - `site:reddit.com/r/MBA (India OR Indian OR IIM OR ISB OR NRI)`
3. For each alert, click "Show options" and set:
   - **How often**: "At most once a day" (or "As-it-happens" if you want
     faster detection -- either works with this script)
   - **Sources**: "Automatic" or "Web" (either is fine)
   - **Deliver to**: your Gmail address
4. Click **Create Alert** for each one (repeat for all 4)

You can always add/edit/remove alerts later at google.com/alerts.

### 3. Turn on Gmail App Passwords
This lets the script read your inbox securely without your actual Gmail
password.
1. Go to **myaccount.google.com/security**
2. Make sure **2-Step Verification** is turned on (turn it on if it isn't --
   required for app passwords)
3. Go to **myaccount.google.com/apppasswords**
4. Under "App name," type something like `ISB Monitor` -> click **Create**
5. Google shows a 16-character password (e.g. `abcd efgh ijkl mnop`) -- copy
   it (spaces don't matter, can be removed or kept)
6. This is a separate, revocable password just for this script -- it
   doesn't expose your real Gmail password, and you can revoke it any time
   from the same App Passwords page

### 4. Get an Anthropic API key
1. console.anthropic.com -> log in -> add billing (pay-as-you-go, pennies/month at this volume)
2. Left sidebar -> **API Keys** -> **Create Key** -> name it -> **Create Key**
3. Copy the key (starts `sk-ant-...`) -- shown only once

### 5. Create a Google Sheet
1. sheets.google.com -> **+ Blank**
2. Rename it, e.g. "ISB Profile Eval Tracker"
3. Copy the **Sheet ID** from the URL -- the string between `/d/` and `/edit`

### 6. Create a Google service account (so the script can write to the sheet)
1. console.cloud.google.com -> new project (any name)
2. Search bar -> "Google Sheets API" -> **Enable**
3. Left sidebar -> APIs & Services -> Credentials -> **+ Create Credentials**
   -> **Service account**
4. Name it -> Create and Continue -> Continue -> Done
5. Click into the service account -> **Keys** tab -> **Add Key** -> **Create
   new key** -> **JSON** -> Create (downloads a file)
6. Open that file, copy its full contents
7. Also copy the `client_email` value from inside it
8. Go to your Google Sheet -> **Share** -> paste that email -> give
   **Editor** access -> Send

### 7. Add secrets to GitHub
Repo -> **Settings** -> **Secrets and variables** -> **Actions** -> **New
repository secret**, one at a time:

| Secret name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | from step 4 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | full JSON contents from step 6 |
| `GOOGLE_SHEET_ID` | from step 5 |
| `GMAIL_ADDRESS` | your Gmail address |
| `GMAIL_APP_PASSWORD` | the 16-character app password from step 3 |

### 8. Test it
- Actions tab -> "Daily ISB Profile-Eval Scan" -> **Run workflow**
- Check the log: it'll print `Found N new alert email(s)` and
  `Done. X new matched post(s) added to the sheet.`
- If it's 0 emails found, that's likely just because no Google Alert has
  fired yet (give it a day) -- check google.com/alerts to confirm your
  alerts are active

After that it runs automatically every day at 9:30 AM IST.

## How to use it day-to-day
- Open the Google Sheet whenever you like
- Each row = one matched post, with a **Context Used** column:
  - "Full post" -> the script managed to fetch the full text, answer is
    based on complete context
  - "Alert snippet only - verify before posting" -> answer is based on just
    the title + a short preview text from Google. **Open the link and read
    the actual post before finalizing this one** -- the draft may be
    generic or miss details only in the full post
- Edit the drafted answer directly in the sheet if needed
- When happy with an answer, change **Status** to "ready to post," then
  copy-paste it to Reddit yourself (still manual for now, until your Reddit
  account can get API posting access)

## Adjusting what it looks for
- Edit the Google Alert queries themselves at google.com/alerts to change
  what gets surfaced in the first place
- Edit `SUBREDDIT_CONFIG` and the `KEYWORDS_*` lists in `reddit_monitor.py`
  to change the secondary filtering the script applies on top of the alerts

## Notes / limitations
- Detection now depends on Google's indexing speed, so there will be some
  lag (usually hours, occasionally longer) compared to real-time Reddit
  monitoring
- `seen_posts.json` tracks which posts have already been processed, so you
  won't get duplicate rows
- If your Reddit account later gets approved for API access (or ages past
  whatever gate is blocking developer app creation), let me know and we can
  switch back to direct, faster, full-text Reddit monitoring, and add an
  auto-post step gated on your "ready to post" status
