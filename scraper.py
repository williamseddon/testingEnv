"""
Streamlit app — Axesso Amazon Product Lookup (API‑key validator)

How to run
----------
1) Create and activate a venv (optional)
2) Install deps:  
   pip install streamlit requests python-dotenv
3) Run:  
   streamlit run app.py   # or whatever you name this file

Recommended secrets / env:
- In `.streamlit/secrets.toml` (preferred) or env vars:
  AXESSO_API_KEY = "<your key>"
  AXESSO_BASE_URL = "https://api.axesso.de/amz/amazon-lookup-product"
  AXESSO_AUTH_HEADER = "Ocp-Apim-Subscription-Key"  # common on Azure API Management
  AXESSO_AUTH_PARAM = "subscription-key"            # if using query param auth

Notes
-----
• Some Axesso tenants run behind Azure API Management. In that case your subscription keys
  (Primary/Secondary) typically go in the header **Ocp-Apim-Subscription-Key** or as
  the query parameter **subscription-key**.  
• A 404 from the lookup endpoint can simply mean the product/URL wasn’t found; it does not
  necessarily indicate an invalid key. This app treats 404 as “key accepted, product not found”.
• We auto‑add `?psc=1` to Amazon URLs.
"""
from __future__ import annotations

import json
import os
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
DEFAULT_AUTH_HEADER = os.getenv("AXESSO_AUTH_HEADER", "Ocp-Apim-Subscription-Key")
DEFAULT_AUTH_PARAM = os.getenv("AXESSO_AUTH_PARAM", "subscription-key")
DEFAULT_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "25"))

# Prefer Streamlit secrets if present
SECRET_KEY = st.secrets.get("AXESSO_API_KEY", None) if hasattr(st, "secrets") else None
SECRETS_BASE_URL = st.secrets.get("AXESSO_BASE_URL", None) if hasattr(st, "secrets") else None
SECRETS_AUTH_HEADER = st.secrets.get("AXESSO_AUTH_HEADER", None) if hasattr(st, "secrets") else None
SECRETS_AUTH_PARAM = st.secrets.get("AXESSO_AUTH_PARAM", None) if hasattr(st, "secrets") else None

# Known-good sample (from your docs dump)
SAMPLE_AMZ_URL = "https://www.amazon.com/dp/B0B17BYJ5R?psc=1"

# ---------------------------
# Helpers
# ---------------------------
def ensure_psc_1(raw_url: str) -> str:
    """Ensure URL contains psc=1.
    Raises ValueError if URL is missing a netloc (i.e., not absolute).
    """
    parts = urlparse(raw_url.strip())
    if not parts.netloc:
        raise ValueError("Please provide a full Amazon product URL, including https://…")
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["psc"] = "1"
    new_query = urlencode(query, doseq=True)
    return urlunparse(
        (parts.scheme or "https", parts.netloc, parts.path, parts.params, new_query, parts.fragment)
    )


def call_axesso(
    amazon_url: str,
    api_key: str,
    *,
    base_url: str,
    auth_mode: str,
    auth_header: str,
    auth_param: str,
    timeout: float,
) -> Tuple[int, Any, Dict[str, Any]]:
    """Make the lookup call. Returns (status_code, parsed_json_or_text, debug_dict)."""
    headers: Dict[str, str] = {"User-Agent": "axesso-streamlit/1.0"}
    params: Dict[str, str] = {"url": amazon_url}

    if auth_mode == "Header":
        headers[auth_header] = api_key
    else:  # Query parameter mode
        params[auth_param] = api_key

    try:
        resp = requests.get(base_url, headers=headers, params=params, timeout=timeout)
        debug = {
            "request": {
                "url": resp.request.url,
                "method": resp.request.method,
                "headers": {
                    k: ("<hidden>" if k.lower() in {auth_header.lower(), "authorization"} else v)
                    for k, v in resp.request.headers.items()
                },
            },
            "response": {"status_code": resp.status_code, "headers": dict(resp.headers)},
        }
        try:
            data = resp.json()
        except ValueError:
            data = resp.text
        return resp.status_code, data, debug
    except requests.RequestException as e:
        return 0, {"error": str(e)}, {"exception": repr(e)}


def validate_api_key(
    api_key: str,
    *,
    base_url: str,
    auth_mode: str,
    auth_header: str,
    auth_param: str,
    timeout: float,
) -> Tuple[bool, str]:
    """Check the key by calling the endpoint.  
    Treat 401/403 as invalid key/header, 404 as key accepted (product not found), 200 as valid.
    """
    code, data, _ = call_axesso(
        SAMPLE_AMZ_URL,
        api_key,
        base_url=base_url,
        auth_mode=auth_mode,
        auth_header=auth_header,
        auth_param=auth_param,
        timeout=timeout,
    )

    if code == 200:
        return True, "Key validated successfully (HTTP 200)."
    if code in (401, 403):
        return False, f"Unauthorized (HTTP {code}). Check your key and auth placement/name."
    if code == 404:
        return True, "Key appears valid (HTTP 404 from backend — product not found). Try your own product URL next."
    if code == 0:
        return False, f"Network error: {data.get('error', 'Unknown error')}"
    return False, f"Unexpected HTTP {code}. See Raw response for details."


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
if "auth_header" not in st.session_state:
    st.session_state.auth_header = SECRETS_AUTH_HEADER or DEFAULT_AUTH_HEADER
if "auth_param" not in st.session_state:
    st.session_state.auth_param = SECRETS_AUTH_PARAM or DEFAULT_AUTH_PARAM
if "auth_mode" not in st.session_state:
    st.session_state.auth_mode = "Header"  # or "Query parameter"

# ---------------------------
# Sidebar — About / Tips
# ---------------------------
with st.sidebar:
    st.header("About this app")
    st.write(
        "Validate your Axesso key and fetch Amazon product details. We automatically add `?psc=1` "
        "to ensure the correct variation is loaded."
    )
    st.caption("Powered by Axesso — Data Service.")
    st.divider()
    st.subheader("Quick Tips")
    st.markdown(
        "- If your portal shows **Primary/Secondary key** (Azure APIM), use header `Ocp-Apim-Subscription-Key`\n"
        "  or switch to *Query parameter* with name `subscription-key`.\n"
        "- A 404 during validation can be normal — it proves the key reached the backend."
    )

# ---------------------------
# Main UI
# ---------------------------
st.title("Axesso Amazon Product Lookup")
st.caption("Validate your API key, then fetch product details by URL.")

with st.expander("Advanced settings", expanded=False):
    c1, c2 = st.columns(2)
    with c1:
        st.session_state.base_url = st.text_input(
            "Base API URL",
            st.session_state.base_url,
            help="Override the Axesso endpoint if needed.",
        )
        st.session_state.auth_mode = st.selectbox(
            "Auth placement",
            options=["Header", "Query parameter"],
            index=0 if st.session_state.auth_mode == "Header" else 1,
            help="Where to put your API key.",
        )
    with c2:
        if st.session_state.auth_mode == "Header":
            st.session_state.auth_header = st.text_input(
                "Auth header name",
                st.session_state.auth_header,
                help="Common values: Ocp-Apim-Subscription-Key, x-axesso-api-key, X-RapidAPI-Key",
            )
        else:
            st.session_state.auth_param = st.text_input(
                "Auth query param name",
                st.session_state.auth_param,
                help="Common value on Azure APIM: subscription-key",
            )

# --- Key entry & validation ---
key_form = st.form("key_form")
with key_form:
    st.subheader("1) Enter your API key")
    api_key_input = st.text_input(
        "Axesso API key",
        value=st.session_state.api_key,
        type="password",
        help="Stored only in session (page) memory. Use Streamlit Secrets for production.",
    )
    kcol1, kcol2, _ = st.columns([1, 1, 2])
    validate_pressed = kcol1.form_submit_button("Validate & Save", use_container_width=True)
    clear_pressed = kcol2.form_submit_button("Clear", use_container_width=True)

if clear_pressed:
    st.session_state.api_key = ""
    st.session_state.validated = False
    st.toast("Cleared key from session.")

if validate_pressed:
    st.session_state.api_key = api_key_input.strip()
    if not st.session_state.api_key:
        st.error("Please enter an API key.")
    else:
        with st.spinner("Validating key against Axesso…"):
            ok, msg = validate_api_key(
                st.session_state.api_key,
                base_url=st.session_state.base_url,
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

st.divider()

# --- Lookup section ---
st.subheader("2) Look up a product by URL")
lookup_disabled = not (st.session_state.api_key)

with st.form("lookup_form", clear_on_submit=False):
    url_input = st.text_input(
        "Amazon product URL",
        placeholder=SAMPLE_AMZ_URL,
        help="We’ll add `?psc=1` if missing.",
        disabled=lookup_disabled,
    )
    cols = st.columns([1, 1, 2])
    submit_lookup = cols[0].form_submit_button("Lookup Product", disabled=lookup_disabled, use_container_width=True)
    sample_clicked = cols[1].form_submit_button("Use sample", use_container_width=True)

if sample_clicked:
    url_input = SAMPLE_AMZ_URL
    st.info("Sample URL inserted. Click ‘Lookup Product’.")

if submit_lookup:
    api_key = st.session_state.api_key or SECRET_KEY or ""
    if not api_key:
        st.error("Enter and validate your API key first.")
    else:
        try:
            ensured = ensure_psc_1(url_input or SAMPLE_AMZ_URL)
        except ValueError as ve:
            st.error(str(ve))
            ensured = None
        if ensured:
            with st.spinner("Calling Axesso…"):
                code, data, debug = call_axesso(
                    ensured,
                    api_key,
                    base_url=st.session_state.base_url,
                    auth_mode=st.session_state.auth_mode,
                    auth_header=st.session_state.auth_header,
                    auth_param=st.session_state.auth_param,
                    timeout=DEFAULT_TIMEOUT,
                )

            # --- Render response ---
            result_container = st.container()
            with result_container:
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

                        # About product (name/value)
                        about = data.get("aboutProduct") or []
                        if about:
                            with st.expander("About product", expanded=False):
                                st.dataframe(about, use_container_width=True)

                        # Product details (name/value)
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
                                st.dataframe(
                                    [
                                        {
                                            "date": r.get("date"),
                                            "rating": r.get("rating"),
                                            "title": r.get("title"),
                                            "userName": r.get("userName"),
                                            "text": r.get("text"),
                                            "variationList": ", ".join(r.get("variationList") or []),
                                        }
                                        for r in reviews
                                    ],
                                    use_container_width=True,
                                )

                        # Global reviews
                        greviews = data.get("globalReviews") or []
                        if greviews:
                            with st.expander("Global reviews", expanded=False):
                                st.dataframe(
                                    [
                                        {
                                            "locale": "/".join(
                                                filter(
                                                    None,
                                                    [
                                                        (r.get("locale") or {}).get("language"),
                                                        (r.get("locale") or {}).get("country"),
                                                    ],
                                                )
                                            ),
                                            "date": r.get("date"),
                                            "rating": r.get("rating"),
                                            "title": r.get("title"),
                                            "userName": r.get("userName"),
                                            "text": r.get("text"),
                                        }
                                        for r in greviews
                                    ],
                                    use_container_width=True,
                                )

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
                        st.download_button(
                            "Download JSON",
                            data=raw_json,
                            file_name=f"axesso_product_{asin or 'result'}.json",
                            mime="application/json",
                            use_container_width=True,
                        )

                    else:
                        # 404 case with array payload (error list) or any other shape
                        if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("message"):
                            st.warning("Backend reached, but product not found — try another Amazon URL.")
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
st.caption(
    "Note: Don’t commit API keys to source control. Prefer Streamlit Secrets or environment variables."
)






