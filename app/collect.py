import html
import re
import time
from datetime import datetime, timezone

import requests

from app.db import get_conn, init_db

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
HEADERS = {"User-Agent": "personal-job-search-tool/0.1"}


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_html(raw):
    text = html.unescape(raw or "")
    text = re.sub(r"<(br|/p|/li|/h\d)\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def fetch_with_retry(token, tries=3):
    last = None
    for attempt in range(tries):
        try:
            resp = requests.get(API.format(token=token), headers=HEADERS, timeout=30)
            resp.raise_for_status()
            return resp.json().get("jobs", [])
        except requests.RequestException as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise last


def normalize(job):
    location = (job.get("location") or {}).get("name") or ""
    pay = (job.get("pay_input_ranges") or [{}])[0] if job.get("pay_input_ranges") else {}
    smin = pay.get("min_cents")
    smax = pay.get("max_cents")
    return {
        "external_id": str(job["id"]),
        "dedupe_key": f"gh:{job['id']}",
        "title": (job.get("title") or "").strip(),
        "description": clean_html(job.get("content")),
        "location": location,
        "workplace_type": "remote" if "remote" in location.lower() else None,
        "salary_min": smin / 100 if smin else None,
        "salary_max": smax / 100 if smax else None,
        "salary_currency": pay.get("currency_type"),
        "salary_interval": "year" if smin else None,
        "published_at": job.get("first_published") or job.get("updated_at"),
        "job_url": job.get("absolute_url"),
    }


def save_company(conn, company, jobs, run_time):
    new = updated = 0
    seen_keys = []
    for raw in jobs:
        j = normalize(raw)
        seen_keys.append(j["dedupe_key"])
        exists = conn.execute(
            "SELECT 1 FROM jobs WHERE company_id = ? AND dedupe_key = ?",
            (company["id"], j["dedupe_key"]),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO jobs (company_id, external_id, dedupe_key, title, description,
                location, workplace_type, salary_min, salary_max, salary_currency,
                salary_interval, published_at, job_url, apply_url, source,
                first_seen, last_seen, last_checked, status, collection_status)
            VALUES (:cid, :external_id, :dedupe_key, :title, :description, :location,
                :workplace_type, :salary_min, :salary_max, :salary_currency,
                :salary_interval, :published_at, :job_url, :job_url, 'greenhouse',
                :t, :t, :t, 'active', 'ok')
            ON CONFLICT(company_id, dedupe_key) DO UPDATE SET
                title = excluded.title, description = excluded.description,
                location = excluded.location, workplace_type = excluded.workplace_type,
                salary_min = excluded.salary_min, salary_max = excluded.salary_max,
                salary_currency = excluded.salary_currency,
                salary_interval = excluded.salary_interval,
                published_at = excluded.published_at, job_url = excluded.job_url,
                apply_url = excluded.apply_url, last_seen = excluded.last_seen,
                last_checked = excluded.last_checked, status = 'active',
                collection_status = 'ok'
            """,
            {**j, "cid": company["id"], "t": run_time},
        )
        if exists:
            updated += 1
        else:
            new += 1
    # Only after a successful full fetch: mark jobs no longer listed as closed.
    placeholders = ",".join("?" * len(seen_keys)) or "''"
    closed = conn.execute(
        f"""UPDATE jobs SET status = 'closed', last_checked = ?
            WHERE company_id = ? AND status = 'active'
            AND dedupe_key NOT IN ({placeholders})""",
        [run_time, company["id"], *seen_keys],
    ).rowcount
    return new, updated, closed


def collect_all():
    init_db()
    with get_conn() as conn:
        companies = conn.execute(
            """SELECT id, canonical_name, board_identifier FROM companies
               WHERE ats_provider = 'greenhouse' AND is_approved = 1 AND is_active = 1"""
        ).fetchall()
        for c in companies:
            started = now_utc()
            try:
                jobs = fetch_with_retry(c["board_identifier"])
                new, updated, closed = save_company(conn, c, jobs, started)
                conn.execute(
                    "UPDATE companies SET last_success_utc = ?, last_error = NULL WHERE id = ?",
                    (now_utc(), c["id"]),
                )
                conn.execute(
                    """INSERT INTO collection_runs (company_id, started_at, finished_at, ok, jobs_found)
                       VALUES (?, ?, ?, 1, ?)""",
                    (c["id"], started, now_utc(), len(jobs)),
                )
                print(f"OK    {c['canonical_name']:<12} found={len(jobs):<4} new={new:<4} updated={updated:<4} closed={closed}")
            except Exception as exc:
                msg = str(exc)[:300]
                conn.execute("UPDATE companies SET last_error = ? WHERE id = ?", (msg, c["id"]))
                conn.execute(
                    """INSERT INTO collection_runs (company_id, started_at, finished_at, ok, error)
                       VALUES (?, ?, ?, 0, ?)""",
                    (c["id"], started, now_utc(), msg),
                )
                print(f"FAIL  {c['canonical_name']:<12} {msg}")
            conn.commit()
            time.sleep(1)


if __name__ == "__main__":
    collect_all()