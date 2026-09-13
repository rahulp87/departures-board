"""
Departures Board — Rahul's job-search operations dashboard (Streamlit edition).

Data lives in a Google Sheet (see README.md for setup). The AI "paste a job"
extractor calls the Anthropic API directly using your own API key.
"""

import base64
import json
from datetime import datetime

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

try:
    import anthropic
except ImportError:
    anthropic = None

st.set_page_config(page_title="Departures Board", page_icon="🛫", layout="wide")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROLE_FAMILIES = [
    "CX & Voice of Customer Leadership",
    "Customer Success Leadership",
    "Advanced Analytics / Data & Insights Leadership",
    "Consumer & Market Insights Leadership",
    "AI Product Management",
    "Strategy / Management Consulting",
    "Brand / Marketing Management",
    "General / Corporate",
]

LEAD_STATUS = ["pending", "needs_user", "skipped", "blocked", "submitted"]
APP_STATUS = ["applied", "replied", "interview", "offer", "rejected", "closed"]
OUTREACH_STATUS = ["not_started", "contacted", "followed_up", "replied", "stalled"]
PRIORITY_OPTIONS = ["High", "Medium", "Low", "Stretch", ""]

CONTACT_CHANNELS = [
    "", "LinkedIn InMail", "LinkedIn Message", "LinkedIn Connection Request",
    "Personal Email", "Work Email", "Phone Call", "Text / WhatsApp", "Referral", "Other",
]
CONTACT_SLOTS = [1, 2, 3, 4, 5]
CONTACT_COLUMNS = [
    f"contact{n}{field}" for n in CONTACT_SLOTS for field in ("Name", "LinkedIn", "Email", "Mobile", "Date", "Channel")
]

SHEETS = {
    "leads": ["company", "title", "roleFamily", "country", "priority", "status", "postedDate", "url", "applicationDate", "notes"] + CONTACT_COLUMNS,
    "applications": ["company", "title", "platform", "date", "contact", "status", "notes"],
    "companies": ["name", "category", "country", "notes", "portalUrl", "hiringManager", "skipLevel", "peer", "supporting", "outreachStatus"],
    "agencies": ["name", "recruiter", "linkedinUrl", "website", "status", "notes"],
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# ---------------------------------------------------------------------------
# Google Sheets plumbing
# ---------------------------------------------------------------------------


@st.cache_resource(show_spinner=False)
def get_spreadsheet():
    # The service account key is stored as a base64 blob of its raw JSON
    # (see README) rather than a nested TOML table: base64 has no quote
    # characters, backslashes, or newlines for a copy/paste step to corrupt.
    if "GCP_SERVICE_ACCOUNT_B64" in st.secrets:
        raw = base64.b64decode(st.secrets["GCP_SERVICE_ACCOUNT_B64"])
        info = json.loads(raw)
    else:
        info = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(st.secrets["SPREADSHEET_ID"])


def get_or_create_worksheet(sheet_name):
    sh = get_spreadsheet()
    headers = SHEETS[sheet_name]
    try:
        ws = sh.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=sheet_name, rows=200, cols=len(headers) + 2)
        ws.append_row(headers)
        return ws
    existing = ws.row_values(1)
    if existing != headers:
        if not existing:
            ws.append_row(headers)
    return ws


def load_df(sheet_name):
    ws = get_or_create_worksheet(sheet_name)
    records = ws.get_all_records()
    df = pd.DataFrame(records)
    for col in SHEETS[sheet_name]:
        if col not in df.columns:
            df[col] = ""
    return df[SHEETS[sheet_name]].astype(str).replace("nan", "")


def save_df(sheet_name, df):
    ws = get_or_create_worksheet(sheet_name)
    headers = SHEETS[sheet_name]
    df = df.fillna("")
    for col in headers:
        if col not in df.columns:
            df[col] = ""
    df = df[headers]
    ws.clear()
    ws.update([headers] + df.values.tolist())


def state_key(name):
    return f"df_{name}"


def get_state_df(name):
    if state_key(name) not in st.session_state:
        st.session_state[state_key(name)] = load_df(name)
    return st.session_state[state_key(name)]


def refresh_state_df(name):
    st.session_state[state_key(name)] = load_df(name)


# ---------------------------------------------------------------------------
# AI extraction (paste a job link + text -> structured lead)
# ---------------------------------------------------------------------------

EXTRACTION_INSTRUCTIONS = """You are extracting one structured job lead from pasted text for a job-search dashboard.

Candidate: targets Senior Manager or Director level roles in UAE, Canada, USA, India, or Singapore.
Primary background: Customer Experience (CX)/Voice of Customer, Customer Success, Advanced Analytics/Data & Insights,
and Consumer/Market Insights leadership.

Pick the closest roleFamily from this exact list (or "Unclassified" if truly none fit):
{role_families}

Pasted text (may include a URL and/or the visible job posting content):
---
{pasted_text}
---

If this text does not contain enough real information to identify the job (for example, it is only a bare URL
with no visible title, company, or description), reply with EXACTLY this JSON and nothing else:
{{"error": "not_enough_text"}}

Otherwise reply with ONLY a JSON object, no other text, with exactly these keys:
{{"company": string, "title": string, "roleFamily": string, "country": string,
"priority": "High"|"Medium"|"Low"|"Stretch", "postedDate": string, "url": string, "notes": string}}

Rules: never invent a fact that isn't in the text - leave a field as an empty string "" if it isn't stated.
"url" is any link found in the pasted text, else "". "priority" reflects fit against the candidate's target
level and role families above. "notes" is one short sentence on why you chose that roleFamily/priority,
or what's uncertain.
"""


def extract_job_details(pasted_text: str) -> dict:
    if anthropic is None:
        raise RuntimeError("The 'anthropic' package isn't installed.")
    api_key = st.secrets.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("No ANTHROPIC_API_KEY configured in secrets.")
    client = anthropic.Anthropic(api_key=api_key)
    prompt = EXTRACTION_INSTRUCTIONS.format(
        role_families="\n".join(f"- {r}" for r in ROLE_FAMILIES),
        pasted_text=pasted_text[:6000],
    )
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    end = max(text.rfind("}"), text.rfind("]"))
    if start == -1 or end == -1:
        raise ValueError(f"Claude's reply wasn't JSON: {text[:200]}")
    return json.loads(text[start : end + 1])


# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@700;800&family=IBM+Plex+Mono:wght@400;600&display=swap');
    h1, h2, h3 { font-family: 'Big Shoulders Display', sans-serif !important; text-transform: uppercase; letter-spacing: 0.02em; }
    [data-testid="stMetricValue"] { font-family: 'IBM Plex Mono', monospace; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Rahul Pathak // Departures")
st.caption(
    "Career operations board — CX, Customer Success, Analytics & Consumer Insights leadership roles "
    "across UAE · Canada · USA · India · Singapore. Precision mode."
)

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

leads_df = get_state_df("leads")
apps_df = get_state_df("applications")
companies_df = get_state_df("companies")
agencies_df = get_state_df("agencies")

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Leads Found", len(leads_df))
m2.metric("Needs Your Call", int((leads_df["status"] == "needs_user").sum()))
m3.metric("Pending Review", int((leads_df["status"] == "pending").sum()))
m4.metric("Applications Sent", len(apps_df))
m5.metric("Interviews Booked", int(apps_df["status"].isin(["interview", "offer"]).sum()))
m6.metric("Target Companies", len(companies_df))

if st.button("🔄 Refresh from Google Sheet"):
    for name in SHEETS:
        refresh_state_df(name)
    st.rerun()

tab_overview, tab_leads, tab_apps, tab_companies, tab_agencies = st.tabs(
    ["Overview", "Job Leads", "Applications", "Target Companies", "Agencies"]
)

# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

with tab_overview:
    col1, col2 = st.columns([1.3, 1])
    with col1:
        st.subheader("Needs Your Call")
        needs = leads_df[leads_df["status"] == "needs_user"]
        stalled = companies_df[companies_df["outreachStatus"] == "stalled"]
        if needs.empty and stalled.empty:
            st.info("Nothing waiting on you right now.")
        else:
            for _, row in needs.iterrows():
                st.markdown(f"**{row['company']} — {row['title']}**  \n{row['notes'] or 'Needs a decision before proceeding'}")
            for _, row in stalled.iterrows():
                st.markdown(f"**{row['name']}**  \nOutreach stalled — follow up or replace on your target list")
    with col2:
        st.subheader("Search Profile")
        st.markdown(
            """
            | | |
            |---|---|
            | **Mode** | Precision |
            | **Target level** | Senior Manager / Director |
            | **Target countries** | UAE · Canada · USA · India · Singapore |
            | **First trial boundary** | Lead finding only |
            | **Primary role families** | CX & VOC, Customer Success, Analytics/BI, Consumer Insights |
            """
        )

# ---------------------------------------------------------------------------
# Job Leads
# ---------------------------------------------------------------------------

with tab_leads:
    st.subheader("Job Leads")
    st.caption(
        "Every role screened, whether pursued or not. Status: Pending → worth review · "
        "Needs user → your call required · Skipped · Blocked · Submitted."
    )

    with st.container(border=True):
        st.markdown("**✨ Paste a job — link + the visible title, company and description text**")
        st.caption(
            "This app can't browse the web on its own — a bare link alone won't work. "
            "Paste the URL *and* the posting text you see on the page."
        )
        pasted = st.text_area("Paste here", key="paste_job_text", label_visibility="collapsed", height=120)
        if st.button("Extract with Claude", type="primary"):
            if not pasted.strip():
                st.warning("Paste the job link and posting text first.")
            else:
                with st.spinner("Thinking…"):
                    try:
                        result = extract_job_details(pasted)
                    except Exception as e:
                        st.error(f"Couldn't extract details: {e}")
                        result = None
                if result is not None:
                    if result.get("error") == "not_enough_text":
                        st.warning("Not enough detail — paste the visible title, company, and description too, not just the link.")
                    else:
                        new_row = {
                            "company": result.get("company", ""),
                            "title": result.get("title", ""),
                            "roleFamily": result.get("roleFamily", ""),
                            "country": result.get("country", ""),
                            "priority": result.get("priority", ""),
                            "status": "pending",
                            "postedDate": result.get("postedDate", ""),
                            "url": result.get("url", ""),
                            "notes": result.get("notes", ""),
                        }
                        st.session_state[state_key("leads")] = pd.concat(
                            [get_state_df("leads"), pd.DataFrame([new_row])], ignore_index=True
                        )
                        st.success("Extracted — review the new row below, then Save changes.")
                        st.rerun()

    countries = ["All Countries"] + sorted([c for c in leads_df["country"].unique() if c])
    country_filter = st.selectbox("Filter by country", countries, key="leads_country_filter")

    if country_filter == "All Countries":
        edited = st.data_editor(
            get_state_df("leads"),
            num_rows="dynamic",
            use_container_width=True,
            key="leads_editor",
            column_config={
                "roleFamily": st.column_config.SelectboxColumn(options=ROLE_FAMILIES + ["Unclassified"]),
                "priority": st.column_config.SelectboxColumn(options=PRIORITY_OPTIONS),
                "status": st.column_config.SelectboxColumn(options=LEAD_STATUS, required=True),
                "url": st.column_config.LinkColumn(),
                **{
                    f"contact{n}LinkedIn": st.column_config.LinkColumn(f"C{n} LinkedIn")
                    for n in CONTACT_SLOTS
                },
                **{
                    f"contact{n}Channel": st.column_config.SelectboxColumn(f"C{n} Channel", options=CONTACT_CHANNELS)
                    for n in CONTACT_SLOTS
                },
                **{f"contact{n}Name": f"C{n} Name" for n in CONTACT_SLOTS},
                **{f"contact{n}Email": f"C{n} Email" for n in CONTACT_SLOTS},
                **{f"contact{n}Mobile": f"C{n} Mobile" for n in CONTACT_SLOTS},
                **{f"contact{n}Date": f"C{n} Date" for n in CONTACT_SLOTS},
            },
        )
        if st.button("💾 Save changes", key="save_leads"):
            save_df("leads", edited)
            st.session_state[state_key("leads")] = edited
            st.success("Saved to Google Sheet.")
    else:
        st.dataframe(leads_df[leads_df["country"] == country_filter], use_container_width=True)
        st.caption("Editing is only available with 'All Countries' selected — clear the filter to edit or add rows.")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

with tab_apps:
    st.subheader("Applications")
    st.caption("Only explicit, confirmed submissions belong here — saved jobs and Easy Apply badges don't count.")
    edited_apps = st.data_editor(
        get_state_df("applications"),
        num_rows="dynamic",
        use_container_width=True,
        key="apps_editor",
        column_config={"status": st.column_config.SelectboxColumn(options=APP_STATUS, required=True)},
    )
    if st.button("💾 Save changes", key="save_apps"):
        save_df("applications", edited_apps)
        st.session_state[state_key("applications")] = edited_apps
        st.success("Saved to Google Sheet.")

# ---------------------------------------------------------------------------
# Target Companies
# ---------------------------------------------------------------------------

with tab_companies:
    st.subheader("Target Companies")
    st.caption(
        "Contacts follow the 4-contact networking framework: Hiring Manager, their Skip-Level Manager, "
        "a Peer at the same title, and a Colleague in a Supporting role."
    )
    countries_c = ["All Countries"] + sorted([c for c in companies_df["country"].unique() if c])
    country_filter_c = st.selectbox("Filter by country", countries_c, key="companies_country_filter")

    if country_filter_c == "All Countries":
        edited_companies = st.data_editor(
            get_state_df("companies"),
            num_rows="dynamic",
            use_container_width=True,
            key="companies_editor",
            column_config={
                "outreachStatus": st.column_config.SelectboxColumn(options=OUTREACH_STATUS, required=True),
                "portalUrl": st.column_config.LinkColumn(),
            },
        )
        if st.button("💾 Save changes", key="save_companies"):
            save_df("companies", edited_companies)
            st.session_state[state_key("companies")] = edited_companies
            st.success("Saved to Google Sheet.")
    else:
        st.dataframe(companies_df[companies_df["country"] == country_filter_c], use_container_width=True)
        st.caption("Editing is only available with 'All Countries' selected — clear the filter to edit or add rows.")

# ---------------------------------------------------------------------------
# Agencies
# ---------------------------------------------------------------------------

with tab_agencies:
    st.subheader("Recruitment Agencies")
    st.caption("Aim for 2+ contacts per agency; follow up every 2 weeks.")
    edited_agencies = st.data_editor(
        get_state_df("agencies"),
        num_rows="dynamic",
        use_container_width=True,
        key="agencies_editor",
        column_config={
            "status": st.column_config.SelectboxColumn(options=OUTREACH_STATUS, required=True),
            "linkedinUrl": st.column_config.LinkColumn(),
            "website": st.column_config.LinkColumn(),
        },
    )
    if st.button("💾 Save changes", key="save_agencies"):
        save_df("agencies", edited_agencies)
        st.session_state[state_key("agencies")] = edited_agencies
        st.success("Saved to Google Sheet.")

st.caption(f"Departures Board · Streamlit edition · last loaded {datetime.now().strftime('%Y-%m-%d %H:%M')}")
