# streamlit_app.py
# Run: streamlit run streamlit_app.py

import json
import re
import time
from typing import List

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Amazon Reviews (Key-Gated)", page_icon="🔐", layout="wide")

# -----------------------------
# Helpers
# -----------------------------
SUPPORTED_DOMAINS = [
    "com","co.uk","de","fr","it","es","ca","com.mx","com.au","co.jp",
    "nl","se","pl","sg","ae","in","br"
]

ASIN_REGEXES = [
    r"/dp/([A-Z0-9]{10})",
    r"/gp/product/([A-Z0-9]{10})",
    r"/product/([A-Z0-9]{10})",
    r"\b([A-Z0-9]{10})\b",
]

def extract_asins(text: str) -> List[str]:
    text = text or ""
    found = []
    for rx in ASIN_REGEXES:
        for m in re.findall(rx, text, flags=re.IGNORECASE):
            a = m.upper()
            if re.fullmatch(r"[A-Z0-9]{10}", a):
                found.append(a)
    # unique while preserving order
    seen, uniq = set(), []
    for a in found:
        if a not in seen:
            seen.add(a); uniq.append(a)
    return uniq

def parse_reviews_from_payload(payload: dict):
    """Tolerant extractor (Axesso payloads vary by plan/region)."""
    candidates = ["reviews", "reviewList", "items", "data", "productReviews"]
    # direct list
    for k in candidates:
        v = payload.get(k)
        if isinstance(v, list) and (not v or isinstance(v[0], dict)):
            return v
    # nested dict -> list
    for k in candidates:
        v = payload.get(k)
        if isinstance(v, dict):
            for kk in candidates:
                vv = v.get(kk)
                if isinstance(vv, list) and (not vv or isinstance(vv[0], dict)):
                    return vv
    # sometimes under result.reviews
    res = payload.get("result")
    if isinstance(res, dict):
        for k in candidates:
            v = res.get(k)
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
    return []

def normalize_review(asin: str, domain: str, meta: dict, it: dict):
    def g(dct, *keys, default=""):
        for k in keys:
            if isinstance(dct, dict) and k in dct and dct[k] is not None:
                return dct[k]
        return default
    return {
        "asin": asin,
        "domainCode": domain,
        "title": g(it, "title", "reviewTitle"),
        "text": g(it, "text", "content", "reviewText", "body", "comment"),
        "rating": g(it, "rating", "stars", "starRating", "ratingValue"),
        "author": g(it, "author", "reviewer", "user", "reviewerName", "nickname"),
        "date": g(it, "date", "reviewDate", "submissionTime", "createdAt", "time"),
        "helpful": g(it, "helpful", "helpfulCount", "helpfulVotes", "votes", "vote"),
        "productTitle": meta.get("productTitle", ""),
        "url": meta.get("url", ""),
        "page": meta.get("page"),
    }

# -----------------------------
# Sidebar — REQUIRED key gate
# -----------------------------
with st.sidebar:
    st.markdown("## 🔐 Enter API Key (required)")
    auth_mode = st.radio(
        "API type",
        options=["Azure APIM (recommended)", "Direct Axesso"],
        index=0,
        help="APIM uses header 'Ocp-Apim-Subscription-Key'. Direct Axesso uses 'x-api-key'."
    )
    user_key = st.text_input("API key", value="", type="password", placeholder="paste your key here")
    show_key = st.checkbox("Show key")
    if show_key and user_key:
        st.caption(f"Key preview: `{user_key[:3]}…{user_key[-2:] if len(user_key) > 5 else ''}`")

    st.markdown("---")
    st.markdown("### 🔧 Endpoint settings")
    # For APIM, these defaults fit Axesso’s Amazon reviews-by-ASIN operation. Adjust to your tenant if needed.
    gateway_default = "https://axesso.azure-api.net"
    base_url = st.text_input("Gateway base URL", value=gateway_default, help="Use the APIM gateway host (not the developer portal).")
    path_default = "/amz/amazon-product-reviews"
    reviews_path = st.text_input("Reviews path", value=path_default, help="Copy the exact path from the portal ‘Try it’.")
    domain_code = st.selectbox("Amazon domainCode", options=SUPPORTED_DOMAINS, index=0)

# Hard gate — no key, no app.
if not user_key.strip():
    st.title("Amazon Reviews (Key-Gated)")
    st.error("An API key is required to use this app. Enter it in the left sidebar.")
    st.stop()

# Build auth headers based on mode
header_name = "Ocp-Apim-Subscription-Key" if auth_mode.startswith("Azure") else "x-api-key"
HEADERS = {header_name: user_key.strip()}

# -----------------------------
# Main UI — minimal functional demo
# -----------------------------
st.title("Amazon Reviews (Key-Gated)")
st.caption("Paste ASINs or product URLs. Your key is required and used only for this session (not saved).")

asin_input = st.text_area(
    "ASINs or URLs (one per line or mixed)",
    height=140,
    placeholder="B08N5WRWNW\nhttps://www.amazon.com/dp/B0C3H9ABCD\nhttps://www.amazon.com/gp/product/B07XYZ1234"
)
asins = extract_asins(asin_input)
st.write(f"**Detected ASINs:** {len(asins)}")

start_page = st.number_input("Start page", min_value=1, value=1, step=1)
max_pages = st.number_input("Max pages to fetch (cap)", min_value=1, value=3, step=1)
delay = st.number_input("Delay between requests (sec)", min_value=0.0, value=0.3, step=0.1)

col_go, col_clear, col_test = st.columns([1,1,1])
with col_go:
    go = st.button("📥 Fetch Reviews", use_container_width=True)
with col_clear:
    clear = st.button("🧹 Clear results", use_container_width=True)
with col_test:
    test = st.button("🔎 Test Key (quick call)", use_container_width=True)

if clear:
    st.session_state.pop("reviews_rows", None)
    st.session_state.pop("reviews_meta", None)

rows = st.session_state.get("reviews_rows", [])
metas = st.session_state.get("reviews_meta", [])

def do_call(asin: str, page: int):
    url = f"{base_url.rstrip('/')}{reviews_path}"
    params = {"asin": asin, "domainCode": domain_code, "page": page}
    return requests.get(url, headers=HEADERS, params=params, timeout=30)

# Quick key test — 1 call only
if test:
    demo_asin = asins[0] if asins else "B08N5WRWNW"
    try:
        r = do_call(demo_asin, 1)
        ok = 200 <= r.status_code < 300
        st.info(f"Test status: {r.status_code}")
        if ok:
            st.success("Key looks valid for this endpoint.")
        else:
            st.error(f"Call failed. Body (first 300 chars): {r.text[:300]}")
    except Exception as e:
        st.error(f"Request error: {e}")

if go:
    if not asins:
        st.error("Paste at least one ASIN or Amazon product URL.")
    else:
        rows, metas = [], []
        total_calls = len(asins) * int(max_pages)
        call_i = 0
        progress = st.progress(0)

        for asin in asins:
            for p in range(int(start_page), int(start_page) + int(max_pages)):
                try:
                    r = do_call(asin, p)
                    call_i += 1
                    progress.progress(min(1.0, call_i / float(total_calls)))

                    if r.status_code != 200:
                        metas.append({"asin": asin, "page": p, "error": f"HTTP {r.status_code}: {r.text[:200]}"})
                        break

                    data = r.json()
                    current_page = data.get("currentPage", p)
                    meta_fields = ["productTitle", "url", "countReview", "productRating"]
                    meta = {k: data.get(k) for k in meta_fields}
                    meta.update({"asin": asin, "page": current_page})
                    metas.append(meta)

                    items = parse_reviews_from_payload(data)
                    for it in items:
                        rows.append(normalize_review(asin, domain_code, meta, it))

                    # stop if response tells us we're done
                    last_page = data.get("lastPage")
                    if isinstance(last_page, int) and int(current_page) >= int(last_page):
                        break

                    if delay > 0:
                        time.sleep(float(delay))

                except Exception as e:
                    metas.append({"asin": asin, "page": p, "error": str(e)})
                    break

        st.session_state["reviews_rows"] = rows
        st.session_state["reviews_meta"] = metas

# Results
if rows:
    st.success(f"Collected {len(rows)} reviews from {len(asins)} ASIN(s).")
    df = pd.DataFrame(rows)

    # derive numeric rating if possible
    if "rating" in df.columns:
        def rating_to_float(x):
            if x is None: return None
            m = re.search(r"(\d+(\.\d+)?)", str(x))
            return float(m.group(1)) if m else None
        df["rating_num"] = df["rating"].apply(rating_to_float)

    st.dataframe(df, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "⬇️ Download Reviews (JSON)",
            data=json.dumps(rows, indent=2),
            file_name="amazon_reviews.json",
            mime="application/json",
            use_container_width=True,
        )
    with c2:
        st.download_button(
            "⬇️ Download Reviews (CSV)",
            data=df.to_csv(index=False),
            file_name="amazon_reviews.csv",
            mime="text/csv",
            use_container_width=True,
        )

if metas:
    dfm = pd.DataFrame(metas)
    with st.expander("Meta / call log"):
        st.dataframe(dfm, use_container_width=True)

