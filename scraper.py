# streamlit_app.py
# Run with: streamlit run streamlit_app.py

import json
import time
from urllib.parse import urlencode

import pandas as pd
import requests
import streamlit as st

# -----------------------------
# Page setup
# -----------------------------
st.set_page_config(
    page_title="Amazon ASIN Finder (Axesso)",
    page_icon="🦈",
    layout="wide",
)

# -----------------------------
# Constants & helpers
# -----------------------------
DEFAULT_BASE_URL = "http://api.axesso.de/amz/amazon-seller-products"

SUPPORTED_DOMAINS = [
    "com", "co.uk", "de", "fr", "it", "es", "ca", "com.mx", "com.au", "co.jp",
    "nl", "se", "pl", "sg", "ae", "in", "br"
]

AMAZON_HOSTS = {
    "com": "amazon.com",
    "co.uk": "amazon.co.uk",
    "de": "amazon.de",
    "fr": "amazon.fr",
    "it": "amazon.it",
    "es": "amazon.es",
    "ca": "amazon.ca",
    "com.mx": "amazon.com.mx",
    "com.au": "amazon.com.au",
    "co.jp": "amazon.co.jp",
    "nl": "amazon.nl",
    "se": "amazon.se",
    "pl": "amazon.pl",
    "sg": "amazon.sg",
    "ae": "amazon.ae",
    "in": "amazon.in",
    "br": "amazon.com.br",
}

EXAMPLE_RESPONSE = {
    "responseStatus": "PRODUCT_FOUND_RESPONSE",
    "responseMessage": "Product successfully found!",
    "sellerId": "A2QWFZRANX2P5J",
    "currentPage": 1,
    "nextPage": 2,
    "lastPage": 3,
    "numberOfProducts": 6,
    "resultCount": 18,
    "searchProductDetails": [
        {
            "productDescription": "Shark Cordless Stick Vacuum UltraLight",
            "asin": "SHARK1111",
            "countReview": 1542,
            "imgUrl": "https://m.media-amazon.com/images/I/71x.jpg",
            "price": 199.99,
            "retailPrice": 249.99,
            "productRating": "4.6 out of 5",
            "prime": True,
            "salesVolume": "1K+ bought in past month"
        },
        {
            "productDescription": "Ninja Foodi 8-qt Air Fryer Deluxe",
            "asin": "NINJA2222",
            "countReview": 8901,
            "imgUrl": "https://m.media-amazon.com/images/I/72x.jpg",
            "price": 159.99,
            "retailPrice": 199.99,
            "productRating": "4.7 out of 5",
            "prime": True,
            "salesVolume": "5K+ bought in past month"
        },
        {
            "productDescription": "Dyson V11 Cordless Vacuum",
            "asin": "DYSON3333",
            "countReview": 12345,
            "imgUrl": "https://m.media-amazon.com/images/I/73x.jpg",
            "price": 499.99,
            "retailPrice": 599.99,
            "productRating": "4.6 out of 5",
            "prime": True,
            "salesVolume": "2K+ bought in past month"
        },
        {
            "productDescription": "Shark Steam Mop",
            "asin": "SHARK4444",
            "countReview": 412,
            "imgUrl": "https://m.media-amazon.com/images/I/74x.jpg",
            "price": 69.99,
            "retailPrice": 79.99,
            "productRating": "4.5 out of 5",
            "prime": False,
            "salesVolume": "200+ bought in past month"
        },
        {
            "productDescription": "Ninja Professional Blender 1000",
            "asin": "NINJA5555",
            "countReview": 22310,
            "imgUrl": "https://m.media-amazon.com/images/I/75x.jpg",
            "price": 99.99,
            "retailPrice": 129.99,
            "productRating": "4.8 out of 5",
            "prime": True,
            "salesVolume": "10K+ bought in past month"
        },
        {
            "productDescription": "Dyson Supersonic Hair Dryer",
            "asin": "DYSON6666",
            "countReview": 30210,
            "imgUrl": "https://m.media-amazon.com/images/I/76x.jpg",
            "price": 429.99,
            "retailPrice": 429.99,
            "productRating": "4.7 out of 5",
            "prime": True,
            "salesVolume": "1K+ bought in past month"
        }
    ]
}

def amazon_dp_url(asin: str, domain_code: str) -> str:
    host = AMAZON_HOSTS.get(domain_code, f"amazon.{domain_code}")
    return f"https://{host}/dp/{asin}"

def to_readable_price(x):
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return x if x not in (None, "") else "—"

def looks_like_brand(s: str, brand: str) -> bool:
    s = (s or "").lower()
    b = brand.lower()
    return b in s

def build_curl(base_url, params, headers: dict | None):
    hlines = []
    if headers:
        for k, v in headers.items():
            hlines.append(f"-H {json.dumps(f'{k}: {v}')}")
    header_str = " \\\n  ".join(hlines)
    if header_str:
        header_str = " \\\n  " + header_str
    return f"""curl -X GET "{base_url}?{urlencode(params)}"{header_str}"""

# -----------------------------
# Sidebar
# -----------------------------
with st.sidebar:
    st.markdown("## 🔧 Settings")
    base_url = st.text_input("API Base URL", value=DEFAULT_BASE_URL)
    domain_code = st.selectbox("Amazon Domain", options=SUPPORTED_DOMAINS, index=0, key="domain_code")
    seller_id = st.text_input(
        "Seller ID",
        value="A2QWFZRANX2P5J",  # SharkNinja (US)
        key="seller_id",
        help="e.g., A2QWFZRANX2P5J (SharkNinja US), A1VLPNTGGRFAR6 (Streamlight US)."
    )
    default_page = st.number_input("Page (for single fetch)", min_value=1, step=1, value=1, key="page_input")

    st.markdown("### 🎯 Presets")
    colp1, colp2, colp3 = st.columns(3)
    with colp1:
        if st.button("SharkNinja (US)", use_container_width=True):
            st.session_state["domain_code"] = "com"
            st.session_state["seller_id"] = "A2QWFZRANX2P5J"
            st.session_state["page_input"] = 1
            st.session_state.page = 1
            st.experimental_rerun()
    with colp2:
        if st.button("Streamlight (US)", use_container_width=True):
            st.session_state["domain_code"] = "com"
            st.session_state["seller_id"] = "A1VLPNTGGRFAR6"
            st.session_state["page_input"] = 1
            st.session_state.page = 1
            st.experimental_rerun()
    with colp3:
        if st.button("Dyson (US)", use_container_width=True):
            # Paste the current official Dyson seller ID if/when you have it.
            st.session_state["domain_code"] = "com"
            st.session_state["seller_id"] = ""  # <- fill when known
            st.session_state["page_input"] = 1
            st.session_state.page = 1
            st.experimental_rerun()

    st.markdown("### ➕ Headers / Keys")
    st.caption("Use secrets or paste JSON headers. Example: {\"x-api-key\":\"YOUR_KEY\"}")
    # Secrets-based headers (preferred)
    secret_headers = None
    try:
        if "AXESSO_API_KEY" in st.secrets:
            secret_headers = {"x-api-key": st.secrets["AXESSO_API_KEY"]}
        elif "RAPIDAPI_KEY" in st.secrets:
            secret_headers = {"X-RapidAPI-Key": st.secrets["RAPIDAPI_KEY"]}
            host = st.secrets.get("RAPIDAPI_HOST")
            if host:
                secret_headers["X-RapidAPI-Host"] = host
    except Exception:
        secret_headers = None

    use_secret = st.checkbox("Use key from secrets (if available)", value=bool(secret_headers))
    if use_secret and secret_headers:
        parsed_headers = secret_headers
        try:
            preview = {k: (v[:3] + "..." if isinstance(v, str) and len(v) > 6 else v) for k, v in secret_headers.items()}
            st.caption(f"Using headers from secrets: {preview}")
        except Exception:
            pass
        headers_raw = ""
    else:
        headers_raw = st.text_area("Headers (JSON)", value="", height=120, placeholder='{"x-api-key":"YOUR_KEY"}')
        parsed_headers = None
        if headers_raw.strip():
            try:
                parsed_headers = json.loads(headers_raw)
                if not isinstance(parsed_headers, dict):
                    st.warning("Headers JSON must be an object, e.g. {\"x-api-key\":\"...\"}")
                    parsed_headers = None
            except Exception as e:
                st.warning(f"Could not parse headers JSON: {e}")
                parsed_headers = None

    st.markdown("### 🧪 Demo / Fallback")
    use_example = st.toggle("Use example response (no network call for first page)", value=False)

    st.markdown("### 📚 Fetch Scope")
    fetch_all = st.checkbox("Fetch **ALL pages**", value=True, help="Loops through pages until lastPage (or safety cap).")
    start_page = st.number_input("Start page", value=1, min_value=1, step=1)
    max_pages_cap = st.number_input("Max pages (safety cap)", value=50, min_value=1, step=1)
    per_request_delay = st.number_input("Delay between requests (seconds)", value=0.5, min_value=0.0, step=0.1)
    dedupe_by_asin = st.checkbox("Dedupe by ASIN", value=True)

# Maintain session page for Prev/Next controls
if "page" not in st.session_state:
    st.session_state.page = int(default_page)
if default_page != st.session_state.page:
    st.session_state.page = int(default_page)

# -----------------------------
# UI
# -----------------------------
st.title("🛒 Amazon ASIN Finder (by Seller)")
st.caption("Powered by Axesso — query any seller, gather ASINs across pages, and filter for Shark, Ninja, or Dyson.")

tab_asin, tab_table, tab_snippets = st.tabs(["ASIN Finder", "Products Table & Gallery", "Code Snippets"])

# --------------------------------------
# Shared request helper
# --------------------------------------
def do_request(page_num: int, base_url, domain_code, seller_id, headers):
    q = {"domainCode": domain_code, "sellerId": seller_id.strip(), "page": page_num}
    r = requests.get(base_url, params=q, headers=headers, timeout=20)
    return r

def brand_filter_row(desc: str, brand_choice: str) -> bool:
    """Return True if row matches selected brand filter."""
    if brand_choice == "Any":
        return True
    return looks_like_brand(desc, brand_choice)

def estimate_fetch_click_calls(fetch_all, use_example, start_page, max_pages_cap, lp_cache):
    if fetch_all:
        # planned pages: lastPage-based or worst-case cap
        if isinstance(lp_cache, int):
            planned_pages = max(0, min(int(max_pages_cap), int(lp_cache) - int(start_page) + 1))
        else:
            planned_pages = int(max_pages_cap)
        # first page demo = no network call
        network_calls = max(0, planned_pages - (1 if use_example else 0))
    else:
        network_calls = 0 if use_example else 1
    return network_calls

# --------------------------------------
# Tab: ASIN Finder
# --------------------------------------
with tab_asin:
    st.subheader("Fetch ASINs from this seller")
    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        fetch_btn = st.button("🔎 Fetch (this page / all pages)", use_container_width=True)
    with c2:
        prev_btn = st.button("⬅️ Prev Page", use_container_width=True)
    with c3:
        next_btn = st.button("➡️ Next Page", use_container_width=True)

    # API call estimate row
    lp_cache = st.session_state.get("lastPage_cache")
    fetch_click_calls = estimate_fetch_click_calls(fetch_all, use_example, start_page, max_pages_cap, lp_cache)
    prev_next_calls = 0 if use_example else 1
    st.caption(f"**API call estimate** — 🔎 Fetch: **{fetch_click_calls}** • ⬅️/➡️ Prev/Next: **{prev_next_calls}**")

    # Prev/Next adjust page
    if prev_btn:
        st.session_state.page = max(1, int(st.session_state.page) - 1)
    if next_btn:
        st.session_state.page = int(st.session_state.page) + 1

    st.divider()
    st.markdown(f"**Domain:** `{domain_code}` • **Seller ID:** `{seller_id or '—'}` • **Current page:** `{st.session_state.page}`")

    # Brand filter and output format
    filt_col1, filt_col2 = st.columns([1, 2])
    with filt_col1:
        brand_choice = st.selectbox("Brand filter for ASIN list", options=["Any", "Shark", "Ninja", "Dyson"], index=0)
    with filt_col2:
        asin_separator = st.selectbox("ASIN separator", options=["newline", "comma", "space"], index=0)

    # Assemble params
    params = {
        "domainCode": domain_code,
        "sellerId": (seller_id or "").strip(),
        "page": int(st.session_state.page),
    }

    trigger = fetch_btn or prev_btn or next_btn
    response_data = None
    error_msg = None
    aggregated_products = None
    pages_fetched = 0

    if trigger:
        if not params["sellerId"]:
            error_msg = "Seller ID is required."
        elif use_example and not fetch_all:
            response_data = EXAMPLE_RESPONSE
        else:
            try:
                if fetch_all:
                    st.info("Fetching ALL pages — this may take a bit depending on caps and delay.")
                    products = []
                    seen_asins = set()
                    last_page_hint = None
                    start = int(start_page)
                    cap = int(max_pages_cap)
                    total_planned = cap  # refined as we learn lastPage
                    progress = st.progress(0)

                    for idx, p in enumerate(range(start, start + cap)):
                        try:
                            if use_example and p == start:
                                data = EXAMPLE_RESPONSE
                            else:
                                r = do_request(p, base_url, domain_code, seller_id, parsed_headers)
                                if r.status_code != 200:
                                    error_msg = f"HTTP {r.status_code} on page {p}: {r.text[:200]}"
                                    break
                                data = r.json()
                            pages_fetched += 1

                            # read lastPage hint
                            last_page_hint = data.get("lastPage", last_page_hint)
                            if isinstance(last_page_hint, int):
                                st.session_state.lastPage_cache = int(last_page_hint)
                                total_planned = min(cap, max(1, last_page_hint - start + 1))

                            page_products = data.get("searchProductDetails") or []
                            if dedupe_by_asin:
                                for item in page_products:
                                    a = item.get("asin")
                                    if a and a not in seen_asins:
                                        seen_asins.add(a)
                                        products.append(item)
                            else:
                                products.extend(page_products)

                            # progress
                            denom = total_planned if total_planned else cap
                            progress.progress(min(1.0, (idx + 1) / float(denom)))

                            # stop conditions
                            if isinstance(last_page_hint, int) and p >= last_page_hint:
                                break
                            if not page_products and not isinstance(last_page_hint, int):
                                break

                            if per_request_delay and per_request_delay > 0:
                                time.sleep(float(per_request_delay))

                        except Exception as inner_e:
                            error_msg = f"Request failed on page {p}: {inner_e}"
                            break

                    aggregated_products = products

                else:
                    with st.spinner("Contacting Axesso API..."):
                        r = do_request(params["page"], base_url, domain_code, seller_id, parsed_headers)
                    if r.status_code == 200:
                        response_data = r.json()
                    else:
                        error_msg = f"HTTP {r.status_code}: {r.text[:300]}"
            except Exception as e:
                error_msg = f"Request failed: {e}"

    if error_msg:
        st.error(error_msg)

    # --------- If we got ALL pages
    if aggregated_products is not None:
        # Build filtered ASIN list
        filtered = [
            p for p in aggregated_products
            if brand_filter_row(p.get("productDescription", ""), brand_choice)
        ]
        asins = [p.get("asin") for p in filtered if p.get("asin")]
        unique_asins = list(dict.fromkeys(asins))

        st.success(
            f"Fetched {pages_fetched} page(s). "
            f"Products (after brand filter '{brand_choice}'): {len(filtered)} • Unique ASINs: {len(unique_asins)}"
        )

        # Show ASINs
        if asin_separator == "newline":
            asin_blob = "\n".join(unique_asins)
        elif asin_separator == "comma":
            asin_blob = ",".join(unique_asins)
        else:
            asin_blob = " ".join(unique_asins)

        st.subheader("ASINs")
        st.code(asin_blob or "—", language="text")

        # Downloads
        colD1, colD2, colD3 = st.columns(3)
        with colD1:
            st.download_button(
                "⬇️ Download ASINs (TXT)",
                data=asin_blob,
                file_name=f"asins_{seller_id}_{domain_code}.txt",
                mime="text/plain",
                use_container_width=True,
            )
        with colD2:
            st.download_button(
                "⬇️ Download ASINs (JSON)",
                data=json.dumps(unique_asins, indent=2),
                file_name=f"asins_{seller_id}_{domain_code}.json",
                mime="application/json",
                use_container_width=True,
            )
        with colD3:
            df_asins = pd.DataFrame({"asin": unique_asins})
            st.download_button(
                "⬇️ Download ASINs (CSV)",
                data=df_asins.to_csv(index=False),
                file_name=f"asins_{seller_id}_{domain_code}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        st.info("Tip: switch the Brand filter to **Shark**, **Ninja**, or **Dyson** to get those specific ASINs.")

    # --------- If we got a single page
    if response_data:
        status = response_data.get("responseStatus")
        currentPage = response_data.get("currentPage", params["page"])
        lastPage = response_data.get("lastPage")
        if isinstance(lastPage, int):
            st.session_state.lastPage_cache = lastPage

        st.write(f"**Status:** {status or '—'} • **Page:** {currentPage} / {lastPage or '—'}")

        products = response_data.get("searchProductDetails") or []
        filtered = [
            p for p in products
            if brand_filter_row(p.get("productDescription", ""), brand_choice)
        ]
        asins = [p.get("asin") for p in filtered if p.get("asin")]
        unique_asins = list(dict.fromkeys(asins))

        st.subheader("ASINs (this page)")
        if asin_separator == "newline":
            asin_blob = "\n".join(unique_asins)
        elif asin_separator == "comma":
            asin_blob = ",".join(unique_asins)
        else:
            asin_blob = " ".join(unique_asins)

        st.code(asin_blob or "—", language="text")

        colD1, colD2, colD3 = st.columns(3)
        with colD1:
            st.download_button(
                "⬇️ Download (TXT)",
                data=asin_blob,
                file_name=f"asins_p{currentPage}.txt",
                mime="text/plain",
                use_container_width=True,
            )
        with colD2:
            st.download_button(
                "⬇️ Download (JSON)",
                data=json.dumps(unique_asins, indent=2),
                file_name=f"asins_p{currentPage}.json",
                mime="application/json",
                use_container_width=True,
            )
        with colD3:
            df_asins = pd.DataFrame({"asin": unique_asins})
            st.download_button(
                "⬇️ Download (CSV)",
                data=df_asins.to_csv(index=False),
                file_name=f"asins_p{currentPage}.csv",
                mime="text/csv",
                use_container_width=True,
            )

# --------------------------------------
# Tab: Products Table & Gallery (nice to have)
# --------------------------------------
with tab_table:
    st.subheader("Optional: Fetch page to view products")
    colA, colB, colC = st.columns([1, 1, 2])
    with colA:
        fetch_one_btn = st.button("🔎 Fetch page (table view)", use_container_width=True, key="fetch_table_btn")
    with colB:
        page_for_table = st.number_input("Page to fetch", min_value=1, step=1, value=int(st.session_state.page), key="tbl_page")

    # Estimate (same as single-page fetch)
    prev_next_calls = 0 if use_example else 1
    st.caption(f"**API call estimate** — this action: **{0 if use_example else 1}**")

    if fetch_one_btn:
        if use_example:
            data = EXAMPLE_RESPONSE
        else:
            try:
                with st.spinner("Contacting Axesso API..."):
                    r = do_request(int(page_for_table), base_url, domain_code, seller_id, parsed_headers)
                if r.status_code == 200:
                    data = r.json()
                else:
                    st.error(f"HTTP {r.status_code}: {r.text[:300]}")
                    data = None
            except Exception as e:
                st.error(f"Request failed: {e}")
                data = None

        if data:
            lastPage = data.get("lastPage")
            if isinstance(lastPage, int):
                st.session_state.lastPage_cache = lastPage

            products = data.get("searchProductDetails") or []
            if products:
                df = pd.DataFrame(products)
                # pretties
                if "asin" in df.columns:
                    df.insert(0, "amazonUrl", df["asin"].apply(lambda a: amazon_dp_url(a, domain_code)))
                if "price" in df.columns:
                    df["price_display"] = df["price"].apply(to_readable_price)
                if "retailPrice" in df.columns:
                    df["retailPrice_display"] = df["retailPrice"].apply(lambda v: to_readable_price(v) if v else "—")

                st.dataframe(
                    df[["productDescription", "asin", "price_display", "retailPrice_display", "countReview", "productRating", "prime", "salesVolume", "amazonUrl"]],
                    use_container_width=True,
                )

                c1, c2 = st.columns(2)
                with c1:
                    st.download_button(
                        "⬇️ Download JSON",
                        data=json.dumps(products, indent=2),
                        file_name=f"seller_products_{seller_id}_{domain_code}_p{data.get('currentPage', page_for_table)}.json",
                        mime="application/json",
                        use_container_width=True,
                    )
                with c2:
                    st.download_button(
                        "⬇️ Download CSV",
                        data=df.to_csv(index=False),
                        file_name=f"seller_products_{seller_id}_{domain_code}_p{data.get('currentPage', page_for_table)}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )

                st.markdown("### Gallery")
                cards_per_row = 3
                rows = (len(products) + cards_per_row - 1) // cards_per_row
                for i in range(rows):
                    cols = st.columns(cards_per_row)
                    for j in range(cards_per_row):
                        idx = i * cards_per_row + j
                        if idx >= len(products):
                            break
                        p = products[idx]
                        with cols[j]:
                            if p.get("imgUrl"):
                                st.image(p["imgUrl"], use_column_width=True)
                            st.markdown(f"**{p.get('productDescription', '(no title)')}**")
                            st.caption(f"ASIN: `{p.get('asin', '—')}`")
                            st.write(f"Price: {to_readable_price(p.get('price'))} | Reviews: {p.get('countReview', '—')}")
                            st.write(f"Rating: {p.get('productRating', '—')} | Prime: {'✅' if p.get('prime') else '❌'}")
                            st.write(f"Sales: {p.get('salesVolume', '—')}")
                            a = p.get("asin")
                            if a:
                                st.link_button("Open on Amazon", amazon_dp_url(a, domain_code), use_container_width=True)
            else:
                st.info("No products returned for this page.")

# --------------------------------------
# Tab: Code Snippets
# --------------------------------------
with tab_snippets:
    st.subheader("cURL / Python for your current inputs")
    cur_params = {
        "domainCode": domain_code,
        "sellerId": (seller_id or "").strip(),
        "page": int(st.session_state.page),
    }
    st.markdown("**cURL**")
    st.code(build_curl(DEFAULT_BASE_URL, cur_params, parsed_headers), language="bash")

    st.markdown("**Python (requests)**")
    py_headers = f", headers={json.dumps(parsed_headers, indent=2)}" if parsed_headers else ""
    st.code(f"""import requests

base_url = "{DEFAULT_BASE_URL}"
params = {json.dumps(cur_params, indent=2)}
res = requests.get(base_url, params=params{py_headers}, timeout=20)
print(res.json())""", language="python")


