import csv
import re
import time
from pathlib import Path

import requests

from app.db import get_conn

HEADERS = {"User-Agent": "personal-job-search-tool/0.1"}
REPORT = Path(__file__).resolve().parent.parent / "data" / "discovery_report.csv"
SUFFIX = {"inc", "ai", "finance", "group", "systems", "corporation", "co", "technologies", "tech"}
GENERIC = {"target", "box", "modal", "ramp", "mercury", "wise", "chime", "toast",
           "replicate", "unstructured", "pave", "glean"}


def get_json(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        return r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None


def words_of(part):
    part = re.sub(r"\(.*?\)", "", part).lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9 ]", " ", part).split()


def parts_of(name):
    return re.split(r"\s*/\s*", re.sub(r"\(.*?\)", "", name))


def slugs(name):
    out = []
    for part in parts_of(name):
        w = words_of(part)
        if not w:
            continue
        core = [x for x in w if x not in SUFFIX] or w
        for s in ("".join(w), "-".join(w), "".join(core), core[0]):
            if s not in out:
                out.append(s)
    return out[:5]


def probe_greenhouse(slug):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    if not data:
        return None
    jobs = data.get("jobs", [])
    board = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}") or {}
    return {"count": len(jobs), "board_name": board.get("name", ""),
            "texts": [j.get("content", "") for j in jobs[:5]],
            "title": jobs[0]["title"] if jobs else "",
            "url": jobs[0].get("absolute_url", "") if jobs else ""}


def probe_lever(slug):
    data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(data, list):
        return None
    return {"count": len(data), "board_name": "",
            "texts": [d.get("descriptionPlain", "") for d in data[:5]],
            "title": data[0]["text"] if data else "",
            "url": data[0].get("hostedUrl", "") if data else ""}


def probe_ashby(slug):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if not data:
        return None
    jobs = data.get("jobs", [])
    return {"count": len(jobs), "board_name": "",
            "texts": [j.get("descriptionPlain", "") or "" for j in jobs[:5]],
            "title": jobs[0]["title"] if jobs else "",
            "url": jobs[0].get("jobUrl", "") if jobs else ""}


PROBES = [("greenhouse", probe_greenhouse), ("lever", probe_lever), ("ashby", probe_ashby)]


def is_verified(name, res):
    ph = [" ".join(words_of(p)) for p in parts_of(name) if words_of(p)]
    joined = {p.replace(" ", "") for p in ph}
    board = re.sub(r"[^a-z0-9]", "", res["board_name"].lower())
    if joined & GENERIC:
        return bool(board) and board in joined
    if board and any(j in board for j in joined):
        return True
    hits = sum(1 for t in res["texts"] if any(p in t.lower() for p in ph))
    return hits >= 3


def discover():
    report = []
    with get_conn() as conn:
        companies = conn.execute(
            """SELECT id, canonical_name FROM companies
               WHERE (ats_provider IS NULL OR ats_provider = '') AND is_approved = 1
               ORDER BY canonical_name""").fetchall()
        for c in companies:
            name = c["canonical_name"]
            best = None
            for slug in slugs(name):
                for prov, fn in PROBES:
                    res = fn(slug)
                    time.sleep(0.3)
                    if not res or res["count"] == 0:
                        continue
                    cand = (prov, slug, res, is_verified(name, res))
                    if cand[3]:
                        best = cand
                        break
                    if best is None:
                        best = cand
                if best and best[3]:
                    break
            if best and best[3]:
                prov, slug, res, _ = best
                conn.execute("UPDATE companies SET ats_provider = ?, board_identifier = ? WHERE id = ?",
                             (prov, slug, c["id"]))
                conn.commit()
                state = "connected"
                print(f"AUTO   {name[:34]:<34} {prov}/{slug}  jobs={res['count']}")
            elif best:
                prov, slug, res, _ = best
                state = "needs_check"
                print(f"CHECK  {name[:34]:<34} {prov}/{slug}  jobs={res['count']}  e.g. {res['title'][:40]}")
            else:
                prov = slug = ""
                res = {"count": 0, "title": "", "url": ""}
                state = "not_found"
                print(f"none   {name}")
            report.append([name, state, prov, slug, res["count"], res["title"], res["url"]])

    with open(REPORT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["company", "state", "provider", "slug", "open_jobs", "sample_title", "sample_url"])
        w.writerows(report)
    for state in ("connected", "needs_check", "not_found"):
        print(f"{state}: {sum(1 for r in report if r[1] == state)}")
    print(f"Report saved to {REPORT}")


if __name__ == "__main__":
    discover()
