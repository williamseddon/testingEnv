# streamlit_app.py
# Streamlit Amazon Product Scraper using Axesso Amazon API
# --------------------------------------------------------
# Features
# - Enter a single Amazon product URL (we auto-append ?psc=1 when missing)
# - Optional bulk mode: paste multiple URLs (one per line)
# - Secure API key input (sidebar); supports Streamlit Secrets if set
# - Product card with title, price, rating, seller, fulfillment, availability
# - Image gallery, features, about section, product details
# - Variations table (if present)
# - Reviews viewer with filters (rating, US-only/global toggle, search)
# - Download raw JSON and flattened CSV
# - Basic caching + rate limiting + graceful error handling
#
# How to provide your API key (choose one):
# 1) In the UI: Sidebar → "Axesso API Key" (masked input)
# 2) In ".streamlit/secrets.toml":
#    [axesso]\nAPI_KEY = "YOUR_KEY"
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
DEFAULT_AMZ_URL = "https://www.amazon.com/dp/B07TCHYBSK?psc=1"
REQUEST_TIMEOUT = 30  # seconds
THROTTLE_SECONDS = 1.0  # be nice to the API in bulk mode

# --------------------------- Utilities ----------------------------

def _load_api_key_from_secrets() -> Optional[str]:
    try:
        return st.secrets.get("axesso", {}).get("API_KEY")
    except Exception:
        return None


def add_psc_param(url: str) -> str:
    """Ensure ?psc=1 is present to load the correct variation (Axesso's recommendation)."""
    if not url:
        return url
    if "psc=" in url:
        return url
    # If URL already has query params, append with &; else use ?
    return url + ("&psc=1" if ("?" in url) else "?psc=1")


def normalize_amazon_url(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    # Accept partials like /dp/ASIN and prefix with https://www.amazon.com
    if url.startswith("/dp/"):
        url = "https://www.amazon.com" + url
    # Ensure https scheme for consistency
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    # Quick sanity check for amazon host (not strict – Amazon has many TLDs)
    if "amazon." not in url:
        return url  # let API decide; we still show a warning in UI
    return url


@st.cache_data(show_spinner=False)
def fetch_product(url: str, api_key: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Call Axesso API. Returns (data, error_message)."""
    params = {"url": url}
    headers = {"axesso-api-key": api_key}
    try:
        resp = requests.get(API_ENDPOINT, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        return None, f"Network error: {e}"

    if resp.status_code != 200:
        # Attempt to surface API error payload when available
        try:
            err_payload = resp.json()
        except Exception:
            err_payload = resp.text
        return None, f"HTTP {resp.status_code}: {err_payload}"

    try:
        data = resp.json()
    except Exception as e:
        return None, f"Failed to parse JSON: {e}"

    # Axesso returns 200 even for some non-found cases; inspect responseStatus/message
    status = str(data.get("responseStatus", "")).upper()
    if "NOT_FOUND" in status:
        return None, data.get("responseMessage") or "Product not found"

    return data, None


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
    """Flatten key product fields and one row per review (if present)."""
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

    # If there are reviews, create one row per review; else add a single row
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
st.caption("Enter an Amazon product URL. We'll call the Axesso API and render the results.")

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
    st.subheader("Mode")
    mode = st.radio("Select mode", ["Single URL", "Bulk URLs"], index=0, horizontal=True)

# --------------------------- Single URL ---------------------------
if mode == "Single URL":
    col_left, col_right = st.columns([2, 1], gap="large")
    with col_left:
        url_input = st.text_input("Amazon Product URL", value=DEFAULT_AMZ_URL, placeholder="https://www.amazon.com/dp/ASIN?psc=1")
        col_a, col_b, col_c = st.columns([1,1,1])
        with col_a:
            ensure_psc = st.checkbox("Ensure ?psc=1", value=True, help="Recommended by Axesso to load the correct variation")
        with col_b:
            show_raw_json = st.checkbox("Show raw JSON", value=False)
        with col_c:
            cache_ok = st.checkbox("Use cache", value=True, help="Disable to force a fresh call")

        run = st.button("Fetch Product", type="primary")

    with col_right:
        st.info(
            "**Tips**\n\n- Works for most amazon.* domains.\n- We'll auto-add `?psc=1` unless you uncheck it.\n- Use the reviews filters below to sift through feedback.")

    if run:
        if not api_key:
            st.error("Please enter your Axesso API key in the sidebar.")
            st.stop()

        url = normalize_amazon_url(url_input)
        if ensure_psc:
            url = add_psc_param(url)

        if "amazon." not in url:
            st.warning("This doesn't look like an Amazon URL; the API may reject it.")

        if not cache_ok:
            fetch_product.clear()  # drop cache for next call

        with st.spinner("Calling Axesso API..."):
            data, err = fetch_product(url, api_key)

        if err:
            st.error(err)
            st.stop()

        # ---------------------- Rendering ----------------------
        # Header card
        title = data.get("productTitle") or "(No title)"
        rating_text = data.get("productRating")
        rating_value = stars_from_text(rating_text)
        count_review = data.get("countReview")
        price = data.get("price")
        retail_price = data.get("retailPrice")
        sold_by = data.get("soldBy")
        fulfilled_by = data.get("fulfilledBy")
        availability = data.get("warehouseAvailability")
        asin = data.get("asin")

        st.subheader(title)
        top_cols = st.columns([1, 2])

        with top_cols[0]:
            main_image = (data.get("mainImage") or {}).get("imageUrl")
            imgs = data.get("imageUrlList") or ([] if not main_image else [main_image])
            if imgs:
                st.image(imgs, use_column_width=True, caption=["image" for _ in imgs] if len(imgs) > 1 else None)
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
            meta_cols[0].write(f"**ASIN**\n\n{asin or '—'}")
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
                    file_name=f"{asin or 'product'}.json",
                    mime="application/json",
                    use_container_width=True,
                )
            with c2:
                df = flatten_product_for_csv(data)
                st.download_button(
                    label="Download flattened CSV",
                    data=df.to_csv(index=False),
                    file_name=f"{asin or 'product'}_flattened.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

# ---------------------------- Bulk Mode ---------------------------
else:
    st.markdown("### Bulk fetch")
    urls_blob = st.text_area(
        "Paste Amazon product URLs (one per line)",
        height=200,
        placeholder=(
            "https://www.amazon.com/dp/B07TCHYBSK?psc=1\n"
            "https://www.amazon.com/dp/B0B17BYJ5R?psc=1"
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

        raw_urls = [u.strip() for u in urls_blob.splitlines() if u.strip()]
        if not raw_urls:
            st.warning("Please paste at least one URL.")
            st.stop()

        results: List[Dict[str, Any]] = []
        errors: List[Tuple[str, str]] = []

        progress = st.progress(0)
        status = st.empty()

        for i, u in enumerate(raw_urls, start=1):
            url = normalize_amazon_url(u)
            if ensure_psc_bulk:
                url = add_psc_param(url)

            status.info(f"Fetching {i}/{len(raw_urls)}: {url}")
            data, err = fetch_product(url, api_key)
            if err:
                errors.append((url, err))
            else:
                results.append(data)

            progress.progress(i / len(raw_urls))
            time.sleep(THROTTLE_SECONDS)

        status.empty()

        if results:
            # Show a compact table of key fields across products
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

            # Bulk downloads
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
                # Concatenate flattened frames
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
            for bad_url, msg in errors:
                st.error(f"{bad_url}\n\n{msg}")

# ---------------------------- Footer ------------------------------
st.divider()
st.caption(
    "Built with ❤️ using Streamlit + Axesso. Note: Respect Axesso rate limits and your plan's quotas.")













