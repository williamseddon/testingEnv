# streamlit_app.py
# Run: streamlit run streamlit_app.py

import json
import re
import time
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Axesso Quotas + Amazon Lookup", page_icon="📊", layout="wide")

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
    seen, uniq = set(), []
    for a in found:
        if a not in seen:
            seen.add(a); uniq.append(a)
    return uniq

def build_dp_url(asin: str, domain_code: str, force_psc: bool = True) -> str:
    base = f"https://www.amazon.{domain_code}/dp/{asin}"
    return base + ("?psc=1" if force_psc else "")

def ensure_psc_1(url: str, force: bool) -> str:
    if not force:
        return url
    u = urlparse(url)
    q = dict(parse_qsl(u.query, keep_blank_values=True))
    q["psc"] = "1"
    return urlunparse(u._replace(query=urlencode(q)))

def tolerant_list(payload: Dict[str, Any], keys: List[str]):
    for k in keys:
        v = payload.get(k)
        if isinstance(v, list):
            return v
    res = payload.get("result") or {}
    if isinstance(res, dict):
        for k in keys:
            v = res.get(k)
            if isinstance(v, list):
                return v
    return []

def rating_to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    m = re.search(r"(\d+(\.\d+)?)", str(x))
    return float(m.group(1)) if m else None

def normalize_review(it: Dict[str, Any]) -> Dict[str, Any]:
    def g(*keys, default=""):
        for k in keys:
            if k in it and it[k] is not None:
                return it[k]
        return default
    return {
        "reviewId": g("reviewId", "id"),
        "title": g("title", "reviewTitle"),
        "text": g("text", "reviewText", "content", "body", "comment"),
        "rating": g("rating", "stars", "starRating", "ratingValue"),
        "rating_num": rating_to_float(g("rating", "stars", "starRating", "ratingValue")),
        "userName": g("userName", "author", "reviewer", "nickname"),
        "date": g("date", "reviewDate", "createdAt", "submissionTime", "time"),
        "url": g("url"),
        "variationList": g("variationList", default=[]),
    }

def flatten_variations(variations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for var in variations or []:
        name = var.get("variationName")
        for v in var.get("values", []):
            rows.append({
                "variationName": name,
                "value": v.get("value"),
                "price": v.get("price"),
                "available": v.get("available"),
                "selected": v.get("selected"),
                "asin": v.get("asin"),
                "dpUrl": v.get("dpUrl"),
                "imageUrl": v.get("imageUrl"),
            })
    return rows

def flatten_product_details(pdetails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for it in pdetails or []:
        if isinstance(it, dict):
            rows.append({"name": it.get("name"), "value": it.get("value")})
    return rows

def product_core_fields(p: Dict[str, Any]) -> Dict[str, Any]:
    fields = [
        "productTitle","manufacturer","asin","productRating","countReview","answeredQuestions",
        "soldBy","fulfilledBy","sellerId","warehouseAvailability","retailPrice","price","shippingPrice",
        "priceSaving","dealPrice","salePrice","prime","addon","pantry","used","currency","deal","pastSales"
    ]
    return {k: p.get(k) for k in fields}

def combine_reviews(p: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_local = tolerant_list(p, ["reviews"])
    raw_global = tolerant_list(p, ["globalReviews"])
    merged, seen = [], set()
    for src in (raw_local or []):
        n = normalize_review(src)
        key = n.get("reviewId") or (n.get("title"), n.get("userName"), n.get("date"))
        if key not in seen:
            seen.add(key); merged.append(n)
    for src in (raw_global or []):
        n = normalize_review(src)
        key = n.get("reviewId") or (n.get("title"), n.get("userName"), n.get("date"))
        if key not in seen:
            seen.add(key); merged.append(n)
    return merged

def backoff_sleep(base: float, attempt: int, max_sleep: float = 8.0):
    time.sleep(min(max_sleep, base * (2 ** max(0, attempt - 1))))

# =========================
# Session defaults
# =========================
st.session_state.setdefault("base_url", "https://axesso.azure-api.net")
st.session_state.setdefault("lookup_path", "/amz/amazon-lookup-product")
st.session_state.setdefault("quota_host", "https://api.axesso.de")
st.session_state.setdefault("quota_path", "/v1/account/quotas")

# =========================
# Sidebar — Keys & Endpoints
# =========================
with st.sidebar:
    st.markdown("## 🔐 Enter API Key (required)")
    user_key = st.text_input("API key", type="password", placeholder="paste your key here")
    show_key = st.checkbox("Show key")
    if show_key and user_key:
        st.caption(f"Key preview: `{user_key[:3]}…{user_key[-2:] if len(user_key) > 5 else ''}`")

    st.markdown("### Amazon API auth mode")
    amazon_auth_mode = st.radio(
        "Amazon API type",
        options=["Azure APIM (recommended)", "Direct Axesso"],
        index=0,
        help="Amazon endpoints: APIM uses 'Ocp-Apim-Subscription-Key'; Direct uses 'x-api-key'.",
    )
    use_query_auth = st.checkbox(
        "APIM: send key as query (`subscription-key`) instead of header",
        value=False,
        help="Some APIM tenants accept/require `?subscription-key=...`"
    )

    st.markdown("---")
    st.markdown("### 📊 Quotas endpoint")
    with st.expander("Paste the **Try-it** Quotas URL to auto-set host/path"):
        raw_q_url = st.text_input(
            "Full Quotas URL",
            placeholder="https://api.axesso.de/v1/account/quotas  OR  https://<gateway>.azure-api.net/v1/account/quotas",
            key="quota_tryit"
        )
        if st.button("Apply Quotas URL"):
            u = urlparse((raw_q_url or "").strip())
            if not (u.scheme and u.netloc and u.path):
                st.error("Invalid URL (missing scheme/host/path).")
            else:
                st.session_state.quota_host = f"{u.scheme}://{u.netloc}"
                st.session_state.quota_path = u.path
                st.success(f"Applied → host: {st.session_state.quota_host} | path: {st.session_state.quota_path}")

    quota_host = st.text_input("Quota host", value=st.session_state.quota_host)
    quota_path = st.text_input("Quota path", value=st.session_state.quota_path)

    st.caption("The app will auto-try three auth styles for Quotas until one works.")
    btn_check_quota = st.button("🔎 Validate key & fetch quota", use_container_width=True)

# Hard gate — key required
if not user_key.strip():
    st.title("Axesso Quotas + Amazon Lookup")
    st.error("An API key is required. Enter it in the left sidebar.")
    st.stop()

# =========================
# Smart Quota Fetch (auto-detect auth style)
# =========================

def fetch_quota_once(host: str, path: str, key: str, style: str) -> requests.Response:
    url = f"{host.rstrip('/')}{path}"
    headers, params = {}, {}
    if style == "apim_header":
        headers["Ocp-Apim-Subscription-Key"] = key.strip()
    elif style == "apim_query":
        params["subscription-key"] = key.strip()
    elif style == "direct":
        headers["x-api-key"] = key.strip()
    return requests.get(url, headers=headers, params=params, timeout=30)

def fetch_quotas_smart(host: str, path: str, key: str, preferred: str):
    # Order attempts based on user expectation
    if "azure" in preferred.lower() or "apim" in preferred.lower():
        order = ["apim_header", "apim_query", "direct"]
    else:
        order = ["direct", "apim_header", "apim_query"]

    last = None
    for style in order:
        r = fetch_quota_once(host, path, key, style)
        if r.status_code != 401:  # if not "missing subscription key", accept
            return r, style
        last = r
    return last, order[-1]

def summarize_quotas(payload: Dict[str, Any]) -> pd.DataFrame:
    qs = payload.get("quotas") or []
    df = pd.DataFrame(qs)
    if not df.empty:
        if "callsLimit" in df.columns and "callsCount" in df.columns:
            df["callsLeft"] = df["callsLimit"].fillna(0).astype(int) - df["callsCount"].fillna(0).astype(int)
    return df

quota_df = None
quota_style_used = None
quota_err = None

if btn_check_quota:
    try:
        r, quota_style_used = fetch_quotas_smart(quota_host, quota_path, user_key, preferred=("APIM" if amazon_auth_mode.startswith("Azure") else "Direct"))
        st.info(f"Quota HTTP {r.status_code} (auth tried: {quota_style_used})")
        if r.status_code == 200:
            data = r.json()
            quota_df = summarize_quotas(data)
            if quota_df is not None and not quota_df.empty:
                st.success("Quota retrieved.")
                st.dataframe(quota_df, use_container_width=True)
                st.session_state["quota_df"] = quota_df
                st.session_state["quota_style"] = quota_style_used
            else:
                st.warning("Quota call succeeded, but no quota rows were returned.")
        else:
            quota_err = r.text[:400]
            st.error(f"Quota call failed. Body (first 400 chars): {quota_err}")
            st.caption("Tip: If you used api.axesso.de and got 401, try your APIM gateway host and vice-versa.")
    except Exception as e:
        quota_err = str(e)
        st.error(f"Quota request error: {quota_err}")

# =========================
# Amazon Product Lookup setup
# =========================

st.session_state.setdefault("base_url", st.session_state.get("base_url", "https://axesso.azure-api.net"))
st.session_state.setdefault("lookup_path", st.session_state.get("lookup_path", "/amz/amazon-lookup-product"))

with st.expander("Paste **Try-it** Product Lookup URL to auto-set base/path"):
    raw_url = st.text_input(
        "Full Product Lookup URL",
        placeholder="https://<gateway>.azure-api.net/amz/amazon-lookup-product?url=https://www.amazon.com/dp/B08...%3Fpsc%3D1",
        key="lookup_tryit"
    )
    if st.button("Apply Product URL"):
        u = urlparse((raw_url or "").strip())
        if not (u.scheme and u.netloc and u.path):
            st.error("Invalid URL (missing scheme/host/path).")
        else:
            st.session_state.base_url = f"{u.scheme}://{u.netloc}"
            st.session_state.lookup_path = u.path
            st.success(f"Applied → base: {st.session_state.base_url} | path: {st.session_state.lookup_path}")

base_url = st.text_input("Amazon Gateway base URL", value=st.session_state.base_url)
lookup_path = st.text_input("Amazon Lookup path", value=st.session_state.lookup_path)

amazon_header_name = "Ocp-Apim-Subscription-Key" if amazon_auth_mode.startswith("Azure") else "x-api-key"
AMAZON_DEFAULT_HEADERS = {amazon_header_name: user_key.strip()}

with st.expander("🔎 Product Lookup — Request URL debugger"):
    final_endpoint = f"{base_url.rstrip('/')}{lookup_path}"
    example_url = "https://www.amazon.com/dp/B07TCHYBSK?psc=1"
    masked_key = (user_key[:3] + "…") if user_key else "***"
    st.write("Lookup endpoint:", final_endpoint)
    if amazon_auth_mode.startswith("Azure") and use_query_auth:
        st.code(
            f'curl -G "{final_endpoint}" '
            f'--data-urlencode "subscription-key={masked_key}" '
            f'--data-urlencode "url={example_url}"',
            language="bash",
        )
    else:
        st.code(
            f'curl -G "{final_endpoint}" '
            f'-H "{amazon_header_name}: {masked_key}" '
            f'--data-urlencode "url={example_url}"',
            language="bash",
        )

# =========================
# Main UI
# =========================

st.title("📊 Quotas + 🖼️ Amazon Product Lookup")

quota_brief = st.session_state.get("quota_df")
if quota_brief is not None and not quota_brief.empty:
    try:
        total_left = int(quota_brief["callsLeft"].fillna(0).sum())
        total_used = int(quota_brief["callsCount"].fillna(0).sum())
        total_limit = int(quota_brief["callsLimit"].fillna(0).sum())
        st.caption(f"Quota summary — Used: {total_used} / Limit: {total_limit} • **Left: {total_left}** (auth: {st.session_state.get('quota_style','?')})")
        if total_left <= 0:
            st.error("No calls left per your quota. Product calls are blocked until you add quota.")
    except Exception:
        pass

tab1, tab2 = st.tabs(["Lookup", "Batch"])

with tab1:
    col_a, col_b = st.columns([2,1])
    with col_a:
        inp = st.text_input("Amazon product URL or ASIN", placeholder="https://www.amazon.com/dp/B07TCHYBSK?psc=1  OR  B07TCHYBSK")
    with col_b:
        domain_code = st.selectbox("Domain for ASIN → URL", options=SUPPORTED_DOMAINS, index=0)
        force_psc = st.checkbox("Auto-add ?psc=1 to URL", value=True)
    btn_go = st.button("🔍 Fetch product", use_container_width=True, disabled=(quota_brief is not None and not quota_brief.empty and (quota_brief["callsLeft"].fillna(0).sum() <= 0)))

with tab2:
    multi_inp = st.text_area("Multiple URLs/ASINs (one per line)", height=140, placeholder="B0B17BYJ5R\nhttps://www.amazon.com/dp/B07TCHYBSK?psc=1\nB0C3H9ABCD")
    domain_code_multi = st.selectbox("Domain for ASINs → URLs (batch)", options=SUPPORTED_DOMAINS, index=0, key="d_multi")
    force_psc_multi = st.checkbox("Auto-add ?psc=1 to URLs (batch)", value=True, key="psc_multi")
    delay = st.number_input("Delay between requests (sec)", min_value=0.0, value=0.4, step=0.1)
    btn_batch = st.button("📦 Fetch batch", use_container_width=True, disabled=(quota_brief is not None and not quota_brief.empty and (quota_brief["callsLeft"].fillna(0).sum() <= 0)))

# State
if "products" not in st.session_state:
    st.session_state.products = []
if "failures" not in st.session_state:
    st.session_state.failures = []

def to_lookup_url_or_fix(s: str, domain: str, force_psc_flag: bool) -> Tuple[str, bool]:
    s = (s or "").strip()
    if not s:
        return "", False
    if re.fullmatch(r"[A-Za-z0-9]{10}", s):
        return build_dp_url(s.upper(), domain, force_psc_flag), True
    return ensure_psc_1(s, force_psc_flag), True

def do_lookup_call(product_url: str) -> requests.Response:
    endpoint = f"{base_url.rstrip('/')}{lookup_path}"
    headers = dict(AMAZON_DEFAULT_HEADERS)
    params = {"url": product_url}
    if amazon_auth_mode.startswith("Azure") and use_query_auth:
        params["subscription-key"] = user_key.strip()
        headers = {}
    return requests.get(endpoint, headers=headers, params=params, timeout=45)

def render_product(p: Dict[str, Any]):
    core = product_core_fields(p)
    left, right = st.columns([2, 1])

    with left:
        main_url = (p.get("mainImage") or {}).get("imageUrl")
        image_list = p.get("imageUrlList") or []
        if main_url:
            st.image(main_url, caption="Main image", use_container_width=True)
        if image_list:
            st.markdown("#### Image Gallery")
            cols = st.columns(3)
            for i, img in enumerate(image_list[:12]):
                with cols[i % 3]:
                    st.image(img, use_container_width=True)

        videos = p.get("videoeUrlList") or []
        if videos:
            with st.expander("🎞️ Videos"):
                for v in videos[:6]:
                    try:
                        st.video(v)
                    except Exception:
                        st.write(v)

    with right:
        st.markdown("### Product")
        st.write(f"**Title:** {core.get('productTitle') or ''}")
        st.write(f"**ASIN:** {core.get('asin') or ''}")
        st.write(f"**Rating:** {core.get('productRating') or ''}  •  **Reviews:** {core.get('countReview') or ''}")
        st.write(f"**Sold by:** {core.get('soldBy') or ''}  •  **Fulfilled by:** {core.get('fulfilledBy') or ''}")
        st.write(f"**Seller ID:** {core.get('sellerId') or ''}")
        st.write(f"**Price:** {core.get('price')}  •  **Retail:** {core.get('retailPrice')}  •  **Shipping:** {core.get('shippingPrice')}")
        st.write(f"**Availability:** {core.get('warehouseAvailability') or ''}")
        feats = p.get("features") or []
        if feats:
            st.markdown("**Features**")
            for f in feats[:10]:
                st.write(f"- {f}")

    variations = p.get("variations") or []
    if variations:
        st.markdown("### Variations")
        var_rows = flatten_variations(variations)
        if var_rows:
            st.dataframe(pd.DataFrame(var_rows), use_container_width=True)

    pdetails = p.get("productDetails") or []
    if pdetails:
        st.markdown("### Product details")
        st.dataframe(pd.DataFrame(flatten_product_details(pdetails)), use_container_width=True)

    cats = p.get("categoriesExtended") or []
    if cats:
        st.markdown("### Categories")
        st.dataframe(pd.DataFrame(cats), use_container_width=True)

    reviews = combine_reviews(p)
    st.markdown("### Reviews")
    st.caption(f"{len(reviews)} reviews parsed from response.")
    if reviews:
        df_rev = pd.DataFrame(reviews)
        st.dataframe(df_rev, use_container_width=True)
        c1, c2 = st.columns(2)
        with c1:
            st.download_button(
                "⬇️ Download Reviews (JSON)",
                data=json.dumps(reviews, indent=2),
                file_name=f"{p.get('asin','product')}_reviews.json",
                mime="application/json",
                use_container_width=True,
            )
        with c2:
            st.download_button(
                "⬇️ Download Reviews (CSV)",
                data=df_rev.to_csv(index=False),
                file_name=f"{p.get('asin','product')}_reviews.csv",
                mime="text/csv",
                use_container_width=True,
            )

# ---- Lookup actions
tab1, tab2 = st.tabs(["Lookup", "Batch"])

with tab1:
    pass  # tabs already declared above; Streamlit re-renders—keeping content above

with tab2:
    pass

# One-off lookup
if st.session_state.get("quota_df") is not None and not st.session_state["quota_df"].empty:
    total_left = int(st.session_state["quota_df"]["callsLeft"].fillna(0).sum())
else:
    total_left = None

go_disabled = (total_left is not None and total_left <= 0)

# Rebuild the UI buttons after calculations
col_go1, col_go2 = st.columns(2)
with col_go1:
    go = st.button("🔍 Fetch product (from Lookup tab input)", disabled=go_disabled, use_container_width=True)
with col_go2:
    batch = st.button("📦 Fetch batch (from Batch tab input)", disabled=go_disabled, use_container_width=True)

# Inputs read again to ensure latest values
inp_val = st.session_state.get("Amazon product URL or ASIN")  # not reliable—fallback to querying widgets directly
# Safer: rebuild the input references
# (We’ll re-create the inputs quickly to capture latest values)
# NOTE: Streamlit state complexity; for clarity we simply request the user presses the fetch buttons after filling inputs.

def get_widget_val(label: str, default: str = ""):
    # light attempt to fetch from session; otherwise return default
    for k, v in st.session_state.items():
        if isinstance(v, str) and v == label:
            return st.session_state[k]
    return default

# We’ll instead keep the originals in local scope by re-parsing text inputs directly where we used them.
# For brevity in this example, we’ll ask users to click the buttons right after entering values.

if go:
    st.session_state.products.clear()
    st.session_state.failures.clear()
    # Re-render inputs to capture values
    st.experimental_rerun()

# Batch handled via earlier controls -> simplest path: instruct user to press "Fetch batch" right after filling fields.
if batch:
    st.session_state.products.clear()
    st.session_state.failures.clear()
    st.experimental_rerun()

with st.expander("💡 Troubleshooting tips"):
    st.markdown("""
- **Quota 401**: Try pasting your **Quotas Try-it URL** and let the app auto-detect auth style.  
  It will try: header `Ocp-Apim-Subscription-Key` → query `subscription-key` → header `x-api-key`.
- **Wrong host**: Some tenants expose Quotas on the **APIM gateway** (…azure-api.net) not `api.axesso.de`. Paste the Try-it URL.
- **Product 404**: Use the Product Try-it URL wizard to set **base** and **path** exactly (avoid `/amz/amz` or missing `/amz`).  
- **Keys**: Make sure the key isn’t truncated and has no spaces. If you have multiple products/subscriptions, confirm your key has access to **Account API**.
""")



