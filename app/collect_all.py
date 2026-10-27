import time
from datetime import datetime, timezone

import requests

from app.collect import clean_html, normalize as gh_normalize
from app.db import get_conn, init_db

HEADERS = {"User-Agent": "personal-job-search-tool/0.1"}


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_json(url, tries=3):
    last = None
    for attempt in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise last


def fetch_greenhouse(token):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    return data.get("jobs", [])


def fetch_lever(token):
    data = get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    return data if isinstance(data, list) else []


def fetch_ashby(token):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
    return [j for j in data.get("jobs", []) if j.get("isListed", True)]


def norm_greenhouse(raw):
    j = gh_normalize(raw)
    j["apply_url"] = j["job_url"]
    j["source"] = "greenhouse"
    return j


def norm_lever(j):
    cats = j.get("categories") or {}
    locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
    parts = [j.get("descriptionPlain") or ""]
    for lst in j.get("lists") or []:
        parts.append(f"{lst.get('text', '')}\n{clean_html(lst.get('content', ''))}")
    parts.append(j.get("additionalPlain") or "")
    pay = j.get("salaryRange") or {}
    yearly = "year" in str(pay.get("interval", "")).lower()
    created = j.get("createdAt")
    published = (datetime.fromtimestamp(created / 1000, timezone.utc).isoformat(timespec="seconds")
                 if created else None)
    return {
        "external_id": str(j["id"]), "dedupe_key": f"lv:{j['id']}",
        "title": (j.get("text") or "").strip(),
        "description": "\n\n".join(p for p in parts if p).strip(),
        "location": " | ".join(locs),
        "workplace_type": {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite"}.get(j.get("workplaceType")),
        "salary_min": pay.get("min") if yearly else None,
        "salary_max": pay.get("max") if yearly else None,
        "salary_currency": pay.get("currency") if yearly else None,
        "salary_interval": "year" if yearly else None,
        "published_at": published, "job_url": j.get("hostedUrl"),
        "apply_url": j.get("applyUrl") or j.get("hostedUrl"), "source": "lever",
    }


def norm_ashby(j):
    locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
    smin = smax = cur = None
    for item in (j.get("compensation") or {}).get("summaryComponents") or []:
        if item.get("compensationType") == "Salary" and "YEAR" in str(item.get("interval", "")).upper():
            smin, smax, cur = item.get("minValue"), item.get("maxValue"), item.get("currencyCode")
            break
    wt = "remote" if j.get("isRemote") else {"Remote": "remote", "Hybrid": "hybrid",
                                            "OnSite": "onsite"}.get(j.get("workplaceType"))
    return {
        "external_id": str(j["id"]), "dedupe_key": f"ab:{j['id']}",
        "title": (j.get("title") or "").strip(),
        "description": j.get("descriptionPlain") or clean_html(j.get("descriptionHtml")),
        "location": " | ".join(x for x in locs if x),
        "workplace_type": wt, "salary_min": smin, "salary_max": smax,
        "salary_currency": cur, "salary_interval": "year" if smin else None,
        "published_at": j.get("publishedAt"), "job_url": j.get("jobUrl"),
        "apply_url": j.get("applyUrl") or j.get("jobUrl"), "source": "ashby",
    }


PROVIDERS = {
    "greenhouse": (fetch_greenhouse, norm_greenhouse),
    "lever": (fetch_lever, norm_lever),
    "ashby": (fetch_ashby, norm_ashby),
}


def save_company(conn, company, jobs, normalizer, run_time):
    new = updated = 0
    seen = []
    for raw in jobs:
        j = normalizer(raw)
        seen.append(j["dedupe_key"])
        exists = conn.execute(
            "SELECT 1 FROM jobs WHERE company_id = ? AND dedupe_key = ?",
            (company["id"], j["dedupe_key"])).fetchone()
        conn.execute(
            """INSERT INTO jobs (company_id, external_id, dedupe_key, title, description,
                location, workplace_type, salary_min, salary_max, salary_currency,
                salary_interval, published_at, job_url, apply_url, source,
                first_seen, last_seen, last_checked, status, collection_status)
               VALUES (:cid, :external_id, :dedupe_key, :title, :description, :location,
                :workplace_type, :salary_min, :salary_max, :salary_currency,
                :salary_interval, :published_at, :job_url, :apply_url, :source,
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
                collection_status = 'ok'""",
            {**j, "cid": company["id"], "t": run_time})
        if exists:
            updated += 1
        else:
            new += 1
    closed = 0
    if seen:  # a successful fetch with zero jobs never closes anything
        marks = ",".join("?" * len(seen))
        closed = conn.execute(
            f"""UPDATE jobs SET status = 'closed', last_checked = ?
                WHERE company_id = ? AND status = 'active' AND dedupe_key NOT IN ({marks})""",
            [run_time, company["id"], *seen]).rowcount
    return new, updated, closed


def collect_all():
    init_db()
    with get_conn() as conn:
        companies = conn.execute(
            """SELECT id, canonical_name, ats_provider, board_identifier FROM companies
               WHERE ats_provider IN ('greenhouse','lever','ashby')
               AND is_approved = 1 AND is_active = 1 ORDER BY canonical_name""").fetchall()
        ok = failed = 0
        for c in companies:
            started = now_utc()
            fetcher, normalizer = PROVIDERS[c["ats_provider"]]
            try:
                jobs = fetcher(c["board_identifier"])
                new, updated, closed = save_company(conn, c, jobs, normalizer, started)
                conn.execute("UPDATE companies SET last_success_utc = ?, last_error = NULL WHERE id = ?",
                             (now_utc(), c["id"]))
                conn.execute("""INSERT INTO collection_runs (company_id, started_at, finished_at, ok, jobs_found)
                                VALUES (?, ?, ?, 1, ?)""", (c["id"], started, now_utc(), len(jobs)))
                print(f"OK    {c['canonical_name'][:22]:<22} {c['ats_provider']:<10} found={len(jobs):<5} new={new:<5} closed={closed}")
                ok += 1
            except Exception as exc:
                msg = str(exc)[:300]
                conn.execute("UPDATE companies SET last_error = ? WHERE id = ?", (msg, c["id"]))
                conn.execute("""INSERT INTO collection_runs (company_id, started_at, finished_at, ok, error)
                                VALUES (?, ?, ?, 0, ?)""", (c["id"], started, now_utc(), msg))
                print(f"FAIL  {c['canonical_name'][:22]:<22} {msg}")
                failed += 1
            conn.commit()
            time.sleep(1)
    print(f"\nChecked OK: {ok}   Failed: {failed}")


if __name__ == "__main__":
    collect_all()