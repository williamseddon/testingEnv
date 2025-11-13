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

st.set_page_config(page_title="Amazon Product Lookup (Pictures, Details & Reviews) • Axesso APIM",
                   page_icon="🖼️", layout="wide")

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

def tolerant_list(payload: Dict[str, Any], keys: List[str]) -> List[Dict[str, Any]]:
    # Return first list-of-dicts found at known keys (direct or nested in "result")
    for k in keys:
        v = payload.get(k)
        if isinstance(v, list) and (not v or isinstance(v[0], (dict, str))):
            return v
    res = payload.get("result") or {}
    if isinstance(res, dict):
        for k in keys:
            v = res.get(k)
            if isinstance(v, list) and (not v or isinstance(v[0], (dict, str))):
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
    """
    Turn variations[].values[] into a flat table with columns: variationName, value, price, available, selected, asin, dpUrl, imageUrl
    """
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
    # Axesso sometimes places page reviews in "reviews" and/or "globalReviews"
    raw_local = tolerant_list(p, ["reviews"])
    raw_global = tolerant_list(p, ["globalReviews"])
    merged = []
    seen = set()
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

# Session defaults for URL wizard
st.session_state.setdefault("base_url", "https://axesso.azure-api.net")
st.session_state.setdefault("lookup_path", "/amz/amazon-lookup-product")

# =========================
# Sidebar — Key Gate + Endpoint
# =========================
with st.sidebar:
    st.markdown("## 🔐 Enter API Key (required)")
    auth_mode = st.radio(
        "API type",
        options=["Azure APIM (recommended)", "Direct Axesso"],
        index=0,
        help="APIM uses 'Ocp-Apim-Subscription-Key'. Direct Axesso uses 'x-api-key'.",
    )
    user_key = st.text_input("API key", type="password", placeholder="paste your key here")
    show_key = st.checkbox("Show key")
    if show_key and user_key:
        st.caption(f"Key preview: `{user_key[:3]}…{user_key[-2:] if len(user_key) > 5 else ''}`")

    use_query_auth = st.checkbox(
        "Use query auth (`subscription-key`) instead of header (APIM only)",
        value=False,
        help="Some APIM tenants accept/require `?subscription-key=...`.",
    )

    st.markdown("---")
    st.markdown("### 🔧 Endpoint settings")
    with st.expander("🔧 Paste the exact Request URL from the Axesso portal (Try it)"):
        raw_url = st.text_input(
            "Full Request URL",
            placeholder="https://<gateway>.azure-api.net/amz/amazon-lookup-product?url=https://www.amazon.com/dp/B08...%3Fpsc%3D1",
        )
        if st.button("Apply URL"):
            u = urlparse((raw_url or "").strip())
            if not (u.scheme and u.netloc and u.path):
                st.error("That doesn’t look like a valid URL (missing scheme/host/path).")
            else:
                st.session_state.base_url = f"{u.scheme}://{u.netloc}"
                st.session_state.lookup_path = u.path
                st.success(f"Applied → base: {st.session_state.base_url} | path: {st.session_state.lookup_path}")

    base_url = st.text_input(
        "Gateway base URL",
        value=st.session_state.base_url,
        help="Use the APIM gateway host (…azure-api.net), not the developer site.",
    )
    lookup_path = st.text_input(
        "Lookup path",
        value=st.session_state.lookup_path,
        help=(
            "Match the portal path exactly. Two valid splits:\n"
            "A) base=https://...  path=/amz/amazon-lookup-product\n"
            "B) base=https://.../amz  path=/amazon-lookup-product"
        ),
    )

# Hard gate — key required
if not user_key.strip():
    st.title("Amazon Product Lookup (Pictures, Details & Reviews)")
    st.error("An API key is required. Enter it in the left sidebar.")
    st.stop()

# Build auth
header_name = "Ocp-Apim-Subscription-Key" if auth_mode.startswith("Azure") else "x-api-key"
DEFAULT_HEADERS = {header_name: user_key.strip()}

# =========================
# URL Debugger
# =========================
with st.expander("🔎 Request URL debugger"):
    example_url = "https://www.amazon.com/dp/B07TCHYBSK?psc=1"
    final_endpoint = f"{base_url.rstrip('/')}{lookup_path}"
    masked_key = (user_key[:3] + "…") if user_key else "***"
    st.write("Lookup endpoint:", final_endpoint)
    st.code(
        (
            f'curl -G "{final_endpoint}" '
            + (f'--data-urlencode "subscription-key={masked_key}" ' if (auth_mode.startswith("Azure") and use_query_auth) else f'-H "{header_name}: {masked_key}" ')
            + f'--data-urlencode "url={example_url}"'
        ),
        language="bash",
    )

# =========================
# Main UI
# =========================
st.title("🖼️ Amazon Product Lookup (Pictures, Details & Reviews)")
st.caption("Paste **Amazon URLs** or **ASINs**. The app will fetch pictures, details, variations, and reviews.")

tab1, tab2 = st.tabs(["Lookup", "Batch"])

with tab1:
    col_a, col_b = st.columns([2,1])
    with col_a:
        inp = st.text_input(
            "Amazon product URL or ASIN",
            placeholder="https://www.amazon.com/dp/B07TCHYBSK?psc=1  OR  B07TCHYBSK"
        )
    with col_b:
        domain_code = st.selectbox("Domain for ASIN → URL", options=SUPPORTED_DOMAINS, index=0)
        force_psc = st.checkbox("Auto-add ?psc=1 to URL", value=True)

    btn_go = st.button("🔍 Fetch product", use_container_width=True)

with tab2:
    multi_inp = st.text_area(
        "Multiple URLs/ASINs (one per line)",
        height=140,
        placeholder="B0B17BYJ5R\nhttps://www.amazon.com/dp/B07TCHYBSK?psc=1\nB0C3H9ABCD",
    )
    domain_code_multi = st.selectbox("Domain for ASINs → URLs (batch)", options=SUPPORTED_DOMAINS, index=0, key="d_multi")
    force_psc_multi = st.checkbox("Auto-add ?psc=1 to URLs (batch)", value=True, key="psc_multi")
    delay = st.number_input("Delay between requests (sec)", min_value=0.0, value=0.4, step=0.1)
    btn_batch = st.button("📦 Fetch batch", use_container_width=True)

# Share state
if "products" not in st.session_state:
    st.session_state.products = []   # raw payloads
if "failures" not in st.session_state:
    st.session_state.failures = []   # errors

def to_lookup_url_or_fix(s: str, domain: str, force_psc_flag: bool) -> Tuple[str, bool]:
    s = (s or "").strip()
    if not s:
        return "", False
    # If it's an ASIN, convert to dp URL
    if re.fullmatch(r"[A-Za-z0-9]{10}", s):
        return build_dp_url(s.upper(), domain, force_psc_flag), True
    # Otherwise, assume it's a URL
    # Force psc=1 if checked
    return ensure_psc_1(s, force_psc_flag), True

def do_lookup_call(product_url: str) -> requests.Response:
    endpoint = f"{base_url.rstrip('/')}{lookup_path}"
    headers = dict(DEFAULT_HEADERS)
    params = {"url": product_url}

    if auth_mode.startswith("Azure") and use_query_auth:
        params["subscription-key"] = user_key.strip()
        headers = {}  # move auth to query

    return requests.get(endpoint, headers=headers, params=params, timeout=45)

def render_product(p: Dict[str, Any]):
    # Top meta
    core = product_core_fields(p)
    left, right = st.columns([2, 1])

    # Images
    with left:
        main_url = (p.get("mainImage") or {}).get("imageUrl")
        image_list = p.get("imageUrlList") or []
        if main_url:
            st.image(main_url, caption="Main image", use_container_width=True)
        if image_list:
            st.markdown("#### Image Gallery")
            # simple grid
            n = min(12, len(image_list))
            cols = st.columns(3)
            for i in range(n):
                with cols[i % 3]:
                    st.image(image_list[i], use_container_width=True)

        # Videos (if present)
        videos = p.get("videoeUrlList") or []
        if videos:
            with st.expander("🎞️ Videos"):
                for v in videos[:6]:
                    try:
                        st.video(v)
                    except Exception:
                        st.write(v)

    # Core fields + features
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

    # Variations
    variations = p.get("variations") or []
    if variations:
        st.markdown("### Variations")
        var_rows = flatten_variations(variations)
        if var_rows:
            st.dataframe(pd.DataFrame(var_rows), use_container_width=True)

    # Product details (bullets like Dimensions, Model, etc.)
    pdetails = p.get("productDetails") or []
    if pdetails:
        st.markdown("### Product details")
        st.dataframe(pd.DataFrame(flatten_product_details(pdetails)), use_container_width=True)

    # Categories
    cats = p.get("categoriesExtended") or []
    if cats:
        st.markdown("### Categories")
        st.dataframe(pd.DataFrame(cats), use_container_width=True)

    # Reviews
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

# ---- One-off lookup
if tab1 and btn_go:
    st.session_state.products.clear()
    st.session_state.failures.clear()

    url_or_asin = (inp or "").strip()
    product_url, ok = to_lookup_url_or_fix(url_or_asin, domain_code, force_psc)
    if not ok:
        st.error("Enter an Amazon product URL or an ASIN.")
    else:
        with st.spinner("Fetching…"):
            try:
                r = do_lookup_call(product_url)
                if r.status_code != 200:
                    st.error(f"HTTP {r.status_code}: {r.text[:300]}")
                else:
                    data = r.json()
                    # Axesso returns details directly at top-level
                    st.session_state.products.append(data)
            except Exception as e:
                st.session_state.failures.append({"input": url_or_asin, "error": str(e)})

# ---- Batch lookup
if tab2 and btn_batch:
    st.session_state.products.clear()
    st.session_state.failures.clear()

    lines = [ln.strip() for ln in (multi_inp or "").splitlines() if ln.strip()]
    total = len(lines)
    progress = st.progress(0)
    for i, item in enumerate(lines, start=1):
        product_url, ok = to_lookup_url_or_fix(item, domain_code_multi, force_psc_multi)
        if not ok:
            st.session_state.failures.append({"input": item, "error": "Invalid line"})
            progress.progress(i/total)
            continue

        try:
            r = do_lookup_call(product_url)
            if r.status_code != 200:
                st.session_state.failures.append({"input": item, "error": f"HTTP {r.status_code}: {r.text[:200]}"})
            else:
                st.session_state.products.append(r.json())
        except Exception as e:
            st.session_state.failures.append({"input": item, "error": str(e)})

        progress.progress(i/total)
        if delay > 0:
            time.sleep(float(delay))

# =========================
# Render Results / Export
# =========================
if st.session_state.products:
    st.success(f"Fetched {len(st.session_state.products)} product payload(s).")
    for idx, prod in enumerate(st.session_state.products, start=1):
        st.divider()
        st.markdown(f"## Result {idx}")
        render_product(prod)

    # Export products (raw)
    st.markdown("### Export all products (raw)")
    st.download_button(
        "⬇️ Download Products (JSON)",
        data=json.dumps(st.session_state.products, indent=2),
        file_name="products_raw.json",
        mime="application/json",
        use_container_width=True,
    )

# Any failures?
if st.session_state.failures:
    st.warning(f"{len(st.session_state.failures)} request(s) failed.")
    st.dataframe(pd.DataFrame(st.session_state.failures), use_container_width=True)

# Helpful tips
with st.expander("💡 Troubleshooting"):
    st.markdown("""
- **404 Not Found**: Usually means your **base + path** are wrong for your tenant. Use the **Paste Try-it URL** wizard above to auto-fill.
- **Include `?psc=1`**: The lookup API recommends it; use the **Auto-add `?psc=1`** option. If you pass an ASIN, the app builds a proper dp URL for you.
- Use real **product dp URLs** (not category pages, search pages, or truncated links).
- **Auth**: Azure APIM header `Ocp-Apim-Subscription-Key` (or toggle query `subscription-key`). Direct Axesso uses `x-api-key`.
""")

