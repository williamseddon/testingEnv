import streamlit as st
import pandas as pd
import re
import json
import time
from io import BytesIO
from openai import OpenAI
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# =========================================================
# App Config
# =========================================================
st.set_page_config(page_title="AI QE Assistant — High Throughput", layout="wide")
st.title("🦈📊 AI QE Assistant — High-Throughput, Big-File Friendly")

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
    Simple leaky-bucket rate limiter based on RPM (requests per minute).
    Call acquire() before each request.
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
# Heuristics & Parsing
# =========================================================
MONTHS = [
    "january","february","march","april","may","june",
    "july","august","september","october","november","december"
]
MONTH_ABBR = ["jan","feb","mar","apr","may","jun","jul","aug","sep","sept","oct","nov","dec"]

# Context keywords for separating purchase vs warranty/coverage dates
WARRANTY_CONTEXT_KEYWORDS = [
    "warranty",
    "extended warranty",
    "expir",         # matches expire, expires, expiration
    "coverage",
    "covered",
    "protection plan",
    "protection",
    "guarantee",
    "guaranteed",
]
PURCHASE_CONTEXT_KEYWORDS = [
    "purchase",
    "purchased",
    "bought",
    "buy",
    "bought it",
    "bought this",
    "purchase date",
    "purchased on",
    "order date",
    "ordered",
    "i got it",
    "got it",
    "have had it since",
    "owned since",
    "since i bought",
]

def infer_year_from_relative(text: str, call_date_str: str) -> Optional[str]:
    """
    If text says 'last September', infer YYYY-MM-15 using call_date year - 1,
    but skip if that phrase is clearly in warranty/coverage context.
    """
    if not isinstance(text, str) or not text:
        return None
    if not isinstance(call_date_str, str) or not call_date_str:
        return None
    try:
        first_token = call_date_str.split()[0]
        call_date = datetime.strptime(first_token, "%Y-%m-%d")
    except Exception:
        return None

    t = text.lower()
    # Full month names
    for i, m in enumerate(MONTHS, start=1):
        pattern = rf"last\s+{m}"
        for match in re.finditer(pattern, t):
            ctx_start = max(0, match.start() - 40)
            ctx_end = min(len(t), match.end() + 40)
            ctx = t[ctx_start:ctx_end]
            if any(k in ctx for k in WARRANTY_CONTEXT_KEYWORDS):
                continue
            return f"{call_date.year - 1}-{i:02d}-15"
    # Abbreviations
    for i, m in enumerate(MONTH_ABBR, start=1):
        pattern = rf"last\s+{m}"
        for match in re.finditer(pattern, t):
            ctx_start = max(0, match.start() - 40)
            ctx_end = min(len(t), match.end() + 40)
            ctx = t[ctx_start:ctx_end]
            if any(k in ctx for k in WARRANTY_CONTEXT_KEYWORDS):
                continue
            month_index = min(i, 12)
            return f"{call_date.year - 1}-{month_index:02d}-15"
    return None

DATE_PATTERNS = [
    r"\b(20\d{2}|19\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b",  # YYYY-MM-DD
    r"\b(0?[1-9]|1[0-2])[/-](0?[1-9]|[12]\d|3[01])[/-]((?:19|20)?\d{2})\b",  # MM/DD/YYYY or MM/DD/YY
    r"\b(0?[1-9]|[12]\d|3[01])[/-](0?[1-9]|1[0-2])[/-]((?:19|20)?\d{2})\b",  # DD/MM/YYYY or DD/MM/YY
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+\d{4}\b",
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+\d{4}\b",
    r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{4}\b",
]
DATE_FORMATS = [
    "%Y-%m-%d",
    "%m/%d/%Y", "%m/%d/%y",
    "%d/%m/%Y", "%d/%m/%y",
    "%b %d, %Y", "%B %d, %Y",
    "%d %B %Y", "%d %b %Y",
    "%B %Y", "%b %Y",
]

def try_parse_date(text: str) -> Optional[str]:
    """
    Parse first explicit date found into YYYY-MM-DD.
    For month+year (no day), assume day=15 to approximate mid-month.
    NOTE: This does not look at warranty context; we use it on short strings.
    """
    if not isinstance(text, str) or not text:
        return None
    for pat in DATE_PATTERNS:
        for m in re.finditer(pat, text):
            chunk = m.group(0)
            chunk = re.sub(r"(st|nd|rd|th)", "", chunk, flags=re.IGNORECASE)
            chunk = re.sub(r"\s{2,}", " ", chunk).strip()
            for fmt in DATE_FORMATS:
                try:
                    dt = datetime.strptime(chunk, fmt)
                    # For month-year patterns, bump day to 15 instead of 1
                    if fmt in ("%B %Y", "%b %Y"):
                        dt = dt.replace(day=15)
                    return dt.strftime("%Y-%m-%d")
                except Exception:
                    continue
    return None

def infer_month_only_date(text: str, call_date_str: Optional[str]) -> Optional[str]:
    """
    If text has a bare month name (e.g. 'in October') with no explicit day/year,
    assume same year as call_date and day=15, but skip warranty/coverage contexts.
    """
    if not isinstance(text, str) or not text:
        return None
    if not isinstance(call_date_str, str) or not call_date_str:
        return None
    try:
        call_year = datetime.strptime(call_date_str.split()[0], "%Y-%m-%d").year
    except Exception:
        return None

    t = text
    lower = t.lower()
    # Full month names
    for i, m in enumerate(MONTHS, start=1):
        for match in re.finditer(rf"\b{m}\b", lower):
            ctx_start = max(0, match.start() - 40)
            ctx_end = min(len(lower), match.end() + 40)
            ctx = lower[ctx_start:ctx_end]
            if any(k in ctx for k in WARRANTY_CONTEXT_KEYWORDS):
                continue
            return f"{call_year}-{i:02d}-15"
    # Abbreviations
    for i, m in enumerate(MONTH_ABBR, start=1):
        for match in re.finditer(rf"\b{m}\b", lower):
            ctx_start = max(0, match.start() - 40)
            ctx_end = min(len(lower), match.end() + 40)
            ctx = lower[ctx_start:ctx_end]
            if any(k in ctx for k in WARRANTY_CONTEXT_KEYWORDS):
                continue
            month_index = min(i, 12)
            return f"{call_year}-{month_index:02d}-15"
    return None

def extract_date_candidates(text: str, call_date_str: Optional[str]) -> List[Dict[str, Any]]:
    """
    Extract explicit date candidates with context:
    - marks if in warranty/coverage context
    - marks if close to purchase-related wording
    - marks if clearly in the future vs CALL_DATE (with small buffer)
    """
    candidates: List[Dict[str, Any]] = []
    if not isinstance(text, str) or not text:
        return candidates

    call_dt = None
    if isinstance(call_date_str, str) and call_date_str:
        try:
            call_dt = datetime.strptime(call_date_str.split()[0], "%Y-%m-%d")
        except Exception:
            call_dt = None

    for pat in DATE_PATTERNS:
        for m in re.finditer(pat, text):
            chunk = m.group(0)
            cleaned = re.sub(r"(st|nd|rd|th)", "", chunk, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
            dt = None
            for fmt in DATE_FORMATS:
                try:
                    dt_tmp = datetime.strptime(cleaned, fmt)
                    if fmt in ("%B %Y", "%b %Y"):
                        dt_tmp = dt_tmp.replace(day=15)
                    dt = dt_tmp
                    break
                except Exception:
                    continue
            if dt is None:
                continue

            ctx_start = max(0, m.start() - 40)
            ctx_end = min(len(text), m.end() + 40)
            ctx = text[ctx_start:ctx_end].lower()
            is_warranty = any(k in ctx for k in WARRANTY_CONTEXT_KEYWORDS)
            is_purchase_ctx = any(k in ctx for k in PURCHASE_CONTEXT_KEYWORDS)

            is_future = False
            if call_dt:
                if dt > call_dt + timedelta(days=7):
                    is_future = True

            candidates.append(
                {
                    "dt": dt,
                    "is_warranty": is_warranty,
                    "is_purchase_ctx": is_purchase_ctx,
                    "is_future": is_future,
                }
            )
    return candidates

def purchase_date_heuristic(text: str, call_date_str: Optional[str]) -> Optional[str]:
    """
    Smarter heuristic for purchase date:
    1) Prefer explicit dates not in warranty/coverage context and not obviously in the future.
    2) Prefer those near purchase-related wording.
    3) If nothing explicit, use 'last <month>' (non-warranty context) or bare month references.
    """
    # 1) explicit dates with context
    candidates = extract_date_candidates(text, call_date_str)
    chosen_dt = None

    if candidates:
        # remove future & obvious warranty candidates first
        good = [c for c in candidates if not c["is_warranty"] and not c["is_future"]]
        if not good:
            # fallback: allow non-future even if we couldn't classify context cleanly
            good = [c for c in candidates if not c["is_warranty"]]

        if good:
            purchase_ctx = [c for c in good if c["is_purchase_ctx"]]
            if purchase_ctx:
                purchase_ctx.sort(key=lambda x: x["dt"])
                chosen_dt = purchase_ctx[0]["dt"]
            else:
                # fallback: earliest good date
                good.sort(key=lambda x: x["dt"])
                chosen_dt = good[0]["dt"]

    if chosen_dt:
        return chosen_dt.strftime("%Y-%m-%d")

    # 2) relative language like "last September" (non-warranty context)
    rel = infer_year_from_relative(text, call_date_str or "")
    if rel:
        return rel

    # 3) month-only references like "in October" (non-warranty context)
    mon_only = infer_month_only_date(text, call_date_str)
    if mon_only:
        return mon_only

    return None

def normalize_purchase_date_output(value: Any, call_date_str: Optional[str]) -> str:
    """
    Normalize any purchase_date string into best-guess ISO date if possible.
    Uses same heuristics as above and assumes day=15 when only month present.
    Drops dates clearly after the call date (interpreted as non-purchase).
    """
    if value is None:
        raw = ""
    else:
        raw = str(value).strip()
    if not raw or raw.lower() in ("unknown", "na", "n/a", "none"):
        return "unknown"

    def guard_future(iso_str: str) -> str:
        if not isinstance(call_date_str, str) or not call_date_str:
            return iso_str
        try:
            call_dt = datetime.strptime(call_date_str.split()[0], "%Y-%m-%d")
            dt = datetime.strptime(iso_str, "%Y-%m-%d")
            if dt > call_dt + timedelta(days=7):
                return "unknown"
        except Exception:
            pass
        return iso_str

    # Try parsing the raw string itself
    iso = try_parse_date(raw)
    if iso:
        return guard_future(iso)

    # Try relative/month-only using call_date
    rel = infer_year_from_relative(raw, call_date_str or "")
    if rel:
        return guard_future(rel)
    mon_only = infer_month_only_date(raw, call_date_str or "")
    if mon_only:
        return guard_future(mon_only)

    # Fallback: raw unchanged (may not be parseable)
    return raw

ERROR_CODE_REGEXES = [
    r"\b[Ee]rr(?:or)?\s?[-:]?\s?\d{1,4}\b",
    r"\b[Ee]\s?[-:]?\s?\d{1,4}\b",
    r"\b[Cc]ode\s?[-:]?\s?\d{1,4}\b",
    r"\b[Ff]\s?[-:]?\s?\d{1,4}\b",
]
FLASHING_TERMS = ["flashing", "blinking", "blinks", "blink", "flash", "strobing", "strobe", "pulsing", "pulse"]
UI_TERMS = ["red light", "blue light", "filter icon", "warning light", "led", "indicator", "icon", "display error"]

def heuristic_error_indicator(text: str) -> Optional[str]:
    if not isinstance(text, str) or not text:
        return None
    t = text.lower()
    for rx in ERROR_CODE_REGEXES:
        m = re.search(rx, t)
        if m:
            return m.group(0)
    found_terms = []
    for term in FLASHING_TERMS + UI_TERMS:
        if term in t:
            found_terms.append(term)
    if found_terms:
        return ", ".join(sorted(set(found_terms))[:3])
    return None

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

def normalize_text(x: Any) -> str:
    if not isinstance(x, str):
        x = "" if pd.isna(x) else str(x)
    return " ".join(x.split())

# =========================================================
# LLM wrappers with retry + limiter
# =========================================================
def llm_retry_json(client, model, system_prompt, user_prompt, schema_name, schema,
                   limiter: Optional[RateLimiter], retries=4, base_delay=1.25):
    last_err = None
    for attempt in range(retries):
        try:
            if limiter: limiter.acquire()
            return client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    },
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
            if limiter: limiter.acquire()
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
        try:
            resp2 = llm_retry_text(client, model, system_prompt, user_prompt, limiter)
            return json.loads(resp2.choices[0].message.content)
        except Exception:
            return {"_raw": "LLM-ERROR or Non-JSON"}

def call_llm_text(client, model, system_prompt, user_prompt, limiter: Optional[RateLimiter]) -> str:
    resp = llm_retry_text(client, model, system_prompt, user_prompt, limiter)
    return resp.choices[0].message.content

# =========================================================
# Cached Loading + Big-file Options
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
def load_excel_cached(file_bytes: bytes, usecols: Optional[List[str]] = None) -> pd.DataFrame:
    import io
    buf = io.BytesIO(file_bytes)
    try:
        return pd.read_excel(buf, usecols=usecols)
    except Exception:
        buf.seek(0)
        return pd.read_excel(buf)

def optimize_dataframe(df: pd.DataFrame, downcast_numeric=True, to_category=True, cat_threshold=2000) -> pd.DataFrame:
    """Memory optimization to smooth big-file interactions."""
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
# Sidebar — API & Model
# =========================================================
st.sidebar.header("🔐 API & Model")
api_key = get_api_key()
model_choice = st.sidebar.selectbox(
    "Model",
    ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o"],
    index=0,
)

# =========================================================
# 1) Upload + Big File Loader
# =========================================================
st.header("1️⃣ Upload Your File")

uploaded_file = st.file_uploader("Upload an Excel or CSV file", type=["xlsx", "csv"])
if not uploaded_file:
    st.stop()

file_bytes = uploaded_file.getvalue()
is_csv = uploaded_file.name.lower().endswith(".csv")

with st.expander("⚡ Big-file loader options (CSV)", expanded=False):
    if is_csv:
        use_pyarrow = st.checkbox("Use pyarrow CSV engine (faster if available)", value=True)
        # Allow header-only to pick columns, then re-load with usecols
        if st.checkbox("Load only selected columns for CSV"):
            import io
            hdr_df = pd.read_csv(io.BytesIO(file_bytes), nrows=0)
            chosen_cols = st.multiselect("Columns to load", options=list(hdr_df.columns), default=list(hdr_df.columns))
            if st.button("Reload CSV with selected columns"):
                df = load_csv_cached(file_bytes, use_pyarrow, usecols=chosen_cols)
            else:
                df = load_csv_cached(file_bytes, use_pyarrow)
        else:
            df = load_csv_cached(file_bytes, use_pyarrow=True)
    else:
        df = load_excel_cached(file_bytes)

# Preview controls
cprev1, cprev2 = st.columns(2)
with cprev1:
    preview_rows = st.slider("Preview rows", 5, 200, 20, 5)
with cprev2:
    sample_preview = st.checkbox("Random sample for preview (instead of head)")
if sample_preview:
    st.dataframe(df.sample(min(preview_rows, len(df)), random_state=42))
else:
    st.dataframe(df.head(preview_rows))

# Optional: memory optimization
with st.expander("🧠 Optimize in-memory DataFrame"):
    do_downcast = st.checkbox("Downcast numeric dtypes", value=True)
    to_category = st.checkbox("Convert low-cardinality object columns to category", value=True)
    if do_downcast or to_category:
        df = optimize_dataframe(df, downcast_numeric=do_downcast, to_category=to_category)

if df.empty:
    st.warning("The uploaded file appears to be empty.")
    st.stop()

# =========================================================
# 2) Configure Columns
# =========================================================
st.header("2️⃣ Configure Columns")

def suggest_text_column(df: pd.DataFrame) -> Optional[str]:
    preferred = ["Zoom Summary", "zoom_summary", "Sentiment Text", "Comment", "Customer Issue"]
    for c in preferred:
        if c in df.columns:
            return c
    obj = df.select_dtypes(include=["object", "category"]).columns.tolist()
    return obj[0] if obj else (df.columns[0] if len(df.columns) else None)

def suggest_date_column(df: pd.DataFrame) -> Optional[str]:
    preferred = ["Start Time (Date/Time)", "Start Time", "Date Created", "Created At"]
    for c in preferred:
        if c in df.columns:
            return c
    for c in df.columns:
        n = str(c).lower()
        if "date" in n or "time" in n:
            return c
    return None

text_suggest = suggest_text_column(df)
text_col = st.selectbox(
    "Text column (sent to LLM)",
    options=df.columns,
    index=(list(df.columns).index(text_suggest) if text_suggest in df.columns else 0),
)

date_suggest = suggest_date_column(df)
date_opts = ["<none>"] + list(df.columns)
date_idx = 0
if date_suggest and date_suggest in df.columns:
    date_idx = date_opts.index(date_suggest)
call_date_col = st.selectbox(
    "Call date/time column (for relative dates & ownership)",
    options=date_opts,
    index=date_idx,
)
if call_date_col == "<none>":
    call_date_col = None

st.caption("Selected text column feeds all prompts. The call date helps infer relative dates and compute ownership period.")

# =========================================================
# 3) Filters
# =========================================================
st.header("3️⃣ Filter Rows (Optional)")
filtered_df = df.copy()

q_def = st.checkbox("Quick Filter: Disposition Sub Group = 'Defective Product'")
if q_def and "Disposition Sub Group" in filtered_df.columns:
    filtered_df = filtered_df[filtered_df["Disposition Sub Group"] == "Defective Product"]

q_tr = st.checkbox("Quick Filter: Troubleshooting Result (multi-select)")
if q_tr and "Troubleshooting Result" in filtered_df.columns:
    vals = sorted(filtered_df["Troubleshooting Result"].dropna().astype(str).unique().tolist())
    chosen = st.multiselect("Select Troubleshooting Result values to keep", options=vals, default=vals)
    filtered_df = filtered_df[filtered_df["Troubleshooting Result"].astype(str).isin(chosen)]

st.write("Custom filters (exact match):")
filter_cols = st.multiselect("Select columns to filter on", options=df.columns)
for col in filter_cols:
    u = sorted(filtered_df[col].dropna().astype(str).unique().tolist())
    sel = st.multiselect(f"Values to keep for '{col}'", options=u, key=f"flt_{col}")
    if sel:
        filtered_df = filtered_df[filtered_df[col].astype(str).isin(sel)]

st.write("### Filtered Preview")
st.dataframe(filtered_df.head(min(preview_rows, len(filtered_df))))
st.caption(f"Rows selected for processing: {len(filtered_df)} / {len(df)}")
if filtered_df.empty:
    st.warning("No rows match the filters.")
    st.stop()

# =========================================================
# 4) Preset Tasks (editable)
# =========================================================
st.header("4️⃣ Preset Tasks / Prompts (Editable JSON outputs)")

default_purchase_prompt = (
    "You are given:\n"
    "- TEXT: A summary or transcript snippet of a customer support call.\n"
    "- CALL_DATE: The date/time of the call.\n"
    "- INFERRED_RELATIVE_DATE_HINT: Optional approximate YYYY-MM-DD derived from phrases like 'last September'.\n\n"
    "Task: Extract the ORIGINAL PRODUCT PURCHASE DATE.\n\n"
    "Rules:\n"
    "- Only return the date the customer originally bought/received the product.\n"
    "- DO NOT return warranty expiration dates, registration dates, coverage periods, delivery dates, or replacement shipment dates.\n"
    "- The purchase date must be on or before CALL_DATE.\n"
    "- If you only see a warranty expiration or any other non-purchase date, return 'unknown'.\n"
    "- If only a month and year are clear (e.g. 'October 2023'), you may return YYYY-MM-15.\n\n"
    "You MUST return ONLY a JSON object matching the schema."
)
default_symptom_prompt = (
    "Given TEXT, identify the PRIMARY technical/functional symptom (e.g., 'no power', 'weak airflow', "
    "'overheating', 'burning smell', 'unit shuts off', 'error code'). Be concise.\n\n"
    "Return ONLY a JSON object matching the schema."
)
default_error_indicator_prompt = (
    "Given TEXT, detect any explicit error code, flashing/blinking lights, or on-screen error icons/messages.\n"
    "If present, return a concise phrase (e.g., 'E02', 'red light flashing', 'filter icon flashing'); else 'none'.\n\n"
    "Return ONLY a JSON object matching the schema."
)
default_model_number_prompt = (
    "Given TEXT, extract the MODEL NUMBER of the product if mentioned (e.g., NV356E, ZU62, HZ2000, HT300). "
    "If multiple, return the most specific. If none, return 'unknown'.\n\n"
    "Return ONLY a JSON object matching the schema."
)

purchase_system = "You extract structured purchase dates from noisy customer support text."
symptom_system = "You classify customer calls by primary technical symptom."
error_system   = "You extract error codes and UI/LED indicators from support text."
model_system   = "You extract product model numbers (e.g., NV356E, ZU62, AZ2000, HT300) from support text."

purchase_schema = {
    "type": "object",
    "properties": {"purchase_date": {"type": "string"}},
    "required": ["purchase_date"],
    "additionalProperties": False,
}
symptom_schema = {
    "type": "object",
    "properties": {"symptom": {"type": "string"}},
    "required": ["symptom"],
    "additionalProperties": False,
}
error_schema = {
    "type": "object",
    "properties": {"error_indicator": {"type": "string"}},
    "required": ["error_indicator"],
    "additionalProperties": False,
}
model_schema = {
    "type": "object",
    "properties": {"model_number": {"type": "string"}},
    "required": ["model_number"],
    "additionalProperties": False,
}

use_purchase = st.checkbox("📅 Purchase date → `purchase_date` (also computes `ownership_period_days`)")
purchase_prompt = st.text_area(
    "Purchase preset (editable):",
    value=default_purchase_prompt,
    height=180,
) if use_purchase else None

use_symptom = st.checkbox("🩺 Primary symptom → `symptom`")
symptom_prompt = st.text_area(
    "Symptom preset (editable):",
    value=default_symptom_prompt,
    height=130,
) if use_symptom else None

use_error = st.checkbox("💡 Error code / flashing indicator → `error_indicator`")
error_prompt = st.text_area(
    "Error/Indicator preset (editable):",
    value=default_error_indicator_prompt,
    height=130,
) if use_error else None

use_modelnum = st.checkbox("🔢 Model number → `model_number`")
model_prompt = st.text_area(
    "Model number preset (editable):",
    value=default_model_number_prompt,
    height=130,
) if use_modelnum else None

# =========================================================
# 5) Custom Prompts + AI Prompt Assistant
# =========================================================
st.header("5️⃣ Custom Tasks")
use_custom = st.checkbox("➕ Enable custom prompts")
custom_prompts: List[str] = []
custom_outcols: List[str] = []

with st.expander("📌 Example custom prompts"):
    st.markdown(
        """
- **Failure Mode** — `Classify the main failure mode into one of: airflow, power, overheating, noise, cosmetic, other. Return only the label.`
- **Resolution Status** — `Determine whether the issue was resolved during this call. Return one of: resolved, unresolved, unclear.`
- **Filter Maintenance** — `Identify whether the customer mentions cleaning or replacing the filter and summarize in one short sentence.`
- **Sentiment** — `Classify sentiment as one of: calm, neutral, frustrated, angry. Return only the label.`
        """
    )

if use_custom:
    n_custom = st.number_input("How many custom prompts?", min_value=1, max_value=12, value=1, step=1)
    for i in range(n_custom):
        c1, c2 = st.columns(2)
        with c1:
            p = st.text_area(f"Prompt {i+1}", key=f"cp_{i}")
        with c2:
            o = st.text_input(f"Output column {i+1}", key=f"co_{i}")
        if p.strip() and o.strip():
            custom_prompts.append(p.strip())
            custom_outcols.append(o.strip())

# ---- AI Prompt Assistant (for custom prompts or presets) ----
st.subheader("✨ AI Prompt Assistant (optional)")
use_prompt_assistant = st.checkbox("Use AI to suggest a prompt for you")
if use_prompt_assistant:
    desc = st.text_area(
        "Describe what you want the model to extract/classify:",
        placeholder="Example: Classify each call into airflow, power, overheating, noise, cosmetic, or other...",
        key="prompt_assistant_desc",
    )
    sample_n = st.slider(
        "How many sample rows from the selected text column to show the AI",
        min_value=1,
        max_value=10,
        value=5,
        key="prompt_assistant_sample_n",
    )
    if st.button("Generate suggested prompt", key="prompt_assistant_btn"):
        if not api_key:
            st.error("Add your API key (secrets or override) to use the AI prompt assistant.")
        else:
            client = get_client(api_key)
            series = filtered_df[text_col].dropna().astype(str)
            if series.empty:
                st.warning("No non-empty values in the selected text column to show as examples.")
            else:
                if len(series) > sample_n:
                    examples = series.sample(sample_n, random_state=42)
                else:
                    examples = series
                example_block = "\n".join(f"- {normalize_text(t)[:400]}" for t in examples)

                system_msg = "You are an expert prompt engineer for LLMs analyzing customer support text row-by-row."
                user_msg = (
                    f"The analyst wants the model to do the following:\n{desc}\n\n"
                    f"Here are sample values from the text column '{text_col}':\n{example_block}\n\n"
                    "Write ONE reusable prompt they can apply row-by-row. "
                    "Do not include examples in the output, just the final prompt."
                )
                try:
                    suggestion = call_llm_text(
                        client,
                        model_choice,
                        system_msg,
                        user_msg,
                        limiter=None,
                    )
                    st.success("Suggested prompt (you can copy-paste this into a custom prompt or preset):")
                    st.code(suggestion)
                except Exception as e:
                    st.error(f"Error generating prompt suggestion: {e}")

# =========================================================
# 6) Throughput & Guardrails
# =========================================================
st.header("6️⃣ Throughput & Guardrails")
cA, cB, cC = st.columns(3)
with cA:
    max_rows = st.number_input(
        "Max rows to process",
        min_value=1,
        max_value=len(filtered_df),
        value=len(filtered_df),
    )
with cB:
    threads = st.slider("Concurrency (parallel requests)", min_value=1, max_value=16, value=6)
with cC:
    target_rpm = st.slider(
        "Target Requests Per Minute (RPM)",
        min_value=0,
        max_value=600,
        value=120,
        help="0 = no throttling; set to your account's safe RPM.",
    )

skip_short = st.checkbox("Skip very short texts (≤ 10 chars)", value=True)
use_heuristics = st.checkbox("Use heuristics before LLM (faster/cheaper)", value=True)
only_missing = st.checkbox("Only process rows where outputs are missing", value=True)

# Session memo to dedupe identical requests
if "memo_cache" not in st.session_state:
    st.session_state.memo_cache: Dict[Tuple[str, str], str] = {}

# =========================================================
# 7) Run LLM Processing (parallel + rate-limited)
# =========================================================
st.header("7️⃣ Run LLM Processing")

# Active outputs
active_cols: List[str] = []
if use_purchase:
    active_cols.append("purchase_date")
if use_symptom:
    active_cols.append("symptom")
if use_error:
    active_cols.append("error_indicator")
if use_modelnum:
    active_cols.append("model_number")
active_cols += custom_outcols

# Preset config
PRESETS = []
if use_purchase:
    PRESETS.append(("purchase_date", purchase_system, purchase_prompt, purchase_schema))
if use_symptom:
    PRESETS.append(("symptom", symptom_system, symptom_prompt, symptom_schema))
if use_error:
    PRESETS.append(("error_indicator", error_system, error_prompt, error_schema))
if use_modelnum:
    PRESETS.append(("model_number", model_system, model_prompt, model_schema))

def process_row(
    idx: int,
    row: pd.Series,
    client: OpenAI,
    model: str,
    presets,
    custom_prompts,
    custom_outcols,
    text_col: str,
    call_date_col: Optional[str],
    use_heuristics: bool,
    skip_short: bool,
    memo: Dict[Tuple[str, str], str],
    limiter: Optional[RateLimiter],
) -> Tuple[int, Dict[str, Any]]:
    out: Dict[str, Any] = {}
    text_val = normalize_text(row.get(text_col, ""))
    call_date_val = str(row.get(call_date_col, "")) if call_date_col else ""
    hint = infer_year_from_relative(text_val, call_date_val) if call_date_col else None

    if skip_short and len(text_val.strip()) <= 10:
        for c in active_cols:
            out[c] = ""
        return idx, out

    # Presets
    for col, sys, prompt, schema in presets:
        key = (text_val, f"preset::{col}")
        if key in memo:
            value = memo[key]
        else:
            value = None
            if use_heuristics:
                if col == "purchase_date":
                    value = purchase_date_heuristic(text_val, call_date_val)
                elif col == "error_indicator":
                    value = heuristic_error_indicator(text_val)
                elif col == "model_number":
                    value = heuristic_model_number(text_val)

            if value is None:
                user_prompt = (prompt or "") + f"\n\nTEXT:\n{text_val}\n"
                if col == "purchase_date":
                    user_prompt += f"\nCALL_DATE: {call_date_val}\nINFERRED_RELATIVE_DATE_HINT: {hint}"
                data = call_llm_json_safe(
                    client,
                    model,
                    sys,
                    user_prompt,
                    f"{col}_extraction",
                    schema,
                    limiter,
                )
                value = data.get(col) or data.get("_raw") or (
                    "unknown" if col in ("purchase_date", "model_number") else ""
                )
            if col == "purchase_date":
                value = normalize_purchase_date_output(value, call_date_val)
            memo[key] = value
        out[col] = value

    # Custom prompts
    for p, c in zip(custom_prompts, custom_outcols):
        key = (text_val, f"custom::{c}")
        if key in memo:
            out[c] = memo[key]
        else:
            user_prompt = f"TEXT:\n{text_val}\n\nCALL_DATE: {call_date_val}\n\nTASK:\n{p}"
            val = call_llm_text(
                client,
                model,
                "You analyze support text precisely.",
                user_prompt,
                limiter,
            )
            memo[key] = val
            out[c] = val

    return idx, out

if st.button("🚀 Run"):
    if not api_key:
        st.error("No API key configured.")
    elif not (PRESETS or custom_prompts):
        st.error("Select at least one preset or add a custom prompt.")
    else:
        client = get_client(api_key)
        results_df = df.copy()

        # Determine rows to process
        rows = filtered_df.head(int(max_rows)).copy()

        # Ensure target columns exist before we compute masks
        for c in active_cols:
            if c not in results_df.columns:
                results_df[c] = pd.NA

        if only_missing and active_cols:
            # Build a boolean need-mask across active output cols (NA or empty → needs processing)
            sub = results_df.loc[rows.index, active_cols]
            miss_mask = sub.apply(
                lambda col: col.isna() | (col.astype(str).str.strip() == ""),
                axis=1,
            )
            need_mask = miss_mask.any(axis=1)
            # Always use .loc with a boolean Series aligned to rows.index
            rows = rows.loc[need_mask]

        total = len(rows)
        if total == 0:
            st.info(
                "Nothing to do (outputs already present). Uncheck 'Only process rows where outputs are missing' or broaden filters."
            )
        else:
            limiter = RateLimiter(target_rpm) if target_rpm > 0 else None
            memo = st.session_state.memo_cache

            progress = st.progress(0.0)
            status = st.empty()

            # parallel execution
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
                            call_date_col,
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
                    progress.progress(done / total)
                    status.write(f"Processed {done}/{total} rows...")

            # Derived ownership period
            if use_purchase and call_date_col:
                try:
                    pd_purchase = pd.to_datetime(results_df["purchase_date"], errors="coerce")
                    pd_call = pd.to_datetime(results_df[call_date_col], errors="coerce")
                    results_df["ownership_period_days"] = (pd_call - pd_purchase).dt.days
                except Exception as e:
                    st.warning(f"Ownership calculation issue: {e}")

            st.session_state["results_df"] = results_df
            st.success("Processing complete ✅")

# =========================================================
# 8) Analyze & Visualize
# =========================================================
st.header("8️⃣ Analyze & Visualize")
results_df = st.session_state.get("results_df")
if results_df is None:
    st.info("Run the processing to see analysis.")
else:
    st.subheader("Enriched Dataset Preview")
    st.dataframe(results_df.head(20))

    if "ownership_period_days" in results_df.columns:
        st.subheader("Ownership Period (Days)")
        series = results_df["ownership_period_days"].dropna()
        if not series.empty:
            st.write(series.describe())
            bins = [-1, 30, 90, 180, 365, 730, 3650]
            labels = ["0–30", "31–90", "91–180", "181–365", "366–730", ">730"]
            bands = pd.cut(series, bins=bins, labels=labels)
            st.write("Ownership bands:")
            st.bar_chart(bands.value_counts().sort_index())
        else:
            st.info("No ownership period values.")

    if "symptom" in results_df.columns:
        st.subheader("Symptom Distribution")
        s = results_df["symptom"].dropna().astype(str).str.strip()
        s = s[s != ""]
        if not s.empty:
            top = s.value_counts().head(20)
            st.bar_chart(top)
            st.table(top.to_frame("count"))
        else:
            st.info("No symptom values.")

    if "error_indicator" in results_df.columns:
        st.subheader("Error Indicators (codes / flashing / UI)")
        ei = results_df["error_indicator"].dropna().astype(str).str.strip()
        ei = ei[ei != ""]
        if not ei.empty:
            top = ei.value_counts().head(20)
            st.bar_chart(top)
            st.table(top.to_frame("count"))
        else:
            st.info("No error_indicator values.")

    if "model_number" in results_df.columns:
        st.subheader("Model Number Mentions")
        mn = results_df["model_number"].dropna().astype(str).str.strip()
        mn = mn[(mn != "") & (mn != "unknown")]
        if not mn.empty:
            top = mn.value_counts().head(20)
            st.bar_chart(top)
            st.table(top.to_frame("count"))
        else:
            st.info("No model numbers extracted.")

    if "purchase_date" in results_df.columns:
        st.subheader("Purchases by Year (from `purchase_date`)")
        pdt = pd.to_datetime(results_df["purchase_date"], errors="coerce").dropna()
        if not pdt.empty:
            st.bar_chart(pdt.dt.year.value_counts().sort_index())
        else:
            st.info("No valid purchase_date values.")

    # Use selected call_date_col if available, otherwise try to infer for visualization only
    viz_call_col = call_date_col
    if viz_call_col is None:
        viz_call_col = next(
            (
                c
                for c in results_df.columns
                if (
                    str(c).lower().startswith("start")
                    or "date" in str(c).lower()
                    or "time" in str(c).lower()
                )
            ),
            None,
        )

    if viz_call_col and viz_call_col in results_df.columns:
        st.subheader(f"Calls Over Time (based on '{viz_call_col}')")
        cdt = pd.to_datetime(results_df[viz_call_col], errors="coerce").dropna()
        if not cdt.empty:
            by_month = cdt.dt.to_period("M").value_counts().sort_index()
            by_month.index = by_month.index.astype(str)
            st.line_chart(by_month)
        else:
            st.info("No valid call dates.")

    # Download
    buffer = BytesIO()
    results_df.to_excel(buffer, index=False)
    buffer.seek(0)
    st.download_button(
        "⬇️ Download processed file",
        data=buffer,
        file_name="processed_output.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


