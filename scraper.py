# streamlit_app.py
# Run: streamlit run streamlit_app.py

import json
import re
import time
from typing import List, Dict, Any, Optional

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Amazon Reviews (Key-Gated)", page_icon="🔐", layout="wide")

# =========================
# Helpers
# =========================

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

def tolerant_reviews(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract reviews from various common shapes across tenants/tiers.
    """
    keys = ("reviews","reviewList","items","data","productReviews")
    # direct list
    for k in keys:
        v = payload.get(k)
        if isinstance(v, list) and (not v or isinstance(v[0], dict)):
            return v
    # nested dict -> list
    for k in keys:
        v = payload.get(k)
        if isinstance(v, dict):
            for kk in keys:
                vv = v.get(kk)
                if isinstance(vv, list) and (not vv or isinstance(vv[0], dict)):
                    return vv
    # sometimes under "result"
    res = payload.get("result")
    if isinstance(res, dict):
        for k in keys:
            v = res.get(k)
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
    return []

def normalize_review(asin: str, domain: str, meta: Dict[str, Any], it: Dict[str, Any]) -> Dict[str, Any]:
    def g(dct, *keys, default=""):
        for k in keys:
            if isinstance(dct, dict) and k in dct and dct[k] is not None:
                return dct[k]
        return default
    return {
        "asin": asin,
        "domainCode": domain,
        "reviewId": g(it, "reviewId", "id"),
        "title": g(it, "title", "reviewTitle"),
        "text": g(it, "text", "content", "reviewText", "body", "comment"),
        "rating": g(it, "rating", "stars", "starRating", "ratingValue"),
        "author": g(it, "author", "reviewer", "user", "reviewerName", "nickname"),
        "date": g(it, "date", "reviewDate", "submissionTime", "createdAt", "time"),
        "helpful": g(it, "helpful", "helpfulCount", "helpfulVotes", "votes", "vote"),
        # page/meta
        "productTitle": meta.get("productTitle", ""),
        "page": meta.get("page"),
        "sourceUrl": meta.get("url") or "",
    }

def rating_to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    m = re.search(r"(\d+(\.\d+)?)", str(x))
    return float(m.group(1)) if m else None

def backoff_sleep(base: float, attempt: int, max_sleep: float = 8.0):
    time.sleep(min(max_sleep, base * (2 ** max(0, attempt - 1))))

# =========================
# Sidebar — Key Gate
# =========================
with st.sidebar:
    st.markdown("## 🔐 Enter API Key (required)")
    auth_mode = st.radio(
        "API type",
        options=["Azure APIM (recommended)", "Direct Axesso"],
        index=0,
        help="APIM uses header 'Ocp-Apim-Subscription-Key'. Direct Axesso uses 'x-api-key'.",
    )
    user_key = st.text_input("API key", value="", type="password", placeholder="paste your key here")
    show_key = st.checkbox("Show key")
    if show_key and user_key:
        st.caption(f"Key preview: `{user_key[:3]}…{user_key[-2:] if len(user_key) > 5 else ''}`")

    st.markdown("---")
    st.markdown("### 🔧 Endpoint settings")
    base_url = st.text_input(
        "Gateway base URL",
        value="https://axesso.azure-api.net",
        help=(
            "Use the **gateway** (…azure-api.net) you see in the portal’s “Try it”. "
            "Not the developer site (…developer.azure-api.net)."
        ),
    )
    reviews_path = st.text_input(
        "Reviews path",
        value="/amz/amazon-product-reviews",
        help=(
            "Copy the exact path from your portal. Two valid splits:\n"
            "A) base=https://...  path=/amz/amazon-product-reviews\n"
            "B) base=https://.../amz  path=/amazon-product-reviews"
        ),
    )
    domain_code = st.selectbox("Amazon domainCode", options=SUPPORTED_DOMAINS, index=0)

# Hard gate — no key, no app.
if not user_key.strip():
    st.title("Amazon Reviews (Key-Gated)")
    st.error("An API key is required to use this app. Enter it in the left sidebar.")
    st.stop()

# Auth header
header_name = "Ocp-Apim-Subscription-Key" if auth_mode.startswith("Azure") else "x-api-key"
HEADERS = {header_name: user_key.strip()}

# =========================
# URL Debugger
# =========================
with st.expander("🔎 Request URL debugger"):
    final_url = f"{base_url.rstrip('/')}{reviews_path}"
    st.write("Resolved URL:", final_url)
    base_has_amz = "/amz" in base_url.rstrip("/")
    path_starts_amz = reviews_path.startswith("/amz/")
    if base_has_amz and path_starts_amz:
        st.error("Double '/amz' detected. Remove '/amz' from either the base URL or the path.")
    if (not base_has_amz) and (not path_starts_amz):
        st.warning("No '/amz' segment found. Many Axesso operations require '/amz' in either the base or the path.")
    masked_key = "***" if not user_key else (user_key[:3] + "…")
    st.code(
        f'curl -G "{final_url}" '
        f'-H "{header_name}: {masked_key}" '
        f'--data-urlencode "asin=B08N5WRWNW" '
        f'--data-urlencode "domainCode={domain_code}" '
        f'--data-urlencode "page=1"',
        language="bash",
    )

# =========================
# Main UI
# =========================
st.title("Amazon Reviews (Key-Gated)")
st.caption("Paste ASINs or product URLs. Your key stays in-memory for this session only.")

asin_input = st.text_area(
    "ASINs or URLs (one per line or mixed)",
    height=140,
    placeholder="B08N5WRWNW\nhttps://www.amazon.com/dp/B0C3H9ABCD\nhttps://www.amazon.com/gp/product/B07XYZ1234",
)
asins = extract_asins(asin_input)
st.write(f"**Detected ASINs:** {len(asins)}")

col1, col2, col3 = st.columns([1,1,1])
with col1:
    start_page = st.number_input("Start page", min_value=1, value=1, step=1)
with col2:
    max_pages = st.number_input("Max pages (cap)", min_value=1, value=5, step=1)
with col3:
    delay = st.number_input("Delay between calls (sec)", min_value=0.0, value=0.4, step=0.1)

st.caption(f"**API call estimate** — up to **{len(asins) * int(max_pages)}** calls (ASINs × pages).")

b1, b2, b3 = st.columns([1,1,1])
with b1:
    test_btn = st.button("🔎 Test Key (single call)", use_container_width=True)
with b2:
    fetch_btn = st.button("📥 Fetch Reviews", use_container_width=True)
with b3:
    clear_btn = st.button("🧹 Clear results", use_container_width=True)

if clear_btn:
    st.session_state.pop("reviews_rows", None)
    st.session_state.pop("reviews_meta", None)

rows = st.session_state.get("reviews_rows", [])
metas = st.session_state.get("reviews_meta", [])

def do_call(asin: str, page: int) -> requests.Response:
    url = f"{base_url.rstrip('/')}{reviews_path}"
    params = {"asin": asin, "domainCode": domain_code, "page": page}
    return requests.get(url, headers=HEADERS, params=params, timeout=30)

# Quick one-page check to validate key + path
if test_btn:
    demo_asin = asins[0] if asins else "B08N5WRWNW"
    try:
        r = do_call(demo_asin, 1)
        st.info(f"HTTP {r.status_code}")
        if 200 <= r.status_code < 300:
            st.success("Key + endpoint look valid.")
            try:
                payload = r.json()
                revs = tolerant_reviews(payload)
                st.caption(f"This page returned {len(revs)} review(s).")
            except Exception as e:
                st.warning(f"Response not JSON or parse issue: {e}")
        else:
            st.error(f"Call failed. Body (first 300 chars): {r.text[:300]}")
    except Exception as e:
        st.error(f"Request error: {e}")

# Full pagination loop
if fetch_btn:
    if not asins:
        st.error("Paste at least one ASIN or Amazon product URL.")
    else:
        rows, metas = [], []
        total_calls = max(1, len(asins) * int(max_pages))
        call_i = 0
        progress = st.progress(0)

        for asin in asins:
            # bounded forward pagination; stop early if API reports lastPage
            attempt = 0
            for p in range(int(start_page), int(start_page) + int(max_pages)):
                try:
                    r = do_call(asin, p)
                    call_i += 1
                    progress.progress(min(1.0, call_i / float(total_calls)))

                    if r.status_code in (429, 500, 502, 503, 504):
                        attempt += 1
                        backoff_sleep(delay or 0.4, attempt)
                        r = do_call(asin, p)  # retry once per page for brevity

                    if r.status_code != 200:
                        metas.append({"asin": asin, "page": p, "error": f"HTTP {r.status_code}: {r.text[:200]}"})
                        break

                    data = r.json()

                    # Meta (page-level)
                    current_page = int(data.get("currentPage", p))
                    last_page = data.get("lastPage")
                    meta_fields = ["productTitle", "url", "countReview", "productRating"]
                    meta = {k: data.get(k) for k in meta_fields}
                    meta.update({"asin": asin, "page": current_page})
                    metas.append(meta)

                    # Extract & normalize reviews
                    items = tolerant_reviews(data)
                    for it in items:
                        rows.append(normalize_review(asin, domain_code, meta, it))

                    # stop if API indicates end
                    if isinstance(last_page, int) and current_page >= int(last_page):
                        break

                    if delay > 0:
                        time.sleep(float(delay))

                except Exception as e:
                    metas.append({"asin": asin, "page": p, "error": str(e)})
                    break

        st.session_state["reviews_rows"] = rows
        st.session_state["reviews_meta"] = metas

# =========================
# Results & Export
# =========================
if rows:
    st.success(f"Collected {len(rows)} review rows from {len(asins)} ASIN(s).")
    df = pd.DataFrame(rows)

    # derive numeric rating if possible
    if "rating" in df.columns:
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



