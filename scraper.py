# Retry with corrected quoting to avoid SyntaxError: use outer triple-double string and only triple-single inside the app code.
import os, zipfile

project_dir = "/mnt/data/streamlit_axesso_amazon_reviews"
os.makedirs(project_dir, exist_ok=True)

app_py = """# streamlit_app.py
# Run with: streamlit run streamlit_app.py

import json
import re
import time
from urllib.parse import urlencode

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Amazon Reviews via Axesso APIM", page_icon="⭐", layout="wide")

# -----------------------------
# Helpers
# -----------------------------

SUPPORTED_DOMAINS = [
    "com","co.uk","de","fr","it","es","ca","com.mx","com.au","co.jp","nl","se","pl","sg","ae","in","br"
]

ASIN_REGEXES = [
    r"/dp/([A-Z0-9]{10})",
    r"/gp/product/([A-Z0-9]{10})",
    r"/product/([A-Z0-9]{10})",
    r"\\b([A-Z0-9]{10})\\b",
]

def extract_asins(text: str) -> list[str]:
    text = text or ""
    found = []
    for rx in ASIN_REGEXES:
        for m in re.findall(rx, text, flags=re.IGNORECASE):
            a = m.upper()
            if re.fullmatch(r"[A-Z0-9]{10}", a):
                found.append(a)
    # unique, preserve order
    seen = set(); uniq = []
    for a in found:
        if a not in seen:
            seen.add(a); uniq.append(a)
    return uniq

def parse_reviews_from_payload(payload: dict):
    # Try common shapes without assuming a single schema
    candidates = ["reviews","reviewList","items","data","productReviews"]
    # direct list
    for k in candidates:
        v = payload.get(k)
        if isinstance(v, list) and (not v or isinstance(v[0], dict)):
            return v
    # nested object -> list
    for k in candidates:
        v = payload.get(k)
        if isinstance(v, dict):
            for kk in candidates:
                vv = v.get(kk)
                if isinstance(vv, list) and (not vv or isinstance(vv[0], dict)):
                    return vv
    # sometimes under payload["result"]["reviews"]
    res = payload.get("result") or {}
    if isinstance(res, dict):
        for k in candidates:
            v = res.get(k)
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
    return []

def normalize_review(asin: str, domain: str, meta: dict, it: dict):
    def g(*keys, default=""):
        for k in keys:
            if isinstance(it, dict) and k in it and it[k] is not None:
                return it[k]
        return default
    return {
        "asin": asin,
        "domainCode": domain,
        "title": g("title","reviewTitle"),
        "text": g("text","content","reviewText","body","comment"),
        "rating": g("rating","stars","starRating","ratingValue"),
        "author": g("author","reviewer","user","reviewerName","nickname"),
        "date": g("date","reviewDate","submissionTime","createdAt","time"),
        "helpful": g("helpful","helpfulCount","helpfulVotes","votes","vote"),
        # attach a few meta fields if present
        "productTitle": meta.get("productTitle",""),
        "url": meta.get("url",""),
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
# Sidebar
# -----------------------------

with st.sidebar:
    st.markdown("## 🔧 APIM Settings")
    gateway = st.text_input("APIM Gateway Base URL", value="https://axesso.azure-api.net", help="Use the gateway host from the developer portal's 'Try it' panel.")
    reviews_path = st.text_input("Reviews endpoint path", value="/amz/amazon-product-reviews", help="Copy the exact path from the portal.")
    domain_code = st.selectbox("Amazon domainCode", options=SUPPORTED_DOMAINS, index=0)
    st.markdown("### 🔑 Auth")
    # Secrets-based header
    secret_headers = None
    try:
        if "OCP_APIM_KEY" in st.secrets:
            secret_headers = {"Ocp-Apim-Subscription-Key": st.secrets["OCP_APIM_KEY"]}
        elif "AXESSO_API_KEY" in st.secrets:
            # Allow using plain Axesso key via APIM header name
            secret_headers = {"Ocp-Apim-Subscription-Key": st.secrets["AXESSO_API_KEY"]}
    except Exception:
        secret_headers = None

    use_secret = st.checkbox("Use key from secrets (if available)", value=bool(secret_headers))
    if use_secret and secret_headers:
        headers = dict(secret_headers)
        try:
            prev = {k: (v[:3] + "..." if isinstance(v,str) and len(v) > 6 else v) for k,v in headers.items()}
            st.caption(f"Using secrets: {prev}")
        except Exception:
            pass
        headers_raw = ""
    else:
        headers_raw = st.text_area("Headers (JSON)", value='{"Ocp-Apim-Subscription-Key":"YOUR_KEY"}', height=100)
        headers = None
        if headers_raw.strip():
            try:
                headers = json.loads(headers_raw)
                if not isinstance(headers, dict):
                    st.warning("Headers JSON must be an object.")
                    headers = None
            except Exception as e:
                st.warning(f"Could not parse headers JSON: {e}")
                headers = None

    st.markdown("### ⚙️ Fetch Scope")
    fetch_all = st.checkbox("Fetch **ALL pages**", value=True, help="Loops pages for each ASIN until last page (or cap).")
    start_page = st.number_input("Start page", value=1, min_value=1, step=1)
    max_pages_cap = st.number_input("Max pages (safety cap)", value=30, min_value=1, step=1)
    delay = st.number_input("Delay between requests (sec)", value=0.4, min_value=0.0, step=0.1)

# Cache for lastPage per ASIN+domain
if "lastpage_cache" not in st.session_state:
    st.session_state.lastpage_cache = {}

# -----------------------------
# Main UI
# -----------------------------

st.title("⭐ Amazon Reviews (Axesso APIM)")
st.caption("Paste ASINs or Amazon URLs. Configure APIM gateway + reviews path + key; fetch reviews with pagination and download results.")

asin_input = st.text_area("ASINs or URLs (one per line or mixed)", height=140, placeholder="B08N5WRWNW\\nhttps://www.amazon.com/dp/B0C3H9ABCD\\nhttps://www.amazon.com/gp/product/B07XYZ1234")
asins = extract_asins(asin_input)
st.write(f"**Detected ASINs:** {len(asins)}")

# Estimate API calls
total_est_calls = estimate_calls(asins, fetch_all, start_page, max_pages_cap, st.session_state.lastpage_cache, domain_code)
st.caption(f"**API call estimate** — Fetch Reviews: **{total_est_calls}** across {len(asins)} ASIN(s)")

colA, colB = st.columns([1,1])
with colA:
    go = st.button("📥 Fetch Reviews", use_container_width=True)
with colB:
    clear = st.button("🧹 Clear results", use_container_width=True)

if clear:
    st.session_state.pop("reviews_rows", None)
    st.session_state.pop("reviews_meta", None)

rows = st.session_state.get("reviews_rows", [])
metas = st.session_state.get("reviews_meta", [])

def do_request(asin: str, page: int):
    url = f"{gateway.rstrip('/')}{reviews_path}"
    params = {"asin": asin, "domainCode": domain_code, "page": page}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    return r

if go:
    if not asins:
        st.error("Please paste at least one ASIN or Amazon product URL.")
    elif not headers:
        st.error("Please provide your APIM subscription key in headers (or secrets).")
    else:
        rows = []; metas = []
        total_calls = max(1, total_est_calls)
        call_i = 0
        progress = st.progress(0)

        for asin in asins:
            last_page_hint = None
            # Plan pages
            if fetch_all:
                if isinstance(st.session_state.lastpage_cache.get(f"{asin}:{domain_code}"), int):
                    lp = int(st.session_state.lastpage_cache[f"{asin}:{domain_code}"])
                    end_p = min(int(max_pages_cap), lp)
                else:
                    end_p = int(start_page) + int(max_pages_cap) - 1
                pages_to_try = list(range(int(start_page), int(end_p)+1))
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

                    # Capture pagination hints if present
                    current_page = data.get("currentPage") or p
                    last_page = data.get("lastPage")
                    if isinstance(last_page, int):
                        st.session_state.lastpage_cache[f"{asin}:{domain_code}"] = last_page
                        last_page_hint = last_page

                    # Meta per page
                    meta_fields = ["productTitle","url","countReview","productRating"]
                    meta = {k: data.get(k) for k in meta_fields}
                    meta.update({"asin": asin, "page": current_page})
                    metas.append(meta)

                    # Extract review items
                    items = parse_reviews_from_payload(data)
                    for it in items:
                        rows.append(normalize_review(asin, domain_code, meta, it))

                    # Stop conditions
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

# Render results
if rows:
    st.success(f"Collected {len(rows)} reviews across {len(asins)} ASIN(s).")
    df = pd.DataFrame(rows)
    # Basic cleanups
    if "rating" in df.columns:
        # Try casting rating text/numbers to float if possible
        def parse_rating(x):
            if x is None: return None
            s = str(x)
            m = re.search(r"(\\d+(\\.\\d+)?)", s)
            return float(m.group(1)) if m else None
        df["rating_num"] = df["rating"].apply(parse_rating)

    st.dataframe(df, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.download_button("⬇️ Download Reviews (JSON)", data=json.dumps(rows, indent=2), file_name="amazon_reviews.json", mime="application/json", use_container_width=True)
    with c2:
        st.download_button("⬇️ Download Reviews (CSV)", data=df.to_csv(index=False), file_name="amazon_reviews.csv", mime="text/csv", use_container_width=True)

if metas:
    dfm = pd.DataFrame(metas)
    with st.expander("Show meta / call log", expanded=False):
        st.dataframe(dfm, use_container_width=True)

st.divider()
st.markdown("### Code Snippets")
st.markdown("**cURL** (replace values as needed)")
first_asin = asins[0] if asins else "B08N5WRWNW"
curl_headers = ""
# only preview header if provided
# (note: we don't reveal secrets; this is a masked preview)
st.code('''curl -G "{}{}" -H "Ocp-Apim-Subscription-Key: {}"
  --data-urlencode "asin={}"
  --data-urlencode "domainCode={}"
  --data-urlencode "page=1"'''.format(
    gateway.rstrip('/'), reviews_path, ("***" if not isinstance(headers, dict) else (headers.get("Ocp-Apim-Subscription-Key","***")[:3] + "***")),
    first_asin, domain_code
), language="bash")

st.markdown("**Python (requests)**")
st.code('''import requests

GATEWAY = "{}"
REVIEWS_PATH = "{}"
HEADERS = {}
params = {{"asin": "{}", "domainCode": "{}", "page": 1}}

r = requests.get(f"{{GATEWAY}}{{REVIEWS_PATH}}", headers=HEADERS, params=params, timeout=30)
print(r.status_code)
print(r.json())'''.format(
    gateway.rstrip('/'), reviews_path, json.dumps(headers or {"Ocp-Apim-Subscription-Key":"YOUR_KEY"}, indent=2),
    first_asin, domain_code
), language="python")
"""

with open(os.path.join(project_dir, "streamlit_app.py"), "w", encoding="utf-8") as f:
    f.write(app_py)

with open(os.path.join(project_dir, "requirements.txt"), "w", encoding="utf-8") as f:
    f.write("streamlit>=1.31\nrequests>=2.31\npandas>=2.0\n")

with open(os.path.join(project_dir, "README.md"), "w", encoding="utf-8") as f:
    f.write("# Amazon Reviews via Axesso APIM — Streamlit App\n\nSee streamlit_app.py for usage.\n")

zip_path = "/mnt/data/streamlit_axesso_amazon_reviews.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(project_dir):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, project_dir)
            z.write(full, arcname=os.path.join("streamlit_axesso_amazon_reviews", rel))

(project_dir, zip_path)

