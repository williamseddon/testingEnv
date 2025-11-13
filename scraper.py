# streamlit_app.py
# Run with: streamlit run streamlit_app.py

import json
import re
import time
from typing import List

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Amazon Reviews via Axesso APIM", page_icon="⭐", layout="wide")

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
    seen, uniq = set(), []
    for a in found:
        if a not in seen:
            seen.add(a); uniq.append(a)
    return uniq

def parse_reviews_from_payload(payload: dict):
    # tolerate multiple response shapes
    candidates = ["reviews", "reviewList", "items", "data", "productReviews"]
    for k in candidates:
        v = payload.get(k)
        if isinstance(v, list) and (not v or isinstance(v[0], dict)):
            return v
    for k in candidates:
        v = payload.get(k)
        if isinstance(v, dict):
            for kk in candidates:
                vv = v.get(kk)
                if isinstance(vv, list) and (not vv or isinstance(vv[0], dict)):
                    return vv
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

def estimate_calls(asins, fetch_all, start_page, max_pages_cap, lastpage_cache, domain):
    def per_asin_calls(a):
        key = f"{a}:{domain}"
        if fetch_all:
            if isinstance(lastpage_cache.get(key), int):
                lp = int(lastpage_cache[key])
                return max(0, min(int(max_pages_cap), lp - int(start_page) + 1))
            return int(max_pages_cap)
        return 1
    return sum(per_asin_calls(a) for a in asins)

# -----------------------------
# Require secret key (no manual headers allowed)
# -----------------------------

API_KEY = None
try:
    # Prefer APIM secret name; fallback to Axesso key name
    API_KEY = st.secrets.get("OCP_APIM_KEY") or st.secrets.get("AXESSO_API_KEY")
except Exception:
    API_KEY = None

if not API_KEY or not isinstance(API_KEY, str) or not API_KEY.strip():
    st.error(
        "Missing API key. Add it to `.streamlit/secrets.toml`:\n\n"
        '```toml\nOCP_APIM_KEY = "3693f51a06054a57a95af4dc56ed319b"\n```'
    )
    st.stop()

HEADERS = {"Ocp-Apim-Subscription-Key": API_KEY.strip()}

# -----------------------------
# Sidebar
# -----------------------------

with st.sidebar:
    st.markdown("## 🔧 APIM Settings")
    gateway = st.text_input(
        "APIM Gateway Base URL",
        value="https://axesso.azure-api.net",
        help="Use the **gateway** host from the portal’s “Try it”."
    )
    reviews_path = st.text_input(
        "Reviews endpoint path",
        value="/amz/amazon-product-reviews",
        help="Copy the exact operation path from the portal."
    )
    domain_code = st.selectbox("Amazon domainCode", options=SUPPORTED_DOMAINS, index=0)

    st.markdown("### 🔑 Key (from secrets)")
    masked = (API_KEY[:3] + "…") if len(API_KEY) > 3 else "***"
    st.caption(f"Using secret key: `{masked}` (from OCP_APIM_KEY / AXESSO_API_KEY)")

    st.markdown("### ⚙️ Fetch Scope")
    fetch_all = st.checkbox("Fetch **ALL pages**", value=True, help="Loop pages until last page (or cap).")
    start_page = st.number_input("Start page", value=1, min_value=1, step=1)
    max_pages_cap = st.number_input("Max pages (safety cap)", value=30, min_value=1, step=1)
    delay = st.number_input("Delay between requests (sec)", value=0.4, min_value=0.0, step=0.1)

# Per-ASIN pagination hints
if "lastpage_cache" not in st.session_state:
    st.session_state.lastpage_cache = {}

# -----------------------------
# Main UI
# -----------------------------

st.title("⭐ Amazon Reviews (Axesso APIM)")
st.caption("Paste ASINs or Amazon URLs. Configure APIM gateway + path; key is required via secrets.")

asin_input = st.text_area(
    "ASINs or URLs (one per line or mixed)",
    height=140,
    placeholder="B08N5WRWNW\nhttps://www.amazon.com/dp/B0C3H9ABCD\nhttps://www.amazon.com/gp/product/B07XYZ1234"
)
asins = extract_asins(asin_input)
st.write(f"**Detected ASINs:** {len(asins)}")

# Estimate API calls
total_est_calls = estimate_calls(asins, fetch_all, start_page, max_pages_cap, st.session_state.lastpage_cache, domain_code)
st.caption(f"**API call estimate** — Fetch Reviews: **{total_est_calls}** across {len(asins)} ASIN(s)")

c1, c2 = st.columns([1, 1])
with c1:
    go = st.button("📥 Fetch Reviews", use_container_width=True)
with c2:
    clear = st.button("🧹 Clear results", use_container_width=True)

if clear:
    st.session_state.pop("reviews_rows", None)
    st.session_state.pop("reviews_meta", None)

rows = st.session_state.get("reviews_rows", [])
metas = st.session_state.get("reviews_meta", [])

def do_request(asin: str, page: int):
    url = f"{gateway.rstrip('/')}{reviews_path}"
    params = {"asin": asin, "domainCode": domain_code, "page": page}
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    return r

if go:
    if not asins:
        st.error("Please paste at least one ASIN or Amazon product URL.")
    else:
        rows, metas = [], []
        total_calls = max(1, total_est_calls)
        call_i = 0
        progress = st.progress(0)

        for asin in asins:
            if fetch_all:
                key = f"{asin}:{domain_code}"
                if isinstance(st.session_state.lastpage_cache.get(key), int):
                    lp = int(st.session_state.lastpage_cache[key])
                    end_p = min(int(max_pages_cap), lp)
                else:
                    end_p = int(start_page) + int(max_pages_cap) - 1
                pages_to_try = list(range(int(start_page), int(end_p) + 1))
            else:
                pages_to_try = [int(start_page)]

            for p in pages_to_try:
                try:
                    r = do_request(asin, p)
                    call_i += 1
                    progress.progress(min(1.0, call_i / float(total_calls)))

                    if r.status_code != 200:
                        metas.append({"asin": asin, "page": p, "error": f"HTTP {r.status_code}: {r.text[:200]}"})
                        break

                    data = r.json()
                    current_page = data.get("currentPage") or p
                    last_page = data.get("lastPage")
                    if isinstance(last_page, int):
                        st.session_state.lastpage_cache[f"{asin}:{domain_code}"] = last_page

                    meta_fields = ["productTitle", "url", "countReview", "productRating"]
                    meta = {k: data.get(k) for k in meta_fields}
                    meta.update({"asin": asin, "page": current_page})
                    metas.append(meta)

                    items = parse_reviews_from_payload(data)
                    for it in items:
                        rows.append(normalize_review(asin, domain_code, meta, it))

                    if isinstance(last_page, int) and int(current_page) >= int(last_page):
                        break
                    if not items and not isinstance(last_page, int):
                        break

                    if delay and delay > 0:
                        time.sleep(float(delay))

                except Exception as e:
                    metas.append({"asin": asin, "page": p, "error": str(e)})
                    break

        st.session_state["reviews_rows"] = rows
        st.session_state["reviews_meta"] = metas

# Results
if rows:
    st.success(f"Collected {len(rows)} reviews across {len(asins)} ASIN(s).")
    df = pd.DataFrame(rows)

    if "rating" in df.columns:
        def parse_rating(x):
            if x is None: return None
            s = str(x)
            m = re.search(r"(\d+(\.\d+)?)", s)
            return float(m.group(1)) if m else None
        df["rating_num"] = df["rating"].apply(parse_rating)

    st.dataframe(df, use_container_width=True)

    coldl, coldr = st.columns(2)
    with coldl:
        st.download_button(
            "⬇️ Download Reviews (JSON)",
            data=json.dumps(rows, indent=2),
            file_name="amazon_reviews.json",
            mime="application/json",
            use_container_width=True,
        )
    with coldr:
        st.download_button(
            "⬇️ Download Reviews (CSV)",
            data=df.to_csv(index=False),
            file_name="amazon_reviews.csv",
            mime="text/csv",
            use_container_width=True,
        )

if metas:
    dfm = pd.DataFrame(metas)
    with st.expander("Show meta / call log", expanded=False):
        st.dataframe(dfm, use_container_width=True)

st.divider()
st.markdown("### Code Snippets")
first_asin = asins[0] if asins else "B08N5WRWNW"
mask = (API_KEY[:3] + "…") if len(API_KEY) > 3 else "***"

st.markdown("**cURL** (replace values as needed)")
st.code(
    f'''curl -G "{gateway.rstrip('/')}{reviews_path}" -H "Ocp-Apim-Subscription-Key: {mask}"
  --data-urlencode "asin={first_asin}"
  --data-urlencode "domainCode={domain_code}"
  --data-urlencode "page=1"''',
    language="bash"
)

st.markdown("**Python (requests)**")
st.code(
f"""import requests

GATEWAY = "{gateway.rstrip('/')}"
REVIEWS_PATH = "{reviews_path}"
HEADERS = {{"Ocp-Apim-Subscription-Key": "YOUR_KEY"}}
params = {{"asin": "{first_asin}", "domainCode": "{domain_code}", "page": 1}}

r = requests.get(f"{{GATEWAY}}{{REVIEWS_PATH}}", headers=HEADERS, params=params, timeout=30)
print(r.status_code)
print(r.json())""",
language="python"
)
