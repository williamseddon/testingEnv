# app.py
# Streamlit: Amazon reviews scraper via Apify Actor
#
# Improvements included:
# - Per-row: Country + ASIN/URL + Reviews + Rating (All/1/2/3/4/5) + Sort override
# - FIX: "All" ratings uses filter_by_ratings=["all_stars"] (prevents 5-star-only skew)
# - Parallel runs (max concurrency), live status, ETA
# - Cost / CU reporting per run (usageTotalUsd, stats.computeUnits)
# - Clean ReviewContent when video reviews inject VSE player JSON + UI text
#   - Extracts: VideoUrl, VideoPosterImageUrl, VideoCaptionsUrl, HasVideoWidget
#   - Optional keep ReviewContent_raw
#
# Install:
#   pip install streamlit apify-client pandas openpyxl
#
# Run:
#   streamlit run app.py

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


# ----------------------------
# Config
# ----------------------------
APP_TITLE = "Amazon Reviews Scraper (Apify) — Clean Text + Correct All-Stars"
DEFAULT_ACTOR_ID = "8vhDnIX6dStLlGVr7"
MAX_PER_ASIN_HARD_CAP = 20000

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$", re.IGNORECASE)

SORT_LABEL_TO_KEY = {"Recent": "recent", "Helpful": "helpful"}
SORT_OVERRIDE_OPTIONS = ["Default", "Recent", "Helpful"]

# Keep this list aligned with what YOUR actor accepts.
# "France" worked for you; add more as you need.
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

# Rating filter per-row:
# IMPORTANT FIX: "All" must map to ["all_stars"] (true Amazon "All stars" filter)
RATING_OPTIONS = ["All", "1", "2", "3", "4", "5"]
RATING_MAP = {
    "All": ["all_stars"],
    "1": ["one_star"],
    "2": ["two_star"],
    "3": ["three_star"],
    "4": ["four_star"],
    "5": ["five_star"],
}

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
    rating_choice: str          # "All" | "1"..."5"
    sort_override: str          # "Default" | "Recent" | "Helpful"


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
# Helpers
# ----------------------------
def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_seconds(sec: float) -> str:
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


def parse_review_score_value(score: Any) -> Optional[float]:
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


# ---- Video-widget cleaning (fix for ReviewContent junk) ----
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
    """
    Pull useful fields from the VSE player JSON.
    """
    if not json_str:
        return {}

    try:
        d = json.loads(json_str)
    except Exception:
        return {}

    meta = {}
    for k in ["videoUrl", "imageUrl", "initialClosedCaptions", "mimeType"]:
        if k in d:
            meta[k] = d.get(k)

    # Normalize field names for export
    out = {}
    if meta.get("videoUrl"):
        out["VideoUrl"] = meta["videoUrl"]
    if meta.get("imageUrl"):
        out["VideoPosterImageUrl"] = meta["imageUrl"]
    if meta.get("initialClosedCaptions"):
        out["VideoCaptionsUrl"] = meta["initialClosedCaptions"]
    if meta:
        out["HasVideoWidget"] = True

    return out


def clean_review_content(raw: Any) -> Tuple[str, Dict[str, Any], bool]:
    """
    Returns (clean_text, video_meta, had_video_widget)
    """
    raw_s = "" if raw is None else str(raw)

    # Only attempt special handling for VSE/video-widget payloads
    if raw_s.startswith("{") and '"videoUrl"' in raw_s and VIDEO_MARKER in raw_s:
        json_str, rem = split_leading_json(raw_s)
        video_meta = parse_video_meta(json_str)

        # Keep only text after the video player accessibility string
        rem = rem.split(VIDEO_MARKER)[-1].strip()

        # Collapse whitespace
        rem = re.sub(r"\s+", " ", rem).strip()

        return rem, video_meta, True

    return raw_s.strip(), {}, False


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


def validate_and_build_jobs(df: pd.DataFrame) -> Tuple[List[JobSpec], pd.DataFrame]:
    jobs: List[JobSpec] = []
    issues: List[dict] = []

    for i, r in df.fillna("").iterrows():
        raw = str(r.get("ASIN or URL", "")).strip()
        country = str(r.get("Country", "")).strip()
        rating = str(r.get("Rating", "All")).strip() or "All"
        sort = str(r.get("Sort", "Default")).strip() or "Default"
        n = r.get("Reviews to pull", 0)

        if not raw:
            continue

        asin = normalize_asin(raw)
        try:
            n_int = int(float(n))
        except Exception:
            n_int = 0

        if country not in COUNTRY_VALUES:
            issues.append({"Row": i + 1, "ASIN or URL": raw, "Problem": "Invalid country."})
            continue
        if rating not in RATING_OPTIONS:
            issues.append({"Row": i + 1, "ASIN or URL": raw, "Problem": "Invalid rating selection."})
            continue
        if sort not in SORT_OVERRIDE_OPTIONS:
            issues.append({"Row": i + 1, "ASIN or URL": raw, "Problem": "Invalid sort selection."})
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
                rating_choice=rating,
                sort_override=sort,
            )
        )

    return jobs, pd.DataFrame(issues)


def build_actor_input(
    spec: JobSpec,
    global_sort_key: str,
    verified_filter: str,
    media_filter: str,
    unique_only: bool,
    get_customers_say: bool,
) -> dict:
    sort_key = resolve_sort_key(global_sort_key, spec.sort_override)
    rating_filters = RATING_MAP.get(spec.rating_choice, ["all_stars"])

    # IMPORTANT: actor expects arrays for these fields
    return {
        "ASIN_or_URL": [spec.asin],
        "country": spec.country,
        "max_reviews": int(spec.max_reviews),
        "sort_reviews_by": [sort_key],
        "filter_by_verified_purchase_only": [verified_filter],  # all_reviews | avp_only_reviews
        "filter_by_mediaType": [media_filter],                  # all_contents | media_reviews_only
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
    keep_raw: bool,
    add_video_meta: bool,
    add_score_value: bool,
    add_effective_filter: bool,
) -> JobResult:
    """
    Worker function (thread-safe). Creates its own ApifyClient per thread.
    """
    t0 = time.time()
    client = ApifyClient(token)

    run_id = None
    dataset_id = None
    usage_total_usd = None
    compute_units = None
    pricing_model = None

    try:
        run_input = build_actor_input(
            spec=spec,
            global_sort_key=global_sort_key,
            verified_filter=verified_filter,
            media_filter=media_filter,
            unique_only=unique_only,
            get_customers_say=get_customers_say,
        )

        run = client.actor(actor_id).call(run_input=run_input)
        run_id = run.get("id")
        dataset_id = run.get("defaultDatasetId")

        items = list(client.dataset(dataset_id).iterate_items()) if dataset_id else []

        # Pull run details for cost/CU (best effort)
        if run_id:
            details = client.run(run_id).get() or {}
            usage_total_usd = details.get("usageTotalUsd")
            stats = details.get("stats") or {}
            compute_units = stats.get("computeUnits")
            pricing_info = details.get("pricingInfo") or {}
            pricing_model = pricing_info.get("pricingModel")

        # Post-process rows
        sort_effective = resolve_sort_key(global_sort_key, spec.sort_override)
        for it in items:
            it["_meta_asin"] = spec.asin
            it["_meta_country"] = spec.country
            it["_meta_rating"] = spec.rating_choice
            it["_meta_sort"] = sort_effective
            it["_meta_requested"] = spec.max_reviews
            it["_meta_run_id"] = run_id
            it["_meta_dataset_id"] = dataset_id

            if add_score_value:
                it["ReviewScoreValue"] = parse_review_score_value(it.get("ReviewScore"))

            if add_effective_filter:
                it["EffectiveFilterByStar"] = extract_filter_by_star_from_pageurl(it.get("PageUrl"))

            if clean_content:
                raw = it.get("ReviewContent")
                cleaned, vmeta, had_video = clean_review_content(raw)

                if keep_raw:
                    it["ReviewContent_raw"] = raw

                it["ReviewContent"] = cleaned
                it["HasVideoWidget"] = bool(had_video) or bool(it.get("HasVideoWidget", False))

                if add_video_meta and vmeta:
                    it.update(vmeta)
            else:
                # still add flag if user wants
                if add_effective_filter:
                    it["EffectiveFilterByStar"] = extract_filter_by_star_from_pageurl(it.get("PageUrl"))

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

    except Exception as e:
        runtime_s = time.time() - t0
        return JobResult(
            spec=spec,
            ok=False,
            collected=0,
            runtime_s=runtime_s,
            run_id=run_id,
            dataset_id=dataset_id,
            usage_total_usd=None,
            compute_units=None,
            pricing_model=None,
            error=str(e),
            items=[],
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
# Streamlit UI
# ----------------------------
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

if "asin_table" not in st.session_state:
    st.session_state.asin_table = pd.DataFrame(
        [
            {"Country": "France", "ASIN or URL": "B0DGV9F4X3", "Reviews to pull": 100, "Rating": "All", "Sort": "Default"},
            {"Country": "France", "ASIN or URL": "B0DHHG7P99", "Reviews to pull": 100, "Rating": "All", "Sort": "Default"},
            {"Country": "France", "ASIN or URL": "B0915C748N", "Reviews to pull": 100, "Rating": "All", "Sort": "Default"},
            {"Country": "France", "ASIN or URL": "B0DPP6C5YP", "Reviews to pull": 100, "Rating": "All", "Sort": "Default"},
            {"Country": "France", "ASIN or URL": "B0F1DKQXJV", "Reviews to pull": 100, "Rating": "All", "Sort": "Default"},
        ]
    )

if "last_results" not in st.session_state:
    st.session_state.last_results = []
if "last_master_df" not in st.session_state:
    st.session_state.last_master_df = None
if "last_per_sheet" not in st.session_state:
    st.session_state.last_per_sheet = None
if "last_run_meta" not in st.session_state:
    st.session_state.last_run_meta = {}


with st.sidebar:
    st.subheader("Run settings")
    actor_id = st.text_input("Apify Actor ID", value=DEFAULT_ACTOR_ID)

    global_sort_label = st.selectbox("Default sort order", options=list(SORT_LABEL_TO_KEY.keys()), index=0)
    global_sort_key = SORT_LABEL_TO_KEY[global_sort_label]

    max_workers = st.slider("Max concurrent runs", min_value=1, max_value=8, value=2, step=1)
    throttle_s = st.slider("Throttle between finished jobs (sec)", min_value=0.0, max_value=5.0, value=0.5, step=0.5)

    st.divider()
    token = st.text_input("Apify API Token", type="password")

    st.divider()
    with st.expander("Advanced actor options", expanded=False):
        verified_filter = st.selectbox("Verified purchase filter", options=["all_reviews", "avp_only_reviews"], index=0)
        media_filter = st.selectbox("Media filter", options=["all_contents", "media_reviews_only"], index=0)
        unique_only = st.checkbox("Unique only (dedupe)", value=False)
        get_customers_say = st.checkbox("Get Customers Say", value=True)

    st.divider()
    with st.expander("Output cleaning", expanded=True):
        clean_content = st.checkbox("Clean ReviewContent (remove VSE video JSON)", value=True)
        keep_raw = st.checkbox("Keep ReviewContent_raw column", value=False)
        add_video_meta = st.checkbox("Extract video metadata columns (VideoUrl, etc.)", value=True)
        add_score_value = st.checkbox("Add numeric ReviewScoreValue", value=True)
        add_effective_filter = st.checkbox("Add EffectiveFilterByStar (debug)", value=True)

    st.caption("Tip: Rating=All maps to filter_by_ratings=['all_stars'] (true unfiltered).")


tabs = st.tabs(["Input", "Run", "Results"])


with tabs[0]:
    st.subheader("ASIN table")

    edited = st.data_editor(
        st.session_state.asin_table,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Country": st.column_config.SelectboxColumn("Country", options=COUNTRY_VALUES, width="medium"),
            "ASIN or URL": st.column_config.TextColumn("ASIN or URL", width="large"),
            "Reviews to pull": st.column_config.NumberColumn("Reviews to pull", min_value=1, max_value=MAX_PER_ASIN_HARD_CAP, step=25),
            "Rating": st.column_config.SelectboxColumn("Rating", options=RATING_OPTIONS, width="small"),
            "Sort": st.column_config.SelectboxColumn("Sort", options=SORT_OVERRIDE_OPTIONS, width="small"),
        },
    )
    st.session_state.asin_table = edited

    jobs, issues_df = validate_and_build_jobs(st.session_state.asin_table)

    if not issues_df.empty:
        st.warning("Fix these rows before running:")
        st.dataframe(issues_df, use_container_width=True)

    st.info("If you previously saw only 5★ when Rating=All, that was because All was being sent as 1–5 buckets. This app sends All as all_stars.")


with tabs[1]:
    st.subheader("Run")

    jobs, issues_df = validate_and_build_jobs(st.session_state.asin_table)
    can_run = bool(token) and bool(actor_id.strip()) and len(jobs) > 0 and issues_df.empty

    c1, c2, c3 = st.columns([1.2, 1.2, 2.4], vertical_alignment="center")
    with c1:
        run_clicked = st.button("Start scrape", type="primary", use_container_width=True, disabled=not can_run)
    with c2:
        clear_clicked = st.button("Clear results", use_container_width=True)
    with c3:
        st.caption(f"Rows: **{len(jobs)}** · Concurrency: **{max_workers}** · Default sort: **{global_sort_label}**")

    if clear_clicked:
        st.session_state.last_results = []
        st.session_state.last_master_df = None
        st.session_state.last_per_sheet = None
        st.session_state.last_run_meta = {}
        st.success("Cleared.")

    if run_clicked:
        st.session_state.last_results = []
        st.session_state.last_master_df = None
        st.session_state.last_per_sheet = None
        st.session_state.last_run_meta = {}

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
                    keep_raw,
                    add_video_meta,
                    add_score_value,
                    add_effective_filter,
                ): spec
                for spec in jobs
            }

            done_so_far = 0
            for fut in as_completed(futures):
                spec = futures[fut]
                res = fut.result()
                results.append(res)
                pending = [p for p in pending if p != spec]

                done_so_far += 1
                progress.progress(int(done_so_far / total * 100))

                if res.ok:
                    with log_box:
                        st.success(
                            f"[{now_ts()}] Row {spec.row_id} OK · {spec.asin} · {spec.country} · "
                            f"Rating {spec.rating_choice} · Collected {res.collected}"
                        )
                else:
                    with log_box:
                        st.error(f"[{now_ts()}] Row {spec.row_id} ERROR · {spec.asin} · {res.error}")

                # Live summary table
                rows = []
                for r in sorted(results, key=lambda x: x.spec.row_id):
                    rows.append(
                        {
                            "Row": r.spec.row_id,
                            "ASIN": r.spec.asin,
                            "Country": r.spec.country,
                            "Rating": r.spec.rating_choice,
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

        # Build exports
        per_sheet: Dict[str, pd.DataFrame] = {}
        all_items: List[dict] = []

        for r in results:
            # Keep sheet keys short
            key = f"{r.spec.asin}-{r.spec.country[:2].upper()}-R{r.spec.rating_choice}-S{r.spec.sort_override[0].upper()}"
            if r.ok and r.items:
                df = pd.json_normalize(r.items)
                per_sheet[key] = df
                all_items.extend(r.items)
            else:
                per_sheet[key] = pd.DataFrame(
                    [{
                        "_meta_row": r.spec.row_id,
                        "_meta_asin": r.spec.asin,
                        "_meta_country": r.spec.country,
                        "_meta_rating": r.spec.rating_choice,
                        "_error": r.error or "",
                    }]
                )

        master_df = pd.json_normalize(all_items) if all_items else pd.DataFrame()

        st.session_state.last_results = results
        st.session_state.last_per_sheet = per_sheet
        st.session_state.last_master_df = master_df
        st.session_state.last_run_meta = {
            "finished_at": now_ts(),
            "total_runtime_s": total_runtime,
            "actor_id": actor_id,
        }

        status_ph.markdown(f"**[{now_ts()}]** Done ✅  (Go to Results tab to download.)")


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

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Succeeded", ok_count)
        c2.metric("Failed", fail_count)
        c3.metric("Collected rows", collected_total)
        c4.metric("Cost total (USD)", f"{cost_total:.4f}" if cost_total is not None else "—")

        if master_df is not None and not master_df.empty:
            with st.expander("Preview MASTER (first 100 rows)", expanded=False):
                st.dataframe(master_df.head(100), use_container_width=True)

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
