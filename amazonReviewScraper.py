# amazonReviewScraper.py
# Streamlit: Amazon Reviews Scraper (Apify) — improved UX + Streamlit secrets support
#
# ✅ Token UX:
#   - Auto-uses st.secrets if present (APIFY_TOKEN or [apify].token)
#   - Manual override optional
#   - Shows exact secrets.toml format
#
# ✅ ASIN UX:
#   - “Manage ASINs” tab with:
#       - Add single row form
#       - Bulk add (one ASIN per line)
#       - Bulk add CSV-like lines (ASIN, Country, Reviews, Rating, Sort)
#       - Import/export config CSV
#       - Editable grid with Enabled + Delete checkboxes
#       - Buttons: Remove checked / Disable checked / Clear table
#
# ✅ Run UX:
#   - Parallel runs (max concurrency)
#   - Live progress + ETA (based on observed throughput)
#   - Cost/CU (best-effort from run details: usageTotalUsd, stats.computeUnits)
#
# ✅ Output:
#   - Excel: one tab per row + MASTER
#   - CSV: MASTER
#   - Cleans ReviewContent for video reviews (removes huge VSE player JSON blob)
#     + optional columns: VideoUrl, VideoPosterImageUrl, VideoCaptionsUrl
#
# Install:
#   pip install streamlit apify-client pandas openpyxl
#
# Run:
#   streamlit run amazonReviewScraper.py


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
# You previously ran successfully with "France" and "United States".
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
    cols = ["Enabled", "Country", "ASIN or URL", "Reviews to pull", "Rating", "Sort"]
    out = df.copy()
    for c in cols:
        if c not in out.columns:
            out[c] = ""
    out = out[cols]
    return out.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


# ----------------------------
# Table helpers
# ----------------------------
def ensure_table_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "Enabled" not in df.columns:
        df["Enabled"] = True
    if "Country" not in df.columns:
        df["Country"] = "France"
    if "ASIN or URL" not in df.columns:
        df["ASIN or URL"] = ""
    if "Reviews to pull" not in df.columns:
        df["Reviews to pull"] = 100
    if "Rating" not in df.columns:
        df["Rating"] = "All stars"
    if "Sort" not in df.columns:
        df["Sort"] = "Default"
    if "Delete" not in df.columns:
        df["Delete"] = False
    return df


def validate_and_build_jobs(df: pd.DataFrame) -> Tuple[List[JobSpec], pd.DataFrame]:
    jobs: List[JobSpec] = []
    issues: List[dict] = []

    df = ensure_table_columns(df)

    for i, r in df.fillna("").iterrows():
        enabled = bool(r.get("Enabled", True))
        raw = str(r.get("ASIN or URL", "")).strip()
        country = str(r.get("Country", "")).strip()
        rating = str(r.get("Rating", "All stars")).strip() or "All stars"
        sort = str(r.get("Sort", "Default")).strip() or "Default"
        n = r.get("Reviews to pull", 0)

        if not enabled or not raw:
            continue

        asin = normalize_asin(raw)
        try:
            n_int = int(float(n))
        except Exception:
            n_int = 0

        if country not in COUNTRY_VALUES:
            issues.append({"Row": i + 1, "ASIN or URL": raw, "Problem": "Invalid country."})
            continue
        if rating not in RATING_UI_OPTIONS:
            issues.append({"Row": i + 1, "ASIN or URL": raw, "Problem": "Invalid rating."})
            continue
        if sort not in SORT_OVERRIDE_OPTIONS:
            issues.append({"Row": i + 1, "ASIN or URL": raw, "Problem": "Invalid sort."})
            continue
        if not is_valid_asin(asin):
            issues.append({"Row": i + 1, "ASIN or URL": raw, "Problem": f"Invalid ASIN parsed: '{asin}'"})
            continue
        if not (1 <= n_int <= MAX_PER_ASIN_HARD_CAP):
            issues.append({"Row": i + 1, "ASIN or URL": raw, "Problem": f"Reviews must be 1..{MAX_PER_ASIN_HARD_CAP}."})
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


def parse_bulk_asin_lines(text: str) -> List[str]:
    out: List[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def parse_bulk_csv_lines(text: str) -> List[dict]:
    """
    Lines like:
      ASIN
      ASIN, Country, Reviews, Rating, Sort
    """
    rows: List[dict] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in re.split(r"[,\t]", line) if p.strip()]
        if not parts:
            continue

        raw_asin = parts[0]
        country = parts[1] if len(parts) > 1 else "France"
        reviews = parts[2] if len(parts) > 2 else "100"
        rating = parts[3] if len(parts) > 3 else "All stars"
        sort = parts[4] if len(parts) > 4 else "Default"

        asin = normalize_asin(raw_asin)
        try:
            n = int(float(reviews))
        except Exception:
            n = 100

        if country not in COUNTRY_VALUES:
            country = "France"
        if rating not in RATING_UI_OPTIONS:
            rating = "All stars"
        if sort not in SORT_OVERRIDE_OPTIONS:
            sort = "Default"

        rows.append(
            {
                "Enabled": True,
                "Country": country,
                "ASIN or URL": asin,
                "Reviews to pull": max(1, min(n, MAX_PER_ASIN_HARD_CAP)),
                "Rating": rating,
                "Sort": sort,
                "Delete": False,
            }
        )
    return rows


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

# Session state init
if "asin_table" not in st.session_state:
    st.session_state.asin_table = ensure_table_columns(
        pd.DataFrame(
            [
                {"Enabled": True, "Country": "France", "ASIN or URL": "B0DGV9F4X3", "Reviews to pull": 100, "Rating": "All stars", "Sort": "Default", "Delete": False},
                {"Enabled": True, "Country": "France", "ASIN or URL": "B0DHHG7P99", "Reviews to pull": 100, "Rating": "All stars", "Sort": "Default", "Delete": False},
                {"Enabled": True, "Country": "France", "ASIN or URL": "B0915C748N", "Reviews to pull": 100, "Rating": "All stars", "Sort": "Default", "Delete": False},
                {"Enabled": True, "Country": "France", "ASIN or URL": "B0DPP6C5YP", "Reviews to pull": 100, "Rating": "All stars", "Sort": "Default", "Delete": False},
                {"Enabled": True, "Country": "France", "ASIN or URL": "B0F1DKQXJV", "Reviews to pull": 100, "Rating": "All stars", "Sort": "Default", "Delete": False},
            ]
        )
    )

if "last_results" not in st.session_state:
    st.session_state.last_results = []
if "last_master_df" not in st.session_state:
    st.session_state.last_master_df = None
if "last_per_sheet" not in st.session_state:
    st.session_state.last_per_sheet = None


# Sidebar
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


tabs = st.tabs(["Manage ASINs", "Run", "Results", "Help"])


# ----------------------------
# Manage ASINs tab
# ----------------------------
with tabs[0]:
    st.subheader("Manage ASINs")

    c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.2, 2.4])
    with c1:
        if st.button("Remove checked rows", use_container_width=True):
            df = ensure_table_columns(st.session_state.asin_table)
            df = df[df["Delete"] != True].copy()
            df["Delete"] = False
            st.session_state.asin_table = df
            st.success("Removed checked rows.")
    with c2:
        if st.button("Disable checked rows", use_container_width=True):
            df = ensure_table_columns(st.session_state.asin_table)
            df.loc[df["Delete"] == True, "Enabled"] = False
            df["Delete"] = False
            st.session_state.asin_table = df
            st.success("Disabled checked rows.")
    with c3:
        if st.button("Clear table", use_container_width=True):
            st.session_state.asin_table = ensure_table_columns(
                pd.DataFrame(columns=["Enabled", "Country", "ASIN or URL", "Reviews to pull", "Rating", "Sort", "Delete"])
            )
            st.success("Cleared.")
    with c4:
        cfg_bytes = export_config_csv_bytes(ensure_table_columns(st.session_state.asin_table))
        st.download_button(
            "Download current config CSV",
            data=cfg_bytes,
            file_name="asin_config.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.divider()
    st.markdown("### Add a single ASIN")
    with st.form("add_single_asin", clear_on_submit=True):
        a, b, c, d, e = st.columns([1.4, 2.0, 1.2, 1.2, 1.2])
        add_country = a.selectbox("Country", options=COUNTRY_VALUES, index=COUNTRY_VALUES.index("France") if "France" in COUNTRY_VALUES else 0)
        add_asin = b.text_input("ASIN or Amazon URL", value="")
        add_reviews = c.number_input("Reviews to pull", min_value=1, max_value=MAX_PER_ASIN_HARD_CAP, value=100, step=25)
        add_rating = d.selectbox("Rating", options=RATING_UI_OPTIONS, index=0)
        add_sort = e.selectbox("Sort", options=SORT_OVERRIDE_OPTIONS, index=0)
        submitted = st.form_submit_button("Add row")

        if submitted:
            asin = normalize_asin(add_asin)
            if not is_valid_asin(asin):
                st.error(f"Could not parse a valid ASIN from: {add_asin}")
            else:
                df = ensure_table_columns(st.session_state.asin_table)
                new_row = {
                    "Enabled": True,
                    "Country": add_country,
                    "ASIN or URL": asin,
                    "Reviews to pull": int(add_reviews),
                    "Rating": add_rating,
                    "Sort": add_sort,
                    "Delete": False,
                }
                st.session_state.asin_table = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
                st.success(f"Added {asin}.")

    st.divider()
    st.markdown("### Bulk add")
    left, right = st.columns(2)

    with left:
        st.write("Paste ASINs/URLs (one per line). They will use the defaults below.")
        bulk_country = st.selectbox("Default Country (bulk)", options=COUNTRY_VALUES, index=COUNTRY_VALUES.index("France") if "France" in COUNTRY_VALUES else 0, key="bulk_country")
        bulk_reviews = st.number_input("Default Reviews (bulk)", min_value=1, max_value=MAX_PER_ASIN_HARD_CAP, value=100, step=25, key="bulk_reviews")
        bulk_rating = st.selectbox("Default Rating (bulk)", options=RATING_UI_OPTIONS, index=0, key="bulk_rating")
        bulk_sort = st.selectbox("Default Sort (bulk)", options=SORT_OVERRIDE_OPTIONS, index=0, key="bulk_sort")
        bulk_text = st.text_area("ASINs/URLs", height=140, key="bulk_text")

        if st.button("Add ASIN list", use_container_width=True):
            lines = parse_bulk_asin_lines(bulk_text)
            rows = []
            for raw in lines:
                asin = normalize_asin(raw)
                if is_valid_asin(asin):
                    rows.append(
                        {
                            "Enabled": True,
                            "Country": bulk_country,
                            "ASIN or URL": asin,
                            "Reviews to pull": int(bulk_reviews),
                            "Rating": bulk_rating,
                            "Sort": bulk_sort,
                            "Delete": False,
                        }
                    )
            if rows:
                df = ensure_table_columns(st.session_state.asin_table)
                st.session_state.asin_table = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
                st.success(f"Added {len(rows)} rows.")
            else:
                st.warning("No valid ASINs found.")

    with right:
        st.write("CSV-like lines: `ASIN, Country, Reviews, Rating, Sort` (Country/Reviews/Rating/Sort optional)")
        st.code("B0XXXXXXX1\nB0XXXXXXX2, France, 200, All stars, Default\nB0XXXXXXX3, Germany, 100, 5-star, Helpful")
        bulk_csv = st.text_area("CSV-like bulk input", height=140, key="bulk_csv")
        if st.button("Add CSV-like lines", use_container_width=True):
            rows = parse_bulk_csv_lines(bulk_csv)
            if rows:
                df = ensure_table_columns(st.session_state.asin_table)
                st.session_state.asin_table = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
                st.success(f"Added {len(rows)} rows.")
            else:
                st.warning("No valid rows found.")

    st.divider()
    st.markdown("### Import config CSV")
    up = st.file_uploader("Upload config CSV exported from this app", type=["csv"])
    if up is not None:
        try:
            imported = pd.read_csv(up)
            imported = ensure_table_columns(imported)
            imported["ASIN or URL"] = imported["ASIN or URL"].astype(str).map(normalize_asin)
            st.session_state.asin_table = imported
            st.success("Config loaded.")
        except Exception as e:
            st.error(f"Failed to load CSV: {e}")

    st.divider()
    st.markdown("### Edit list")
    st.write("Tip: Use **Enabled** to skip a row. Use **Delete** checkbox + **Remove checked rows** to remove quickly.")

    st.session_state.asin_table = ensure_table_columns(st.session_state.asin_table)

    edited = st.data_editor(
        st.session_state.asin_table,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "Enabled": st.column_config.CheckboxColumn("Enabled", width="small"),
            "Country": st.column_config.SelectboxColumn("Country", options=COUNTRY_VALUES, width="medium"),
            "ASIN or URL": st.column_config.TextColumn("ASIN or URL", width="large"),
            "Reviews to pull": st.column_config.NumberColumn("Reviews to pull", min_value=1, max_value=MAX_PER_ASIN_HARD_CAP, step=25, width="small"),
            "Rating": st.column_config.SelectboxColumn("Rating", options=RATING_UI_OPTIONS, width="small"),
            "Sort": st.column_config.SelectboxColumn("Sort", options=SORT_OVERRIDE_OPTIONS, width="small"),
            "Delete": st.column_config.CheckboxColumn("Delete", width="small"),
        },
    )
    st.session_state.asin_table = ensure_table_columns(edited)

    jobs, issues_df = validate_and_build_jobs(st.session_state.asin_table)
    if not issues_df.empty:
        st.warning("Fix these rows before running:")
        st.dataframe(issues_df, use_container_width=True, hide_index=True)
    st.caption(f"Enabled rows ready: {len(jobs)}")


# ----------------------------
# Run tab
# ----------------------------
with tabs[1]:
    st.subheader("Run")

    df = ensure_table_columns(st.session_state.asin_table)
    jobs, issues_df = validate_and_build_jobs(df)

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
        st.warning("Fix table issues first (Manage ASINs tab).")

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

        status_ph.markdown(f"**[{now_ts()}]** Done ✅  (Go to Results tab to download.)")


# ----------------------------
# Results tab
# ----------------------------
with tabs[2]:
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
# Help tab (no triple-quoted strings)
# ----------------------------
with tabs[3]:
    st.subheader("Help")

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
