# Departures Board (Streamlit edition)

A career-operations dashboard for a multi-country CX/Customer Success/Analytics/Insights
job search — Job Leads, Applications, Target Companies, and Agencies — backed by a
Google Sheet so data persists and is editable from anywhere.

This is a from-scratch rebuild of a Claude Artifacts version of the same board:
Artifacts' built-in live database and in-page AI assistant don't exist outside
Claude's environment, so here the data store is a Google Sheet (via a service
account) and the "paste a job" extractor calls the Anthropic API directly with
your own key.

## 1. Google Sheets setup (one-time)

1. Create a new Google Sheet (any name, e.g. "Departures Board Data"). Copy its
   ID from the URL: `https://docs.google.com/spreadsheets/d/<THIS-PART>/edit`.
2. Go to [Google Cloud Console](https://console.cloud.google.com/) → create a
   project (or reuse one) → **APIs & Services → Library** → enable
   **Google Sheets API** and **Google Drive API**.
3. **APIs & Services → Credentials → Create Credentials → Service Account**.
   Give it any name (e.g. `departures-board`). No roles needed at the project
   level — skip that step.
4. Open the new service account → **Keys → Add Key → Create new key → JSON**.
   This downloads a `.json` file — keep it private, never commit it.
5. Open your Google Sheet → **Share** → paste the service account's email
   (looks like `departures-board@your-project.iam.gserviceaccount.com`,
   found in the JSON file's `client_email` field) → give it **Editor** access.
6. The app creates its own tabs (`leads`, `applications`, `companies`,
   `agencies`) with headers automatically on first run — you don't need to
   set those up by hand.

## 2. Anthropic API key

Get a key from [console.anthropic.com](https://console.anthropic.com/) →
**API Keys**. This is billed separately from any Claude subscription — the
extraction feature uses it directly, not your Claude.ai account.

## 3. Configure secrets

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` for local
runs (this file is gitignored — never commit it). Fill in:

- `ANTHROPIC_API_KEY` — your key from step 2.
- `SPREADSHEET_ID` — the Sheet ID from step 1.
- `[gcp_service_account]` — open the downloaded JSON key file and copy each
  field across (the shapes match exactly; just convert JSON syntax to TOML).

Run locally to test:

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 4. Deploy on Streamlit Community Cloud

1. Push this folder to a GitHub repo (see the deploy steps I ran with you —
   or `git init && git add -A && git commit -m "Departures Board" && gh repo create ...`).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** →
   pick the repo, branch (`main`), and `app.py` as the entry point.
3. Before or after the first deploy, open the app's **Settings → Secrets**
   and paste the *contents* of your local `secrets.toml` there (same TOML
   format). This is the only place your API key and service account key
   should ever live outside your own machine — never commit them to git.
4. Deploy. Streamlit installs `requirements.txt` and starts the app; first
   boot takes a minute or two.

## Notes on this version vs. the Claude Artifact version

- **Saving is explicit**: each tab has a "💾 Save changes" button — edits in
  the table aren't written to the Sheet until you click it (Google Sheets
  doesn't support the Artifact's per-keystroke live sync).
- **Country filter**: when a country filter is active, the table switches to
  read-only. Clear the filter (back to "All Countries") to edit or add rows.
- **Adding rows**: use the editable table's own "+" row at the bottom
  (`num_rows="dynamic"`) for manual entries, or the "Paste a job" box for
  AI-assisted entries — either way, click Save afterward.
