import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobs.db"
if not DB_PATH.exists():
    DB_PATH = DB_PATH.with_name("jobs_cloud.db")
SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    name_key TEXT NOT NULL UNIQUE,
    sector TEXT,
    target_roles TEXT,
    is_approved INTEGER NOT NULL DEFAULT 1,
    careers_url TEXT,
    ats_provider TEXT,
    board_identifier TEXT,
    preferred_locations TEXT,
    api_supported TEXT DEFAULT 'Unknown',
    last_success_utc TEXT,
    last_error TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    external_id TEXT,
    dedupe_key TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    location TEXT,
    workplace_type TEXT,
    employment_type TEXT,
    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT,
    salary_interval TEXT,
    published_at TEXT,
    job_url TEXT,
    apply_url TEXT,
    source TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_checked TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    collection_status TEXT,
    UNIQUE (company_id, dedupe_key)
);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);

CREATE TABLE IF NOT EXISTS match_results (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    profile TEXT NOT NULL,
    overall_score REAL,
    skills_score REAL,
    experience_score REAL,
    location_score REAL,
    seniority_score REAL,
    evidence TEXT,
    missing TEXT,
    eligibility_note TEXT,
    explanation TEXT,
    scored_at TEXT NOT NULL,
    UNIQUE (job_id, profile)
);

CREATE TABLE IF NOT EXISTS applications (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id),
    status TEXT NOT NULL DEFAULT 'New' CHECK (status IN
        ('New','Review','Shortlisted','Applied','Interview',
         'Rejected','Offer','Not Interested','Closed')),
    saved_at TEXT,
    applied_at TEXT,
    followup_date TEXT,
    notes TEXT,
    resume_version TEXT,
    cover_letter_status TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    ok INTEGER,
    jobs_found INTEGER,
    error TEXT
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)