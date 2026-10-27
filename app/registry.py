import csv
import re
from pathlib import Path

from app.db import get_conn, init_db

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "target_company_registry.csv"

# Same employer listed under two names. Keys are the normalized form.
ALIASES = {
    "twiliosendgrid": "twilio",
}


def make_key(name: str) -> str:
    name = re.sub(r"\(.*?\)", "", name)           # drop parentheticals
    name = name.lower().replace("&", "and")
    key = re.sub(r"[^a-z0-9]", "", name)
    return ALIASES.get(key, key)


def load_rows():
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def merge_rows(rows):
    merged = {}
    groups = {}
    for r in rows:
        name = (r.get("company_name") or "").strip()
        if not name:
            continue
        key = make_key(name)
        groups.setdefault(key, []).append(name)
        if key not in merged:
            merged[key] = {
                "canonical_name": name,
                "sector": (r.get("sector") or "").strip(),
                "target_roles": (r.get("target_roles") or "").strip(),
                "careers_url": (r.get("official_careers_url") or "").strip(),
                "ats_provider": (r.get("ats_provider") or "").strip(),
                "board_identifier": (r.get("board_identifier") or "").strip(),
                "api_supported": (r.get("api_supported") or "Unknown").strip(),
                "notes": [],
            }
            cur = merged[key]
        else:
            cur = merged[key]
            sec = (r.get("sector") or "").strip()
            if sec and sec != cur["sector"]:
                cur["notes"].append(f"Also listed under: {sec}")
            if name != cur["canonical_name"]:
                cur["notes"].append(f"Also listed as: {name}")
        note = (r.get("notes") or "").strip()
        if note and note not in cur["notes"]:
            cur["notes"].append(note)
    return merged, groups


def import_registry():
    init_db()
    rows = load_rows()
    merged, groups = merge_rows(rows)
    with get_conn() as conn:
        for key, c in merged.items():
            conn.execute(
                """
                INSERT INTO companies (canonical_name, name_key, sector, target_roles,
                    careers_url, ats_provider, board_identifier, api_supported, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name_key) DO UPDATE SET
                    canonical_name = excluded.canonical_name,
                    sector = excluded.sector,
                    target_roles = excluded.target_roles,
                    careers_url = COALESCE(NULLIF(excluded.careers_url,''), companies.careers_url),
                    ats_provider = COALESCE(NULLIF(excluded.ats_provider,''), companies.ats_provider),
                    board_identifier = COALESCE(NULLIF(excluded.board_identifier,''), companies.board_identifier),
                    notes = excluded.notes
                """,
                (c["canonical_name"], key, c["sector"], c["target_roles"],
                 c["careers_url"], c["ats_provider"], c["board_identifier"],
                 c["api_supported"], "; ".join(c["notes"])),
            )
    print(f"CSV rows read:        {len(rows)}")
    print(f"Unique employers:     {len(merged)}")
    merged_groups = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"Duplicates merged:    {len(merged_groups)}")
    for names in merged_groups.values():
        print("  -", " | ".join(names))


if __name__ == "__main__":
    import_registry()