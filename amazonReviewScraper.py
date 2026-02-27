# app.py (UPDATED AGAIN)
# Adds per-ASIN rating filter selection (All, 1, 2, 3, 4, 5) next to each ASIN row.

from __future__ import annotations

import io
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple

import pandas as pd
import streamlit as st
from apify_client import ApifyClient


APP_TITLE = "Amazon Reviews Scraper (Apify)"
DEFAULT_ACTOR_ID = "8vhDnIX6dStLlGVr7"
MAX_PER_ASIN_HARD_CAP = 5000

SORT_CHOICES = {"Recent": "recent", "Helpful": "helpful"}
ASIN_RE = re.compile(r"^[A-Z0-9]{10}$", re.IGNORECASE)

# Actor-dependent country values
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

# Ratings filter (per row)
RATING_OPTIONS = ["All", "1", "2", "3", "4", "5"]
RATING_MAP = {
    "1": ["one_star"],
    "2": ["two_star"],
    "3": ["three_star"],
    "4": ["four_star"],
    "5": ["five_star"],
    "All": ["one_star", "two_star", "three_star", "four_star", "five_star"],
}


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def build_actor_input(asin: str, reviews_n: int, sort_key: str, country: str, rating_choice: str) -> dict:
    rating_filter = RATING_MAP.get(rating_choice, RATING_MAP["All"])

    return {
        "ASIN_or_URL": [f"https://www.amazon.fr/dp/{asin}"],
        "country": country,
        "max_reviews": int(reviews_n),

        # Actor expects arrays for these fields
        "sort_reviews_by": [sort_key],
        "filter_by_ratings": rating_filter,
        "filter_by_verified_purchase_only": ["all_reviews"],
        "filter_by_mediaType": ["all_contents"],

        "unique_only": False,
        "get_customers_say": True,
    }


def apify_fetch_reviews(
    client: ApifyClient,
    actor_id: str,
    asin: str,
    reviews_n: int,
    sort_key: str,
    country: str,
    rating_choice: str,
) -> List[dict]:
    run_input = build_actor_input(asin, reviews_n, sort_key, country, rating_choice)
    run = client.actor(actor_id).call(run_input=run_input)
    dataset_id = run["defaultDatasetId"]
    items = list(client.dataset(dataset_id).iterate_items())

    for it in items:
        it["asin"] = asin
        it["country"] = country
        it["rating_filter"] = rating_choice

    return items


def export_excel_bytes(per_sheet: Dict[str, pd.DataFrame], master: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet, df in per_sheet.items():
            df.to_excel(writer, sheet_name=safe_sheet_name(sheet), index=False)
        master.to_excel(writer, sheet_name="MASTER", index=False)
    buf.seek(0)
    return buf.read()


def export_csv_bytes(master: pd.DataFrame) -> bytes:
    return master.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


@dataclass
class RowSpec:
    asin: str
    n_reviews: int
    country: str
    rating_choice: str


st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

with st.sidebar:
    st.subheader("Settings")
    actor_id = st.text_input("Apify Actor ID", value=DEFAULT_ACTOR_ID)
    sort_label = st.selectbox("Sort order", options=list(SORT_CHOICES.keys()), index=0)
    sort_key = SORT_CHOICES[sort_label]
    st.divider()
    token = st.text_input("Apify API Token", type="password", help="Stored only in this session memory (not saved).")
    st.divider()
    st.caption("If the actor complains about Country values, update COUNTRY_VALUES to match the actor schema.")


st.subheader("ASIN list (per-row country + per-row rating filter + per-row review count)")

if "asin_table" not in st.session_state:
    st.session_state.asin_table = pd.DataFrame(
        [
            {"Country": "France", "Rating filter": "All", "ASIN or URL": "B0DGV9F4X3", "Reviews to pull": 100},
            {"Country": "France", "Rating filter": "All", "ASIN or URL": "B0DHHG7P99", "Reviews to pull": 100},
            {"Country": "France", "Rating filter": "All", "ASIN or URL": "B0915C748N", "Reviews to pull": 100},
            {"Country": "France", "Rating filter": "All", "ASIN or URL": "B0DPP6C5YP", "Reviews to pull": 100},
            {"Country": "France", "Rating filter": "All", "ASIN or URL": "B0F1DKQXJV", "Reviews to pull": 100},
        ]
    )

colA, colB = st.columns([3, 2], vertical_alignment="top")

with colA:
    st.write("Add/edit rows below. Country + rating filter are per ASIN.")
    edited = st.data_editor(
        st.session_state.asin_table,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Country": st.column_config.SelectboxColumn("Country", options=COUNTRY_VALUES, width="medium"),
            "Rating filter": st.column_config.SelectboxColumn("Rating filter", options=RATING_OPTIONS, width="small"),
            "ASIN or URL": st.column_config.TextColumn("ASIN or URL", width="large"),
            "Reviews to pull": st.column_config.NumberColumn(
                "Reviews to pull", min_value=1, max_value=MAX_PER_ASIN_HARD_CAP, step=25, width="medium"
            ),
        },
    )
    st.session_state.asin_table = edited

with colB:
    st.write("Quick add")
    quick_country = st.selectbox("Country", options=COUNTRY_VALUES, index=0)
    quick_rating = st.selectbox("Rating filter", options=RATING_OPTIONS, index=0)
    quick_asin = st.text_input("ASIN or URL to add", value="")
    quick_n = st.number_input("Reviews for this ASIN", min_value=1, max_value=MAX_PER_ASIN_HARD_CAP, value=100, step=25)

    if st.button("Add to table", use_container_width=True):
        if quick_asin.strip():
            new_row = {
                "Country": quick_country,
                "Rating filter": quick_rating,
                "ASIN or URL": quick_asin.strip(),
                "Reviews to pull": int(quick_n),
            }
            st.session_state.asin_table = pd.concat(
                [st.session_state.asin_table, pd.DataFrame([new_row])],
                ignore_index=True,
            )
            st.success("Added.")
        else:
            st.warning("Enter an ASIN or URL first.")


def get_rowspecs(df: pd.DataFrame) -> Tuple[List[RowSpec], pd.DataFrame]:
    cleaned: List[RowSpec] = []
    issues: List[dict] = []

    for i, r in df.fillna("").iterrows():
        raw = str(r.get("ASIN or URL", "")).strip()
        country = str(r.get("Country", "")).strip()
        rating_choice = str(r.get("Rating filter", "All")).strip() or "All"
        n = r.get("Reviews to pull", 0)

        if not raw:
            continue

        asin = normalize_asin(raw)

        try:
            n_int = int(n)
        except Exception:
            n_int = 0

        if not country:
            issues.append({"Row": i + 1, "ASIN or URL": raw, "Problem": "Country is empty."})
            continue

        if rating_choice not in RATING_OPTIONS:
            issues.append({"Row": i + 1, "ASIN or URL": raw, "Problem": f"Rating filter must be one of {RATING_OPTIONS}."})
            continue

        if not is_valid_asin(asin):
            issues.append({"Row": i + 1, "ASIN or URL": raw, "Problem": f"Could not parse valid ASIN (got '{asin}')."})
            continue

        if n_int < 1 or n_int > MAX_PER_ASIN_HARD_CAP:
            issues.append({"Row": i + 1, "ASIN or URL": raw, "Problem": f"Reviews must be 1..{MAX_PER_ASIN_HARD_CAP} (got {n_int})."})
            continue

        cleaned.append(RowSpec(asin=asin, n_reviews=n_int, country=country, rating_choice=rating_choice))

    return cleaned, pd.DataFrame(issues)


rowspecs, issues_df = get_rowspecs(st.session_state.asin_table)

if not issues_df.empty:
    st.warning("Fix these rows before scraping:")
    st.dataframe(issues_df, use_container_width=True)

st.divider()

run_col1, run_col2, run_col3 = st.columns([1.2, 1, 1.8], vertical_alignment="center")

with run_col1:
    do_scrape = st.button(
        "Scrape reviews",
        type="primary",
        use_container_width=True,
        disabled=(not token or len(rowspecs) == 0 or not issues_df.empty),
    )

with run_col2:
    throttle = st.slider("Throttle between ASINs (sec)", min_value=0.0, max_value=10.0, value=1.0, step=0.5)

with run_col3:
    st.caption(f"Sort: **{sort_label}** · Actor: **{actor_id}** · Rows ready: **{len(rowspecs)}**")


if do_scrape:
    client = ApifyClient(token)

    status = st.empty()
    progress = st.progress(0)
    log_box = st.container()

    # Per-sheet mapping: use "ASIN (Country) [Rating]" to avoid collisions
    per_sheet: Dict[str, pd.DataFrame] = {}
    all_items: List[dict] = []
    err_rows: List[dict] = []

    total = len(rowspecs)
    status.markdown(f"**[{now_ts()}]** Starting…")

    for idx, spec in enumerate(rowspecs, start=1):
        asin, n_reviews, country, rating_choice = spec.asin, spec.n_reviews, spec.country, spec.rating_choice

        status.markdown(
            f"**[{now_ts()}]** ({idx}/{total}) Scraping **{asin}** · **{country}** · "
            f"**Rating {rating_choice}** (target {n_reviews})…"
        )
        t0 = time.time()

        try:
            items = apify_fetch_reviews(
                client=client,
                actor_id=actor_id,
                asin=asin,
                reviews_n=n_reviews,
                sort_key=sort_key,
                country=country,
                rating_choice=rating_choice,
            )

            df = pd.json_normalize(items)
            sheet_key = f"{asin} ({country}) [{rating_choice}]"
            per_sheet[sheet_key] = df

            all_items.extend(items)

            elapsed = time.time() - t0
            with log_box:
                st.success(
                    f"[{now_ts()}] {asin} · {country} · Rating {rating_choice}: collected {len(items)} rows in {elapsed:.1f}s"
                )

        except Exception as e:
            elapsed = time.time() - t0
            err_rows.append(
                {"asin": asin, "country": country, "rating_filter": rating_choice, "requested": n_reviews, "error": str(e)}
            )
            with log_box:
                st.error(f"[{now_ts()}] {asin} · {country} · Rating {rating_choice}: ERROR after {elapsed:.1f}s → {e}")

        progress.progress(int(idx / total * 100))
        if throttle > 0 and idx < total:
            time.sleep(throttle)

    status.markdown(f"**[{now_ts()}]** Finished. Building exports…")

    master_df = pd.json_normalize(all_items) if all_items else pd.DataFrame()

    st.subheader("Results summary")
    summary_rows = []
    for spec in rowspecs:
        key = f"{spec.asin} ({spec.country}) [{spec.rating_choice}]"
        got = 0 if key not in per_sheet else len(per_sheet[key])
        summary_rows.append(
            {"asin": spec.asin, "country": spec.country, "rating_filter": spec.rating_choice, "requested": spec.n_reviews, "collected": got}
        )
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)

    if not master_df.empty:
        st.write("Preview (MASTER)")
        st.dataframe(master_df.head(50), use_container_width=True)

        ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        excel_name = f"amazon_reviews_{ts_tag}.xlsx"
        csv_name = f"amazon_reviews_master_{ts_tag}.csv"

        excel_bytes = export_excel_bytes(per_sheet, master_df)
        csv_bytes = export_csv_bytes(master_df)

        dl1, dl2 = st.columns(2)
        with dl1:
            st.download_button(
                "Download Excel (tabs per ASIN+Country+Rating + MASTER)",
                data=excel_bytes,
                file_name=excel_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        with dl2:
            st.download_button(
                "Download Master CSV",
                data=csv_bytes,
                file_name=csv_name,
                mime="text/csv",
                use_container_width=True,
            )
    else:
        st.warning("No rows collected. If unexpected, the most common cause is an actor schema mismatch (especially Country).")

    if err_rows:
        st.subheader("Errors")
        err_df = pd.DataFrame(err_rows)
        st.dataframe(err_df, use_container_width=True)
        st.download_button(
            "Download error log CSV",
            data=err_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            file_name=f"scrape_errors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )

    status.markdown(f"**[{now_ts()}]** Done ✅")
