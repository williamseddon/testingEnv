
# amazonReviewScraper_streamlined.py
# Streamlit: Amazon Reviews Scraper (Apify) — streamlined queue UI
#
# Install:
#   pip install streamlit apify-client pandas openpyxl
#
# Run:
#   streamlit run amazonReviewScraper_streamlined.py

from __future__ import annotations

import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import pandas as pd
import streamlit as st
from apify_client import ApifyClient
from apify_client.errors import ApifyApiError


# ----------------------------
# Config
# ----------------------------
APP_TITLE = "Amazon Reviews Scraper (Apify)"
DEFAULT_ACTOR_ID = "8vhDnIX6dStLlGVr7"
MAX_PER_ASIN_HARD_CAP = 20000

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$", re.IGNORECASE)

SORT_LABEL_TO_KEY = {"Recent": "recent", "Helpful": "helpful"}
SORT_OVERRIDE_OPTIONS = ["Default", "Recent", "Helpful"]

# Keep these aligned to what your actor expects.
COUNTRY_VALUES = [
    "France",
    "United States",
    "United Kingdom",
    "Germany",
    "Italy",
    "Spain",
    "Canada",
    "Japan",
]

# Rating UI -> actor values
# Preferred: All stars => ["all_stars"]. If the actor rejects it, we fallback to 1..5 buckets.
RATING_UI_OPTIONS = ["All stars", "1-star", "2-star", "3-star", "4-star", "5-star"]
RATING_UI_TO_ACTOR = {
    "All stars": ["all_stars"],
    "1-star": ["one_star"],
    "2-star": ["two_star"],
    "3-star": ["three_star"],
    "4-star": ["four_star"],
    "5-star": ["five_star"],
}
RATING_FALLBACK_ALL = ["one_star", "two_star", "three_star", "four_star", "five_star"]

# Video-widget “junk” signature captured in ReviewContent
VIDEO_MARKER = "This is a modal window."

# Internal table columns (we still accept/import the older "Delete" column)
COL_ENABLED = "Enabled"
COL_COUNTRY = "Country"
COL_ASIN = "ASIN or URL"
COL_REVIEWS = "Reviews to pull"
COL_RATING = "Rating"
COL_SORT = "Sort"
COL_SELECTED = "Selected"   # checkbox used for remove/disable/enable actions


# ----------------------------
# Data models
# ----------------------------
@dataclass(frozen=True)
class JobSpec:
    row_id: int
    asin: str
    country: str
    max_reviews: int
    rating_ui: str
    sort_override: str


@dataclass
class JobResult:
    spec: JobSpec
    ok: bool
    collected: int
    runtime_s: float
    run_id: Optional[str]
    dataset_id: Optional[str]
    usage_total_usd: Optional[float]
    compute_units: Optional[float]
    pricing_model: Optional[str]
    error: Optional[str]
    items: List[dict]


# ----------------------------
# General helpers
# ----------------------------
def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_seconds(sec: Optional[float]) -> str:
    if sec is None:
        return "—"
    sec = max(0.0, float(sec))
    if sec < 60:
        return f"{sec:.0f}s"
    if sec < 3600:
        m = int(sec // 60)
        s = int(sec % 60)
        return f"{m}m {s:02d}s"
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    return f"{h}h {m:02d}m"


def normalize_asin(raw: str) -> str:
    raw = (raw or "").strip()
    m = re.search(r"/dp/([A-Z0-9]{10})", raw, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b([A-Z0-9]{10})\b", raw, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return raw.upper()


def is_valid_asin(asin: str) -> bool:
    return bool(ASIN_RE.match(asin or ""))


def safe_sheet_name(name: str) -> str:
    name = re.sub(r"[:\\/?*\[\]]", "_", name)
    return name[:31] if len(name) > 31 else name


def resolve_sort_key(global_sort_key: str, override: str) -> str:
    if override == "Default":
        return global_sort_key
    return SORT_LABEL_TO_KEY.get(override, global_sort_key)


def parse_score_value(score: Any) -> Optional[float]:
    if score is None:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", str(score))
    return float(m.group(1)) if m else None


def extract_filter_by_star_from_pageurl(url: Any) -> Optional[str]:
    if not isinstance(url, str) or not url:
        return None
    q = parse_qs(urlparse(url).query)
    v = q.get("filterByStar")
    return v[0] if v else None


# ----------------------------
# Streamlit secrets / token helper
# ----------------------------
def get_apify_token_from_secrets() -> str:
    """
    Supports either:
      APIFY_TOKEN="..."
    or:
      [apify]
      token="..."
    """
    try:
        if "APIFY_TOKEN" in st.secrets:
            return str(st.secrets["APIFY_TOKEN"]).strip()
        if "apify" in st.secrets and "token" in st.secrets["apify"]:
            return str(st.secrets["apify"]["token"]).strip()
    except Exception:
        pass
    return ""


# ----------------------------
# ReviewContent cleaning (video widget blob)
# ----------------------------
def split_leading_json(s: str) -> Tuple[Optional[str], str]:
    """
    If s starts with a JSON object, return (json_str, remainder_after_json).
    Otherwise (None, s).
    """
    s = "" if s is None else str(s)
    if not s.startswith("{"):
        return None, s

    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[: i + 1], s[i + 1 :]
    return None, s


def parse_video_meta(json_str: Optional[str]) -> Dict[str, Any]:
    if not json_str:
        return {}
    try:
        d = json.loads(json_str)
    except Exception:
        return {}

    out: Dict[str, Any] = {}
    if d.get("videoUrl"):
        out["VideoUrl"] = d.get("videoUrl")
    if d.get("imageUrl"):
        out["VideoPosterImageUrl"] = d.get("imageUrl")
    if d.get("initialClosedCaptions"):
        out["VideoCaptionsUrl"] = d.get("initialClosedCaptions")
    if out:
        out["HasVideoWidget"] = True
    return out


def clean_review_content(raw: Any) -> Tuple[str, Dict[str, Any], bool]:
    """
    Returns (clean_text, video_meta, had_video_widget)
    """
    s = "" if raw is None else str(raw)

    # Signature: JSON starts the field + contains videoUrl + modal marker text
    if s.startswith("{") and '"videoUrl"' in s and VIDEO_MARKER in s:
        json_str, rem = split_leading_json(s)
        vmeta = parse_video_meta(json_str)
        rem = rem.split(VIDEO_MARKER)[-1].strip()
        rem = re.sub(r"\s+", " ", rem).strip()
        return rem, vmeta, True

    return s.strip(), {}, False


# ----------------------------
# Export helpers
# ----------------------------
def export_excel_bytes(per_sheet: Dict[str, pd.DataFrame], master: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet_name, df in per_sheet.items():
            df.to_excel(writer, sheet_name=safe_sheet_name(sheet_name), index=False)
        master.to_excel(writer, sheet_name="MASTER", index=False)
    buf.seek(0)
    return buf.read()


def export_csv_bytes(master: pd.DataFrame) -> bytes:
    return master.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


def export_config_csv_bytes(df: pd.DataFrame) -> bytes:
    # Backwards compatible config columns
    cols = [COL_ENABLED, COL_COUNTRY, COL_ASIN, COL_REVIEWS, COL_RATING, COL_SORT]
    out = ensure_table_columns(df).copy()
    out = out[cols]
    return out.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


# ----------------------------
# Queue table helpers
# ----------------------------
def ensure_table_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensures required columns exist.

    Also accepts legacy exports that have a "Delete" column, mapping it to "Selected".
    """
    df = df.copy()

    # Legacy support
    if "Delete" in df.columns and COL_SELECTED not in df.columns:
        df = df.rename(columns={"Delete": COL_SELECTED})

    if COL_ENABLED not in df.columns:
        df[COL_ENABLED] = True
    if COL_COUNTRY not in df.columns:
        df[COL_COUNTRY] = "France"
    if COL_ASIN not in df.columns:
        df[COL_ASIN] = ""
    if COL_REVIEWS not in df.columns:
        df[COL_REVIEWS] = 100
    if COL_RATING not in df.columns:
        df[COL_RATING] = "All stars"
    if COL_SORT not in df.columns:
        df[COL_SORT] = "Default"
    if COL_SELECTED not in df.columns:
        df[COL_SELECTED] = False

    # Clean types
    df[COL_ENABLED] = df[COL_ENABLED].fillna(True).astype(bool)
    df[COL_SELECTED] = df[COL_SELECTED].fillna(False).astype(bool)
    df[COL_COUNTRY] = df[COL_COUNTRY].fillna("France").astype(str)
    df[COL_ASIN] = df[COL_ASIN].fillna("").astype(str)
    df[COL_RATING] = df[COL_RATING].fillna("All stars").astype(str)
    df[COL_SORT] = df[COL_SORT].fillna("Default").astype(str)

    # Reviews column can contain floats/strings after CSV import; keep as-is and validate later
    return df


def normalize_table_asins(df: pd.DataFrame) -> pd.DataFrame:
    df = ensure_table_columns(df)
    out = df.copy()
    out[COL_ASIN] = out[COL_ASIN].astype(str).map(normalize_asin)
    return out


def dedupe_table(df: pd.DataFrame, key_cols: Optional[List[str]] = None) -> pd.DataFrame:
    """
    De-duplicate rows (keeping first) by a reasonable key.

    Default key preserves the ability to run same ASIN in multiple countries or rating/sort.
    """
    df = ensure_table_columns(df)
    key_cols = key_cols or [COL_ASIN, COL_COUNTRY, COL_RATING, COL_SORT]
    # Keep order stable, keep first
    return df.drop_duplicates(subset=key_cols, keep="first").reset_index(drop=True)


def parse_asins_from_text(text: str) -> List[str]:
    """
    Extract ASINs (or /dp/ URLs) from pasted text. One per line.
    """
    out: List[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[,\t]", line) if p.strip()]
        raw = parts[0] if parts else ""
        asin = normalize_asin(raw)
        if is_valid_asin(asin):
            out.append(asin)
    return out


def smart_parse_bulk_add(
    text: str,
    default_country: str,
    default_reviews: int,
    default_rating: str,
    default_sort: str,
) -> Tuple[List[dict], List[str]]:
    """
    Accepts:
      - One ASIN/URL per line
      - Or CSV-ish lines: ASIN, Country, Reviews, Rating, Sort

    Returns: (rows, invalid_lines)
    """
    rows: List[dict] = []
    invalid: List[str] = []

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in re.split(r"[,\t]", line) if p.strip()]

        raw_asin = parts[0] if len(parts) > 0 else ""
        country = parts[1] if len(parts) > 1 else default_country
        reviews = parts[2] if len(parts) > 2 else str(default_reviews)
        rating = parts[3] if len(parts) > 3 else default_rating
        sort = parts[4] if len(parts) > 4 else default_sort

        asin = normalize_asin(raw_asin)
        if not is_valid_asin(asin):
            invalid.append(raw_line)
            continue

        if country not in COUNTRY_VALUES:
            country = default_country
        try:
            n = int(float(reviews))
        except Exception:
            n = int(default_reviews)
        n = max(1, min(n, MAX_PER_ASIN_HARD_CAP))

        if rating not in RATING_UI_OPTIONS:
            rating = default_rating
        if sort not in SORT_OVERRIDE_OPTIONS:
            sort = default_sort

        rows.append(
            {
                COL_ENABLED: True,
                COL_COUNTRY: country,
                COL_ASIN: asin,
                COL_REVIEWS: n,
                COL_RATING: rating,
                COL_SORT: sort,
                COL_SELECTED: False,
            }
        )

    return rows, invalid


def upsert_rows(
    df: pd.DataFrame,
    new_rows: List[dict],
    update_existing: bool,
    key_cols: Optional[List[str]] = None,
) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """
    Add rows, skipping duplicates (default) or updating existing rows in-place.

    Duplicate key defaults to (ASIN, Country, Rating, Sort).
    """
    df = ensure_table_columns(df)
    key_cols = key_cols or [COL_ASIN, COL_COUNTRY, COL_RATING, COL_SORT]

    if not new_rows:
        return df, {"added": 0, "updated": 0, "skipped": 0}

    # Map first occurrence of key -> row index
    key_to_idx: Dict[Tuple[str, ...], int] = {}
    for idx, r in df.iterrows():
        key = tuple(str(r.get(c, "")).strip() for c in key_cols)
        if key not in key_to_idx:
            key_to_idx[key] = int(idx)

    added = updated = skipped = 0
    to_append: List[dict] = []

    for row in new_rows:
        key = tuple(str(row.get(c, "")).strip() for c in key_cols)
        if key in key_to_idx:
            if update_existing:
                idx = key_to_idx[key]
                for col, val in row.items():
                    if col == COL_SELECTED:
                        continue
                    df.at[idx, col] = val
                updated += 1
            else:
                skipped += 1
        else:
            to_append.append(row)
            added += 1

    if to_append:
        df = pd.concat([df, pd.DataFrame(to_append)], ignore_index=True)

    df = ensure_table_columns(df)
    return df, {"added": added, "updated": updated, "skipped": skipped}


def apply_action_to_selected(df: pd.DataFrame, action: str) -> Tuple[pd.DataFrame, int]:
    """
    action ∈ {"remove", "disable", "enable", "select_all", "clear_selection"}
    """
    df = ensure_table_columns(df)
    sel = df[COL_SELECTED] == True
    n = int(sel.sum())

    if action == "remove":
        df = df.loc[~sel].copy()
        df[COL_SELECTED] = False
        return ensure_table_columns(df).reset_index(drop=True), n

    if action == "disable":
        df.loc[sel, COL_ENABLED] = False
        df[COL_SELECTED] = False
        return df, n

    if action == "enable":
        df.loc[sel, COL_ENABLED] = True
        df[COL_SELECTED] = False
        return df, n

    if action == "select_all":
        df[COL_SELECTED] = True
        return df, len(df)

    if action == "clear_selection":
        df[COL_SELECTED] = False
        return df, n

    return df, 0


def remove_by_asin_list(df: pd.DataFrame, asins: List[str]) -> Tuple[pd.DataFrame, int]:
    df = ensure_table_columns(df)
    if not asins:
        return df, 0
    s = set(asins)
    norm_col = df[COL_ASIN].astype(str).map(normalize_asin)
    keep = ~norm_col.isin(s)
    removed = int((~keep).sum())
    out = df.loc[keep].copy().reset_index(drop=True)
    out[COL_SELECTED] = False
    return out, removed


def validate_and_build_jobs(df: pd.DataFrame) -> Tuple[List[JobSpec], pd.DataFrame]:
    jobs: List[JobSpec] = []
    issues: List[dict] = []

    df = ensure_table_columns(df)

    for i, r in df.fillna("").iterrows():
        enabled = bool(r.get(COL_ENABLED, True))
        raw = str(r.get(COL_ASIN, "")).strip()
        country = str(r.get(COL_COUNTRY, "")).strip()
        rating = str(r.get(COL_RATING, "All stars")).strip() or "All stars"
        sort = str(r.get(COL_SORT, "Default")).strip() or "Default"
        n = r.get(COL_REVIEWS, 0)

        if not enabled or not raw:
            continue

        asin = normalize_asin(raw)
        try:
            n_int = int(float(n))
        except Exception:
            n_int = 0

        if country not in COUNTRY_VALUES:
            issues.append({"Row": i + 1, COL_ASIN: raw, "Problem": "Invalid country."})
            continue
        if rating not in RATING_UI_OPTIONS:
            issues.append({"Row": i + 1, COL_ASIN: raw, "Problem": "Invalid rating."})
            continue
        if sort not in SORT_OVERRIDE_OPTIONS:
            issues.append({"Row": i + 1, COL_ASIN: raw, "Problem": "Invalid sort."})
            continue
        if not is_valid_asin(asin):
            issues.append({"Row": i + 1, COL_ASIN: raw, "Problem": f"Invalid ASIN parsed: '{asin}'"})
            continue
        if not (1 <= n_int <= MAX_PER_ASIN_HARD_CAP):
            issues.append({"Row": i + 1, COL_ASIN: raw, "Problem": f"Reviews must be 1..{MAX_PER_ASIN_HARD_CAP}."})
            continue

        jobs.append(
            JobSpec(
                row_id=i + 1,
                asin=asin,
                country=country,
                max_reviews=n_int,
                rating_ui=rating,
                sort_override=sort,
            )
        )

    return jobs, pd.DataFrame(issues)


# ----------------------------
# Actor input + run worker
# ----------------------------
def build_actor_input(
    spec: JobSpec,
    global_sort_key: str,
    verified_filter: str,
    media_filter: str,
    unique_only: bool,
    get_customers_say: bool,
    rating_filters: List[str],
) -> dict:
    sort_key = resolve_sort_key(global_sort_key, spec.sort_override)
    return {
        "ASIN_or_URL": [spec.asin],
        "country": spec.country,
        "max_reviews": int(spec.max_reviews),
        "sort_reviews_by": [sort_key],  # actor expects array
        "filter_by_verified_purchase_only": [verified_filter],
        "filter_by_mediaType": [media_filter],
        "filter_by_ratings": rating_filters,
        "unique_only": bool(unique_only),
        "get_customers_say": bool(get_customers_say),
    }


def run_one_job(
    token: str,
    actor_id: str,
    spec: JobSpec,
    global_sort_key: str,
    verified_filter: str,
    media_filter: str,
    unique_only: bool,
    get_customers_say: bool,
    clean_content: bool,
    keep_raw_content: bool,
    extract_video_meta: bool,
    add_score_value: bool,
    add_effective_filter: bool,
) -> JobResult:
    """
    Thread-safe job runner. Creates its own ApifyClient per thread.
    Includes fallback if actor rejects "all_stars".
    """
    t0 = time.time()
    client = ApifyClient(token)

    run_id = None
    dataset_id = None
    usage_total_usd = None
    compute_units = None
    pricing_model = None

    rating_filters_primary = RATING_UI_TO_ACTOR.get(spec.rating_ui, ["five_star"])
    rating_filters_fallback = RATING_FALLBACK_ALL if spec.rating_ui == "All stars" else rating_filters_primary

    def _call(filters: List[str]) -> Tuple[dict, List[dict]]:
        run_input = build_actor_input(
            spec=spec,
            global_sort_key=global_sort_key,
            verified_filter=verified_filter,
            media_filter=media_filter,
            unique_only=unique_only,
            get_customers_say=get_customers_say,
            rating_filters=filters,
        )
        run_obj = client.actor(actor_id).call(run_input=run_input)
        ds_id = run_obj.get("defaultDatasetId")
        items_local = list(client.dataset(ds_id).iterate_items()) if ds_id else []
        return run_obj, items_local

    try:
        run_obj, items = _call(rating_filters_primary)
        run_id = run_obj.get("id")
        dataset_id = run_obj.get("defaultDatasetId")
    except ApifyApiError as e:
        msg = str(e)
        # If the actor doesn't accept "all_stars", fallback to selecting all five buckets.
        if spec.rating_ui == "All stars" and ("all_stars" in msg or "filter_by_ratings" in msg or "ratings" in msg):
            run_obj, items = _call(rating_filters_fallback)
            run_id = run_obj.get("id")
            dataset_id = run_obj.get("defaultDatasetId")
        else:
            raise

    # Best-effort run usage
    try:
        if run_id:
            details = client.run(run_id).get() or {}
            usage_total_usd = details.get("usageTotalUsd")
            stats = details.get("stats") or {}
            compute_units = stats.get("computeUnits")
            pricing_info = details.get("pricingInfo") or {}
            pricing_model = pricing_info.get("pricingModel")
    except Exception:
        pass

    sort_effective = resolve_sort_key(global_sort_key, spec.sort_override)

    for it in items:
        it["_meta_asin"] = spec.asin
        it["_meta_country"] = spec.country
        it["_meta_rating"] = spec.rating_ui
        it["_meta_sort"] = sort_effective
        it["_meta_requested"] = spec.max_reviews
        it["_meta_run_id"] = run_id
        it["_meta_dataset_id"] = dataset_id

        if add_score_value:
            it["ReviewScoreValue"] = parse_score_value(it.get("ReviewScore"))
        if add_effective_filter:
            it["EffectiveFilterByStar"] = extract_filter_by_star_from_pageurl(it.get("PageUrl"))

        if clean_content:
            raw = it.get("ReviewContent")
            cleaned, vmeta, had_video = clean_review_content(raw)
            if keep_raw_content:
                it["ReviewContent_raw"] = raw
            it["ReviewContent"] = cleaned
            it["HasVideoWidget"] = bool(had_video) or bool(it.get("HasVideoWidget", False))
            if extract_video_meta and vmeta:
                it.update(vmeta)

    runtime_s = time.time() - t0
    return JobResult(
        spec=spec,
        ok=True,
        collected=len(items),
        runtime_s=runtime_s,
        run_id=run_id,
        dataset_id=dataset_id,
        usage_total_usd=float(usage_total_usd) if isinstance(usage_total_usd, (int, float)) else None,
        compute_units=float(compute_units) if isinstance(compute_units, (int, float)) else None,
        pricing_model=str(pricing_model) if pricing_model else None,
        error=None,
        items=items,
    )


def compute_eta_seconds(done: List[JobResult], pending: List[JobSpec]) -> Optional[float]:
    ok_done = [r for r in done if r.ok]
    if not ok_done:
        return None
    total_runtime = sum(r.runtime_s for r in ok_done)
    total_collected = sum(max(0, r.collected) for r in ok_done)
    if total_collected <= 0:
        avg_job = total_runtime / max(len(ok_done), 1)
        return avg_job * len(pending)
    sec_per_review = total_runtime / total_collected
    remaining_requested = sum(s.max_reviews for s in pending)
    return sec_per_review * remaining_requested


def compute_cost_projection(done: List[JobResult], pending: List[JobSpec]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    with_cost = [r for r in done if r.ok and isinstance(r.usage_total_usd, (int, float)) and r.usage_total_usd is not None]
    if not with_cost:
        return None, None, None
    cost_so_far = sum(float(r.usage_total_usd) for r in with_cost)
    reviews_so_far = sum(max(0, r.collected) for r in with_cost)
    if reviews_so_far <= 0:
        return cost_so_far, None, None
    usd_per_review = cost_so_far / reviews_so_far
    remaining_requested = sum(s.max_reviews for s in pending)
    projected_total = cost_so_far + usd_per_review * remaining_requested
    return cost_so_far, projected_total, usd_per_review


# ----------------------------
# Streamlit layout
# ----------------------------
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)
st.caption("Streamlined queue UI: one paste box to add, one paste box to remove, and action buttons right under the table.")


# Session state init
if "asin_table" not in st.session_state:
    st.session_state.asin_table = ensure_table_columns(
        pd.DataFrame(
            [
                {COL_ENABLED: True, COL_COUNTRY: "France", COL_ASIN: "B0DGV9F4X3", COL_REVIEWS: 100, COL_RATING: "All stars", COL_SORT: "Default", COL_SELECTED: False},
                {COL_ENABLED: True, COL_COUNTRY: "France", COL_ASIN: "B0DHHG7P99", COL_REVIEWS: 100, COL_RATING: "All stars", COL_SORT: "Default", COL_SELECTED: False},
                {COL_ENABLED: True, COL_COUNTRY: "France", COL_ASIN: "B0915C748N", COL_REVIEWS: 100, COL_RATING: "All stars", COL_SORT: "Default", COL_SELECTED: False},
            ]
        )
    )

if "last_results" not in st.session_state:
    st.session_state.last_results = []
if "last_master_df" not in st.session_state:
    st.session_state.last_master_df = None
if "last_per_sheet" not in st.session_state:
    st.session_state.last_per_sheet = None


# ----------------------------
# Sidebar: token + run settings
# ----------------------------
with st.sidebar:
    st.subheader("Token")
    secret_token = get_apify_token_from_secrets()
    use_secrets = st.checkbox("Use Streamlit secrets token if available", value=True)

    if secret_token and use_secrets:
        st.success("APIFY_TOKEN loaded from Streamlit secrets.")
    elif use_secrets:
        st.info("No APIFY_TOKEN found in Streamlit secrets (manual entry below).")

    token_manual = st.text_input("Apify API Token (manual override)", type="password", value="")
    token = (token_manual.strip() or (secret_token.strip() if use_secrets else "")).strip()

    with st.expander("Streamlit secrets format", expanded=False):
        st.markdown("Create `.streamlit/secrets.toml` with either:")
        st.code('APIFY_TOKEN = "apify_api_your_token_here"', language="toml")
        st.markdown("Or namespaced:")
        st.code('[apify]\ntoken = "apify_api_your_token_here"', language="toml")

    st.divider()
    st.subheader("Actor / Run settings")
    actor_id = st.text_input("Apify Actor ID", value=DEFAULT_ACTOR_ID)

    global_sort_label = st.selectbox("Default sort order", options=list(SORT_LABEL_TO_KEY.keys()), index=0)
    global_sort_key = SORT_LABEL_TO_KEY[global_sort_label]

    max_workers = st.slider("Max concurrent runs", min_value=1, max_value=8, value=2, step=1)
    throttle_s = st.slider("Throttle between finished jobs (sec)", min_value=0.0, max_value=5.0, value=0.5, step=0.5)

    with st.expander("Advanced actor options", expanded=False):
        verified_filter = st.selectbox("Verified purchase filter", options=["all_reviews", "avp_only_reviews"], index=0)
        media_filter = st.selectbox("Media filter", options=["all_contents", "media_reviews_only"], index=0)
        unique_only = st.checkbox("Unique only (dedupe)", value=False)
        get_customers_say = st.checkbox("Get Customers Say", value=True)

    with st.expander("Output cleaning", expanded=True):
        clean_content = st.checkbox("Clean ReviewContent (remove video widget blob)", value=True)
        keep_raw_content = st.checkbox("Keep ReviewContent_raw", value=False)
        extract_video_meta = st.checkbox("Extract video metadata columns", value=True)
        add_score_value = st.checkbox("Add numeric ReviewScoreValue", value=True)
        add_effective_filter = st.checkbox("Add EffectiveFilterByStar (debug)", value=True)


tabs = st.tabs(["Queue & Run", "Results", "Help"])


# ----------------------------
# Queue & Run
# ----------------------------
with tabs[0]:
    st.subheader("Queue")

    # Keep table normalized (but do NOT force normalize on every keystroke in editor)
    st.session_state.asin_table = ensure_table_columns(st.session_state.asin_table)

    # Quick add / remove area
    qa, qr = st.columns([1.25, 1.0], vertical_alignment="top")

    with qa:
        st.markdown("### Quick add (paste once)")
        st.caption("Paste **one per line**. Optional CSV format per line: `ASIN, Country, Reviews, Rating, Sort`.")
        default_country = st.selectbox("Defaults: Country", options=COUNTRY_VALUES, index=COUNTRY_VALUES.index("France") if "France" in COUNTRY_VALUES else 0, key="qa_country")
        default_reviews = st.number_input("Defaults: Reviews to pull", min_value=1, max_value=MAX_PER_ASIN_HARD_CAP, value=100, step=25, key="qa_reviews")
        default_rating = st.selectbox("Defaults: Rating", options=RATING_UI_OPTIONS, index=0, key="qa_rating")
        default_sort = st.selectbox("Defaults: Sort", options=SORT_OVERRIDE_OPTIONS, index=0, key="qa_sort")
        update_existing = st.checkbox("If duplicate exists: update settings instead of skipping", value=False, key="qa_upd")

        add_text = st.text_area("Paste ASINs/URLs", height=140, key="qa_text")
        add_btn = st.button("Add to queue", use_container_width=True)

        if add_btn:
            rows, invalid = smart_parse_bulk_add(
                add_text,
                default_country=default_country,
                default_reviews=int(default_reviews),
                default_rating=default_rating,
                default_sort=default_sort,
            )
            df0 = ensure_table_columns(st.session_state.asin_table)
            df1, stats = upsert_rows(df0, rows, update_existing=bool(update_existing))
            df1 = dedupe_table(df1)
            st.session_state.asin_table = df1

            msg = f"Added **{stats['added']}**"
            if update_existing:
                msg += f" · Updated **{stats['updated']}**"
            else:
                msg += f" · Skipped duplicates **{stats['skipped']}**"
            st.success(msg)

            if invalid:
                with st.expander(f"{len(invalid)} invalid line(s) skipped", expanded=False):
                    st.code("\n".join(invalid))

    with qr:
        st.markdown("### Quick remove (paste once)")
        st.caption("Paste ASINs/URLs to remove (one per line).")
        remove_text = st.text_area("ASINs/URLs to remove", height=140, key="qr_text")
        remove_btn = st.button("Remove from queue", use_container_width=True)

        if remove_btn:
            asins = parse_asins_from_text(remove_text)
            df0 = ensure_table_columns(st.session_state.asin_table)
            df1, removed = remove_by_asin_list(df0, asins)
            st.session_state.asin_table = df1
            st.success(f"Removed **{removed}** row(s).")

        st.divider()
        st.markdown("### Import / export")
        cfg_bytes = export_config_csv_bytes(st.session_state.asin_table)
        st.download_button(
            "Download config CSV",
            data=cfg_bytes,
            file_name="asin_config.csv",
            mime="text/csv",
            use_container_width=True,
        )

        up = st.file_uploader("Upload config CSV", type=["csv"])
        if up is not None:
            try:
                imported = pd.read_csv(up)
                imported = ensure_table_columns(imported)
                # Don't forcibly normalize URLs into ASINs; keep as user input (validation will parse).
                st.session_state.asin_table = imported
                st.success("Config loaded.")
            except Exception as e:
                st.error(f"Failed to load CSV: {e}")

    st.divider()

    st.markdown("### Edit queue")
    st.caption("Tip: check **Selected** on rows, then use the action buttons right under the table (no scrolling).")

    df_for_editor = ensure_table_columns(st.session_state.asin_table)

    edited = st.data_editor(
        df_for_editor,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_order=[COL_ENABLED, COL_ASIN, COL_COUNTRY, COL_REVIEWS, COL_RATING, COL_SORT, COL_SELECTED],
        column_config={
            COL_ENABLED: st.column_config.CheckboxColumn("Enabled", width="small"),
            COL_COUNTRY: st.column_config.SelectboxColumn("Country", options=COUNTRY_VALUES, width="medium"),
            COL_ASIN: st.column_config.TextColumn("ASIN or URL", width="large"),
            COL_REVIEWS: st.column_config.NumberColumn("Reviews to pull", min_value=1, max_value=MAX_PER_ASIN_HARD_CAP, step=25, width="small"),
            COL_RATING: st.column_config.SelectboxColumn("Rating", options=RATING_UI_OPTIONS, width="small"),
            COL_SORT: st.column_config.SelectboxColumn("Sort", options=SORT_OVERRIDE_OPTIONS, width="small"),
            COL_SELECTED: st.column_config.CheckboxColumn("Selected", width="small"),
        },
        key="queue_editor",
    )
    st.session_state.asin_table = ensure_table_columns(edited)

    # Action bar directly under table
    a1, a2, a3, a4, a5, a6 = st.columns([1, 1, 1, 1, 1, 1], vertical_alignment="center")
    with a1:
        if st.button("Remove selected", use_container_width=True):
            df0 = ensure_table_columns(st.session_state.asin_table)
            df1, n = apply_action_to_selected(df0, "remove")
            st.session_state.asin_table = df1
            st.success(f"Removed {n} row(s).")
    with a2:
        if st.button("Disable selected", use_container_width=True):
            df0 = ensure_table_columns(st.session_state.asin_table)
            df1, n = apply_action_to_selected(df0, "disable")
            st.session_state.asin_table = df1
            st.success(f"Disabled {n} row(s).")
    with a3:
        if st.button("Enable selected", use_container_width=True):
            df0 = ensure_table_columns(st.session_state.asin_table)
            df1, n = apply_action_to_selected(df0, "enable")
            st.session_state.asin_table = df1
            st.success(f"Enabled {n} row(s).")
    with a4:
        if st.button("Select all", use_container_width=True):
            df0 = ensure_table_columns(st.session_state.asin_table)
            df1, _ = apply_action_to_selected(df0, "select_all")
            st.session_state.asin_table = df1
    with a5:
        if st.button("Clear selection", use_container_width=True):
            df0 = ensure_table_columns(st.session_state.asin_table)
            df1, _ = apply_action_to_selected(df0, "clear_selection")
            st.session_state.asin_table = df1
    with a6:
        if st.button("Dedupe", use_container_width=True):
            df0 = ensure_table_columns(st.session_state.asin_table)
            before = len(df0)
            df1 = dedupe_table(df0)
            st.session_state.asin_table = df1
            st.success(f"Removed {before - len(df1)} duplicate row(s).")

    b1, b2, b3 = st.columns([1, 1, 2], vertical_alignment="center")
    with b1:
        if st.button("Normalize ASINs", use_container_width=True):
            df0 = ensure_table_columns(st.session_state.asin_table)
            st.session_state.asin_table = normalize_table_asins(df0)
            st.success("Normalized ASIN/URL column (URLs → ASIN when possible).")
    with b2:
        if st.button("Clear queue", use_container_width=True):
            st.session_state.asin_table = ensure_table_columns(pd.DataFrame(columns=[COL_ENABLED, COL_COUNTRY, COL_ASIN, COL_REVIEWS, COL_RATING, COL_SORT, COL_SELECTED]))
            st.success("Cleared queue.")
    with b3:
        df0 = ensure_table_columns(st.session_state.asin_table)
        jobs, issues_df = validate_and_build_jobs(df0)
        st.caption(f"Enabled rows ready: **{len(jobs)}** · Table rows: **{len(df0)}**")

    if not issues_df.empty:
        st.warning("Fix these rows before running:")
        st.dataframe(issues_df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Run")

    can_run = bool(token) and bool(actor_id.strip()) and len(jobs) > 0 and issues_df.empty

    r1, r2, r3 = st.columns([1.2, 1.2, 2.6], vertical_alignment="center")
    with r1:
        run_clicked = st.button("Start scrape", type="primary", use_container_width=True, disabled=not can_run)
    with r2:
        clear_clicked = st.button("Clear results", use_container_width=True)
    with r3:
        st.caption(f"Rows: **{len(jobs)}** · Concurrency: **{max_workers}** · Default sort: **{global_sort_label}**")

    if not token:
        st.info("Add your Apify token (sidebar). You can store it in Streamlit secrets.")
    if not issues_df.empty:
        st.warning("Fix queue issues first (above).")

    if clear_clicked:
        st.session_state.last_results = []
        st.session_state.last_master_df = None
        st.session_state.last_per_sheet = None
        st.success("Cleared.")

    if run_clicked:
        st.session_state.last_results = []
        st.session_state.last_master_df = None
        st.session_state.last_per_sheet = None

        total = len(jobs)
        status_ph = st.empty()
        metrics_ph = st.empty()
        progress = st.progress(0)
        table_ph = st.empty()
        log_box = st.container()

        start_all = time.time()
        results: List[JobResult] = []
        pending = jobs.copy()

        def render_metrics():
            done = len(results)
            ok_count = sum(1 for r in results if r.ok)
            fail_count = done - ok_count
            collected_total = sum(r.collected for r in results if r.ok)

            eta_s = compute_eta_seconds(done=results, pending=pending)
            cost_so_far, projected_total, usd_per_review = compute_cost_projection(done=results, pending=pending)

            with metrics_ph.container():
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("Done", f"{done}/{total}")
                m2.metric("Succeeded", f"{ok_count}")
                m3.metric("Failed", f"{fail_count}")
                m4.metric("Rows collected", f"{collected_total}")
                m5.metric("ETA", format_seconds(eta_s) if eta_s is not None else "—")

                c1, c2, c3 = st.columns(3)
                c1.metric("Cost so far (USD)", f"{cost_so_far:.4f}" if cost_so_far is not None else "—")
                c2.metric("Projected total (USD)", f"{projected_total:.4f}" if projected_total is not None else "—")
                c3.metric("$/review (observed)", f"{usd_per_review:.6f}" if usd_per_review is not None else "—")

        render_metrics()
        status_ph.markdown(f"**[{now_ts()}]** Starting {total} runs… (parallelism={max_workers})")

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(
                    run_one_job,
                    token,
                    actor_id,
                    spec,
                    global_sort_key,
                    verified_filter,
                    media_filter,
                    unique_only,
                    get_customers_say,
                    clean_content,
                    keep_raw_content,
                    extract_video_meta,
                    add_score_value,
                    add_effective_filter,
                ): spec
                for spec in jobs
            }

            done_so_far = 0
            for fut in as_completed(futures):
                spec = futures[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    res = JobResult(
                        spec=spec,
                        ok=False,
                        collected=0,
                        runtime_s=0.0,
                        run_id=None,
                        dataset_id=None,
                        usage_total_usd=None,
                        compute_units=None,
                        pricing_model=None,
                        error=str(e),
                        items=[],
                    )

                results.append(res)
                pending = [p for p in pending if p != spec]
                done_so_far += 1
                progress.progress(int(done_so_far / total * 100))

                if res.ok:
                    with log_box:
                        st.success(
                            f"[{now_ts()}] Row {spec.row_id} OK · {spec.asin} · {spec.country} · "
                            f"{spec.rating_ui} · Collected {res.collected} · {format_seconds(res.runtime_s)}"
                        )
                else:
                    with log_box:
                        st.error(f"[{now_ts()}] Row {spec.row_id} ERROR · {spec.asin} · {res.error}")

                rows = []
                for r in sorted(results, key=lambda x: x.spec.row_id):
                    rows.append(
                        {
                            "Row": r.spec.row_id,
                            "ASIN": r.spec.asin,
                            "Country": r.spec.country,
                            "Rating": r.spec.rating_ui,
                            "Sort": r.spec.sort_override,
                            "Requested": r.spec.max_reviews,
                            "Collected": r.collected,
                            "Runtime": format_seconds(r.runtime_s),
                            "Cost USD": r.usage_total_usd,
                            "CUs": r.compute_units,
                            "Status": "OK" if r.ok else "ERROR",
                            "Error": r.error or "",
                        }
                    )
                table_ph.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                render_metrics()

                if throttle_s > 0:
                    time.sleep(throttle_s)

        total_runtime = time.time() - start_all
        status_ph.markdown(f"**[{now_ts()}]** Finished in {format_seconds(total_runtime)}. Building exports…")

        per_sheet: Dict[str, pd.DataFrame] = {}
        all_items: List[dict] = []

        for r in results:
            sheet_key = f"{r.spec.asin}-{r.spec.country[:2].upper()}-{r.spec.rating_ui.split()[0]}-{r.spec.sort_override[0]}"
            if r.ok and r.items:
                df_sheet = pd.json_normalize(r.items)
                per_sheet[sheet_key] = df_sheet
                all_items.extend(r.items)
            else:
                per_sheet[sheet_key] = pd.DataFrame(
                    [{
                        "_meta_row": r.spec.row_id,
                        "_meta_asin": r.spec.asin,
                        "_meta_country": r.spec.country,
                        "_meta_rating": r.spec.rating_ui,
                        "_error": r.error or "",
                    }]
                )

        master_df = pd.json_normalize(all_items) if all_items else pd.DataFrame()

        st.session_state.last_results = results
        st.session_state.last_per_sheet = per_sheet
        st.session_state.last_master_df = master_df

        status_ph.markdown(f"**[{now_ts()}]** Done ✅  (Switch to the Results tab to download.)")


# ----------------------------
# Results tab
# ----------------------------
with tabs[1]:
    st.subheader("Results")

    results: List[JobResult] = st.session_state.last_results or []
    master_df: Optional[pd.DataFrame] = st.session_state.last_master_df
    per_sheet: Optional[Dict[str, pd.DataFrame]] = st.session_state.last_per_sheet

    if not results:
        st.info("No run results yet.")
    else:
        ok_count = sum(1 for r in results if r.ok)
        fail_count = len(results) - ok_count
        collected_total = sum(r.collected for r in results if r.ok)

        with_cost = [r for r in results if r.ok and r.usage_total_usd is not None]
        cost_total = sum(float(r.usage_total_usd) for r in with_cost) if with_cost else None

        a, b, c, d = st.columns(4)
        a.metric("Succeeded", ok_count)
        b.metric("Failed", fail_count)
        c.metric("Collected rows", collected_total)
        d.metric("Cost total (USD)", f"{cost_total:.4f}" if cost_total is not None else "—")

        if master_df is not None and not master_df.empty:
            with st.expander("Preview MASTER (first 100 rows)", expanded=False):
                st.dataframe(master_df.head(100), use_container_width=True)

            if "ReviewScoreValue" in master_df.columns:
                with st.expander("Star distribution (MASTER)", expanded=False):
                    dist = master_df["ReviewScoreValue"].value_counts(dropna=True).sort_index()
                    st.dataframe(
                        dist.rename("count").reset_index().rename(columns={"index": "ReviewScoreValue"}),
                        use_container_width=True,
                        hide_index=True,
                    )

        ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        excel_name = f"amazon_reviews_{ts_tag}.xlsx"
        csv_name = f"amazon_reviews_master_{ts_tag}.csv"

        d1, d2 = st.columns(2)
        with d1:
            if per_sheet is not None and master_df is not None:
                excel_bytes = export_excel_bytes(per_sheet, master_df)
                st.download_button(
                    "Download Excel (tabs per row + MASTER)",
                    data=excel_bytes,
                    file_name=excel_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
        with d2:
            if master_df is not None and not master_df.empty:
                csv_bytes = export_csv_bytes(master_df)
                st.download_button(
                    "Download MASTER CSV",
                    data=csv_bytes,
                    file_name=csv_name,
                    mime="text/csv",
                    use_container_width=True,
                )


# ----------------------------
# Help tab
# ----------------------------
with tabs[2]:
    st.subheader("Help")

    st.markdown("### Queue UX changes (what’s different vs your previous UI)")
    st.markdown(
        "- **One paste box to add**, instead of separate “single add” + multiple bulk modes.\n"
        "- **One paste box to remove** (remove by ASIN list), plus **Selected** checkboxes + action buttons **right under the table**.\n"
        "- “Delete” column was renamed to **Selected** (legacy CSVs that have `Delete` still import correctly).\n"
        "- Optional buttons: **Dedupe** and **Normalize ASINs**."
    )

    st.markdown("### Streamlit secrets")
    st.markdown("Create `.streamlit/secrets.toml` locally, or paste the same TOML in Streamlit Cloud → App → Settings → Secrets.")
    st.code('APIFY_TOKEN = "apify_api_your_token_here"', language="toml")
    st.markdown("Or namespaced:")
    st.code('[apify]\ntoken = "apify_api_your_token_here"', language="toml")

    st.markdown("### Why ReviewContent sometimes contains a huge JSON blob")
    st.markdown(
        "Some reviews include inline video. Amazon embeds a VSE player config JSON and UI text in the same container as "
        "the review body. Enable **Clean ReviewContent** to strip it and optionally extract `VideoUrl` and poster image URL."
    )

    st.markdown("### “All stars” sometimes looks like only 5★")
    st.markdown(
        "This app tries `filter_by_ratings=['all_stars']` for **All stars**. If your actor rejects `all_stars`, it falls "
        "back to requesting all five star buckets. If you still see only 5★, turn on the debug column "
        "`EffectiveFilterByStar` and check the generated filter."
    )

