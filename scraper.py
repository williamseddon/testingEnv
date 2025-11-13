"""
Streamlit app — Axesso Amazon Product Lookup (API‑key validator + Quotas + ASIN input)

How to run
----------
1) Optional venv
2) Install deps:
   pip install streamlit requests python-dotenv
3) Run:
   streamlit run app.py

Recommended secrets / env (examples):
- In `.streamlit/secrets.toml` (preferred) or env vars:
  AXESSO_API_KEY = "<your key>"
  AXESSO_BASE_URL = "https://api.axesso.de/amz/amazon-lookup-product"
  AXESSO_QUOTAS_URL = "https://api.axesso.de/v1/account/quotas"
  AXESSO_AUTH_HEADER = "Ocp-Apim-Subscription-Key"  # Azure APIM default
  AXESSO_AUTH_PARAM = "subscription-key"            # if using query param auth

Notes
-----
• Many Axesso tenants run behind Azure API Management (APIM). In that case your subscription key
  (Primary/Secondary) typically goes in header **Ocp-Apim-Subscription-Key** or query param
  **subscription-key**.
• Validation prioritizes the Account/Quotas endpoint (200 ⇒ key is valid). A 404 from the
  product-lookup endpoint can simply mean the product URL wasn’t found.
• You can now paste **raw ASINs** (e.g., `B0FKKQ2PH1`) or full Amazon URLs. The app will build
  `https://<marketplace>/dp/<ASIN>?psc=1` automatically.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Tuple
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

import requests
import streamlit as st

# ---------------------------
# Page setup
# ---------------------------
st.set_page_config(
    page_title="Axesso Amazon Lookup — Streamlit",
    page_icon="🛒",
    layout="wide",
)

# ---------------------------
# Config & defaults
# ---------------------------
DEFAULT_BASE_URL = os.getenv("AXESSO_BASE_URL", "https://api.axesso.de/amz/amazon-lookup-product")
DEFAULT_QUOTAS_URL = os.getenv("AXESSO_QUOTAS_URL", "https://api.axesso.de/v1/account/quotas")
DEFAULT_AUTH_HEADER = os.getenv("AXESSO_AUTH_HEADER", "Ocp-Apim-Subscription-Key")
DEFAULT_AUTH_PARAM = os.getenv("AXESSO_AUTH_PARAM", "subscription-key")
DEFAULT_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "25"))

# Prefer Streamlit secrets if present
SECRET_KEY = st.secrets.get("AXESSO_API_KEY", None) if hasattr(st, "secrets") else None
SECRETS_BASE_URL = st.secrets.get("AXESSO_BASE_URL", None) if hasattr(st, "secrets") else None
SECRETS_QUOTAS_URL = st.secrets.get("AXESSO_QUOTAS_URL", None) if hasattr(st, "secrets") else None
SECRETS_AUTH_HEADER = st.secrets.get("AXESSO_AUTH_HEADER", None) if hasattr(st, "secrets") else None
SECRETS_AUTH_PARAM = st.secrets.get("AXESSO_AUTH_PARAM", None) if hasattr(st, "secrets") else None

# Sample values
SAMPLE_ASIN = "B0FKKQ2PH1"
SAMPLE_AMZ_URL = f"https://www.amazon.com/dp/{SAMPLE_ASIN}?psc=1"

# Marketplace selector
MARKET_DOMAINS = {
    "United States": "www.amazon.com",
    "Canada": "www.amazon.ca",
    "United Kingdom": "www.amazon.co.uk",
    "Germany": "www.amazon.de",
    "France": "www.amazon.fr",
    "Italy": "www.amazon.it",
    "Spain": "www.amazon.es",
    "Japan": "www.amazon.co.jp",
    "Australia": "www.amazon.com.au",
}
DEFAULT_MARKET = "United States"

# ---------------------------
# Helpers
# ---------------------------
ASIN_RE = re.compile(r"^[A-Za-z0-9]{10}$")


def ensure_psc_1(raw_url: str) -> str:
    """Ensure URL contains psc=1. Raises ValueError if URL is not absolute."""
    parts = urlparse(raw_url.strip())
    if not parts.netloc:
        raise ValueError("Please provide a full Amazon product URL, including https://…")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["psc"] = "1"
    new_query = urlencode(query, doseq=True)
    return urlunparse((parts.scheme or "https", parts.netloc, parts.path, parts.params, new_query, parts.fragment))


def is_asin(text: str) -> bool:
    return bool(ASIN_RE.fullmatch((text or "").strip()))


def build_url_from_input(text: str, market: str) -> str:
    """Accept raw ASIN or a full URL; return normalized product URL with psc=1."""
    t = (text or "").strip()
    if not t:
        raise ValueError("Please enter an ASIN or an Amazon product URL.")
    if is_asin(t):
        domain = MARKET_DOMAINS.get(market, MARKET_DOMAINS[DEFAULT_MARKET])
        return f"https://{domain}/dp/{t.upper()}?psc=1"
    # Otherwise treat as URL
    return ensure_psc_1(t)


def _auth_apply(headers: Dict[str, str], params: Dict[str, str], *, mode: str, header_name: str, param_name: str, api_key: str) -> None:
    if mode == "Header":
        headers[header_name] = api_key
    else:
        params[param_name] = api_key


def call_axesso_lookup(amazon_url: str, api_key: str, *, base_url: str, auth_mode: str, auth_header: str, auth_param: str, timeout: float) -> Tuple[int, Any, Dict[str, Any]]:
    headers: Dict[str, str] = {"User-Agent": "axesso-streamlit/1.2"}
    params: Dict[str, str] = {"url": amazon_url}
    _auth_apply(headers, params, mode=auth_mode, header_name=auth_header, param_name=auth_param, api_key=api_key)
    try:
        resp = requests.get(base_url, headers=headers, params=params, timeout=timeout)
        debug = {"request": {"url": resp.request.url, "method": resp.request.method, "headers": {k: ("<hidden>" if k.lower() in {auth_header.lower(), "authorization"} else v) for k, v in resp.request.headers.items()}}, "response": {"status_code": resp.status_code, "headers": dict(resp.headers)}}
        try:
            data = resp.json()
        except ValueError:
            data = resp.text
        return resp.status_code, data, debug
    except requests.RequestException as e:
        return 0, {"error": str(e)}, {"exception": repr(e)}


def call_account_quotas(api_key: str, *, quotas_url: str, auth_mode: str, auth_header: str, auth_param: str, timeout: float) -> Tuple[int, Any, Dict[str, Any]]:
    headers: Dict[str, str] = {"User-Agent": "axesso-streamlit/1.2"}
    params: Dict[str, str] = {}
    _auth_apply(headers, params, mode=auth_mode, header_name=auth_header, param_name=auth_param, api_key=api_key)
    try:
        resp = requests.get(quotas_url, headers=headers, params=params, timeout=timeout)
        debug = {"request": {"url": resp.request.url, "method": resp.request.method, "headers": {k: ("<hidden>" if k.lower() in {auth_header.lower(), "authorization"} else v) for k, v in resp.request.headers.items()}}, "response": {"status_code": resp.status_code, "headers": dict(resp.headers)}}
        try:
            data = resp.json()
        except ValueError:
            data = resp.text
        return resp.status_code, data, debug
    except requests.RequestException as e:
        return 0, {"error": str(e)}, {"exception": repr(e)}


def validate_api_key(api_key: str, *, base_url: str, quotas_url: str, auth_mode: str, auth_header: str, auth_param: str, timeout: float) -> Tuple[bool, str]:
    """Validate using Account/Quotas first; fall back to product lookup if ambiguous."""
    q_code, q_data, _ = call_account_quotas(api_key, quotas_url=quotas_url, auth_mode=auth_mode, auth_header=auth_header, auth_param=auth_param, timeout=timeout)
    if q_code == 200:
        return True, "Key validated via Account/Quotas (HTTP 200)."
    if q_code in (401, 403):
        return False, f"Unauthorized to Account/Quotas (HTTP {q_code}). Check key + auth placement/name."

    # Fallback to lookup sample URL — just in case quotas endpoint is tenant-restricted
    l_code, l_data, _ = call_axesso_lookup(SAMPLE_AMZ_URL, api_key, base_url=base_url, auth_mode=auth_mode, auth_header=auth_header, auth_param=auth_param, timeout=timeout)
    if l_code == 200:
        return True, "Key validated via Product Lookup (HTTP 200)."
    if l_code in (401, 403):
        return False, f"Unauthorized to Product Lookup (HTTP {l_code})."
    if l_code == 404:
        return True, "Backend reached (HTTP 404 on sample product) — key likely valid. Try your own product." \
            + ""
    if l_code == 0:
        return False, f"Network error: {l_data.get('error', 'Unknown error')}"
    return False, f"Unexpected validation responses (Quotas {q_code}, Lookup {l_code}). See Raw response for details."


def get_first_image(data: dict) -> str | None:
    main = (data or {}).get("mainImage") or {}
    if isinstance(main, dict) and main.get("imageUrl"):
        return main.get("imageUrl")
    urls = (data or {}).get("imageUrlList") or []
    return urls[0] if urls else None


# ---------------------------
# Session state init
# ---------------------------
if "api_key" not in st.session_state:
    st.session_state.api_key = SECRET_KEY or ""
if "validated" not in st.session_state:
    st.session_state.validated = False
if "base_url" not in st.session_state:
    st.session_state.base_url = SECRETS_BASE_URL or DEFAULT_BASE_URL
if "quotas_url" not in st.session_state:
    st.session_state.quotas_url = SECRETS_QUOTAS_URL or DEFAULT_QUOTAS_URL
if "auth_header" not in st.session_state:
    st.session_state.auth_header = SECRETS_AUTH_HEADER or DEFAULT_AUTH_HEADER
if "auth_param" not in st.session_state:
    st.session_state.auth_param = SECRETS_AUTH_PARAM or DEFAULT_AUTH_PARAM
if "auth_mode" not in st.session_state:
    st.session_state.auth_mode = "Header"  # or "Query parameter"
if "market" not in st.session_state:
    st.session_state.market = DEFAULT_MARKET

# ---------------------------
# Sidebar — About / Tips
# ---------------------------
with st.sidebar:
    st.header("About this app")
    st.write("Validate your Axesso key, check quotas, and fetch Amazon product details from an **ASIN or URL**. We automatically add `?psc=1`.")
    st.caption("Powered by Axesso — Data Service.")
    st.divider()
    st.subheader("Quick Tips")
    st.markdown(
        "- Paste a raw ASIN like `B0FKKQ2PH1` **or** a full product URL.\n"
        "- Pick the marketplace domain if you’re using an ASIN.\n"
        "- If your portal shows Primary/Secondary keys (Azure APIM), use header `Ocp-Apim-Subscription-Key` or query param `subscription-key`."
    )

# ---------------------------
# Main UI
# ---------------------------
st.title("Axesso Amazon Product Lookup")
st.caption("Validate your API key, check quotas, then fetch product details by **ASIN or URL**.")

with st.expander("Advanced settings", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.session_state.base_url = st.text_input("Base API URL (product lookup)", st.session_state.base_url)
        st.session_state.quotas_url = st.text_input("Account/Quotas URL", st.session_state.quotas_url)
        st.session_state.auth_mode = st.selectbox("Auth placement", options=["Header", "Query parameter"], index=0 if st.session_state.auth_mode == "Header" else 1)
    with c2:
        if st.session_state.auth_mode == "Header":
            st.session_state.auth_header = st.text_input("Auth header name", st.session_state.auth_header, help="Azure APIM default: Ocp-Apim-Subscription-Key")
        else:
            st.session_state.auth_param = st.text_input("Auth query param name", st.session_state.auth_param, help="Azure APIM default: subscription-key")

# --- Key entry, validation & quotas ---
key_form = st.form("key_form")
with key_form:
    st.subheader("1) Enter your API key")
    api_key_input = st.text_input("Axesso API key", value=st.session_state.api_key, type="password", help="Stored only in session state. Use Streamlit Secrets for production.")
    # Sanitize obvious paste artifacts (spaces, commas, quotes, newlines)
    sanitized = api_key_input.replace(" ", "").replace(",", "").replace("
", "").replace("	", "").strip().strip("'\"")
    if api_key_input and api_key_input != sanitized:
        st.info("We removed spaces/commas/quotes from the key you pasted.")
    # Quick format hint (Axesso APIM keys are often 32 hex chars)
    if sanitized and not re.fullmatch(r"[0-9A-Fa-f]{32}", sanitized):
        st.caption("Heads up: your key doesn’t look like a 32‑char hex token.")
    kcol1, kcol2, kcol3 = st.columns([1, 1, 1])
    validate_pressed = kcol1.form_submit_button("Validate & Save", use_container_width=True)
    quotas_pressed = kcol2.form_submit_button("Check quotas", use_container_width=True)
    clear_pressed = kcol3.form_submit_button("Clear", use_container_width=True)

if clear_pressed:
    st.session_state.api_key = ""
    st.session_state.validated = False
    st.toast("Cleared key from session.")

if validate_pressed:
    st.session_state.api_key = sanitized
    if not st.session_state.api_key:
        st.error("Please enter an API key.")
    else:
        with st.spinner("Validating (Quotas → Lookup fallback)…"):
            ok, msg = validate_api_key(
                st.session_state.api_key,
                base_url=st.session_state.base_url,
                quotas_url=st.session_state.quotas_url,
                auth_mode=st.session_state.auth_mode,
                auth_header=st.session_state.auth_header,
                auth_param=st.session_state.auth_param,
                timeout=DEFAULT_TIMEOUT,
            )
        if ok:
            st.session_state.validated = True
            st.success(msg)
        else:
            st.session_state.validated = False
            st.warning(msg)

if quotas_pressed:
    st.session_state.api_key = sanitized
    if not st.session_state.api_key:
        st.error("Please enter an API key.")
    else:
        with st.spinner("Fetching quotas…"):
            q_code, q_data, q_debug = call_account_quotas(
                st.session_state.api_key,
                quotas_url=st.session_state.quotas_url,
                auth_mode=st.session_state.auth_mode,
                auth_header=st.session_state.auth_header,
                auth_param=st.session_state.auth_param,
                timeout=DEFAULT_TIMEOUT,
            )
        st.markdown(f"**Quotas HTTP status:** `{q_code}`")
        if q_code == 200 and isinstance(q_data, dict) and isinstance(q_data.get("quotas"), list):
            rows = []
            for q in q_data["quotas"]:
                try:
                    limit = int(q.get("callsLimit", 0))
                    used = int(q.get("callsCount", 0))
                except Exception:
                    limit, used = 0, 0
                remaining = max(limit - used, 0)
                pct = (used / limit) if limit else 0
                rows.append({
                    "subscriptionId": q.get("subscriptionId"),
                    "productId": q.get("productId"),
                    "displayName": q.get("displayName"),
                    "periodStartTime": q.get("periodStartTime"),
                    "periodEndTime": q.get("periodEndTime"),
                    "callsUsed": used,
                    "callsLimit": limit,
                    "callsRemaining": remaining,
                    "utilization": f"{pct:.0%}",
                })
            st.dataframe(rows, use_container_width=True)
            total_limit = sum(int(r["callsLimit"]) for r in rows)
            total_used = sum(int(r["callsUsed"]) for r in rows)
            overall_pct = (total_used / total_limit) if total_limit else 0
            st.progress(min(max(overall_pct, 0.0), 1.0), text=f"Overall utilization: {overall_pct:.1%}")
        else:
            if isinstance(q_data, dict) and q_data.get("message"):
                st.error(q_data.get("message"))
            elif isinstance(q_data, dict) and q_data.get("error"):
                st.error(q_data.get("error"))
            else:
                st.warning("Couldn’t parse quotas payload. See Raw & Debug below.")
        with st.expander("Test this call via cURL", expanded=False):
            if st.session_state.auth_mode == "Header":
                st.code(f"curl -sS '{st.session_state.quotas_url}' -H '{st.session_state.auth_header}: {st.session_state.api_key}'")
            else:
                st.code(f"curl -sS '{st.session_state.quotas_url}?{st.session_state.auth_param}={st.session_state.api_key}'")
        with st.expander("Raw quotas response", expanded=False):
            st.write(q_data)
        with st.expander("Quotas request debug", expanded=False):
            st.json(q_debug)

st.divider()

# --- Lookup section (ASIN or URL) ---
st.subheader("2) Look up a product by **ASIN or URL**")
lookup_disabled = not (st.session_state.api_key)

with st.form("lookup_form", clear_on_submit=False):
    left, right = st.columns([2, 1])
    with left:
        asin_or_url = st.text_input(
            "ASIN or Amazon product URL",
            placeholder=SAMPLE_ASIN,
            help="Paste a 10-char ASIN (e.g., B0FKKQ2PH1) or a full URL.",
            disabled=lookup_disabled,
        )
    with right:
        st.session_state.market = st.selectbox(
            "Marketplace (for ASIN)", list(MARKET_DOMAINS.keys()), index=list(MARKET_DOMAINS.keys()).index(DEFAULT_MARKET), disabled=lookup_disabled,
        )
    cols = st.columns([1, 1, 2])
    submit_lookup = cols[0].form_submit_button("Lookup Product", disabled=lookup_disabled, use_container_width=True)
    sample_clicked = cols[1].form_submit_button("Use sample ASIN", use_container_width=True)

if sample_clicked:
    asin_or_url = SAMPLE_ASIN
    st.info("Sample ASIN inserted. Click ‘Lookup Product’.")

if submit_lookup:
    api_key = st.session_state.api_key or SECRET_KEY or ""
    if not api_key:
        st.error("Enter and validate your API key first.")
    else:
        try:
            built_url = build_url_from_input(asin_or_url or SAMPLE_ASIN, st.session_state.market)
        except ValueError as ve:
            st.error(str(ve))
            built_url = None
        if built_url:
            with st.spinner("Calling Axesso product lookup…"):
                code, data, debug = call_axesso_lookup(
                    built_url,
                    api_key,
                    base_url=st.session_state.base_url,
                    auth_mode=st.session_state.auth_mode,
                    auth_header=st.session_state.auth_header,
                    auth_param=st.session_state.auth_param,
                    timeout=DEFAULT_TIMEOUT,
                )

            # --- Render response ---
            st.markdown(f"**Resolved URL:** `{built_url}`")
            st.markdown(f"**HTTP status:** `{code}`")

            if code in (200, 404) and isinstance(data, (dict, list)):
                if isinstance(data, dict):
                    title = data.get("productTitle") or "(No title)"
                    img = get_first_image(data)
                    asin = data.get("asin") or "—"

                    ctop = st.columns([1, 2, 2])
                    with ctop[0]:
                        if img:
                            st.image(img, caption=asin, use_column_width=True)
                    with ctop[1]:
                        st.markdown(f"### {title}")
                        meta = []
                        if asin: meta.append(f"**ASIN:** {asin}")
                        if data.get("productRating"): meta.append(f"**Rating:** {data.get('productRating')}")
                        if data.get("countReview") is not None: meta.append(f"**Reviews:** {data.get('countReview')}")
                        if data.get("answeredQuestions") is not None: meta.append(f"**Q&A:** {data.get('answeredQuestions')}")
                        st.markdown(" • ".join(meta))

                        econ = []
                        price = data.get("price")
                        if price is not None: econ.append(f"**Price:** {price}")
                        rprice = data.get("retailPrice")
                        if rprice is not None: econ.append(f"**Retail:** {rprice}")
                        ship = data.get("shippingPrice")
                        if ship is not None: econ.append(f"**Shipping:** {ship}")
                        psi = data.get("priceShippingInformation")
                        if psi: econ.append(f"**Shipping info:** {psi}")
                        if econ:
                            st.markdown("<br/>" + " • ".join(econ), unsafe_allow_html=True)

                    with ctop[2]:
                        seller_bits = []
                        if data.get("soldBy"): seller_bits.append(f"**Sold by:** {data['soldBy']}")
                        if data.get("fulfilledBy"): seller_bits.append(f"**Fulfilled by:** {data['fulfilledBy']}")
                        if data.get("sellerId"): seller_bits.append(f"**Seller ID:** {data['sellerId']}")
                        if seller_bits:
                            st.markdown("\n".join(seller_bits))
                        cats = data.get("categories") or []
                        if cats:
                            st.markdown("**Categories:** " + " › ".join(cats))

                    # Feature bullets
                    if data.get("features"):
                        with st.expander("Feature bullets", expanded=False):
                            for i, f in enumerate(data["features"], 1):
                                st.write(f"{i}. {f}")

                    # About product
                    about = data.get("aboutProduct") or []
                    if about:
                        with st.expander("About product", expanded=False):
                            st.dataframe(about, use_container_width=True)

                    # Product details
                    details = data.get("productDetails") or []
                    if details:
                        with st.expander("Product details", expanded=False):
                            st.dataframe(details, use_container_width=True)

                    # Variations
                    variations = data.get("variations") or []
                    if variations:
                        with st.expander("Variations", expanded=False):
                            for v in variations:
                                st.markdown(f"**{v.get('variationName','(variation)')}**")
                                values = v.get("values") or []
                                st.dataframe(values, use_container_width=True)

                    # Reviews (local)
                    reviews = data.get("reviews") or []
                    if reviews:
                        with st.expander("Reviews (local)", expanded=False):
                            st.dataframe([
                                {"date": r.get("date"), "rating": r.get("rating"), "title": r.get("title"), "userName": r.get("userName"), "text": r.get("text"), "variationList": ", ".join(r.get("variationList") or [])}
                                for r in reviews
                            ], use_container_width=True)

                    # Global reviews
                    greviews = data.get("globalReviews") or []
                    if greviews:
                        with st.expander("Global reviews", expanded=False):
                            st.dataframe([
                                {"locale": "/".join(filter(None, [(r.get("locale") or {}).get("language"), (r.get("locale") or {}).get("country")])), "date": r.get("date"), "rating": r.get("rating"), "title": r.get("title"), "userName": r.get("userName"), "text": r.get("text")}
                                for r in greviews
                            ], use_container_width=True)

                    # Review insights
                    ri = (data.get("reviewInsights") or {})
                    if ri:
                        with st.expander("Review insights", expanded=False):
                            banner = ri.get("banner")
                            summary = ri.get("summary")
                            if banner or summary:
                                st.markdown((banner or "") + ("\n\n" + summary if summary else ""))
                            aspects = ri.get("featureAspects") or []
                            if aspects:
                                st.dataframe(aspects, use_container_width=True)

                    # Download raw JSON
                    raw_json = json.dumps(data, indent=2, ensure_ascii=False)
                    st.download_button("Download JSON", data=raw_json, file_name=f"axesso_product_{asin or 'result'}.json", mime="application/json", use_container_width=True)

                else:
                    # 404 case with array payload (error list) or any other shape
                    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("message"):
                        st.warning("Backend reached, but product not found — try another ASIN/marketplace or confirm listing is active.")
                        st.error(data[0].get("message"))
                    else:
                        st.warning("Backend reached, but no product payload returned.")

            else:
                # Handle non-200/404 responses (401, 403, 5xx, etc.)
                if isinstance(data, dict) and data.get("message"):
                    st.error(data.get("message"))
                elif isinstance(data, dict) and data.get("error"):
                    st.error(data.get("error"))
                else:
                    st.error("Lookup failed. See raw response below.")

            # Debug & raw
            with st.expander("Raw response", expanded=False):
                st.write(data)
            with st.expander("Request debug", expanded=False):
                st.json(debug)

# Footer note
st.caption("Note: Don’t commit API keys to source control. Prefer Streamlit Secrets or environment variables.")








