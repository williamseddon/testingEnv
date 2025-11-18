# Write the latest full Streamlit app code to a file for you to download
code = r'''# streamlit_app.py
# Amazon Scraper (Axesso) — Scrape‑first UI + FULL REVIEWS
# --------------------------------------------------------
# This version adds a **Reviews Harvester** that can fetch (near) *all* reviews
# for a product using the Axesso Amazon Reviews Scraper on Apify.
#
# Highlights
# - Queue-first product scraping (Axesso REST) as before.
# - NEW: Reviews Harvester (Apify): supply ASIN + marketplace → get paginated
#   reviews via Apify Actor `axesso_data/amazon-reviews-scraper`.
# - Options for sort, star filter, reviewer type, media filter, and max pages.
# - De-duplication by `reviewId` + heuristic fallback.
# - One‑click CSV export of *all* fetched reviews.
#
# Auth
# - Axesso REST key in sidebar or .streamlit/secrets.toml as:
#     [axesso]
#     API_KEY = "..."
# - Apify token for reviews in sidebar or secrets as:
#     [apify]
#     TOKEN = "..."

from __future__ import annotations
import json
import time
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

# ----------------------------- Config -----------------------------
st.set_page_config(
    page_title="Amazon Scraper (Axesso) — Full Reviews",
    page_icon="🧲",
    layout="wide",
)

API_ENDPOINT = "https://api.axesso.de/amz/amazon-lookup-product"
APIFY_RUN_SYNC_DATASET = "https://api.apify.com/v2/acts/axesso_data~amazon-reviews-scraper/run-sync-get-dataset-items"
REQUEST_TIMEOUT = 30
DEFAULT_THROTTLE = 1.0
DEFAULT_MARKET = "com"
MARKETS = ["com", "de", "co.uk", "fr", "it", "es", "ca", "com.au", "co.jp"]

# --------------------------- Utilities ----------------------------

def _load_api_key_from_secrets() -> Optional[str]:
    try:
        return st.secrets.get("axesso", {}).get("API_KEY")
    except Exception:
        return None


def _load_apify_token_from_secrets() -> Optional[str]:
    try:
        return st.secrets.get("apify", {}).get("TOKEN")
    except Exception:
        return None


def add_psc(url: str) -> str:
    if not url:
        return url
    return url if "psc=" in url else (url + ("&psc=1" if "?" in url else "?psc=1"))


def normalize_url_or_asin(text: str, market: str) -> Tuple[str, Optional[str]]:
    s = (text or "").strip()
    if not s:
        return "", None
    # ASIN token
    m = re.fullmatch(r"[A-Z0-9]{10}", s, flags=re.I)
    if m:
        asin = m.group(0).upper()
        return f"https://www.amazon.{market}/dp/{asin}", asin
    # Try extract from URL
    url = s
    if url.startswith("/dp/"):
        url = f"https://www.amazon.{market}{url}"
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    asin2 = extract_asin(url)
    return url, asin2


def extract_asin(s: str) -> Optional[str]:
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", s, flags=re.I)
    if m:
        return m.group(1).upper()
    m = re.search(r"(?<![A-Z0-9])([A-Z0-9]{10})(?![A-Z0-9])", s, flags=re.I)
    return m.group(1).upper() if m else None


@st.cache_data(show_spinner=False)
def fetch_product(url: str, api_key: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    headers = {"axesso-api-key": api_key}
    try:
        resp = requests.get(API_ENDPOINT, params={"url": url}, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        return None, f"Network error: {e}"
    if resp.status_code != 200:
        try:
            payload = resp.json()
        except Exception:
            payload = resp.text
        return None, f"HTTP {resp.status_code}: {payload}"
    try:
        data = resp.json()
    except Exception as e:
        return None, f"Bad JSON: {e}"
    status = str(data.get("responseStatus", "")).upper()
    if "NOT_FOUND" in status:
        return None, data.get("responseMessage") or "Product not found"
    return data, None


def flatten_product_for_csv(data: Dict[str, Any]) -> pd.DataFrame:
    base = {
        "asin": data.get("asin"),
        "title": data.get("productTitle"),
        "price": data.get("price"),
        "retailPrice": data.get("retailPrice"),
        "shippingPrice": data.get("shippingPrice"),
        "ratingText": data.get("productRating"),
        "reviewCount": data.get("countReview"),
        "soldBy": data.get("soldBy"),
        "fulfilledBy": data.get("fulfilledBy"),
        "availability": data.get("warehouseAvailability"),
        "categories": ", ".join(data.get("categories", []) or []),
    }
    rows: List[Dict[str, Any]] = []
    reviews = data.get("reviews") or data.get("globalReviews") or []
    if reviews:
        for rv in reviews:
            r = base.copy()
            r.update({
                "reviewId": rv.get("reviewId"),
                "reviewTitle": rv.get("title"),
                "reviewText": rv.get("text"),
                "reviewRating": rv.get("rating"),
                "reviewDate": rv.get("date"),
                "reviewUser": rv.get("userName"),
                "reviewUrl": rv.get("url"),
                "reviewLocale": json.dumps(rv.get("locale")) if isinstance(rv.get("locale"), dict) else rv.get("locale"),
            })
            rows.append(r)
    else:
        rows.append(base)
    return pd.DataFrame(rows)


def dedupe_reviews_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if "reviewId" in df.columns:
        return df.drop_duplicates(subset=["reviewId"], keep="first")
    tmp = df.copy()
    txt = tmp.get("reviewText")
    if txt is not None:
        tmp["_k"] = (
            tmp.get("asin", "").astype(str)
            + "|" + tmp.get("reviewUser", "").astype(str)
            + "|" + tmp.get("reviewDate", "").astype(str)
            + "|" + txt.astype(str).str.slice(0, 80)
        )
    else:
        tmp["_k"] = (
            tmp.get("asin", "").astype(str)
            + "|" + tmp.get("reviewUser", "").astype(str)
            + "|" + tmp.get("reviewDate", "").astype(str)
        )
    return tmp.drop_duplicates(subset=["_k"], keep="first").drop(columns=["_k"], errors="ignore")

# ------------------------- Apify Reviews API -----------------------

def fetch_reviews_apify(
    apify_token: str,
    asin: str,
    domain_code: str = "com",
    sort_by: str = "recent",          # recent | top | helpful
    filter_by_star: str = "all_stars", # all_stars | five_star | four_star | ...
    reviewer_type: str = "all_reviews",# all_reviews | verified_purchase
    media_type: str = "all_contents",  # all_contents | with_media | text_only
    max_pages: int = 50,               # how many review pages to crawl
) -> Tuple[pd.DataFrame, Optional[str]]:
    """Run the Apify Actor synchronously and return a DataFrame of reviews."""
    headers = {"Content-Type": "application/json"}
    params = {"token": apify_token}

    actor_input = {
        "input": [
            {
                "asin": asin,
                "domainCode": domain_code,
                "sortBy": sort_by,
                "maxPages": max_pages,
                "filterByStar": filter_by_star,
                "reviewerType": reviewer_type,
                "formatType": "current_format",
                "mediaType": media_type,
            }
        ]
    }
    try:
        resp = requests.post(
            APIFY_RUN_SYNC_DATASET,
            params=params,
            headers=headers,
            data=json.dumps(actor_input),
            timeout=1200,
        )
    except requests.RequestException as e:
        return pd.DataFrame(), f"Network error calling Apify: {e}"

    if resp.status_code != 200:
        try:
            err = resp.json()
        except Exception:
            err = resp.text
        return pd.DataFrame(), f"Apify HTTP {resp.status_code}: {err}"

    # Apify returns dataset items directly (JSON array)
    try:
        items = resp.json()
    except Exception as e:
        return pd.DataFrame(), f"Failed to parse Apify dataset JSON: {e}"

    if not isinstance(items, list):
        return pd.DataFrame(), "Unexpected Apify response format"

    df = pd.DataFrame(items)
    # Standardize some expected fields
    rename_map = {
        "id": "reviewId",
        "title": "reviewTitle",
        "text": "reviewText",
        "rating": "reviewRating",
        "date": "reviewDate",
        "userName": "reviewUser",
        "asin": "asin",
    }
    for k, v in rename_map.items():
        if k in df.columns and v not in df.columns:
            df[v] = df[k]
    if "asin" not in df.columns:
        df["asin"] = asin

    return df, None

# ------------------------------ UI -------------------------------

st.title("🧲 Amazon Scraper (Axesso) — Full Reviews")
st.caption("Queue products for scraping and harvest full reviews via Apify. Minimal UI, scrape-first.")

with st.sidebar:
    st.header("Auth & Controls")
    default_axesso = _load_api_key_from_secrets()
    default_apify = _load_apify_token_from_secrets()

    api_key = st.text_input("Axesso API Key", value=st.session_state.get("axesso_api_key", default_axesso or ""), type="password")
    if api_key:
        st.session_state["axesso_api_key"] = api_key

    apify_token = st.text_input("Apify Token (for full reviews)", value=st.session_state.get("apify_token", default_apify or ""), type="password")
    if apify_token:
        st.session_state["apify_token"] = apify_token

    market = st.selectbox("Marketplace for bare ASINs", MARKETS, index=MARKETS.index(DEFAULT_MARKET))
    ensure_psc = st.checkbox("Ensure ?psc=1", value=True)
    throttle = st.number_input("Throttle between calls (sec)", min_value=0.0, max_value=10.0, step=0.1, value=DEFAULT_THROTTLE)
    max_items = st.number_input("Max items to fetch (0 = no cap)", min_value=0, value=0, step=50)
    dedupe_inputs = st.checkbox("De-duplicate inputs by ASIN", value=True)
    use_cache = st.checkbox("Use cache (Axesso)", value=True)

st.markdown("### 1) Scrape Queue (Products)")
queue_text = st.text_area(
    "Paste ASINs or Amazon product URLs (one per line)",
    height=160,
    placeholder="B07TCHYBSK\nhttps://www.amazon.com/dp/B0B17BYJ5R?psc=1",
)
col_a, col_b = st.columns([1,1])
with col_a:
    prepared = st.button("Prepare Queue", use_container_width=True)
with col_b:
    run = st.button("Run Product Scraper", type="primary", use_container_width=True)

# Session slots
if "prepared_items" not in st.session_state:
    st.session_state["prepared_items"] = []

if prepared:
    raw = [ln.strip() for ln in queue_text.splitlines() if ln.strip()]
    items: List[Tuple[str, Optional[str]]] = []
    seen: set[str] = set()
    for line in raw:
        url, asin_guess = normalize_url_or_asin(line, market)
        if not url:
            continue
        if ensure_psc:
            url = add_psc(url)
        if dedupe_inputs and asin_guess:
            if asin_guess in seen:
                continue
            seen.add(asin_guess)
        items.append((url, asin_guess))
    if max_items and max_items > 0:
        items = items[: max_items]
    st.session_state["prepared_items"] = items
    st.success(f"Prepared {len(items)} item(s). Click 'Run Product Scraper'.")

if run:
    if not api_key:
        st.error("Enter your Axesso API key in the sidebar.")
        st.stop()
    if not use_cache:
        fetch_product.clear()

    items = st.session_state.get("prepared_items") or []
    if not items:
        raw = [ln.strip() for ln in queue_text.splitlines() if ln.strip()]
        items = []
        seen: set[str] = set()
        for line in raw:
            url, asin_guess = normalize_url_or_asin(line, market)
            if not url:
                continue
            if ensure_psc:
                url = add_psc(url)
            if dedupe_inputs and asin_guess:
                if asin_guess in seen:
                    continue
                seen.add(asin_guess)
            items.append((url, asin_guess))
        if max_items and max_items > 0:
            items = items[: max_items]
        st.session_state["prepared_items"] = items

    if not items:
        st.warning("No items to scrape.")
        st.stop()

    st.markdown("### 2) Scraping Products")
    progress = st.progress(0)
    status = st.empty()

    results: List[Dict[str, Any]] = []
    errors: List[Tuple[str, str]] = []

    for i, (target_url, asin_guess) in enumerate(items, start=1):
        status.info(f"Fetching {i}/{len(items)}: {target_url}")
        data, err = fetch_product(target_url, api_key)
        if err:
            errors.append((target_url, err))
        else:
            results.append(data)
        progress.progress(i / len(items))
        if throttle:
            time.sleep(float(throttle))

    status.empty()

    st.markdown("### 3) Results")
    if results:
        # Compact table
        rows = [{
            "asin": r.get("asin"),
            "title": r.get("productTitle"),
            "price": r.get("price"),
            "rating": r.get("productRating"),
            "reviews": r.get("countReview"),
            "soldBy": r.get("soldBy"),
            "fulfilledBy": r.get("fulfilledBy"),
        } for r in results]
        df_summary = pd.DataFrame(rows).drop_duplicates(subset=["asin"], keep="first")
        st.dataframe(df_summary, use_container_width=True, hide_index=True)

        # Exports
        st.markdown("#### Exports (Products)")
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "Download all (JSON)",
                data=json.dumps(results, indent=2),
                file_name="axesso_products.json",
                mime="application/json",
                use_container_width=True,
            )
        with col2:
            frames = [flatten_product_for_csv(r) for r in results]
            products_csv = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            st.download_button(
                "Download flattened products CSV",
                data=products_csv.to_csv(index=False),
                file_name="products_flattened.csv",
                mime="text/csv",
                use_container_width=True,
            )

    if errors:
        with st.expander("Error log"):
            for u, msg in errors:
                st.error(f"{u}\n\n{msg}")

# ------------------------- REVIEWS HARVESTER ----------------------

st.markdown("---")
st.header("Reviews Harvester (Full Reviews via Apify)")

col_r1, col_r2, col_r3 = st.columns(3)
with col_r1:
    asin_input = st.text_input("ASIN for reviews", placeholder="e.g., B07TCHYBSK")
with col_r2:
    domain_code = st.selectbox("Marketplace", MARKETS, index=MARKETS.index(DEFAULT_MARKET))
with col_r3:
    max_pages = st.number_input("Max pages", min_value=1, max_value=500, value=50, step=1, help="Higher = more reviews. 50–200 is common.")

col_r4, col_r5, col_r6 = st.columns(3)
with col_r4:
    sort_by = st.selectbox("Sort by", ["recent", "top", "helpful"], index=0)
with col_r5:
    filter_star = st.selectbox("Star filter", [
        "all_stars", "five_star", "four_star", "three_star", "two_star", "one_star"
    ], index=0)
with col_r6:
    reviewer_type = st.selectbox("Reviewer type", ["all_reviews", "verified_purchase"], index=0)

media_type = st.selectbox("Media filter", ["all_contents", "with_media", "text_only"], index=0)
run_reviews = st.button("Run Reviews Harvester", type="primary")

if run_reviews:
    if not st.session_state.get("apify_token"):
        st.error("Enter your Apify token in the sidebar to fetch full reviews.")
        st.stop()
    asin_clean = (asin_input or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{10}", asin_clean):
        st.error("Please enter a valid 10‑char ASIN.")
        st.stop()

    with st.spinner("Fetching reviews via Apify (this may take a while for many pages)..."):
        df_reviews, err = fetch_reviews_apify(
            apify_token=st.session_state["apify_token"],
            asin=asin_clean,
            domain_code=domain_code,
            sort_by=sort_by,
            filter_by_star=filter_star,
            reviewer_type=reviewer_type,
            media_type=media_type,
            max_pages=int(max_pages),
        )

    if err:
        st.error(err)
    else:
        # Deduplicate and export
        before = len(df_reviews)
        df_reviews = dedupe_reviews_df(df_reviews)
        after = len(df_reviews)
        st.success(f"Fetched {before} reviews; {after} unique after de-dup.")
        st.dataframe(df_reviews, use_container_width=True, hide_index=True)
        st.download_button(
            "Download FULL reviews (CSV)",
            data=df_reviews.to_csv(index=False),
            file_name=f"{asin_clean}_reviews_full.csv",
            mime="text/csv",
            use_container_width=True,
        )

st.divider()
st.caption("Scrape responsibly. **Full reviews** use Apify's Axesso Actor and can return thousands of reviews depending on product and filters.")
'''
with open('/mnt/data/streamlit_app.py', 'w', encoding='utf-8') as f:
    f.write(code)
print("Saved to /mnt/data/streamlit_app.py")















