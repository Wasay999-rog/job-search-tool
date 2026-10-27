import re
from datetime import datetime, timezone

from app.db import get_conn, init_db

# ---------------- Settings you can edit ----------------
MY_YEARS = 3          # years of data-engineering experience on your resume
MIN_SALARY = 95000    # yearly USD; only applied when pay is published
HIGH_MATCH = 65       # score at or above this counts as a high match
WEIGHTS = {"skills": 0.35, "title": 0.25, "experience": 0.15,
           "location": 0.15, "seniority": 0.10}

# name: (regex, evidence from YOUR resume, or None if it is not on your resume)
TERMS = {
    "python": (r"\bpython\b", "Python ETL pipelines at Xclusive Trading and KGS"),
    "sql": (r"(?<![a-z])sql(?![a-z])", "SQL transformations and query tuning at Sutherland"),
    "airflow": (r"\bairflow\b", "Airflow pipeline (~500K Stripe records/day to S3) at KGS; DAGs at Sutherland"),
    "dbt": (r"\bdbt\b", "dbt models on Snowflake at KGS (dashboards from 2 hrs to near real-time)"),
    "snowflake": (r"\bsnowflake\b", "Snowflake dbt models at KGS"),
    "spark": (r"\bpyspark\b|apache spark|\bspark(?:sql)?\b", "PySpark in the real-time e-commerce platform project"),
    "databricks": (r"\bdatabricks\b|delta lake", "Databricks in the e-commerce data platform project"),
    "kafka": (r"\bkafka\b", "Kafka in the real-time e-commerce platform project"),
    "aws": (r"\baws\b|\bs3\b|amazon web services", "AWS S3, Redshift, Glue and CloudWatch at KGS and Sutherland"),
    "redshift": (r"\bredshift\b", "Redshift pipelines at Sutherland; Power BI on Redshift at Mahindra"),
    "azure": (r"\bazure\b|synapse|data factory", "Azure data layer and portals at Xclusive Trading"),
    "etl": (r"\betl\b|\belt\b|data pipelines?|ingestion", "ETL/ELT pipelines at Xclusive, KGS and Sutherland"),
    "postgres": (r"\bpostgres(?:ql)?\b", "PostgreSQL at Sutherland"),
    "power_bi": (r"power ?bi", "Power BI dashboards at Xclusive Trading and Mahindra"),
    "tableau": (r"\btableau\b", "Tableau (listed in your skills)"),
    "dimensional": (r"dimensional model|star schema|fact (?:and|&) dimension|data model(?:l)?ing|data models?",
                    "Reusable fact/dimension tables at Sutherland"),
    "dashboard": (r"dashboard|data visuali[sz]ation|\bbi tools?\b", "Power BI dashboards at Xclusive and Mahindra"),
    "data_warehouse": (r"data warehous|warehousing|\bdwh\b", "Redshift and Snowflake warehouse work"),
    "data_quality": (r"data quality|data validation|great expectations|data observability|data testing",
                     "Great Expectations checks and CloudWatch alerts at KGS (98% reliability)"),
    "rest_api": (r"rest(?:ful)? apis?|api integrations?|api ingestion", "Stripe API ingestion at KGS"),
    "ci_cd": (r"ci/cd|github actions|continuous integration|\bcicd\b", "GitHub Actions CI/CD at Xclusive Trading"),
    "ab_testing": (r"a/b test|experimentation", "A/B tests at Mahindra Finance"),
    "llm": (r"\bllms?\b|large language model|claude|generative ai|\bgenai\b", "Claude API integration in Clavira Finance"),
    # Not on your resume: shown as "missing" when a posting asks for them
    "bigquery": (r"\bbigquery\b", None),
    "looker": (r"\blooker\b|lookml", None),
    "kubernetes": (r"kubernetes|\bk8s\b", None),
    "terraform": (r"\bterraform\b", None),
    "scala": (r"\bscala\b", None),
    "java": (r"\bjava\b(?!script)", None),
    "docker": (r"\bdocker\b", None),
    "dagster": (r"dagster|prefect", None),
    "fivetran": (r"fivetran|airbyte|stitch", None),
    "flink": (r"\bflink\b", None),
    "hadoop": (r"hadoop|\bhive\b|\bhdfs\b", None),
    "gcp": (r"\bgcp\b|google cloud", None),
    "lakehouse": (r"iceberg|lakehouse|\bhudi\b", None),
}
TERMS = {k: (re.compile(p, re.I), ev) for k, (p, ev) in TERMS.items()}

PROFILES = {
    "Data Engineer": {
        "weights": {"python": 3, "sql": 3, "airflow": 3, "spark": 3, "etl": 3, "dbt": 2,
                    "snowflake": 2, "aws": 2, "kafka": 2, "databricks": 2, "data_warehouse": 2,
                    "bigquery": 2, "gcp": 2, "redshift": 1, "azure": 1, "data_quality": 1,
                    "postgres": 1, "ci_cd": 1, "rest_api": 1, "dimensional": 1, "kubernetes": 1,
                    "terraform": 1, "scala": 1, "java": 1, "docker": 1, "dagster": 1,
                    "fivetran": 1, "flink": 1, "hadoop": 1, "lakehouse": 1},
        "title_positive": [(r"data engineer|\betl\b|\belt developer", 1.0),
                           (r"analytics engineer", 0.7),
                           (r"data platform|data infrastructure|data pipeline|data warehouse|big data", 0.7),
                           (r"software engineer.*\bdata\b|\bdata\b.*software engineer", 0.6)],
    },
    "Analytics/BI Engineer": {
        "weights": {"sql": 3, "dbt": 3, "power_bi": 3, "tableau": 3, "looker": 3, "dimensional": 3,
                    "dashboard": 2, "data_warehouse": 2, "snowflake": 2, "bigquery": 2,
                    "python": 1, "redshift": 1, "azure": 1, "etl": 1, "ab_testing": 1,
                    "data_quality": 1},
        "title_positive": [(r"analytics engineer|bi engineer|business intelligence|bi developer|bi analyst|reporting engineer", 1.0),
                           (r"data analyst|analytics", 0.5),
                           (r"data engineer", 0.4)],
    },
}

TITLE_NEGATIVE = re.compile(
    r"\b(manager|director|head of|vp|vice president|sales|solutions architect|solutions engineer|"
    r"account (executive|manager)|marketing|counsel|attorney|designer|product manager|"
    r"program manager|partnerships?|customer success|scientist|researcher|recruiter|"
    r"intern(?:ship)?|new grad|quantitative|strategy|business partner)\b", re.I)
LOCATION_OK = [re.compile(p, re.I) for p in (
    r"\bmiami\b", r"\bflorida\b", r",\s*fl\b", r"\bdenver\b", r"\bboulder\b", r"\bcolorado\b",
    r",\s*co\b", r"new york", r"\bnyc\b", r",\s*ny\b", r"\byoungstown\b", r"\bwarren, oh",
    r"\bakron\b", r"\bcleveland\b", r"\bcanton\b", r"\bpittsburgh\b", r"\bboardman\b")]
NON_US = re.compile(
    r"\b(canada|united kingdom|uk|london|india|bengaluru|bangalore|hyderabad|emea|europe|"
    r"germany|berlin|ireland|dublin|singapore|australia|sydney|brazil|são paulo|sao paulo|"
    r"(?<!new )mexico|japan|tokyo|toronto|vancouver|amsterdam|netherlands|france|paris|"
    r"poland|warsaw|krakow|spain|madrid|barcelona|lisbon|milan|munich|zurich|stockholm|copenhagen|oslo|helsinki|prague|bucharest|budapest|dubai|seoul|shanghai|beijing|hong kong|manila|jakarta|bogota|buenos aires|santiago|portugal|israel|tel aviv|philippines|argentina|colombia|chile|"
    r"apac|latam|sweden|switzerland)\b", re.I)
US_HINT = re.compile(
    r"united states|\bUSA?\b|\bUS-|new york|,\s?(?:A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|"
    r"K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])\b")

NO_SPONSOR = re.compile(
    r"(?:not|unable to|cannot|can't|won't|will not|do not|does not|don't|doesn't)\s+(?:\w+\s+){0,3}sponsor"
    r"|without (?:the need for )?(?:visa )?sponsorship|no (?:visa )?sponsorship"
    r"|sponsorship (?:is )?not (?:available|offered)", re.I)
YES_SPONSOR = re.compile(r"visa sponsorship (?:is )?(?:available|offered)|sponsorship (?:is )?available|we (?:will )?sponsor", re.I)
CITIZEN = re.compile(r"u\.?s\.? citizen|security clearance|\bitar\b|export control", re.I)
YEARS = re.compile(r"(\d{1,2})\+?\s*(?:-\s*\d{1,2}\+?\s*|to\s*\d{1,2}\s*)?years?[^.\n]{0,60}?experience", re.I)


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def title_score(title, profile):
    if TITLE_NEGATIVE.search(title):
        return 0.0
    best = 0.0
    for pattern, score in PROFILES[profile]["title_positive"]:
        if re.search(pattern, title, re.I):
            best = max(best, score)
    return best


def seniority_score(title):
    t = title.lower()
    if re.search(r"staff|principal|distinguished|architect|\blead\b", t):
        return 0.2
    if re.search(r"\bsenior\b|\bsr\b|\biii\b", t):
        return 0.5
    if "intern" in t:
        return 0.2
    return 1.0


def experience_score(text):
    found = [int(m.group(1)) for m in YEARS.finditer(text)]
    if not found:
        return 0.6, None
    need = max(found)
    if need <= MY_YEARS:
        return 1.0, need
    return {4: 0.7, 5: 0.4}.get(need, 0.1), need


def location_score(loc):
    raw = loc or ""
    l = raw.lower()
    if NON_US.search(raw) and not US_HINT.search(raw):
        return 0.0, "Outside the US (excluded)"
    if any(p.search(l) for p in LOCATION_OK):
        return 1.0, "Location matches one of your preferred areas"
    if "remote" in l:
        return 0.9, "Remote (confirm it is open to US-based hires)"
    return 0.8, "Outside your preferred areas (not penalized: target company)"


def score_job(job, profile):
    title = job["title"] or ""
    t_score = title_score(title, profile)
    if t_score == 0:
        return None
    text = f"{title}\n{job['description'] or ''}"
    weights = PROFILES[profile]["weights"]

    asked = [n for n in weights if TERMS[n][0].search(text)]
    asked_weight = sum(weights[n] for n in asked)
    have = [n for n in asked if TERMS[n][1]]
    missing = sorted([n for n in asked if not TERMS[n][1]], key=lambda n: -weights[n])
    if asked_weight:
        raw = sum(weights[n] for n in have) / asked_weight
        confidence = min(1.0, asked_weight / 6)
        s_score = raw * confidence + 0.3 * (1 - confidence)
    else:
        s_score = 0.3

    e_score, need_years = experience_score(text)
    sen = seniority_score(title)
    l_score, l_note = location_score(job["location"])
    if l_score == 0.0:
        return None

    overall = 100 * (WEIGHTS["skills"] * s_score + WEIGHTS["title"] * t_score +
                     WEIGHTS["experience"] * e_score + WEIGHTS["location"] * l_score +
                     WEIGHTS["seniority"] * sen)

    notes = []
    smin, smax = job["salary_min"], job["salary_max"]
    if smin and job["salary_interval"] == "year" and job["salary_currency"] in (None, "USD"):
        top = smax or smin
        if top < MIN_SALARY:
            overall -= 10
            notes.append(f"Review: published pay tops out at ${top:,.0f}, below your minimum")
        else:
            notes.append(f"Published pay: ${smin:,.0f}-${top:,.0f}")
    else:
        notes.append("Salary not published (unknown)")
    if NO_SPONSOR.search(text):
        notes.append("Review: posting appears to say it does not sponsor visas")
    elif YES_SPONSOR.search(text):
        notes.append("Posting mentions sponsorship may be available (verify with employer)")
    else:
        notes.append("Sponsorship: not mentioned (unknown)")
    if CITIZEN.search(text):
        notes.append("Review: mentions US citizenship, clearance or export-control rules")

    top_have = sorted(have, key=lambda n: -weights[n])[:4]
    reasons = [f"{n.replace('_', ' ')}: {TERMS[n][1]}" for n in top_have]
    reasons.append(f"Title fit {t_score:.0%} for {profile}")
    reasons.append(l_note)

    missing_notes = [f"Not on resume: {', '.join(n.replace('_', ' ') for n in missing[:5])}"] if missing else []
    if need_years and need_years > MY_YEARS:
        missing_notes.append(f"Asks for {need_years}+ years of experience (you have about {MY_YEARS})")
    elif need_years is None:
        missing_notes.append("Years of experience not stated (unknown)")

    return {
        "overall": round(max(0, min(100, overall)), 1),
        "skills": round(s_score, 2), "experience": round(e_score, 2),
        "location": round(l_score, 2), "seniority": round(sen, 2),
        "evidence": " | ".join(reasons),
        "missing": " | ".join(missing_notes),
        "eligibility": " | ".join(notes),
        "explanation": (f"Skills {s_score:.0%}, title {t_score:.0%}, experience {e_score:.0%}, "
                        f"location {l_score:.0%}, seniority {sen:.0%}. A high score is not a guarantee of an interview."),
    }


def score_all():
    init_db()
    stamp = now_utc()
    with get_conn() as conn:
        jobs = conn.execute(
            """SELECT id, title, description, location, salary_min, salary_max,
                      salary_currency, salary_interval
               FROM jobs WHERE status = 'active'""").fetchall()
        for job in jobs:
            for profile in PROFILES:
                res = score_job(job, profile)
                if res is None:
                    conn.execute("DELETE FROM match_results WHERE job_id = ? AND profile = ?",
                                 (job["id"], profile))
                    continue
                conn.execute(
                    """INSERT INTO match_results (job_id, profile, overall_score, skills_score,
                         experience_score, location_score, seniority_score, evidence, missing,
                         eligibility_note, explanation, scored_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(job_id, profile) DO UPDATE SET
                         overall_score=excluded.overall_score, skills_score=excluded.skills_score,
                         experience_score=excluded.experience_score,
                         location_score=excluded.location_score,
                         seniority_score=excluded.seniority_score, evidence=excluded.evidence,
                         missing=excluded.missing, eligibility_note=excluded.eligibility_note,
                         explanation=excluded.explanation, scored_at=excluded.scored_at""",
                    (job["id"], profile, res["overall"], res["skills"], res["experience"],
                     res["location"], res["seniority"], res["evidence"], res["missing"],
                     res["eligibility"], res["explanation"], stamp))
        conn.commit()

        print(f"Scored {len(jobs)} active jobs.\n")
        for profile in PROFILES:
            counts = conn.execute(
                """SELECT COUNT(*), SUM(overall_score >= ?),
                          SUM(overall_score >= ? AND location_score >= 1)
                                      FROM match_results m JOIN jobs j ON j.id = m.job_id
                   WHERE m.profile = ? AND j.status = 'active'""",
                (HIGH_MATCH, HIGH_MATCH, profile)).fetchone()
            print(f"== {profile}: {counts[0]} relevant titles, {counts[1] or 0} high matches, "
                  f"{counts[2] or 0} high matches in your locations")
            top = conn.execute(
                """SELECT m.overall_score, c.canonical_name, j.title, j.location
                   FROM match_results m JOIN jobs j ON j.id = m.job_id
                   JOIN companies c ON c.id = j.company_id
                   WHERE m.profile = ? AND j.status = 'active'
                   ORDER BY m.overall_score DESC LIMIT 10""", (profile,)).fetchall()
            for r in top:
                print(f"  {r['overall_score']:5.1f}  {r['canonical_name']:<11} {r['title'][:55]:<55} {r['location'][:40]}")
            print()


if __name__ == "__main__":
    score_all()