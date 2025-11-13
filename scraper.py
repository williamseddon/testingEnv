# streamlit_app.py
# Run: streamlit run streamlit_app.py

import json
import re
import time
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Axesso: API Setup → Quotas → Amazon Lookup", page_icon="✅", layout="wide")

# =============== Utilities ===============

SUPPORTED_DOMAINS = [
    "com","co.uk","de","fr","it","es","ca","com.mx","com.au","co.jp",
    "nl","se","pl","sg","ae","in","br"
]

ASIN_PATTERNS = [
    r"/dp/([A-Z0-9]{10})",
    r"/gp/product/([A-Z0-9]{10})",
    r"/product/([A-Z0-9]{10})",
    r"\b([A-Z0-9]{10})\b",
]

def extract_asins(text: str) -> List[str]:
    text = text or ""
    found = []
    for rx in ASIN_PATTERNS:
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

def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s: return None
    try:
        # Accept Z suffix
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except Exception:
        return None

def window_status(now_utc: datetime, start: Optional[datetime], end: Optional[datetime]) -> str:
    if start and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if start and now_utc < start:
        return f"⏳ Subscription window not active yet. Starts {start.isoformat()}"
    if end and now_utc > end:
        return f"⛔ Subscription window ended {end.isoformat()}"
    return "✅ Subscription window appears active."

def tolerant_reviews_block(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Tries several common keys
    for k in ("reviews","reviewList","items","data","productReviews"):
        v = payload.get(k)
        if isinstance(v, list): return v
    res = payload.get("result") or {}
    for k in ("reviews","reviewList","items","data","productReviews"):
        v = res.get(k)
        if isinstance(v, list): return v
    return []

def normalize_review(it: Dict[str, Any]) -> Dict[str, Any]:
    def g(*keys, default=""):
        for k in keys:
            if k in it and it[k] is not None:
                return it[k]
        return default
    # numeric rating if possible
    rt = g("rating", "stars", "starRating", "ratingValue")
    m = re.search(r"(\d+(\.\d+)?)", str(rt)) if rt else None
    rnum = float(m.group(1)) if m else None
    return {
        "reviewId": g("reviewId", "id"),
        "title": g("title", "reviewTitle"),
        "text": g("text", "reviewText", "content", "body", "comment"),
        "rating": rt, "rating_num": rnum,
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

# =============== FRONT & CENTER: API SETUP ===============

st.title("✅ Axesso API Setup → Quotas → Amazon Lookup")

with st.container():
    st.markdown("### 1) Paste your API key")
    key = st.text_input("API key (Primary or Secondary)", type="password", placeholder="paste your key here")
    show = st.checkbox("Show key")
    if show and key:
        st.caption(f"Key preview: `{key[:4]}…{key[-4:] if len(key)>8 else ''}`")

    st.markdown("### 2) Where is your **Quotas** endpoint?")
    st.caption("Defaults for Axesso Account API. If your portal shows a different host/path, paste it below.")
    quota_host = st.text_input("Quotas host", value="https://api.axesso.de")
    quota_path = st.text_input("Quotas path", value="/v1/account/quotas")

    st.markdown("**Validation strategy:** The app will auto-try these auth styles until one works:")
    st.write("• APIM header `Ocp-Apim-Subscription-Key` → APIM query `subscription-key` → Direct header `x-api-key`")

    go_validate = st.button("🔎 Validate key & show quota", type="primary", use_container_width=True)

# Validate key
quota_df = None
quota_msg = ""
auth_used = None

def do_quota(host: str, path: str, key: str, style: str) -> requests.Response:
    url = f"{host.rstrip('/')}{path}"
    headers, params = {}, {}
    if style == "apim_header":
        headers["Ocp-Apim-Subscription-Key"] = key.strip()
    elif style == "apim_query":
        params["subscription-key"] = key.strip()
    elif style == "direct":
        headers["x-api-key"] = key.strip()
    return requests.get(url, headers=headers, params=params, timeout=30)

if go_validate:
    if not key.strip():
        st.error("Please paste your API key first.")
    else:
        tried = []
        for style in ("apim_header", "apim_query", "direct"):
            try:
                r = do_quota(quota_host, quota_path, key, style)
                tried.append((style, r.status_code))
                if r.status_code == 200:
                    auth_used = style
                    data = r.json()
                    qs = data.get("quotas") or []
                    quota_df = pd.DataFrame(qs)
                    if not quota_df.empty:
                        # compute callsLeft and window status
                        if all(c in quota_df.columns for c in ("callsLimit","callsCount")):
                            quota_df["callsLeft"] = quota_df["callsLimit"].fillna(0).astype(int) - quota_df["callsCount"].fillna(0).astype(int)
                        st.success(f"Quota OK (auth: {auth_used}).")
                        st.dataframe(quota_df, use_container_width=True)

                        # Window check
                        now = datetime.now(timezone.utc)
                        ps = parse_iso(qs[0].get("periodStartTime") if qs else None)
                        pe = parse_iso(qs[0].get("periodEndTime") if qs else None)
                        st.info(window_status(now, ps, pe))

                        total_left = int(quota_df["callsLeft"].fillna(0).sum()) if "callsLeft" in quota_df else None
                        if total_left is not None:
                            st.caption(f"**Calls left (total across subscriptions): {total_left}**")
                        break
                    else:
                        st.warning("Quota call succeeded, but returned no rows. Your key may not include Account API access.")
                        auth_used = style
                        break
                # If 401/403/404, keep trying other styles
            except Exception as e:
                tried.append((style, f"error: {e}"))
        else:
            st.error("Could not validate your key against the Quotas API.")
            st.caption(f"Tried: {tried}")

# =============== PRODUCT LOOKUP (unlocked after key validation or if you want to try anyway) ===============

st.markdown("---")
st.subheader("2) Amazon Product Lookup (Pictures, Details & Reviews)")

colA, colB, colC = st.columns([2,1,1])

with colA:
    st.markdown("**a) Endpoint**")
    st.caption("Paste the **exact Try-it Request URL** from your portal to avoid path errors.")
    tri = st.text_input(
        "Try-it Request URL (optional)",
        placeholder="https://<gateway>.azure-api.net/amz/amazon-lookup-product?url=...",
        key="tryit_url"
    )
    if st.button("Apply Try-it URL"):
        u = urlparse((tri or "").strip())
        if u.scheme and u.netloc and u.path:
            st.session_state["prod_base"] = f"{u.scheme}://{u.netloc}"
            st.session_state["prod_path"] = u.path
            st.success(f"Applied → base: {st.session_state['prod_base']} | path: {st.session_state['prod_path']}")
        else:
            st.error("That doesn't look like a valid URL (missing scheme/host/path).")

with colB:
    base_default = st.session_state.get("prod_base", "https://axesso.azure-api.net")
    st.text_input("Product base URL", value=base_default, key="prod_base_in")
with colC:
    path_default = st.session_state.get("prod_path", "/amz/amazon-lookup-product")
    st.text_input("Product path", value=path_default, key="prod_path_in")

# Auth style for product (we’ll try header first; optional query toggle)
col1, col2, col3 = st.columns([1,1,1])
with col1:
    product_auth = st.selectbox("Auth style", ["APIM header", "APIM query (?subscription-key=)", "Direct (x-api-key)"])
with col2:
    domain_code = st.selectbox("Amazon domainCode (for ASIN→URL)", SUPPORTED_DOMAINS, index=0)
with col3:
    force_psc = st.checkbox("Auto-add ?psc=1", value=True)

st.markdown("**b) Enter ASIN or full product URL**")
item = st.text_input("ASIN or URL", placeholder="B07TCHYBSK  or  https://www.amazon.com/dp/B07TCHYBSK?psc=1")
fetch_btn = st.button("📥 Fetch product", use_container_width=True)

def do_product_lookup(base: str, path: str, key: str, auth: str, product_url: str) -> Tuple[int, str, Optional[Dict[str, Any]]]:
    url = f"{base.rstrip('/')}{path}"
    headers, params = {}, {"url": product_url}
    if auth.startswith("APIM header"):
        headers["Ocp-Apim-Subscription-Key"] = key.strip()
    elif auth.startswith("APIM query"):
        params["subscription-key"] = key.strip()
    else:
        headers["x-api-key"] = key.strip()
    r = requests.get(url, headers=headers, params=params, timeout=45)
    try:
        data = r.json()
    except Exception:
        data = None
    return r.status_code, r.text[:400], data

def render_product(payload: Dict[str, Any]):
    # Images + headline
    left, right = st.columns([2,1])
    with left:
        main = (payload.get("mainImage") or {}).get("imageUrl")
        imgs = payload.get("imageUrlList") or []
        if main:
            st.image(main, caption="Main image", use_container_width=True)
        if imgs:
            st.markdown("#### Gallery")
            cols = st.columns(3)
            for i, img in enumerate(imgs[:12]):
                with cols[i % 3]:
                    st.image(img, use_container_width=True)
        vids = payload.get("videoeUrlList") or []
        if vids:
            with st.expander("🎞️ Videos"):
                for v in vids[:6]:
                    try: st.video(v)
                    except: st.write(v)
    with right:
        st.markdown("### Product")
        for k in ("productTitle","asin","productRating","countReview","soldBy","fulfilledBy","sellerId","price","retailPrice","shippingPrice","warehouseAvailability"):
            if payload.get(k) not in (None, "", []):
                st.write(f"**{k}**: {payload.get(k)}")
        feats = payload.get("features") or []
        if feats:
            st.markdown("**Features**")
            for f in feats[:10]:
                st.write(f"- {f}")

    # Variations
    vars_ = payload.get("variations") or []
    if vars_:
        flat = flatten_variations(vars_)
        if flat:
            st.markdown("### Variations")
            st.dataframe(pd.DataFrame(flat), use_container_width=True)

    # Product details
    pdet = payload.get("productDetails") or []
    if pdet:
        dfp = pd.DataFrame([{"name": d.get("name"), "value": d.get("value")} for d in pdet if isinstance(d, dict)])
        st.markdown("### Product details")
        st.dataframe(dfp, use_container_width=True)

    # Reviews (local + global)
    reviews = []
    for block_key in ("reviews","globalReviews"):
        block = payload.get(block_key) or []
        if isinstance(block, list):
            for it in block:
                reviews.append(normalize_review(it))
    st.markdown("### Reviews")
    st.caption(f"{len(reviews)} review rows parsed.")
    if reviews:
        dfr = pd.DataFrame(reviews)
        st.dataframe(dfr, use_container_width=True)
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("⬇️ Reviews (JSON)", data=json.dumps(reviews, indent=2),
                               file_name=f"{payload.get('asin','product')}_reviews.json",
                               mime="application/json", use_container_width=True)
        with c2:
            st.download_button("⬇️ Reviews (CSV)", data=dfr.to_csv(index=False),
                               file_name=f"{payload.get('asin','product')}_reviews.csv",
                               mime="text/csv", use_container_width=True)

# Run product fetch
if fetch_btn:
    if not key.strip():
        st.error("Paste your API key in Step 1 first.")
    else:
        # Make a proper product URL
        s = (item or "").strip()
        if not s:
            st.error("Enter an ASIN or a product URL.")
        else:
            if re.fullmatch(r"[A-Za-z0-9]{10}", s):
                product_url = build_dp_url(s.upper(), domain_code, force_psc)
            else:
                product_url = ensure_psc_1(s, force_psc)

            base = st.session_state.get("prod_base_in") or st.session_state.get("prod_base") or "https://axesso.azure-api.net"
            path = st.session_state.get("prod_path_in") or st.session_state.get("prod_path") or "/amz/amazon-lookup-product"

            with st.expander("🔎 Request URL debugger"):
                st.write("Endpoint:", f"{base.rstrip('/')}{path}")
                mk = key[:4] + "…" if key else "***"
                if product_auth.startswith("APIM header"):
                    st.code(f'curl -G "{base.rstrip("/")}{path}" -H "Ocp-Apim-Subscription-Key: {mk}" --data-urlencode "url={product_url}"', language="bash")
                elif product_auth.startswith("APIM query"):
                    st.code(f'curl -G "{base.rstrip("/")}{path}" --data-urlencode "subscription-key={mk}" --data-urlencode "url={product_url}"', language="bash")
                else:
                    st.code(f'curl -G "{base.rstrip("/")}{path}" -H "x-api-key: {mk}" --data-urlencode "url={product_url}"', language="bash")

            code, body, data = do_product_lookup(base, path, key, product_auth, product_url)
            st.info(f"HTTP {code}")
            if code == 200 and isinstance(data, dict):
                st.success("Product retrieved.")
                render_product(data)
            else:
                # Friendly diagnoses
                if code in (401, 403):
                    st.error("Auth rejected (401/403). This often means wrong auth style or your subscription window isn’t active yet.")
                    st.caption("Try switching auth style (APIM header/query vs Direct), and confirm your subscription **Start date** has already begun.")
                elif code == 404:
                    st.error("404 Not Found: usually a **base/path** mismatch.")
                    st.caption("Paste the **exact Try-it URL** from your portal and click Apply; avoid double `/amz` or missing `/amz`.")
                else:
                    st.error(f"Call failed. Body (first 400 chars): {body}")

# =============== Notes / Why it might not work ===============

with st.expander("🛠 Why keys fail & quick fixes"):
    st.markdown("""
- **Subscription not active yet**: If your portal shows **“Started on 11/13/2025”** and today is 11/12/2025, your window hasn't opened.  
  Until the start time passes, APIM will return **401** even with a correct key.
- **Wrong host**:  
  • **Quotas** are typically on `https://api.axesso.de/v1/account/quotas`.  
  • **Amazon endpoints** are often on an **APIM gateway** like `https://axesso.azure-api.net`.  
  Use the **Try-it URL** from your portal to set base/path exactly.
- **Wrong auth style**:  
  • APIM expects `Ocp-Apim-Subscription-Key` **header** or `subscription-key` **query**.  
  • Direct Axesso uses **`x-api-key`** header.  
  If you send the wrong one, you’ll get a 401 with “missing subscription key”.
- **Path mismatch** (`/amz` gotchas):  
  Either put `/amz` on the **base** or on the **path**, **not both**.  
  Example: `base=https://…` + `path=/amz/amazon-lookup-product` **OR** `base=https://…/amz` + `path=/amazon-lookup-product`.
- **Product URL**: Use a **dp URL** (or just paste an ASIN). The app appends `?psc=1` to stabilize the selected variation.
""")




