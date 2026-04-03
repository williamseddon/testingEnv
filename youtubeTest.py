"""
YouTube Transcript & Comments Scraper — Streamlit App
======================================================
Install:
    pip install streamlit youtube-transcript-api google-api-python-client

Run:
    streamlit run yt_scraper_app.py
"""

import io
import json
import re
import time

import pandas as pd
import streamlit as st

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="YT Scraper",
    page_icon="📺",
    layout="wide",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    /* Tighten sidebar */
    [data-testid="stSidebar"] { min-width: 300px; max-width: 300px; }
    /* Metric card labels */
    [data-testid="stMetricLabel"] { font-size: 12px !important; }
    /* Download buttons */
    .stDownloadButton > button { width: 100%; }
    /* Status messages */
    .status-box {
        background: #f0f2f6;
        border-left: 3px solid #4c8bf5;
        padding: 10px 14px;
        border-radius: 4px;
        font-size: 13px;
        margin-bottom: 8px;
    }
</style>
""", unsafe_allow_html=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_video_id(url_or_id: str) -> str | None:
    patterns = [r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})"]
    for pat in patterns:
        m = re.search(pat, url_or_id)
        if m:
            return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url_or_id.strip()):
        return url_or_id.strip()
    return None


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def to_json_bytes(df: pd.DataFrame) -> bytes:
    return df.to_json(orient="records", indent=2, force_ascii=False).encode("utf-8")


def format_seconds(s: float) -> str:
    m, sec = divmod(int(s), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


# ── Scraping functions ────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def fetch_transcript(video_id: str, lang: str) -> tuple[pd.DataFrame | None, str]:
    try:
        from youtube_transcript_api import (
            YouTubeTranscriptApi,
            NoTranscriptFound,
            TranscriptsDisabled,
        )
    except ImportError:
        return None, "❌ `youtube-transcript-api` not installed. Run: `pip install youtube-transcript-api`"

    try:
        if lang and lang.lower() != "auto":
            raw = YouTubeTranscriptApi.get_transcript(video_id, languages=[lang])
        else:
            raw = YouTubeTranscriptApi.get_transcript(video_id)
    except TranscriptsDisabled:
        return None, "❌ Transcripts are disabled for this video."
    except NoTranscriptFound as e:
        return None, f"❌ No transcript found: {e}"
    except Exception as e:
        return None, f"❌ Error: {e}"

    rows = [
        {
            "timestamp": format_seconds(seg["start"]),
            "start_sec": round(seg["start"], 3),
            "duration_sec": round(seg.get("duration", 0), 3),
            "text": seg["text"].replace("\n", " "),
        }
        for seg in raw
    ]
    return pd.DataFrame(rows), f"✅ {len(rows):,} segments fetched."


@st.cache_data(show_spinner=False)
def fetch_comments(video_id: str, api_key: str, max_comments: int, order: str) -> tuple[pd.DataFrame | None, str]:
    try:
        from googleapiclient.discovery import build
    except ImportError:
        return None, "❌ `google-api-python-client` not installed. Run: `pip install google-api-python-client`"

    try:
        youtube = build("youtube", "v3", developerKey=api_key)
        rows = []
        page_token = None

        while len(rows) < max_comments:
            batch = min(100, max_comments - len(rows))
            req = youtube.commentThreads().list(
                part="snippet",
                videoId=video_id,
                maxResults=batch,
                pageToken=page_token,
                textFormat="plainText",
                order=order,
            )
            resp = req.execute()
            for item in resp.get("items", []):
                top = item["snippet"]["topLevelComment"]["snippet"]
                rows.append({
                    "author": top.get("authorDisplayName", ""),
                    "text": top.get("textDisplay", "").replace("\n", " "),
                    "likes": top.get("likeCount", 0),
                    "reply_count": item["snippet"].get("totalReplyCount", 0),
                    "published_at": top.get("publishedAt", ""),
                    "updated_at": top.get("updatedAt", ""),
                    "comment_id": item["id"],
                })
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        return pd.DataFrame(rows), f"✅ {len(rows):,} comments fetched."
    except Exception as e:
        return None, f"❌ Error: {e}"


# ── Sidebar — inputs ──────────────────────────────────────────────────────────

with st.sidebar:
    st.title("📺 YT Scraper")
    st.caption("Transcript & comments extractor")
    st.divider()

    url_input = st.text_input(
        "YouTube URL or Video ID",
        placeholder="https://youtube.com/watch?v=...",
    )

    video_id = extract_video_id(url_input) if url_input else None
    if url_input and not video_id:
        st.error("Couldn't parse a video ID from that URL.")

    st.subheader("Transcript")
    fetch_transcript_flag = st.toggle("Fetch transcript", value=True)
    lang_input = st.text_input("Language code", value="auto", help="e.g. 'en', 'es', 'fr'. Leave 'auto' to use YouTube's default.")

    st.subheader("Comments")
    fetch_comments_flag = st.toggle("Fetch comments", value=False)
    api_key_input = st.text_input("YouTube Data API v3 key", type="password", help="Get a free key at console.cloud.google.com")
    max_comments = st.slider("Max comments", min_value=10, max_value=2000, value=200, step=10)
    comment_order = st.selectbox("Sort order", ["relevance", "time"], index=0)

    st.divider()
    run_button = st.button("▶ Run", type="primary", disabled=not video_id, use_container_width=True)

    if not video_id and url_input:
        st.warning("Enter a valid URL to continue.")
    elif not url_input:
        st.info("Paste a YouTube URL above to get started.")


# ── Main area ─────────────────────────────────────────────────────────────────

if not run_button and "transcript_df" not in st.session_state:
    # Landing state
    st.markdown("## YouTube Transcript & Comments Scraper")
    st.markdown(
        "Paste a YouTube URL in the sidebar, configure your options, and click **▶ Run**.\n\n"
        "- **Transcript** — no API key required\n"
        "- **Comments** — requires a free [YouTube Data API v3 key](https://console.cloud.google.com/apis/library/youtube.googleapis.com)"
    )
    st.stop()

# Run scraping
if run_button and video_id:
    st.session_state.clear()  # clear previous results on new run
    st.session_state["video_id"] = video_id

    if fetch_transcript_flag:
        with st.spinner("Fetching transcript…"):
            t_df, t_msg = fetch_transcript(video_id, lang_input)
        st.session_state["transcript_df"] = t_df
        st.session_state["transcript_msg"] = t_msg

    if fetch_comments_flag:
        if not api_key_input:
            st.session_state["comments_msg"] = "⚠️ Provide a YouTube Data API v3 key to fetch comments."
            st.session_state["comments_df"] = None
        else:
            with st.spinner("Fetching comments…"):
                c_df, c_msg = fetch_comments(video_id, api_key_input, max_comments, comment_order)
            st.session_state["comments_df"] = c_df
            st.session_state["comments_msg"] = c_msg


# ── Display results ───────────────────────────────────────────────────────────

vid = st.session_state.get("video_id", video_id)
if vid:
    st.markdown(f"### Results — `{vid}`")
    st.markdown(f"🔗 [Open on YouTube](https://www.youtube.com/watch?v={vid})")

t_df: pd.DataFrame | None = st.session_state.get("transcript_df")
c_df: pd.DataFrame | None = st.session_state.get("comments_df")

# Metrics row
cols = st.columns(4)
with cols[0]:
    st.metric("Transcript segments", f"{len(t_df):,}" if t_df is not None else "—")
with cols[1]:
    duration = f"{format_seconds(t_df['start_sec'].max())}" if t_df is not None and not t_df.empty else "—"
    st.metric("Video length (approx)", duration)
with cols[2]:
    st.metric("Comments fetched", f"{len(c_df):,}" if c_df is not None else "—")
with cols[3]:
    avg_likes = f"{c_df['likes'].mean():.1f}" if c_df is not None and not c_df.empty else "—"
    st.metric("Avg likes / comment", avg_likes)

st.divider()

# Tabs
tab_t, tab_c = st.tabs(["📄 Transcript", "💬 Comments"])

# ── Transcript tab ────────────────────────────────────────────────────────────
with tab_t:
    msg = st.session_state.get("transcript_msg")
    if msg:
        if msg.startswith("✅"):
            st.success(msg)
        else:
            st.error(msg)

    if t_df is not None and not t_df.empty:
        search = st.text_input("🔍 Search transcript", placeholder="Type a keyword…", key="t_search")
        display_df = t_df[t_df["text"].str.contains(search, case=False, na=False)] if search else t_df

        st.dataframe(
            display_df,
            use_container_width=True,
            height=400,
            column_config={
                "timestamp": st.column_config.TextColumn("Time", width=80),
                "start_sec": st.column_config.NumberColumn("Start (s)", width=90, format="%.2f"),
                "duration_sec": st.column_config.NumberColumn("Duration (s)", width=100, format="%.2f"),
                "text": st.column_config.TextColumn("Text", width=None),
            },
        )

        # Full transcript text
        with st.expander("📋 Full transcript text"):
            full_text = " ".join(t_df["text"].tolist())
            st.text_area("", value=full_text, height=200, label_visibility="collapsed")

        # Downloads
        st.markdown("**Download transcript**")
        dl1, dl2 = st.columns(2)
        with dl1:
            st.download_button("⬇ CSV", to_csv_bytes(t_df), f"{vid}_transcript.csv", "text/csv")
        with dl2:
            st.download_button("⬇ JSON", to_json_bytes(t_df), f"{vid}_transcript.json", "application/json")

    elif t_df is not None and t_df.empty:
        st.info("No transcript data returned.")
    elif not fetch_transcript_flag:
        st.info("Transcript fetching is disabled — toggle it on in the sidebar.")

# ── Comments tab ──────────────────────────────────────────────────────────────
with tab_c:
    msg = st.session_state.get("comments_msg")
    if msg:
        if msg.startswith("✅"):
            st.success(msg)
        elif msg.startswith("⚠️"):
            st.warning(msg)
        else:
            st.error(msg)

    if c_df is not None and not c_df.empty:
        search_c = st.text_input("🔍 Search comments", placeholder="Type a keyword…", key="c_search")
        display_c = c_df[c_df["text"].str.contains(search_c, case=False, na=False)] if search_c else c_df

        st.dataframe(
            display_c,
            use_container_width=True,
            height=400,
            column_config={
                "author": st.column_config.TextColumn("Author", width=140),
                "text": st.column_config.TextColumn("Comment", width=None),
                "likes": st.column_config.NumberColumn("Likes", width=70),
                "reply_count": st.column_config.NumberColumn("Replies", width=80),
                "published_at": st.column_config.TextColumn("Posted", width=160),
                "updated_at": None,
                "comment_id": None,
            },
        )

        # Top comments
        with st.expander("🏆 Top 10 most liked comments"):
            top10 = c_df.nlargest(10, "likes")[["author", "text", "likes", "reply_count"]]
            st.dataframe(top10, use_container_width=True, hide_index=True)

        # Downloads
        st.markdown("**Download comments**")
        dl3, dl4 = st.columns(2)
        with dl3:
            st.download_button("⬇ CSV", to_csv_bytes(c_df), f"{vid}_comments.csv", "text/csv")
        with dl4:
            st.download_button("⬇ JSON", to_json_bytes(c_df), f"{vid}_comments.json", "application/json")

    elif c_df is not None and c_df.empty:
        st.info("No comments returned.")
    elif not fetch_comments_flag:
        st.info("Comment fetching is disabled — toggle it on in the sidebar.")
