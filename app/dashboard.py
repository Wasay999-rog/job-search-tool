import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from app.db import get_conn, init_db
from app.scoring import HIGH_MATCH

STATUSES = ["New", "Review", "Shortlisted", "Applied", "Interview",
            "Rejected", "Offer", "Not Interested", "Closed"]

st.set_page_config(page_title="Job Search", layout="wide")
init_db()


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run(sql, params=()):
    conn = get_conn()
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    finally:
        conn.close()
    return pd.DataFrame([tuple(r) for r in rows], columns=cols)


def save_tracking(job_id, status, followup, notes):
    t = now_utc()
    applied = t if status == "Applied" else None
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO applications (job_id, status, saved_at, applied_at,
                   followup_date, notes, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET
                   status = excluded.status,
                   applied_at = COALESCE(applications.applied_at, excluded.applied_at),
                   followup_date = excluded.followup_date,
                   notes = excluded.notes,
                   updated_at = excluded.updated_at""",
            (job_id, status, t, applied, followup or None, notes or None, t))
        conn.commit()
    finally:
        conn.close()


def show_detail(job_id, profile):
    d = run(
        """SELECT j.title, j.location, j.job_url, j.description, j.first_seen,
                  j.last_checked, j.salary_min, j.salary_max, j.salary_currency,
                  c.canonical_name AS company, m.overall_score, m.evidence, m.missing,
                  m.eligibility_note, m.explanation, a.status AS app_status,
                  a.notes AS app_notes, a.followup_date
           FROM jobs j JOIN companies c ON c.id = j.company_id
           JOIN match_results m ON m.job_id = j.id AND m.profile = ?
           LEFT JOIN applications a ON a.job_id = j.id
           WHERE j.id = ?""", (profile, job_id))
    if d.empty:
        return
    r = d.iloc[0]

    def val(k):
        return None if pd.isna(r[k]) else r[k]

    st.subheader(f"{r['title']} at {r['company']}")
    st.caption(f"{r['location']} | Profile: {profile} | Score: {r['overall_score']:.0f} / 100 | "
               f"First seen {str(r['first_seen'])[:10]} | Last checked {str(r['last_checked'])[:16]} UTC")
    if val("salary_min"):
        top = val("salary_max") or r["salary_min"]
        st.write(f"**Published pay:** {r['salary_currency'] or ''} {r['salary_min']:,.0f} to {top:,.0f} per year")
    else:
        st.write("**Pay:** not published (unknown)")
    if val("job_url"):
        st.link_button("Open official posting", r["job_url"])

    left, right = st.columns(2)
    with left:
        st.markdown("**Why it matches**")
        for line in str(r["evidence"] or "").split(" | "):
            st.write(f"- {line}")
    with right:
        st.markdown("**Missing or uncertain**")
        for line in str(r["missing"] or "").split(" | "):
            st.write(f"- {line}")
        st.markdown("**Eligibility and pay notes (manual review)**")
        for line in str(r["eligibility_note"] or "").split(" | "):
            st.write(f"- {line}")
    st.caption(r["explanation"])

    with st.expander("Full job description"):
        st.text(r["description"] or "No description available.")

    cur = val("app_status") or "New"
    with st.form(f"track_{job_id}"):
        status = st.selectbox("Status", STATUSES, index=STATUSES.index(cur))
        followup = st.text_input("Follow-up date (YYYY-MM-DD)", value=val("followup_date") or "")
        notes = st.text_area("Notes", value=val("app_notes") or "")
        if st.form_submit_button("Save"):
            save_tracking(job_id, status, followup.strip(), notes.strip())
            st.success("Saved. Refresh the page to see it in the list.")


# ---------------- Sidebar ----------------
st.sidebar.header("Filters")
new_days = st.sidebar.number_input("Count as new if first seen within (days)", 1, 30, 1)
max_age = st.sidebar.number_input("Max posting age in days (0 = any)", 0, 365, 14)
cutoff = (datetime.now(timezone.utc) - timedelta(days=int(new_days))).isoformat(timespec="seconds")

# ---------------- Top metrics ----------------
m = run(
    """SELECT
       (SELECT COUNT(*) FROM companies WHERE is_approved = 1) AS companies,
       (SELECT COUNT(*) FROM companies WHERE last_success_utc IS NOT NULL) AS checked,
       (SELECT COUNT(*) FROM jobs WHERE status = 'active') AS active,
       (SELECT COUNT(*) FROM jobs WHERE status = 'active' AND first_seen >= ?) AS new_jobs,
       (SELECT COUNT(DISTINCT m.job_id) FROM match_results m JOIN jobs j ON j.id = m.job_id
          WHERE j.status = 'active' AND m.overall_score >= ?) AS high""",
    (cutoff, HIGH_MATCH)).iloc[0]

st.title("Job Search Dashboard")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Target companies", int(m["companies"]))
c2.metric("Checked OK", int(m["checked"]))
c3.metric("Active jobs", int(m["active"]))
c4.metric("New jobs", int(m["new_jobs"]))
c5.metric("High matches", int(m["high"]))

errors = run("SELECT canonical_name, last_error FROM companies WHERE last_error IS NOT NULL")
if not errors.empty:
    with st.expander(f"Collection errors ({len(errors)})"):
        st.dataframe(errors, hide_index=True)

tab_jobs, tab_track, tab_cov = st.tabs(["Jobs", "Tracker", "Coverage"])

# ---------------- Jobs tab ----------------
with tab_jobs:
    f1, f2, f3, f4 = st.columns([2, 2, 2, 3])
    profile = f1.selectbox("Profile", ["All", "Data Engineer", "Analytics/BI Engineer"])
    min_score = f2.slider("Minimum score", 0, 100, int(HIGH_MATCH))
    only_new = f3.checkbox("New only")
    pref_only = f3.checkbox("Preferred locations only")
    search = f4.text_input("Search title or company")

    df = run(
        """SELECT j.id AS job_id, m.profile AS profile, c.canonical_name AS company,
                  j.title AS title, j.location AS location, m.overall_score AS score,
                  COALESCE(a.status, 'New') AS status, m.location_score AS loc_score,
                  substr(j.first_seen, 1, 10) AS first_seen,
                  substr(j.published_at, 1, 10) AS posted,
                  CAST(julianday('now') - julianday(substr(j.published_at, 1, 10)) AS INTEGER) AS age_days,
                  CASE WHEN j.first_seen >= ? THEN 'NEW' ELSE '' END AS is_new
           FROM match_results m JOIN jobs j ON j.id = m.job_id
           JOIN companies c ON c.id = j.company_id
           LEFT JOIN applications a ON a.job_id = j.id
           WHERE j.status = 'active' AND m.overall_score >= ?""",
        (cutoff, min_score))
    if profile != "All":
        df = df[df["profile"] == profile]
        if max_age:
        df = df[df["age_days"].isna() | (df["age_days"] <= max_age)]
    if only_new:
        df = df[df["is_new"] == "NEW"]
    if pref_only:
        df = df[df["loc_score"] >= 1]
    df = df[~df["status"].isin(["Not Interested", "Closed", "Rejected"])]
    if search:
        s = search.lower()
        df = df[df["title"].str.lower().str.contains(s) | df["company"].str.lower().str.contains(s)]
    df = df.sort_values("score", ascending=False).reset_index(drop=True)

    st.write(f"{len(df)} jobs. Click a row to see details.")
    event = st.dataframe(
        df[["is_new", "score", "profile", "company", "title", "location", "status", "first_seen", "posted"]],
        hide_index=True, on_select="rerun", selection_mode="single-row",
        use_container_width=True, height=380)
    if event.selection.rows:
        row = df.iloc[event.selection.rows[0]]
        st.divider()
        show_detail(int(row["job_id"]), row["profile"])

# ---------------- Tracker tab ----------------
with tab_track:
    t = run(
        """SELECT a.status AS status, c.canonical_name AS company, j.title AS title,
                  a.followup_date AS follow_up, a.notes AS notes,
                  substr(a.updated_at, 1, 10) AS updated, j.job_url AS link
           FROM applications a JOIN jobs j ON j.id = a.job_id
           JOIN companies c ON c.id = j.company_id
           ORDER BY a.updated_at DESC""")
    if t.empty:
        st.info("Nothing tracked yet. Open a job in the Jobs tab and save a status.")
    else:
        for status in STATUSES:
            part = t[t["status"] == status]
            if not part.empty:
                st.markdown(f"**{status}** ({len(part)})")
                st.dataframe(part.drop(columns=["status"]), hide_index=True,
                             use_container_width=True)

# ---------------- Coverage tab ----------------
with tab_cov:
    cov = run(
        """SELECT c.canonical_name AS company, c.sector AS sector,
                  COALESCE(c.ats_provider, '') AS source, COALESCE(c.board_identifier, '') AS board,
                  c.last_success_utc AS last_checked, c.last_error AS error,
                  COALESCE(c.careers_url, '') AS careers_link,
                  (SELECT COUNT(*) FROM jobs j WHERE j.company_id = c.id AND j.status = 'active') AS active_jobs
           FROM companies c ORDER BY c.canonical_name""")

    def state(r):
        if not pd.isna(r["error"]):
            return "Check failed"
        if not pd.isna(r["last_checked"]):
            return "Checked OK"
        return "Not connected yet"

    cov["state"] = cov.apply(state, axis=1)
    st.write(cov["state"].value_counts().to_dict())
    st.dataframe(cov[["company", "state", "source", "board", "active_jobs", "last_checked", "sector", "error"]],
                 hide_index=True, use_container_width=True, height=500)