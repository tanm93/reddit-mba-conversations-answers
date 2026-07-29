# ISB / MBA India Profile-Eval Monitor

Daily automated scan of r/ISB_Aspirants, r/ISBapplications, r/MBAIndia, and
r/MBA for profile evaluation, application strategy, and admissions-consulting
questions -- including executive/mid-career programs like ISB PGP, IIM
Ahmedabad PGPX, IIM Bangalore EPGP, IIM Calcutta Executive MBA, and SP Jain.
r/MBA is a large, mostly non-Indian subreddit, so posts there are additionally
filtered to ones with an "India signal" (mentions of India, NRI, specific IIMs,
Indian cities, etc.) so it only catches Indian applicants asking about MBA
abroad, not the whole firehose of that subreddit.

Drafts an answer for each matched post using Claude, and logs everything to a
Google Sheet for you to review. Does not post anything automatically.

## One-time setup (about 15-20 minutes)

### 1. Create the GitHub repo
1. Go to github.com, click "New repository."
2. Name it something like `isb-profile-eval-monitor`. Keep it **private**.
3. Upload all the files in this folder to the repo (or use `git push` if
   you're comfortable with git).

### 2. Get an Anthropic API key
1. Go to console.anthropic.com, log in.
2. Go to "API Keys" → "Create Key."
3. Copy the key (starts with `sk-ant-...`). You'll paste it into GitHub in
   step 5.

### 3. Create a Google Sheet
1. Go to sheets.google.com, create a new blank sheet.
2. Name it e.g. "ISB Profile Eval Tracker."
3. Copy the Sheet ID from the URL: it's the long string between `/d/` and
   `/edit`, e.g. `docs.google.com/spreadsheets/d/THIS_PART/edit`.

### 4. Create a Google service account (lets the script write to your sheet)
1. Go to console.cloud.google.com, create a new project (any name).
2. In the search bar, go to "Google Sheets API" and click **Enable**.
3. Go to "APIs & Services" → "Credentials" → "Create Credentials" →
   "Service Account."
4. Give it any name, click through the defaults, click "Done."
5. Click on the service account you just created → "Keys" tab → "Add Key" →
   "Create new key" → choose **JSON** → it downloads a `.json` file.
6. Open that file, copy its **entire contents** (you'll paste this into
   GitHub in step 5).
7. Also copy the service account's **email address** (looks like
   `something@your-project.iam.gserviceaccount.com`, visible on the
   service account page or inside the JSON as `client_email`).
8. Go back to your Google Sheet → click "Share" → paste that email in →
   give it **Editor** access → Send.

### 5. Add secrets to GitHub
In your GitHub repo: Settings → Secrets and variables → Actions → New
repository secret. Add these three:

| Secret name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | the key from step 2 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | the entire contents of the JSON file from step 4 |
| `GOOGLE_SHEET_ID` | the Sheet ID from step 3 |

### 6. Test it
Go to the "Actions" tab in your repo → click "Daily ISB Profile-Eval Scan"
on the left → click "Run workflow" (this runs it immediately instead of
waiting for the schedule). Check your Google Sheet after a minute or two —
new rows should appear for any matching posts found.

After that, it runs automatically every day at 9:30 AM IST.

## How to use it day-to-day
- Open the Google Sheet whenever you like.
- Each row = one matched post: subreddit, question title, link, drafted
  answer, and a **Status** column (starts as "draft").
- Edit the drafted answer directly in the sheet if you want to change
  anything.
- When you're happy with an answer, change Status to "ready to post" (this
  is just a marker for you right now — since your Reddit account is new,
  posting is still manual: copy the answer and paste it as a reply on the
  linked post yourself).

## Adjusting what it looks for
Open `reddit_monitor.py` and edit:
- `SUBREDDITS` — add/remove subreddits, or flip `"strict"` if a subreddit
  is producing too much noise or missing too much.
- `KEYWORDS_PROFILE_EVAL` / `KEYWORDS_APPLICATION` / `KEYWORDS_CONSULTING`
  — add phrases you notice showing up in posts that should have matched
  but didn't.

## Notes
- This only reads public Reddit data — no Reddit login or API app needed
  for this stage.
- `seen_posts.json` keeps track of which posts have already been
  processed, so you won't get duplicate rows on future runs.
- Once your Reddit account ages a bit and you can create a Reddit API app,
  let me know and we can add an auto-post step gated on your "ready to
  post" status.
