import streamlit as st
import requests
import pandas as pd
from urllib.parse import urlparse
from datetime import datetime

# =========================
# CONFIG
# =========================
st.set_page_config(page_title="SharkNinja Review Intelligence", layout="wide")

BV_BASE_URL = "https://api.bazaarvoice.com/data/reviews.json"
DEFAULT_LIMIT = 100

# =========================
# HELPERS
# =========================

def get_bv_key():
    try:
        return st.secrets["bazaarvoice"]["passkey"]
    except Exception:
        return None


def extract_product_id(url: str):
    """Basic heuristic: last part of URL or SKU-like string"""
    try:
        path = urlparse(url).path
        parts = [p for p in path.split("/") if p]
        if parts:
            return parts[-1].upper()
    except Exception:
        pass
    return None


def fetch_reviews(product_id: str, passkey: str, max_reviews=100):
    reviews = []
    offset = 0

    while len(reviews) < max_reviews:
        params = {
            "apiversion": "5.4",
            "passkey": passkey,
            "Filter": f"ProductId:{product_id}",
            "Limit": min(100, max_reviews - len(reviews)),
            "Offset": offset,
            "Include": "Products",
            "Stats": "Reviews"
        }

        r = requests.get(BV_BASE_URL, params=params)
        if r.status_code != 200:
            break

        data = r.json()
        batch = data.get("Results", [])

        if not batch:
            break

        reviews.extend(batch)
        offset += len(batch)

    return reviews


def normalize_reviews(raw_reviews):
    cleaned = []
    for i, r in enumerate(raw_reviews, start=1):
        cleaned.append({
            "ReviewID": f"R{i:03d}",
            "Rating": r.get("Rating"),
            "Title": r.get("Title"),
            "Text": r.get("ReviewText"),
            "Author": r.get("UserNickname"),
            "Date": r.get("SubmissionTime")
        })
    return pd.DataFrame(cleaned)

# =========================
# UI
# =========================

st.title("SharkNinja Review Intelligence (Bazaarvoice Powered)")

url = st.text_input("Paste SharkNinja Product URL")

col1, col2 = st.columns(2)
with col1:
    max_reviews = st.number_input("Reviews to pull", 10, 200, 100)
with col2:
    run_btn = st.button("Fetch Reviews")

passkey = get_bv_key()

if not passkey:
    st.error("Missing Bazaarvoice passkey in secrets.toml")

if run_btn and url and passkey:
    with st.spinner("Fetching reviews from Bazaarvoice..."):
        product_id = extract_product_id(url)

        if not product_id:
            st.error("Could not determine product ID")
        else:
            raw = fetch_reviews(product_id, passkey, max_reviews)

            if not raw:
                st.warning("No reviews returned from API")
            else:
                df = normalize_reviews(raw)

                st.success(f"Fetched {len(df)} reviews")
                st.dataframe(df.head(50), use_container_width=True)

                # Download
                csv = df.to_csv(index=False).encode("utf-8")
                st.download_button("Download CSV", csv, "reviews.csv")

                # Basic analytics
                st.subheader("Insights")
                if "Rating" in df.columns:
                    st.bar_chart(df["Rating"].value_counts().sort_index())

                # AI placeholder (can hook OpenAI here)
                st.subheader("AI Summary (placeholder)")
                st.write("Hook OpenAI GPT-5.4 here for summaries, detractors, etc.")

# =========================
# FOOTER
# =========================
st.caption("Powered by Bazaarvoice Conversations API")
