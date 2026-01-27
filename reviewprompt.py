import streamlit as st
import pandas as pd
import re
import json
import time
import hashlib
from io import BytesIO
from openai import OpenAI
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# =========================================================
# IMPORTANT: Fix for your Streamlit Cloud error
# =========================================================
# Do NOT write code files at runtime (no Path.write_text / /mnt/data).
# This app is a normal Streamlit script you can deploy directly.

# =========================================================
# Arrow-safe wrapper for st.dataframe
# =========================================================
_original_st_dataframe = st.dataframe

def _dataframe_arrow_safe(data, *args, **kwargs):
    """
    Render dataframe; if Arrow type issues occur, coerce object columns to strings.
    """
    try:
        return _original_st_dataframe(data, *args, **kwargs)
    except Exception:
        try:
            if isinstance(data, pd.DataFrame):
                df = data.copy()
                for col in df.columns:
                    if df[col].dtype == "object":
                        df[col] = df[col].astype(str)
                return _original_st_dataframe(df, *args, **kwargs)
            return _original_st_dataframe(pd.DataFrame({"value": [str(data)]}), *args, **kwargs)
        except Exception:
            st.warning("Couldn't render DataFrame interactively (Arrow issue). Showing text preview.")
            st.text(str(data))
            return None

st.dataframe = _dataframe_arrow_safe

# =========================================================
# App Config / UI Polish
# =========================================================
st.set_page_config(page_title="AI Review Assistant", layout="wide")
st.markdown(
    """
<style>
.block-container { padding-top: 1.0rem; padding-bottom: 2.5rem; }
.small-muted { color: rgba(255,255,255,0.65); font-size: 0.9rem; }
[data-testid="stMetric"] { background: rgba(255,255,255,0.03); padding: 12px; border-radius: 14px; }
hr { margin: 0.8rem 0 1.2rem 0; }
</style>
""",
    unsafe_allow_html=True,
)

st.title("🦈⭐ AI Review Assistant")
st.caption("Upload a review file → filter → enrich with AI → analyze → export. Includes keyword-gated processing + Ask-the-Reviews.")

RUN_STAMP = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

# =========================================================
# Secrets & Client
# =========================================================
@st.cache_resource
def get_client(api_key: str):
    return OpenAI(api_key=api_key)

def get_api_key() -> str:
    try:
        default_key = st.secrets["OPENAI_API_KEY"]
        st.sidebar.success("Using OPENAI_API_KEY from Streamlit secrets.")
    except Exception:
        default_key = ""
        st.sidebar.warning("No OPENAI_API_KEY in secrets. You can paste one below.")
    override = st.sidebar.text_input(
        "OpenAI API Key (optional override)",
        type="password",
        help="Leave blank to use OPENAI_API_KEY from Streamlit secrets.",
    )
    return override or default_key

# =========================================================
# Rate Limiter (RPM)
# =========================================================
class RateLimiter:
    """
    Simple leaky-bucket limiter based on RPM (requests per minute).
    """
    def __init__(self, rpm: int):
        self.rpm = max(0, int(rpm))
        self.interval = 60.0 / self.rpm if self.rpm > 0 else 0.0
        self._lock = Lock()
        self._next_time = 0.0

    def acquire(self):
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if now < self._next_time:
                time.sleep(self._next_time - now)
                now = time.monotonic()
            self._next_time = now + self.interval

# =========================================================
# Helpers
# =========================================================
def normalize_text(x: Any) -> str:
    if not isinstance(x, str):
        x = "" if pd.isna(x) else str(x)
    return " ".join(x.split())

def parse_keywords(raw: str) -> List[str]:
    if not isinstance(raw, str):
        return []
    parts = re.split(r"[,\n\r]+", raw)
    out, seen = [], set()
    for p in parts:
        p = p.strip()
        if not p:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out

def keyword_mask(
    series: pd.Series,
    keywords: List[str],
    mode_any: bool = True,
    case_sensitive: bool = False,
    use_regex: bool = False,
    whole_word: bool = False,
) -> pd.Series:
    """
    Boolean mask where each row matches ANY or ALL keywords.
    """
    ser = series.fillna("").astype(str)
    flags = 0 if case_sensitive else re.IGNORECASE

    def build_pat(k: str) -> str:
        pat = k if use_regex else re.escape(k)
        return rf"\b{pat}\b" if whole_word else pat

    pats = [build_pat(k) for k in keywords if k]
    if not pats:
        return pd.Series([True] * len(ser), index=ser.index)

    if mode_any:
        big = "(" + "|".join(pats) + ")"
        return ser.str.contains(big, regex=True, flags=flags, na=False)

    m = pd.Series([True] * len(ser), index=ser.index)
    for p in pats:
        m = m & ser.str.contains(p, regex=True, flags=flags, na=False)
    return m

def safe_show_df(df: pd.DataFrame, max_rows: int, label: str = ""):
    subset = df.head(max_rows)
    try:
        st.dataframe(subset)
    except Exception:
        st.warning(f"Couldn't render '{label or 'DataFrame'}' as interactive table. Showing text preview.")
        st.text(subset.to_string())

def normalize_predicted_rating(value: Any) -> Any:
    """
    Normalize into int 1-5 or pd.NA
    """
    if value is None:
        return pd.NA
    try:
        v = int(str(value).strip())
        return v if 1 <= v <= 5 else pd.NA
    except Exception:
        return pd.NA

# =========================================================
# Heuristics (purchase date, error indicator, model number, predicted rating)
# =========================================================
MONTHS = ["january","february","march","april","may","june","july","august","september","october","november","december"]
MONTH_ABBR = ["jan","feb","mar","apr","may","jun","jul","aug","sep","sept","oct","nov","dec"]

WARRANTY_CONTEXT_KEYWORDS = ["warranty","extended warranty","expir","coverage","covered","protection plan","protection","guarantee","guaranteed"]
PURCHASE_CONTEXT_KEYWORDS = ["purchase","purchased","bought","buy","purchase date","purchased on","order date","ordered","i got it","got it","have had it since","owned since","since i bought"]

def infer_year_from_relative(text: str, date_str: str) -> Optional[str]:
    if not isinstance(text, str) or not text:
        return None
    if not isinstance(date_str, str) or not date_str:
        return None
    try:
        base_date = datetime.strptime(date_str.split()[0], "%Y-%m-%d")
    except Exception:
        return None

    t = text.lower()
    for i, m in enumerate(MONTHS, start=1):
        for match in re.finditer(rf"last\s+{m}", t):
            ctx = t[max(0, match.start()-40):min(len(t), match.end()+40)]
            if any(k in ctx for k in WARRANTY_CONTEXT_KEYWORDS):
                continue
            return f"{base_date.year - 1}-{i:02d}-15"

    for i, m in enumerate(MONTH_ABBR, start=1):
        for match in re.finditer(rf"last\s+{m}", t):
            ctx = t[max(0, match.start()-40):min(len(t), match.end()+40)]
            if any(k in ctx for k in WARRANTY_CONTEXT_KEYWORDS):
                continue
            return f"{base_date.year - 1}-{min(i,12):02d}-15"
    return None

DATE_PATTERNS = [
    r"\b(20\d{2}|19\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b",
    r"\b(0?[1-9]|1[0-2])[/-](0?[1-9]|[12]\d|3[01])[/-]((?:19|20)?\d{2})\b",
    r"\b(0?[1-9]|[12]\d|3[01])[/-](0?[1-9]|1[0-2])[/-]((?:19|20)?\d{2})\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+\d{4}\b",
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+\d{4}\b",
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{4}\b",
]
DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y", "%m/%d/%y",
    "%d/%m/%Y", "%d/%m/%y",
    "%b %d, %Y", "%B %d, %Y",
    "%B %Y", "%b %Y",
]

def try_parse_date(text: str) -> Optional[str]:
    if not isinstance(text, str) or not text:
        return None
    for pat in DATE_PATTERNS:
        for m in re.finditer(pat, text):
            chunk = re.sub(r"(st|nd|rd|th)", "", m.group(0), flags=re.IGNORECASE).strip()
            chunk = re.sub(r"\s{2,}", " ", chunk)
            for fmt in DATE_FORMATS:
                try:
                    dt = datetime.strptime(chunk, fmt)
                    if fmt in ("%B %Y", "%b %Y"):
                        dt = dt.replace(day=15)
                    return dt.strftime("%Y-%m-%d")
                except Exception:
                    continue
    return None

def normalize_purchase_date_output(value: Any, date_str: Optional[str]) -> str:
    raw = "" if value is None else str(value).strip()
    if not raw or raw.lower() in ("unknown", "na", "n/a", "none"):
        return "unknown"

    def guard_future(iso_str: str) -> str:
        if not date_str:
            return iso_str
        try:
            base_dt = datetime.strptime(date_str.split()[0], "%Y-%m-%d")
            dt = datetime.strptime(iso_str, "%Y-%m-%d")
            if dt > base_dt + timedelta(days=7):
                return "unknown"
        except Exception:
            pass
        return iso_str

    iso = try_parse_date(raw)
    if iso:
        return guard_future(iso)

    rel = infer_year_from_relative(raw, date_str or "")
    if rel:
        return guard_future(rel)

    return raw

ERROR_CODE_REGEXES = [
    r"\b[Ee]rr(?:or)?\s?[-:]?\s?\d{1,4}\b",
    r"\b[Ee]\s?[-:]?\s?\d{1,4}\b",
    r"\b[Cc]ode\s?[-:]?\s?\d{1,4}\b",
    r"\b[Ff]\s?[-:]?\s?\d{1,4}\b",
]
FLASHING_TERMS = ["flashing","blinking","blinks","blink","flash","strobing","strobe","pulsing","pulse"]
UI_TERMS = ["red light","blue light","filter icon","warning light","led","indicator","icon","display error"]

def heuristic_error_indicator(text: str) -> Optional[str]:
    if not isinstance(text, str) or not text:
        return None
    t = text.lower()
    for rx in ERROR_CODE_REGEXES:
        m = re.search(rx, t)
        if m:
            return m.group(0)
    hits = [term for term in (FLASHING_TERMS + UI_TERMS) if term in t]
    return ", ".join(sorted(set(hits))[:3]) if hits else None

MODEL_TOKEN = re.compile(r"\b([A-Z]{1,3}\d{2,4}[A-Z0-9\-]*)\b")
def heuristic_model_number(text: str) -> Optional[str]:
    if not isinstance(text, str) or not text:
        return None
    matches = MODEL_TOKEN.findall(text.upper())
    if not matches:
        return None
    prefixes = ("NV","ZU","AZ","LA","HZ","HV","IZ","IF","HT","CM","ZS","XZ","VM","SV","WV","HP")
    prioritized = [m for m in matches if m.startswith(prefixes)]
    return prioritized[0] if prioritized else matches[0]

# NEW: quick heuristic for predicted rating (optional pre-LLM)
POS_HINTS = [
    "love", "loved", "amazing", "excellent", "perfect", "great", "works great", "highly recommend",
    "fantastic", "awesome", "best", "so good", "super happy", "five stars", "5 stars",
]
NEG_HINTS = [
    "hate", "hated", "terrible", "awful", "worst", "broken", "doesn't work", "didn't work", "stopped working",
    "disappointed", "waste", "refund", "returned", "returning", "noisy", "loud", "1 star", "one star",
]

def heuristic_predicted_rating(text: str) -> Optional[int]:
    if not isinstance(text, str) or not text.strip():
        return None
    t = text.lower()
    pos = sum(1 for w in POS_HINTS if w in t)
    neg = sum(1 for w in NEG_HINTS if w in t)

    if pos == 0 and neg == 0:
        return None

    net = pos - neg
    if net >= 3:
        return 5
    if net == 2:
        return 4
    if net in (0, 1, -1):
        return 3
    if net == -2:
        return 2
    return 1

# =========================================================
# LLM wrappers with retry + limiter
# =========================================================
def llm_retry_json(client, model, system_prompt, user_prompt, schema_name, schema,
                   limiter: Optional[RateLimiter], retries=4, base_delay=1.25):
    last_err = None
    for attempt in range(retries):
        try:
            if limiter:
                limiter.acquire()
            return client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema, "strict": True},
                },
                temperature=0,
            )
        except Exception as e:
            last_err = e
            time.sleep(base_delay * (2 ** attempt))
    raise last_err

def llm_retry_text(client, model, system_prompt, user_prompt,
                   limiter: Optional[RateLimiter], retries=4, base_delay=1.25):
    last_err = None
    for attempt in range(retries):
        try:
            if limiter:
                limiter.acquire()
            return client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
            )
        except Exception as e:
            last_err = e
            time.sleep(base_delay * (2 ** attempt))
    raise last_err

def call_llm_json_safe(client, model, system_prompt, user_prompt, schema_name, schema,
                       limiter: Optional[RateLimiter]) -> Dict[str, Any]:
    try:
        resp = llm_retry_json(client, model, system_prompt, user_prompt, schema_name, schema, limiter)
        return json.loads(resp.choices[0].message.content)
    except Exception:
        # fallback: try plain text and parse
        try:
            resp2 = llm_retry_text(client, model, system_prompt, user_prompt, limiter)
            return json.loads(resp2.choices[0].message.content)
        except Exception:
            return {"_raw": "LLM-ERROR or Non-JSON"}

def call_llm_text(client, model, system_prompt, user_prompt, limiter: Optional[RateLimiter]) -> str:
    resp = llm_retry_text(client, model, system_prompt, user_prompt, limiter)
    return resp.choices[0].message.content

# =========================================================
# Cached loading
# =========================================================
@st.cache_data(show_spinner=False)
def load_csv_cached(file_bytes: bytes, use_pyarrow: bool, usecols: Optional[List[str]] = None) -> pd.DataFrame:
    import io
    buf = io.BytesIO(file_bytes)
    if use_pyarrow:
        try:
            return pd.read_csv(buf, engine="pyarrow", usecols=usecols)
        except Exception:
            buf.seek(0)
            return pd.read_csv(buf, usecols=usecols)
    return pd.read_csv(buf, usecols=usecols)

@st.cache_data(show_spinner=False)
def load_excel_cached(file_bytes: bytes, sheet_name: Optional[str] = None, usecols: Optional[List[str]] = None) -> pd.DataFrame:
    import io
    buf = io.BytesIO(file_bytes)
    kwargs = {}
    if usecols is not None:
        kwargs["usecols"] = usecols
    if sheet_name is not None:
        return pd.read_excel(buf, sheet_name=sheet_name, **kwargs)
    return pd.read_excel(buf, **kwargs)

def optimize_dataframe(df: pd.DataFrame, downcast_numeric=True, to_category=True, cat_threshold=2000) -> pd.DataFrame:
    out = df.copy()
    if downcast_numeric:
        for col in out.select_dtypes(include=["int", "int64", "float", "float64"]).columns:
            out[col] = pd.to_numeric(out[col], errors="ignore", downcast="integer")
            out[col] = pd.to_numeric(out[col], errors="ignore", downcast="float")
    if to_category:
        for col in out.select_dtypes(include=["object"]).columns:
            uniq = out[col].nunique(dropna=True)
            if 0 < uniq <= cat_threshold:
                out[col] = out[col].astype("category")
    return out

# =========================================================
# Sidebar (global)
# =========================================================
st.sidebar.header("🔐 API & Model")
api_key = get_api_key()
model_choice = st.sidebar.selectbox("Model", ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o"], index=0)

st.sidebar.divider()
st.sidebar.header("⚙️ Throughput")
threads = st.sidebar.slider("Concurrency", 1, 16, 6)
target_rpm = st.sidebar.slider("Target RPM", 0, 600, 120, help="0 = no throttling")
skip_short = st.sidebar.checkbox("Skip very short texts (≤10 chars)", True)
use_heuristics = st.sidebar.checkbox("Use heuristics before LLM", True)
only_missing = st.sidebar.checkbox("Only process rows where outputs are missing", True)

st.sidebar.divider()
with st.sidebar.expander("🧹 Session", expanded=False):
    if st.button("Reset session state"):
        for k in ["df","results_df","memo_cache","file_sig","cfg","filtered_df","process_df","task_cfg","keyword_gate"]:
            st.session_state.pop(k, None)
        st.rerun()

# =========================================================
# Tabs
# =========================================================
tabs = st.tabs(["📥 Load", "🧩 Configure", "🔎 Filter", "🧰 Tasks", "🚀 Run", "💬 Ask", "📈 Analyze", "⬇️ Export"])

# =========================================================
# 1) LOAD
# =========================================================
with tabs[0]:
    st.subheader("1) Upload your reviews file")
    uploaded_file = st.file_uploader("Upload an Excel or CSV file of reviews", type=["xlsx", "csv"])
    if not uploaded_file:
        st.info("Upload a file to begin.")
        st.stop()

    file_bytes = uploaded_file.getvalue()
    is_csv = uploaded_file.name.lower().endswith(".csv")

    # Reset caches if file changed
    file_sig = (uploaded_file.name, len(file_bytes), hashlib.md5(file_bytes[:2048]).hexdigest())
    if st.session_state.get("file_sig") != file_sig:
        st.session_state["file_sig"] = file_sig
        for k in ["df","results_df","memo_cache","cfg","filtered_df","process_df","task_cfg","keyword_gate"]:
            st.session_state.pop(k, None)

    with st.expander("⚡ Loader options", expanded=False):
        if is_csv:
            use_pyarrow = st.checkbox("Use pyarrow CSV engine", value=True)
            if st.checkbox("Load only selected columns", value=False):
                import io
                hdr_df = pd.read_csv(io.BytesIO(file_bytes), nrows=0)
                chosen_cols = st.multiselect("Columns to load", options=list(hdr_df.columns), default=list(hdr_df.columns))
                df = load_csv_cached(file_bytes, use_pyarrow, usecols=chosen_cols)
            else:
                df = load_csv_cached(file_bytes, use_pyarrow=True)
        else:
            xls = pd.ExcelFile(BytesIO(file_bytes))
            sheet_names = xls.sheet_names
            default_sheet = "Star Walk scrubbed verbatims" if "Star Walk scrubbed verbatims" in sheet_names else sheet_names[0]
            sheet_name = st.selectbox("Sheet to load", options=sheet_names, index=sheet_names.index(default_sheet))
            df = load_excel_cached(file_bytes, sheet_name=sheet_name)

    if df.empty:
        st.warning("The uploaded file appears to be empty.")
        st.stop()

    st.session_state["df"] = df

    c1, c2, c3 = st.columns(3)
    c1.metric("Rows", f"{len(df):,}")
    c2.metric("Columns", f"{df.shape[1]:,}")
    c3.metric("Run stamp", RUN_STAMP)

    with st.expander("🧠 Optional memory optimization", expanded=False):
        do_downcast = st.checkbox("Downcast numeric dtypes", value=True)
        to_category = st.checkbox("Convert low-cardinality object columns to category", value=True)
        if st.button("Apply optimization"):
            st.session_state["df"] = optimize_dataframe(df, downcast_numeric=do_downcast, to_category=to_category)
            st.success("Optimization applied.")
            st.rerun()

    st.write("### Preview")
    preview_rows = st.slider("Preview rows", 5, 200, 25, 5)
    sample_preview = st.checkbox("Random sample preview (instead of head)")
    df = st.session_state["df"]
    if sample_preview:
        safe_show_df(df.sample(min(preview_rows, len(df)), random_state=42), preview_rows, "Preview sample")
    else:
        safe_show_df(df, preview_rows, "Preview")

# =========================================================
# 2) CONFIGURE
# =========================================================
with tabs[1]:
    st.subheader("2) Configure columns")
    df = st.session_state.get("df")
    if df is None:
        st.info("Upload a file first.")
        st.stop()

    def suggest_text_column(df: pd.DataFrame) -> Optional[str]:
        preferred = ["Verbatim","Review Text","Review","Review Body","Comment","Customer Review","Text","Body"]
        for c in preferred:
            if c in df.columns:
                return c
        obj = df.select_dtypes(include=["object","category"]).columns.tolist()
        return obj[0] if obj else (df.columns[0] if len(df.columns) else None)

    def suggest_date_column(df: pd.DataFrame) -> Optional[str]:
        preferred = ["Review Date","Date","Created At","Date Created","Submitted At","Timestamp"]
        for c in preferred:
            if c in df.columns:
                return c
        for c in df.columns:
            n = str(c).lower()
            if "date" in n or "time" in n:
                return c
        return None

    def suggest_rating_column(df: pd.DataFrame) -> Optional[str]:
        preferred = ["Star Rating","Rating","Stars","Score","Overall Rating"]
        for c in preferred:
            if c in df.columns:
                return c
        return None

    text_suggest = suggest_text_column(df)
    text_col = st.selectbox(
        "Text column (review text sent to LLM)",
        options=df.columns,
        index=(list(df.columns).index(text_suggest) if text_suggest in df.columns else 0),
    )

    date_suggest = suggest_date_column(df)
    date_opts = ["<none>"] + list(df.columns)
    date_idx = date_opts.index(date_suggest) if (date_suggest and date_suggest in df.columns) else 0
    review_date_col = st.selectbox("Review date/time column (optional)", options=date_opts, index=date_idx)
    if review_date_col == "<none>":
        review_date_col = None

    rating_suggest = suggest_rating_column(df)
    rating_opts = ["<none>"] + list(df.columns)
    rating_idx = rating_opts.index(rating_suggest) if (rating_suggest and rating_suggest in df.columns) else 0
    rating_col = st.selectbox("Actual rating column (optional, for comparison)", options=rating_opts, index=rating_idx)
    if rating_col == "<none>":
        rating_col = None

    st.session_state["cfg"] = {"text_col": text_col, "review_date_col": review_date_col, "rating_col": rating_col}

    st.markdown(
        "<div class='small-muted'>Text column is what gets sent to the model row-by-row. "
        "Review date helps with purchase-date logic. Rating column helps compare predicted vs actual.</div>",
        unsafe_allow_html=True,
    )

# =========================================================
# 3) FILTER
# =========================================================
with tabs[2]:
    st.subheader("3) Filter reviews")
    df = st.session_state.get("df")
    cfg = st.session_state.get("cfg", {})
    if df is None or not cfg:
        st.info("Complete Load → Configure first.")
        st.stop()

    text_col = cfg["text_col"]
    rating_col = cfg.get("rating_col")

    filtered_df = df.copy()

    # Quick rating filters (if actual rating exists)
    if rating_col and rating_col in filtered_df.columns:
        cA, cB = st.columns(2)
        with cA:
            q_low = st.checkbox("Quick Filter: Actual rating ≤ 3 (detractors)")
        with cB:
            q_high = st.checkbox("Quick Filter: Actual rating ≥ 4 (positive/advocates)")
        if q_low and q_high:
            st.warning("Choose only one of the quick rating filters.")
        elif q_low:
            filtered_df = filtered_df[pd.to_numeric(filtered_df[rating_col], errors="coerce") <= 3]
        elif q_high:
            filtered_df = filtered_df[pd.to_numeric(filtered_df[rating_col], errors="coerce") >= 4]
    else:
        st.info("No actual rating column selected (Configure tab). Quick rating filters are hidden.")

    with st.expander("Custom filters (exact match)", expanded=True):
        filter_cols = st.multiselect("Select columns to filter on", options=df.columns)
        for col in filter_cols:
            u = sorted(filtered_df[col].dropna().astype(str).unique().tolist())
            sel = st.multiselect(f"Values to keep for '{col}'", options=u, key=f"flt_{col}")
            if sel:
                filtered_df = filtered_df[filtered_df[col].astype(str).isin(sel)]

    st.write("### Filtered Preview (view)")
    safe_show_df(filtered_df, max_rows=min(25, len(filtered_df)), label="Filtered preview")
    st.caption(f"Rows in view: {len(filtered_df):,} / {len(df):,}")

    if filtered_df.empty:
        st.warning("No rows match the filters.")
        st.stop()

    st.divider()

    # Keyword gate for processing only
    st.markdown("#### ✅ Processing keyword gate (only affects what gets sent to the model)")
    kw_enabled = st.checkbox("Enable keyword gate for processing", value=False)
    kw_raw = st.text_area("Keywords (comma or newline separated)", value="", height=70, placeholder="e.g., loud, noisy, error, filter, blockage")
    kw_mode = st.radio("Match rule", ["Any keyword", "All keywords"], horizontal=True, index=0)
    kw_whole = st.checkbox("Whole word match", value=False)
    kw_regex = st.checkbox("Treat keywords as regex", value=False)
    kw_case = st.checkbox("Case sensitive", value=False)

    kw_list = parse_keywords(kw_raw) if kw_enabled else []
    process_df = filtered_df
    if kw_enabled and kw_list:
        m = keyword_mask(
            process_df[text_col].astype(str),
            kw_list,
            mode_any=(kw_mode == "Any keyword"),
            case_sensitive=kw_case,
            use_regex=kw_regex,
            whole_word=kw_whole,
        )
        process_df = process_df.loc[m]

    st.session_state["filtered_df"] = filtered_df
    st.session_state["process_df"] = process_df
    st.session_state["keyword_gate"] = {
        "enabled": kw_enabled,
        "keywords": kw_list,
        "mode_any": (kw_mode == "Any keyword"),
        "whole_word": kw_whole,
        "use_regex": kw_regex,
        "case_sensitive": kw_case,
    }

    a, b, c = st.columns(3)
    a.metric("View rows", f"{len(filtered_df):,}")
    b.metric("Process rows (after keyword gate)", f"{len(process_df):,}")
    c.metric("Keyword gate", "On" if kw_enabled else "Off")

# =========================================================
# 4) TASKS
# =========================================================
with tabs[3]:
    st.subheader("4) Select tasks / presets")
    df = st.session_state.get("df")
    cfg = st.session_state.get("cfg", {})
    filtered_df = st.session_state.get("filtered_df")
    process_df = st.session_state.get("process_df")
    if df is None or not cfg or filtered_df is None or process_df is None:
        st.info("Complete Load → Configure → Filter first.")
        st.stop()

    text_col = cfg["text_col"]
    review_date_col = cfg.get("review_date_col")

    # Preset prompts
    default_purchase_prompt = (
        "You are given:\n"
        "- TEXT: A customer product review.\n"
        "- REVIEW_DATE: When the review was written.\n"
        "- INFERRED_RELATIVE_DATE_HINT: Optional YYYY-MM-DD derived from phrases like 'last September'.\n\n"
        "Task: Extract the ORIGINAL PRODUCT PURCHASE DATE.\n\n"
        "Rules:\n"
        "- Only return when the customer bought/received the product.\n"
        "- DO NOT return warranty expiration, registration, delivery, replacement shipment, etc.\n"
        "- The purchase date must be on or before REVIEW_DATE.\n"
        "- If unclear or only non-purchase dates, return 'unknown'.\n"
        "- If only month+year are clear, return YYYY-MM-15.\n\n"
        "Return ONLY a JSON object matching the schema."
    )
    default_symptom_prompt = (
        "Given TEXT (a customer review), identify the PRIMARY technical/functional symptom "
        "(e.g., 'no power', 'weak airflow', 'overheating', 'burning smell', 'unit shuts off', 'error code'). "
        "Be concise.\n\n"
        "Return ONLY a JSON object matching the schema."
    )
    default_error_indicator_prompt = (
        "Given TEXT (a customer review), detect any explicit error code, flashing/blinking lights, "
        "or on-product/on-screen error icons/messages.\n"
        "If present, return a concise phrase (e.g., 'E02', 'red light flashing'); else 'none'.\n\n"
        "Return ONLY a JSON object matching the schema."
    )
    default_model_number_prompt = (
        "Given TEXT (a customer review), extract the MODEL NUMBER if mentioned (e.g., NV356E, ZU62, HZ2000, HT300). "
        "If multiple, return the most specific. If none, return 'unknown'.\n\n"
        "Return ONLY a JSON object matching the schema."
    )

    # NEW preset: predicted rating 1-5
    default_predicted_rating_prompt = (
        "Given TEXT (a customer review), predict the star rating the customer likely gave on a 1–5 scale.\n\n"
        "Guidance:\n"
        "- 5 = very positive, enthusiastic, strongly recommends, no meaningful issues.\n"
        "- 4 = positive overall with minor complaints.\n"
        "- 3 = mixed / neutral / some pros and cons.\n"
        "- 2 = negative, significant issues, dissatisfaction.\n"
        "- 1 = extremely negative, angry, product failure, return/refund likely.\n\n"
        "Return ONLY a JSON object matching the schema with an integer 1–5."
    )

    purchase_system = "You extract structured purchase dates from noisy customer review text."
    symptom_system  = "You classify customer reviews by primary technical symptom."
    error_system    = "You extract error codes and UI/LED indicators from customer reviews."
    model_system    = "You extract product model numbers from review text."
    rating_system   = "You predict 1-5 star ratings from review text sentiment and content."

    purchase_schema = {"type": "object", "properties": {"purchase_date": {"type": "string"}}, "required": ["purchase_date"], "additionalProperties": False}
    symptom_schema  = {"type": "object", "properties": {"symptom": {"type": "string"}}, "required": ["symptom"], "additionalProperties": False}
    error_schema    = {"type": "object", "properties": {"error_indicator": {"type": "string"}}, "required": ["error_indicator"], "additionalProperties": False}
    model_schema    = {"type": "object", "properties": {"model_number": {"type": "string"}}, "required": ["model_number"], "additionalProperties": False}
    rating_schema   = {
        "type": "object",
        "properties": {"predicted_rating": {"type": "integer", "minimum": 1, "maximum": 5}},
        "required": ["predicted_rating"],
        "additionalProperties": False
    }

    st.markdown("#### Presets")
    c1, c2, c3, c4, c5 = st.columns(5)
    use_purchase  = c1.checkbox("📅 purchase_date", value=False)
    use_symptom   = c2.checkbox("🩺 symptom", value=True)
    use_error     = c3.checkbox("💡 error_indicator", value=True)
    use_modelnum  = c4.checkbox("🔢 model_number", value=False)
    use_pred_rate = c5.checkbox("⭐ predicted_rating (1–5)", value=True)

    with st.expander("✏️ Edit preset prompts", expanded=False):
        purchase_prompt = st.text_area("Purchase prompt", value=default_purchase_prompt, height=180, disabled=not use_purchase) if use_purchase else None
        symptom_prompt  = st.text_area("Symptom prompt", value=default_symptom_prompt, height=140, disabled=not use_symptom) if use_symptom else None
        error_prompt    = st.text_area("Error prompt", value=default_error_indicator_prompt, height=140, disabled=not use_error) if use_error else None
        model_prompt    = st.text_area("Model prompt", value=default_model_number_prompt, height=140, disabled=not use_modelnum) if use_modelnum else None
        pred_prompt     = st.text_area("Predicted rating prompt", value=default_predicted_rating_prompt, height=170, disabled=not use_pred_rate) if use_pred_rate else None

    st.markdown("#### Custom prompts (free-form text output)")
    use_custom = st.checkbox("Enable custom prompts", value=False)
    custom_prompts: List[str] = []
    custom_outcols: List[str] = []
    if use_custom:
        n_custom = st.number_input("How many custom prompts?", min_value=1, max_value=12, value=1, step=1)
        for i in range(int(n_custom)):
            a, b = st.columns(2)
            with a:
                p = st.text_area(f"Prompt {i+1}", key=f"cp_{i}")
            with b:
                o = st.text_input(f"Output column {i+1}", key=f"co_{i}")
            if p.strip() and o.strip():
                custom_prompts.append(p.strip())
                custom_outcols.append(o.strip())

    st.subheader("✨ AI Prompt Assistant (optional)")
    use_prompt_assistant = st.checkbox("Use AI to suggest a prompt for you", value=False)
    if use_prompt_assistant:
        desc = st.text_area(
            "Describe what you want the model to extract/classify from each review:",
            placeholder="Example: Classify each review into airflow, power, overheating, noise, cosmetic, or other...",
        )
        sample_n = st.slider("How many sample rows to show the AI", min_value=1, max_value=10, value=5)
        if st.button("Generate suggested prompt"):
            if not api_key:
                st.error("Add your API key (secrets or override) to use the prompt assistant.")
            else:
                client = get_client(api_key)
                series = filtered_df[text_col].dropna().astype(str)
                if series.empty:
                    st.warning("No non-empty values in the selected text column.")
                else:
                    examples = series.sample(min(sample_n, len(series)), random_state=42)
                    example_block = "\n".join(f"- {normalize_text(t)[:400]}" for t in examples)
                    system_msg = "You are an expert prompt engineer for LLMs analyzing customer product reviews row-by-row."
                    user_msg = (
                        f"The analyst wants the model to do the following:\n{desc}\n\n"
                        f"Here are sample values from the text column '{text_col}':\n{example_block}\n\n"
                        "Write ONE reusable prompt they can apply row-by-row. "
                        "Do not include examples in the output, just the final prompt."
                    )
                    try:
                        suggestion = call_llm_text(client, model_choice, system_msg, user_msg, limiter=None)
                        st.success("Suggested prompt:")
                        st.code(suggestion)
                    except Exception as e:
                        st.error(f"Error generating prompt suggestion: {e}")

    # Build task config
    PRESETS = []
    active_cols: List[str] = []

    if use_purchase:
        PRESETS.append(("purchase_date", purchase_system, purchase_prompt, purchase_schema))
        active_cols.append("purchase_date")
    if use_symptom:
        PRESETS.append(("symptom", symptom_system, symptom_prompt, symptom_schema))
        active_cols.append("symptom")
    if use_error:
        PRESETS.append(("error_indicator", error_system, error_prompt, error_schema))
        active_cols.append("error_indicator")
    if use_modelnum:
        PRESETS.append(("model_number", model_system, model_prompt, model_schema))
        active_cols.append("model_number")
    if use_pred_rate:
        PRESETS.append(("predicted_rating", rating_system, pred_prompt, rating_schema))
        active_cols.append("predicted_rating")

    active_cols += custom_outcols

    st.session_state["task_cfg"] = {
        "presets": PRESETS,
        "custom_prompts": custom_prompts,
        "custom_outcols": custom_outcols,
        "active_cols": active_cols,
    }

    mx1, mx2, mx3, mx4 = st.columns(4)
    mx1.metric("View rows", f"{len(filtered_df):,}")
    mx2.metric("Process rows", f"{len(process_df):,}")
    mx3.metric("Selected outputs", f"{len(active_cols):,}")
    mx4.metric("Keyword gate", "On" if st.session_state.get("keyword_gate", {}).get("enabled") else "Off")

# =========================================================
# 5) RUN
# =========================================================
with tabs[4]:
    st.subheader("5) Run processing")
    df = st.session_state.get("df")
    cfg = st.session_state.get("cfg", {})
    task_cfg = st.session_state.get("task_cfg")
    filtered_df = st.session_state.get("filtered_df")
    process_df = st.session_state.get("process_df")

    if df is None or not cfg or task_cfg is None or filtered_df is None or process_df is None:
        st.info("Complete Load → Configure → Filter → Tasks first.")
        st.stop()

    text_col = cfg["text_col"]
    review_date_col = cfg.get("review_date_col")
    PRESETS = task_cfg["presets"]
    custom_prompts = task_cfg["custom_prompts"]
    custom_outcols = task_cfg["custom_outcols"]
    active_cols = task_cfg["active_cols"]

    if "memo_cache" not in st.session_state:
        st.session_state.memo_cache = {}

    max_rows = st.number_input("Max rows to process (this run)", min_value=1, max_value=max(1, len(process_df)), value=min(len(process_df), 2000))
    dry_run = st.checkbox("Dry run (no API calls)", value=False)

    # Determine rows to process
    rows = process_df.head(int(max_rows)).copy()

    # Initialize results_df
    results_df = st.session_state.get("results_df")
    if results_df is None or len(results_df) != len(df):
        results_df = df.copy()

    for c in active_cols:
        if c not in results_df.columns:
            results_df[c] = pd.NA

    # Only missing
    if only_missing and active_cols and not rows.empty:
        sub = results_df.loc[rows.index, active_cols]
        miss_mask = sub.apply(lambda col: col.isna() | (col.astype(str).str.strip() == ""), axis=1)
        need_mask = miss_mask.any(axis=1)
        rows = rows.loc[need_mask]

    est_calls = len(rows) * (len(PRESETS) + len(custom_prompts))
    st.info(
        f"Queued rows: **{len(rows):,}** (process_df={len(process_df):,}). "
        f"Outputs: **{len(active_cols)}**. Upper-bound API calls: **{est_calls:,}**."
    )

    def process_row(
        idx: int,
        row: pd.Series,
        client: OpenAI,
        model: str,
        presets,
        custom_prompts,
        custom_outcols,
        text_col: str,
        review_date_col: Optional[str],
        use_heuristics: bool,
        skip_short: bool,
        memo: Dict[Tuple[str, str], Any],
        limiter: Optional[RateLimiter],
    ) -> Tuple[int, Dict[str, Any]]:
        out: Dict[str, Any] = {}
        text_val = normalize_text(row.get(text_col, ""))
        date_val = str(row.get(review_date_col, "")) if review_date_col else ""
        hint = infer_year_from_relative(text_val, date_val) if review_date_col else None

        if skip_short and len(text_val.strip()) <= 10:
            for c in active_cols:
                out[c] = ""
            return idx, out

        for col, sys, prompt, schema in presets:
            key = (text_val, f"preset::{col}")
            if key in memo:
                value = memo[key]
            else:
                value = None

                if use_heuristics:
                    if col == "error_indicator":
                        value = heuristic_error_indicator(text_val)
                    elif col == "model_number":
                        value = heuristic_model_number(text_val)
                    elif col == "predicted_rating":
                        value = heuristic_predicted_rating(text_val)

                if value is None:
                    user_prompt = (prompt or "") + f"\n\nTEXT:\n{text_val}\n"
                    if col == "purchase_date":
                        user_prompt += f"\nREVIEW_DATE: {date_val}\nINFERRED_RELATIVE_DATE_HINT: {hint}"
                    data = call_llm_json_safe(
                        client,
                        model,
                        sys,
                        user_prompt,
                        f"{col}_extraction",
                        schema,
                        limiter,
                    )
                    value = data.get(col) if isinstance(data, dict) else None
                    if value is None and isinstance(data, dict):
                        value = data.get("_raw")

                # normalize
                if col == "purchase_date":
                    value = normalize_purchase_date_output(value, date_val)
                elif col == "predicted_rating":
                    value = normalize_predicted_rating(value)
                elif col == "error_indicator":
                    if value is None:
                        value = "none"

                memo[key] = value
            out[col] = value

        # Custom prompts
        for p, c in zip(custom_prompts, custom_outcols):
            key = (text_val, f"custom::{c}")
            if key in memo:
                out[c] = memo[key]
            else:
                user_prompt = f"TEXT:\n{text_val}\n\nTASK:\n{p}"
                val = call_llm_text(client, model, "You analyze customer product review text precisely.", user_prompt, limiter)
                memo[key] = val
                out[c] = val

        return idx, out

    if st.button("🚀 Run"):
        if dry_run:
            st.success("Dry run complete ✅ (no API calls made)")
            safe_show_df(rows, max_rows=min(50, len(rows)), label="Dry run rows")
        else:
            if not api_key:
                st.error("No API key configured.")
            elif not (PRESETS or custom_prompts):
                st.error("Select at least one preset or add a custom prompt.")
            elif rows.empty:
                st.info("Nothing to do (no rows queued).")
            else:
                client = get_client(api_key)
                limiter = RateLimiter(target_rpm) if target_rpm > 0 else None
                memo = st.session_state.memo_cache

                progress = st.progress(0.0)
                status = st.empty()

                futures = []
                with ThreadPoolExecutor(max_workers=threads) as pool:
                    for idx, row in rows.iterrows():
                        futures.append(
                            pool.submit(
                                process_row,
                                idx,
                                row,
                                client,
                                model_choice,
                                PRESETS,
                                custom_prompts,
                                custom_outcols,
                                text_col,
                                review_date_col,
                                use_heuristics,
                                skip_short,
                                memo,
                                limiter,
                            )
                        )

                    done = 0
                    for fut in as_completed(futures):
                        idx, out = fut.result()
                        for col, val in out.items():
                            results_df.loc[idx, col] = val
                        done += 1
                        progress.progress(done / len(futures))
                        status.write(f"Processed {done}/{len(futures)} rows...")

                # Derived ownership period if both exist
                if "purchase_date" in results_df.columns and review_date_col and review_date_col in results_df.columns:
                    try:
                        pd_purchase = pd.to_datetime(results_df["purchase_date"], errors="coerce")
                        pd_review = pd.to_datetime(results_df[review_date_col], errors="coerce")
                        results_df["ownership_period_days"] = (pd_review - pd_purchase).dt.days
                    except Exception as e:
                        st.warning(f"Ownership calculation issue: {e}")

                st.session_state["results_df"] = results_df
                st.success("Processing complete ✅")

    st.markdown("### Current enriched preview (filtered view)")
    results_df = st.session_state.get("results_df")
    if results_df is None:
        st.info("Run processing to see enriched outputs.")
    else:
        view = results_df.loc[filtered_df.index]
        cols = [text_col] + [c for c in (active_cols or []) if c in view.columns]
        if review_date_col and review_date_col in view.columns:
            cols = [review_date_col] + cols
        safe_show_df(view[cols], max_rows=30, label="Enriched preview")

# =========================================================
# 6) ASK (dataset Q&A)
# =========================================================
with tabs[5]:
    st.subheader("6) Ask the Reviews (Q&A)")
    st.caption("Asks are answered from the most relevant reviews (simple retrieval) + the model. The answer cites review IDs (row index).")

    df = st.session_state.get("df")
    cfg = st.session_state.get("cfg", {})
    filtered_df = st.session_state.get("filtered_df")
    process_df = st.session_state.get("process_df")
    results_df = st.session_state.get("results_df")

    if df is None or not cfg or filtered_df is None or process_df is None:
        st.info("Complete Load → Configure → Filter first.")
        st.stop()

    text_col = cfg["text_col"]
    rating_col = cfg.get("rating_col")
    review_date_col = cfg.get("review_date_col")

    base = results_df if results_df is not None else df

    scope = st.radio("Scope", ["Filtered view", "Process queue", "All rows"], horizontal=True, index=0)
    if scope == "Filtered view":
        scope_idx = filtered_df.index
    elif scope == "Process queue":
        scope_idx = process_df.index
    else:
        scope_idx = base.index

    question = st.text_input("Question", placeholder="Example: Which setting is the most loud? What do users complain about most?")
    top_k = st.slider("How many matching reviews to use as evidence", 5, 60, 20, 5)
    max_chars = st.slider("Max chars per review in context", 120, 600, 280, 20)

    def get_top_matches(query: str, series: pd.Series, k: int) -> pd.Index:
        q = (query or "").strip().lower()
        if not q:
            return series.sample(min(k, len(series)), random_state=42).index

        terms = re.findall(r"[a-z0-9']{3,}", q)
        terms = list(dict.fromkeys(terms))[:20]
        if not terms:
            return series.sample(min(k, len(series)), random_state=42).index

        s = series.fillna("").astype(str).str.lower()
        scores = pd.Series(0, index=s.index, dtype="int")
        for t in terms:
            scores += s.str.contains(re.escape(t), regex=True, na=False).astype(int)

        scored = scores[scores > 0].sort_values(ascending=False)
        if scored.empty:
            return series.sample(min(k, len(series)), random_state=42).index
        return scored.head(k).index

    if st.button("Answer"):
        if not api_key:
            st.error("Add your API key (secrets or override) to answer questions.")
        elif not question.strip():
            st.warning("Type a question first.")
        else:
            scope_df = base.loc[scope_idx]
            text_series = scope_df[text_col].astype(str)

            top_idx = get_top_matches(question, text_series, top_k)
            evidence_df = scope_df.loc[top_idx].copy()

            # Build evidence block with IDs
            lines = []
            for rid, r in evidence_df.iterrows():
                meta = []
                if rating_col and rating_col in evidence_df.columns:
                    meta.append(f"actual_rating={r.get(rating_col)}")
                if "predicted_rating" in evidence_df.columns:
                    meta.append(f"pred_rating={r.get('predicted_rating')}")
                if review_date_col and review_date_col in evidence_df.columns:
                    meta.append(f"date={r.get(review_date_col)}")
                meta_s = (" | " + ", ".join(meta)) if meta else ""
                txt = normalize_text(r.get(text_col, ""))[:max_chars]
                lines.append(f"[RID {rid}]{meta_s}: {txt}")

            evidence_block = "\n".join(lines)

            system_msg = (
                "You are a product quality analyst. Answer using ONLY the provided review excerpts. "
                "If the evidence is insufficient, say what is missing. "
                "When making claims, cite the relevant RIDs."
            )
            user_msg = (
                f"QUESTION:\n{question}\n\n"
                f"EVIDENCE (review excerpts):\n{evidence_block}\n\n"
                "Answer clearly. Provide:\n"
                "1) Direct answer\n"
                "2) Supporting evidence with RID citations\n"
                "3) If relevant, quick summary of patterns\n"
            )

            client = get_client(api_key)
            try:
                ans = call_llm_text(client, model_choice, system_msg, user_msg, limiter=None)
                st.markdown("### Answer")
                st.write(ans)

                st.markdown("### Evidence used")
                show_cols = [text_col]
                if rating_col and rating_col in evidence_df.columns:
                    show_cols = [rating_col] + show_cols
                if "predicted_rating" in evidence_df.columns:
                    show_cols = ["predicted_rating"] + show_cols
                if review_date_col and review_date_col in evidence_df.columns:
                    show_cols = [review_date_col] + show_cols
                safe_show_df(evidence_df[show_cols], max_rows=min(40, len(evidence_df)), label="Evidence table")
            except Exception as e:
                st.error(f"Error answering: {e}")

# =========================================================
# 7) ANALYZE
# =========================================================
with tabs[6]:
    st.subheader("7) Analyze")
    results_df = st.session_state.get("results_df")
    cfg = st.session_state.get("cfg", {})
    filtered_df = st.session_state.get("filtered_df")

    if results_df is None or not cfg or filtered_df is None:
        st.info("Run processing first.")
        st.stop()

    text_col = cfg["text_col"]
    rating_col = cfg.get("rating_col")
    review_date_col = cfg.get("review_date_col")

    view = results_df.loc[filtered_df.index]

    st.markdown("### Enriched preview")
    safe_show_df(view, max_rows=25, label="Enriched view preview")

    if "predicted_rating" in view.columns:
        st.markdown("### Predicted Rating Distribution (1–5)")
        pr = pd.to_numeric(view["predicted_rating"], errors="coerce").dropna()
        if not pr.empty:
            st.bar_chart(pr.value_counts().sort_index())
        else:
            st.info("No predicted_rating values yet.")

    if rating_col and rating_col in view.columns and "predicted_rating" in view.columns:
        st.markdown("### Predicted vs Actual (if available)")
        a = pd.to_numeric(view[rating_col], errors="coerce")
        p = pd.to_numeric(view["predicted_rating"], errors="coerce")
        both = pd.DataFrame({"actual": a, "pred": p}).dropna()
        if both.empty:
            st.info("No rows have both actual and predicted ratings.")
        else:
            ct = pd.crosstab(both["actual"].astype(int), both["pred"].astype(int), rownames=["Actual"], colnames=["Predicted"])
            st.dataframe(ct)
            both["diff"] = both["pred"] - both["actual"]
            st.write("Mean (pred - actual):", float(both["diff"].mean()))

    if "symptom" in view.columns:
        st.markdown("### Symptom Distribution")
        s = view["symptom"].dropna().astype(str).str.strip()
        s = s[s != ""]
        if not s.empty:
            top = s.value_counts().head(25)
            st.bar_chart(top)
            st.dataframe(top.to_frame("count"))
        else:
            st.info("No symptom values.")

    if "error_indicator" in view.columns:
        st.markdown("### Error Indicators")
        ei = view["error_indicator"].dropna().astype(str).str.strip()
        ei = ei[(ei != "") & (ei.str.lower() != "none")]
        if not ei.empty:
            top = ei.value_counts().head(25)
            st.bar_chart(top)
            st.dataframe(top.to_frame("count"))
        else:
            st.info("No error indicators extracted.")

    if review_date_col and review_date_col in view.columns:
        st.markdown(f"### Reviews Over Time (based on '{review_date_col}')")
        cdt = pd.to_datetime(view[review_date_col], errors="coerce").dropna()
        if not cdt.empty:
            by_month = cdt.dt.to_period("M").value_counts().sort_index()
            by_month.index = by_month.index.astype(str)
            st.line_chart(by_month)
        else:
            st.info("No valid review dates.")

# =========================================================
# 8) EXPORT
# =========================================================
with tabs[7]:
    st.subheader("8) Export")
    results_df = st.session_state.get("results_df")
    if results_df is None:
        st.info("Run processing first to create an enriched dataset.")
        st.stop()

    buffer = BytesIO()
    results_df.to_excel(buffer, index=False)
    buffer.seek(0)
    st.download_button(
        "⬇️ Download processed file",
        data=buffer,
        file_name="processed_reviews_output.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    with st.expander("Show current config"):
        st.json(
            {
                "run_stamp": RUN_STAMP,
                "cfg": st.session_state.get("cfg", {}),
                "keyword_gate": st.session_state.get("keyword_gate", {}),
                "tasks": list((st.session_state.get("task_cfg") or {}).get("active_cols", [])),
            }
        )
