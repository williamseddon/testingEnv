import streamlit as st
import requests
import re
from urllib.parse import urlparse, parse_qs
from datetime import datetime

API_KEY = "capxzF3xnCmhSCHhkomxF1sQkZmh2zK2fNb8D1VDNl3hY"
BASE_URL = "https://api.bazaarvoice.com/data/reviews.json"
LIMIT = 10


def extract_product_id(url: str) -> str | None:
    try:
        parsed = urlparse(url.strip())
        path = parsed.path
        params = parse_qs(parsed.query)

        for key in ["productId", "product_id", "itemId", "item_id", "pid", "id", "sku"]:
            if key in params:
                return params[key][0]

        zid_match = re.search(r"[_\-]zid([A-Z0-9\-_]+)$", path, re.IGNORECASE)
        if zid_match:
            return zid_match.group(1)

        segments = [s for s in path.split("/") if s]
        if segments:
            last = re.sub(r"\.html?$", "", segments[-1], flags=re.IGNORECASE)
            trailing = re.search(r"([A-Z0-9]{4,20})$", last, re.IGNORECASE)
            if trailing:
                return trailing.group(1)
            return last or None

    except Exception:
        return url.strip()[:50]

    return None


def fetch_reviews(product_id: str, offset: int, sort: str, rating_filter: str):
    params = {
        "apiversion": "5.4",
        "passkey": API_KEY,
        "Include": "Products",
        "Stats": "Reviews",
        "Limit": LIMIT,
        "Offset": offset,
        "Sort": sort,
    }

    filters = [f"ProductId:{product_id}"]
    if rating_filter:
        filters.append(f"Rating:{rating_filter}")

    query = "&".join(f"Filter={f}" for f in filters)
    query += "&" + "&".join(f"{k}={v}" for k, v in params.items())

    response = requests.get(f"{BASE_URL}?{query}")
    response.raise_for_status()
    return response.json()


def render_stars(rating: int) -> str:
    return "★" * rating + "☆" * (5 - rating)


st.set_page_config(page_title="BazaarVoice Review Lookup", page_icon="⭐", layout="centered")

st.title("BazaarVoice Review Lookup")
st.caption("Paste a product URL to fetch reviews via the BazaarVoice API.")

url_input = st.text_input("Product URL", placeholder="https://sharkclean.co.uk/product/...")

col1, col2 = st.columns(2)
with col1:
    sort_option = st.selectbox(
        "Sort by",
        options=[
            ("Newest first", "SubmissionTime:desc"),
            ("Oldest first", "SubmissionTime:asc"),
            ("Highest rated", "Rating:desc"),
            ("Lowest rated", "Rating:asc"),
            ("Most helpful", "Helpfulness:desc"),
        ],
        format_func=lambda x: x[0],
    )
with col2:
    rating_option = st.selectbox(
        "Filter by rating",
        options=[
            ("All ratings", ""),
            ("5 stars only", "5"),
            ("4 stars only", "4"),
            ("3 stars only", "3"),
            ("2 stars only", "2"),
            ("1 star only", "1"),
        ],
        format_func=lambda x: x[0],
    )

if "offset" not in st.session_state:
    st.session_state.offset = 0
if "last_key" not in st.session_state:
    st.session_state.last_key = ""

current_key = f"{url_input}|{sort_option[1]}|{rating_option[1]}"
if current_key != st.session_state.last_key:
    st.session_state.offset = 0
    st.session_state.last_key = current_key

if url_input:
    product_id = extract_product_id(url_input)

    if not product_id:
        st.error("Could not extract a product ID from this URL.")
    else:
        st.caption(f"Extracted product ID: `{product_id}`")

        with st.spinner(f'Fetching reviews for "{product_id}"…'):
            try:
                data = fetch_reviews(
                    product_id,
                    st.session_state.offset,
                    sort_option[1],
                    rating_option[1],
                )

                if data.get("HasErrors"):
                    errors = " | ".join(e["Message"] for e in data.get("Errors", []))
                    st.error(f"BazaarVoice API error: {errors}")
                else:
                    reviews = data.get("Results", [])
                    total = data.get("TotalResults", 0)
                    product_data = (data.get("Includes") or {}).get("Products", {}).get(product_id, {})
                    stats = product_data.get("ReviewStatistics", {})
                    avg = stats.get("AverageOverallRating")

                    if total == 0:
                        st.warning("No reviews found for this product ID.")
                    else:
                        name = product_data.get("Name")
                        if name:
                            st.subheader(name)

                        m1, m2, m3 = st.columns(3)
                        m1.metric("Total reviews", f"{total:,}")
                        m2.metric("Avg rating", f"{float(avg):.1f} / 5" if avg else "—")
                        offset = st.session_state.offset
                        m3.metric("Showing", f"{offset + 1}–{min(offset + LIMIT, total)}")

                        st.divider()

                        for r in reviews:
                            with st.container(border=True):
                                title = r.get("Title") or "Untitled review"
                                nickname = r.get("UserNickname") or "Anonymous"
                                rating = r.get("Rating", 0)
                                text = r.get("ReviewText") or "_No review text provided._"
                                recommended = r.get("IsRecommended")

                                raw_date = r.get("SubmissionTime", "")
                                try:
                                    date_str = datetime.fromisoformat(
                                        raw_date.replace("Z", "+00:00")
                                    ).strftime("%d %b %Y")
                                except Exception:
                                    date_str = raw_date[:10] if raw_date else ""

                                hcol, dcol = st.columns([3, 1])
                                hcol.markdown(f"**{title}**")
                                dcol.caption(f"{nickname} · {date_str}")

                                st.markdown(
                                    f"<span style='color:#d97706;font-size:16px'>{render_stars(rating)}</span> "
                                    f"<span style='color:#888;font-size:13px'>{rating}/5</span>",
                                    unsafe_allow_html=True,
                                )
                                st.write(text)

                                if recommended is True:
                                    st.success("✓ Recommends this product")
                                elif recommended is False:
                                    st.error("✗ Does not recommend this product")

                        total_pages = (total + LIMIT - 1) // LIMIT
                        current_page = st.session_state.offset // LIMIT

                        st.divider()
                        pcol1, pcol2, pcol3 = st.columns([1, 2, 1])

                        with pcol1:
                            if current_page > 0:
                                if st.button("← Previous"):
                                    st.session_state.offset -= LIMIT
                                    st.rerun()

                        with pcol2:
                            st.markdown(
                                f"<p style='text-align:center;color:#888;font-size:13px'>"
                                f"Page {current_page + 1} of {total_pages}</p>",
                                unsafe_allow_html=True,
                            )

                        with pcol3:
                            if current_page < total_pages - 1:
                                if st.button("Next →"):
                                    st.session_state.offset += LIMIT
                                    st.rerun()

            except requests.HTTPError as e:
                st.error(f"HTTP error: {e}")
            except Exception as e:
                st.error(f"Something went wrong: {e}")
