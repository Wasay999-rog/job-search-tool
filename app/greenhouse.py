import time
from datetime import datetime, timezone

import requests

from app.db import get_conn
from app.registry import make_key

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
HEADERS = {"User-Agent": "personal-job-search-tool/0.1"}

# Company name (must match your registry) -> guessed Greenhouse board token.
CANDIDATES = {
    "Anthropic": "anthropic",
    "Databricks": "databricks",
    "Stripe": "stripe",
    "Brex": "brex",
    "Datadog": "datadog",
    "GitLab": "gitlab",
    "Figma": "figma",
    "Robinhood": "robinhood",
    "Affirm": "affirm",
    "Instacart": "instacart",
}


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_board(token):
    resp = requests.get(API.format(token=token), headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json().get("jobs", [])


def probe():
    with get_conn() as conn:
        for name, token in CANDIDATES.items():
            row = conn.execute(
                "SELECT id FROM companies WHERE name_key = ?", (make_key(name),)
            ).fetchone()
            if not row:
                print(f"SKIP  {name:<14} not in your registry")
                continue
            try:
                jobs = fetch_board(token)
            except requests.RequestException as exc:
                conn.execute(
                    "UPDATE companies SET last_error = ? WHERE id = ?",
                    (f"greenhouse probe '{token}': {exc}"[:300], row["id"]),
                )
                print(f"FAIL  {name:<14} token={token}  ({exc})")
            else:
                conn.execute(
                    """UPDATE companies SET ats_provider = 'greenhouse',
                       board_identifier = ?, last_success_utc = ?, last_error = NULL
                       WHERE id = ?""",
                    (token, now_utc(), row["id"]),
                )
                print(f"OK    {name:<14} token={token}  open jobs={len(jobs)}")
            time.sleep(1)


if __name__ == "__main__":
    probe()