# streamlit_app.py
# Streamlit Amazon Product Scraper using Axesso Amazon API
# --------------------------------------------------------
# New (v2):
# - Works with any ASIN for any Amazon marketplace (or a direct URL)
# - Marketplace selector (amazon.com, .de, .co.uk, .fr, .it, .es, .ca, .com.au, .co.jp)
# - Bulk mode accepts ASINs or URLs mixed; we normalize automatically
# - Smarter retry with backoff, improved error surfacing
# - Clearer review counts + note on upstream limits
# - "Open on Amazon" quick links
# - Everything else from v1: review filters, JSON/CSV export, caching, etc.
#
# How to provide your API key (choose one):
# 1) In the UI: Sidebar -> Axesso API Key (masked input)
# 2) In ".streamlit/secrets.toml":
#    [axesso]
#    API_KEY = "YOUR_KEY"
# --------------------------------------------------------

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
    page_title="Amazon Product Scraper (Axesso)",
    page_icon="🛒",
    layout="wide",
)

API_ENDPOINT = "https://api.axesso.de/amz/amazon-lookup-product"
REQUEST_TIMEOUT = 30  # seconds
THROTTLE_SECONDS = 1.0  # in bulk mode
MAX_RETRIES = 3
BACKOFF_BASE = 1.6

DEFAULT_ASIN = "B07TCHYBSK"
DEFAULT_DOMAIN = "com"

MARKETPLACES = [
    "com", "de", "co.uk", "fr", "it", "es", "ca", "com.au", "co.jp"
]

# --------------------------- Utilities ----------------------------

def _load_api_key_from_secrets() -> Optional[str]:
    try:
        return st.secrets.get("axesso", {}).get("API_KEY")
    except Exception:
        return None


def add_psc_param(url: str) -> str:
    if not url:
        return url
    if "psc=" in url:
        return url
    return url + ("&psc=1" if ("?" in url) else "?psc=1")


def normalize_input_to_url(text: str, domain: str = DEFAULT_DOMAIN) -> str:
    """Accept ASIN or URL; return a normalized amazon URL with ?psc=1."""
    s = (text or "").strip()
    if not s:
        return s
    asin_like = re.fullmatch(r"[A-Z0-9]{10}", s, flags=re.I)
    if asin_like:
        url = f"https://www.amazon.{domain}/dp/{s.upper()}"
        return add_psc_param(url)
    # else assume it's a URL (we'll trust Axesso to validate)
    # normalize scheme
    if s.startswith("http://"):
        s = "https://" + s[len("http://"):]
    # allow partial /dp/ASIN
    if s.startswith("/dp/"):
        s = f"https://www.amazon.{domain}{s}"
    return add_psc_param(s)


@st.cache_data(show_spinner=False)
def fetch_product(url: str, api_key: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Call Axesso API with retry/backoff. Returns (data, error_message)."""
    params = {"url": url}
    headers = {"axesso-api-key": api_key}

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(API_ENDPOINT, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            last_err = f"Network error: {e}"
        else:
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception as e:
                    return None, f"Failed to parse JSON: {e}"
                status = str(data.get("responseStatus", "")).upper()
                if "NOT_FOUND" in status:
                    return None, data.get("responseMessage") or "Product not found"
                return data, None
            else:
                # include any error payload
                try:
                    err_payload = resp.json()
                except Exception:
                    err_payload = resp.text
                last_err = f"HTTP {resp.status_code}: {err_payload}"
        # backoff before next attempt
        time.sleep((BACKOFF_BASE ** (attempt - 1)) / 2)

    return None, (last_err or "Unknown error")


def stars_from_text(rating_text: Optional[str]) -> Optional[float]:
    if not rating_text:
        return None
    m = re.search(r"([0-9]+(?:\.[0-9])?)\s*out of\s*5", rating_text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def flatten_product_for_csv(data: Dict[str, Any]) -> pd.DataFrame:
    base = {
        "asin": data.get("asin"),
        "productTitle": data.get("productTitle"),
        "price": data.get("price"),
        "retailPrice": data.get("retailPrice"),
        "shippingPrice": data.get("shippingPrice"),
        "productRating": data.get("productRating"),
        "countReview": data.get("countReview"),
        "soldBy": data.get("soldBy"),
        "fulfilledBy": data.get("fulfilledBy"),
        "warehouseAvailability": data.get("warehouseAvailability"),
        "categories": ", ".join(data.get("categories", []) or []),
    }

    rows: List[Dict[str, Any]] = []

    def _append(row_extra: Dict[str, Any]):
        r = base.copy()
        r.update(row_extra)
        rows.append(r)

    reviews = data.get("reviews") or data.get("globalReviews") or []
    if reviews:
        for rv in reviews:
            _append({
                "reviewTitle": rv.get("title"),
                "reviewText": rv.get("text"),
                "reviewRating": rv.get("rating"),
                "reviewDate": rv.get("date"),
                "reviewUser": rv.get("userName"),
                "reviewUrl": rv.get("url"),
                "reviewLocale": json.dumps(rv.get("locale")) if isinstance(rv.get("locale"), dict) else rv.get("locale"),
            })
    else:
        _append({})

    return pd.DataFrame(rows)


# ------------------------------ UI -------------------------------

st.title("🛒 Amazon Product Scraper (Axesso)")
st.caption("Enter an ASIN (any marketplace) or a full Amazon URL. We'll call the Axesso API and render the results.")

with st.sidebar:
    st.header("Settings")
    default_key = _load_api_key_from_secrets()
    api_key = st.text_input(
        "Axesso API Key",
        value=st.session_state.get("axesso_api_key", default_key or ""),
        type="password",
        help=(
            "Your Axesso subscription key. You can also set it in .streamlit/secrets.toml as:\n"
            "[axesso]\nAPI_KEY='YOUR_KEY'"
        ),
    )
    if api_key:
        st.session_state["axesso_api_key"] = api_key

    st.divider()
    st.subheader("Input mode")
    mode = st.radio("Select mode", ["Single", "Bulk"], index=0, horizontal=True)

# --------------------------- Single Mode --------------------------
if mode == "Single":
    col_left, col_right = st.columns([2, 1], gap="large")
    with col_left:
        input_kind = st.tabs(["By ASIN", "By URL"])
        with input_kind[0]:
            domain = st.selectbox("Marketplace", MARKETPLACES, index=MARKETPLACES.index(DEFAULT_DOMAIN), help="Select the Amazon site for your ASIN")
            asin = st.text_input("ASIN", value=DEFAULT_ASIN, placeholder="e.g., B0B17BYJ5R").strip()
        with input_kind[1]:
            url_input = st.text_input("Amazon URL (optional alternative)", value="", placeholder="https://www.amazon.com/dp/ASIN?psc=1").strip()

        col_a, col_b, col_c = st.columns([1,1,1])
        with col_a:
            ensure_psc = st.checkbox("Ensure ?psc=1", value=True)
        with col_b:
            show_raw_json = st.checkbox("Show raw JSON", value=False)
        with col_c:
            cache_ok = st.checkbox("Use cache", value=True, help="Disable to force a fresh call")

        run = st.button("Fetch Product", type="primary")

    with col_right:
        st.info(
            "**Tips**\n\n- Paste either an ASIN or a URL.\n- We'll auto-add `?psc=1` unless you uncheck it.\n- Use the Reviews tabs to filter and export.")

    if run:
        if not api_key:
            st.error("Please enter your Axesso API key in the sidebar.")
            st.stop()

        # Build the URL from whichever input is present
        if url_input:
            target_url = normalize_input_to_url(url_input, domain)
        else:
            target_url = normalize_input_to_url(asin, domain)

        if ensure_psc:
            target_url = add_psc_param(target_url)

        if "amazon." not in target_url:
            st.warning("This doesn't look like an Amazon URL; the API may reject it.")

        if not cache_ok:
            fetch_product.clear()  # drop cache for next call

        with st.spinner("Calling Axesso API..."):
            data, err = fetch_product(target_url, api_key)

        if err:
            st.error(err)
            st.stop()

        # ---------------------- Rendering ----------------------
        # Quick link to Amazon
        st.markdown(f"[Open on Amazon]({target_url})")

        title = data.get("productTitle") or "(No title)"
        rating_text = data.get("productRating")
        rating_value = stars_from_text(rating_text)
        count_review = data.get("countReview")
        price = data.get("price")
        retail_price = data.get("retailPrice")
        sold_by = data.get("soldBy")
        fulfilled_by = data.get("fulfilledBy")
        availability = data.get("warehouseAvailability")
        asin_val = data.get("asin")

        st.subheader(title)
        top_cols = st.columns([1, 2])

        with top_cols[0]:
            main_image = (data.get("mainImage") or {}).get("imageUrl")
            imgs = data.get("imageUrlList") or ([] if not main_image else [main_image])
            if imgs:
                st.image(imgs, use_column_width=True)
            else:
                st.caption("No images available")

        with top_cols[1]:
            if price not in (None, 0, 0.0):
                st.metric("Price", f"${price:,.2f}")
            elif retail_price not in (None, 0, 0.0):
                st.metric("Retail Price", f"${retail_price:,.2f}")
            else:
                st.metric("Price", "N/A")

            meta_cols = st.columns(3)
            meta_cols[0].write(f"**ASIN**\n\n{asin_val or '—'}")
            meta_cols[1].write(f"**Sold by**\n\n{sold_by or '—'}")
            meta_cols[2].write(f"**Fulfilled by**\n\n{fulfilled_by or '—'}")

            if rating_value is not None:
                st.write(f"**Rating**: {rating_value:.1f} / 5  (\~{count_review or 0} reviews)")
            elif rating_text:
                st.write(f"**Rating**: {rating_text}")

            if availability:
                st.success(availability)

        tabs = st.tabs(["Overview", "Details", "Variations", "Reviews", "Global Reviews", "Downloads"])        

        with tabs[0]:
            features = data.get("features") or []
            about = data.get("aboutProduct") or []
            desc = data.get("productDescription")
            if desc:
                st.markdown(desc)
            if features:
                st.markdown("### Features")
                for f in features:
                    st.markdown(f"- {f}")
            if about:
                st.markdown("### About this item")
                for pair in about:
                    st.markdown(f"- **{pair.get('name','')}**: {pair.get('value','')}")

        with tabs[1]:
            details = data.get("productDetails") or []
            if details:
                dt = pd.DataFrame(details)
                dt.columns = ["Name", "Value"]
                st.dataframe(dt, use_container_width=True, hide_index=True)
            else:
                st.caption("No product details found")

        with tabs[2]:
            variations = data.get("variations") or []
            if variations:
                all_rows: List[Dict[str, Any]] = []
                for var in variations:
                    vname = var.get("variationName")
                    for val in var.get("values", []):
                        all_rows.append({
                            "variationName": vname,
                            "value": val.get("value"),
                            "selected": val.get("selected"),
                            "available": val.get("available"),
                            "price": val.get("price"),
                            "asin": val.get("asin"),
                            "dpUrl": val.get("dpUrl"),
                        })
                st.dataframe(pd.DataFrame(all_rows), use_container_width=True, hide_index=True)
            else:
                st.caption("No variations present")

        def render_reviews(area_label: str, reviews: List[Dict[str, Any]]):
            if not reviews:
                st.caption("No reviews to display")
                return

            st.caption(f"Showing {len(reviews)} {area_label.lower()} in API response.")
            col1, col2, col3 = st.columns([1, 1, 2])
            with col1:
                rating_filter = st.selectbox(
                    f"{area_label} — Min stars",
                    options=["All", 5, 4, 3, 2, 1],
                    format_func=lambda x: str(x) if x == "All" else f">= {x}",
                    key=f"minstars_{area_label}",
                )
            with col2:
                search_text = st.text_input(f"{area_label} — Search in text", key=f"search_{area_label}")
            with col3:
                max_rows = st.slider(f"{area_label} — Max rows", 0, 200, 25, 5, key=f"max_{area_label}")

            def _rating_to_float(r: Any) -> Optional[float]:
                if isinstance(r, (int, float)):
                    return float(r)
                if isinstance(r, str):
                    m = re.search(r"([0-9]+(?:\.[0-9])?)", r)
                    if m:
                        try:
                            return float(m.group(1))
                        except Exception:
                            return None
                return None

            filt = []
            for rv in reviews:
                ok = True
                if rating_filter != "All":
                    val = _rating_to_float(rv.get("rating"))
                    ok = ok and (val is not None and val >= float(rating_filter))
                if search_text:
                    blob = " ".join([
                        str(rv.get("title", "")),
                        str(rv.get("text", "")),
                        str(rv.get("userName", "")),
                    ]).lower()
                    ok = ok and (search_text.lower() in blob)
                if ok:
                    filt.append(rv)
                if len(filt) >= max_rows:
                    break

            if not filt:
                st.caption("No reviews match your filters")
                return

            df = pd.DataFrame([
                {
                    "date": rv.get("date"),
                    "rating": rv.get("rating"),
                    "title": rv.get("title"),
                    "user": rv.get("userName"),
                    "text": rv.get("text"),
                    "url": rv.get("url"),
                    "variation": ", ".join(rv.get("variationList", []) or []),
                }
                for rv in filt
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

            st.info("Note: Axesso's dedicated reviews scrapers commonly cap to ~10 pages of Amazon reviews per run (≈100 reviews). This endpoint returns only a subset. For larger pulls, use a reviews-specific endpoint or dataset.")

        with tabs[3]:
            render_reviews("US Reviews", data.get("reviews", []))

        with tabs[4]:
            render_reviews("Global Reviews", data.get("globalReviews", []))

        with tabs[5]:
            c1, c2 = st.columns(2)
            with c1:
                st.download_button(
                    label="Download raw JSON",
                    data=json.dumps(data, indent=2),
                    file_name=f"{asin_val or 'product'}.json",
                    mime="application/json",
                    use_container_width=True,
                )
            with c2:
                df = flatten_product_for_csv(data)
                st.download_button(
                    label="Download flattened CSV",
                    data=df.to_csv(index=False),
                    file_name=f"{asin_val or 'product'}_flattened.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

# ---------------------------- Bulk Mode ---------------------------
else:
    st.markdown("### Bulk fetch (ASINs or URLs)")
    domain_bulk = st.selectbox("Default marketplace (used for bare ASINs)", MARKETPLACES, index=MARKETPLACES.index(DEFAULT_DOMAIN))
    urls_blob = st.text_area(
        "Paste ASINs or Amazon URLs (one per line)",
        height=220,
        placeholder=(
            "B07TCHYBSK\n"
            "https://www.amazon.com/dp/B0B17BYJ5R?psc=1\n"
        ),
    )
    ensure_psc_bulk = st.checkbox("Ensure ?psc=1 on all", value=True)
    cache_ok = st.checkbox("Use cache", value=True)
    run_bulk = st.button("Fetch All", type="primary")

    if run_bulk:
        if not api_key:
            st.error("Please enter your Axesso API key in the sidebar.")
            st.stop()

        if not cache_ok:
            fetch_product.clear()

        raw_lines = [u.strip() for u in urls_blob.splitlines() if u.strip()]
        if not raw_lines:
            st.warning("Please paste at least one ASIN or URL.")
            st.stop()

        targets = [normalize_input_to_url(line, domain_bulk) for line in raw_lines]
        if ensure_psc_bulk:
            targets = [add_psc_param(t) for t in targets]

        results: List[Dict[str, Any]] = []
        errors: List[Tuple[str, str]] = []

        progress = st.progress(0)
        status = st.empty()

        for i, target in enumerate(targets, start=1):
            status.info(f"Fetching {i}/{len(targets)}: {target}")
            data, err = fetch_product(target, api_key)
            if err:
                errors.append((target, err))
            else:
                results.append(data)
            progress.progress(i / len(targets))
            time.sleep(THROTTLE_SECONDS)

        status.empty()

        if results:
            table_rows = []
            for r in results:
                table_rows.append({
                    "asin": r.get("asin"),
                    "title": r.get("productTitle"),
                    "price": r.get("price"),
                    "rating": r.get("productRating"),
                    "reviews": r.get("countReview"),
                    "soldBy": r.get("soldBy"),
                    "fulfilledBy": r.get("fulfilledBy"),
                })
            st.markdown("#### Summary")
            st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

            colj, colc = st.columns(2)
            with colj:
                st.download_button(
                    label="Download all (JSON)",
                    data=json.dumps(results, indent=2),
                    file_name="axesso_products.json",
                    mime="application/json",
                    use_container_width=True,
                )
            with colc:
                frames = [flatten_product_for_csv(r) for r in results]
                csv_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                st.download_button(
                    label="Download flattened CSV",
                    data=csv_df.to_csv(index=False),
                    file_name="axesso_products_flattened.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

        if errors:
            st.markdown("#### Errors")
            for bad_target, msg in errors:
                st.error(f"{bad_target}\n\n{msg}")

# ---------------------------- Footer ------------------------------
st.divider()
st.caption(
    "Built with ❤️ using Streamlit + Axesso. Respect rate limits and your plan's quotas. For large-scale review mining, consider Axesso's reviews endpoints or datasets.")











