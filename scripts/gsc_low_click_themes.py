#!/usr/bin/env python3
"""
Pull Google Search Console queries for crossingwatersnetwork.org,
keep high-impression / low-click non-branded queries, group them into
themes (no facility names), and write a review CSV.

This script is standalone. It does not call WordPress, Buffer, HubSpot,
or the newsletter pipeline.

Usage (from the repo root):
  python scripts/gsc_low_click_themes.py
"""

from __future__ import annotations

import csv
import os
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Tunable thresholds (change these; do not hard-code filters elsewhere)
# ---------------------------------------------------------------------------
LOOKBACK_DAYS = 90
MIN_IMPRESSIONS = 50  # "high impressions"
MAX_CLICKS = 2  # zero or very few clicks (inclusive)

# Search Console property URL must match Search Console exactly
# (URL-prefix property vs Domain property). Override with GSC_PROPERTY_URL.
DEFAULT_SITE_URL = "https://crossingwatersnetwork.org/"

# Read-only Search Console scope
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"

# Brand terms (case-insensitive substring). Anything matching is dropped.
BRAND_TERMS = (
    "crossing waters",
    "crossingwatersnetwork",
)

# First matching theme wins. Labels stay generic (no facility, program, or org names).
# Queries that are only a named facility/org go to FACILITY_NAME_THEME — the names
# are never written to the CSV.
THEME_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "Location searches",
        (
            "near me",
            "nearby",
            "in my area",
            "around me",
            "close to me",
            "alabama",
            "alaska",
            "arizona",
            "arkansas",
            "california",
            "colorado",
            "connecticut",
            "delaware",
            "florida",
            "georgia",
            "hawaii",
            "idaho",
            "illinois",
            "indiana",
            "iowa",
            "kansas",
            "kentucky",
            "louisiana",
            "maine",
            "maryland",
            "massachusetts",
            "michigan",
            "minnesota",
            "mississippi",
            "missouri",
            "montana",
            "nebraska",
            "nevada",
            "new hampshire",
            "new jersey",
            "new mexico",
            "new york",
            "north carolina",
            "north dakota",
            "ohio",
            "oklahoma",
            "oregon",
            "pennsylvania",
            "rhode island",
            "south carolina",
            "south dakota",
            "tennessee",
            "texas",
            "utah",
            "vermont",
            "virginia",
            "washington",
            "west virginia",
            "wisconsin",
            "wyoming",
            "district of columbia",
            "northeast",
            "midwest",
            "southeast",
            "southwest",
            "pacific northwest",
        ),
    ),
    (
        "Treatment types",
        (
            "inpatient",
            "residential",
            "outpatient",
            "hospital",
            "psychiatric",
            "php",
            "iop",
            "day treatment",
            "partial hospitalization",
            "intensive outpatient",
            "therapy",
            "therapist",
            "counseling",
            "counselor",
            "psychotherapy",
            "group therapy",
            "individual therapy",
        ),
    ),
    (
        "Symptoms and conditions",
        (
            "depression",
            "anxiety disorder",
            "bipolar",
            "ptsd",
            "trauma",
            "adhd",
            "ocd",
            "schizophrenia",
            "panic",
            "eating disorder",
        ),
    ),
    (
        "Family support",
        ("family", "loved one", "parent", "caregiver", "spouse", "for families"),
    ),
    (
        "Insurance and cost",
        (
            "insurance",
            "medicaid",
            "medicare",
            "tricare",
            "cost",
            "afford",
            "sliding scale",
            "accepts",
        ),
    ),
    (
        "Crisis and urgent help",
        ("crisis", "988", "emergency", "suicide", "urgent", "hotline"),
    ),
    (
        "Levels of care",
        (
            "level of care",
            "levels of care",
            "what is inpatient",
            "what is outpatient",
            "what is residential",
            "how to find",
            "how to choose",
        ),
    ),
]

FACILITY_NAME_THEME = "Facility name searches"
UNCATEGORIZED_THEME = "Other / uncategorized"

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_CSV = ROOT / "output" / "gsc_low_click_themes.csv"
GSC_ROW_LIMIT = 25000  # API max per request


def load_env() -> None:
    load_dotenv(ROOT / ".env")


def site_url() -> str:
    return os.environ.get("GSC_PROPERTY_URL", DEFAULT_SITE_URL).strip() or DEFAULT_SITE_URL


def credentials_path() -> Path:
    raw = os.environ.get("GSC_SERVICE_ACCOUNT_FILE", "gsc-service-account.json").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path


def is_branded(query: str) -> bool:
    q = query.casefold()
    return any(term in q for term in BRAND_TERMS)


def _keyword_in_query(query: str, keyword: str) -> bool:
    """Whole-phrase match so short tokens do not hit inside other words."""
    return re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", query) is not None


def assign_theme(query: str) -> str:
    """Map a query to one generic theme. Never uses facility names as labels."""
    q = query.casefold()
    for theme, keywords in THEME_RULES:
        for keyword in keywords:
            if _keyword_in_query(q, keyword):
                return theme
    # Remaining filtered queries in this dataset are named-org searches.
    return FACILITY_NAME_THEME


def gsc_client():
    path = credentials_path()
    if not path.is_file():
        raise SystemExit(
            f"Missing Search Console service-account JSON: {path}\n"
            "Set GSC_SERVICE_ACCOUNT_FILE in .env. See README (Search Console)."
        )
    creds = service_account.Credentials.from_service_account_file(
        path, scopes=[GSC_SCOPE]
    )
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def fetch_queries(service, site: str, start: str, end: str) -> list[dict]:
    """All query rows for the date range (paginated)."""
    rows: list[dict] = []
    start_row = 0
    while True:
        body = {
            "startDate": start,
            "endDate": end,
            "dimensions": ["query"],
            "rowLimit": GSC_ROW_LIMIT,
            "startRow": start_row,
        }
        data = service.searchanalytics().query(siteUrl=site, body=body).execute()
        batch = data.get("rows") or []
        for row in batch:
            keys = row.get("keys") or [""]
            rows.append(
                {
                    "query": keys[0],
                    "clicks": int(row.get("clicks") or 0),
                    "impressions": int(row.get("impressions") or 0),
                    "ctr": float(row.get("ctr") or 0),
                    "position": float(row.get("position") or 0),
                }
            )
        if len(batch) < GSC_ROW_LIMIT:
            break
        start_row += GSC_ROW_LIMIT
    return rows


def filter_queries(rows: list[dict]) -> list[dict]:
    kept = []
    for row in rows:
        if is_branded(row["query"]):
            continue
        if row["impressions"] < MIN_IMPRESSIONS:
            continue
        if row["clicks"] > MAX_CLICKS:
            continue
        kept.append(row)
    return kept


def group_themes(rows: list[dict]) -> list[dict]:
    totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"query_count": 0, "total_impressions": 0}
    )
    for row in rows:
        theme = assign_theme(row["query"])
        totals[theme]["query_count"] += 1
        totals[theme]["total_impressions"] += row["impressions"]
    out = [
        {
            "theme": theme,
            "query_count": stats["query_count"],
            "total_impressions": stats["total_impressions"],
        }
        for theme, stats in totals.items()
    ]
    out.sort(key=lambda r: r["total_impressions"], reverse=True)
    return out


def write_csv(path: Path, themes: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["theme", "query_count", "total_impressions"]
        )
        writer.writeheader()
        writer.writerows(themes)


def main() -> None:
    load_env()
    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    site = site_url()
    service = gsc_client()
    raw = fetch_queries(service, site, start.isoformat(), end.isoformat())
    filtered = filter_queries(raw)
    uncategorized = [
        row["query"]
        for row in filtered
        if assign_theme(row["query"]) == UNCATEGORIZED_THEME
    ]
    print("Uncategorized queries (terminal only, not written to CSV):")
    if uncategorized:
        for query in uncategorized:
            print(query)
    else:
        print("(none)")
    print(f"Still uncategorized: {len(uncategorized)}")
    themes = group_themes(filtered)
    print(f"Site:        {site}")
    print(f"Dates:       {start.isoformat()} to {end.isoformat()}")
    print(f"API rows:    {len(raw)}")
    print(f"After filter:{len(filtered)}  (impressions>={MIN_IMPRESSIONS}, clicks<={MAX_CLICKS}, not branded)")
    print(f"Themes:      {len(themes)}")
    print("Theme table:")
    print(f"{'theme':<28} {'query_count':>12} {'total_impressions':>18}")
    for row in themes:
        print(
            f"{row['theme']:<28} {row['query_count']:>12} {row['total_impressions']:>18}"
        )
    write_csv(OUTPUT_CSV, themes)
    print(f"Wrote:       {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
