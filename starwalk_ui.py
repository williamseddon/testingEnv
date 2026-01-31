# starwalk_ui_v7_7_knowledge_plus_stable_fast.py — v7.7
# v7.6 (Stable) + FAST batching + Subset Filters + Optional Throttle
#
# Key upgrades:
#   - ✅ Batch symptomization: multiple reviews per OpenAI request (big speedup, fewer 429s)
#   - ✅ Subset selection filters: Source / Model (SKU) / Seeded / Country / New Review / Review Date / Star Rating
#   - ✅ Optional RPM/TPM throttle (coarse) to reduce rate limit hits
#   - ✅ Keeps evidence-locked labeling, canonical merging, inbox, lazy export, session persistence, retries, etc.
#
# Requirements: streamlit>=1.28, pandas, openpyxl, openai (optional)
# Optional: numpy, scikit-learn (for better clustering), tiktoken (for better token counts)

import streamlit as st
import pandas as pd
import io, os, json, difflib, time, re, html, math, random, hashlib, traceback, gc
from typing import List, Dict, Tuple, Optional, Set, Any, Iterable

# Optional: OpenAI
try:
    from openai import OpenAI  # type: ignore
    _HAS_OPENAI = True
except Exception:
    OpenAI = None  # type: ignore
    _HAS_OPENAI = False

# Optional: better token counting
try:
    import tiktoken  # type: ignore
    _HAS_TIKTOKEN = True
except Exception:
    tiktoken = None  # type: ignore
    _HAS_TIKTOKEN = False

# Optional: clustering helpers
try:
    import numpy as np  # type: ignore
    _HAS_NUMPY = True
except Exception:
    np = None  # type: ignore
    _HAS_NUMPY = False

try:
    from sklearn.cluster import KMeans  # type: ignore
    _HAS_SKLEARN = True
except Exception:
    KMeans = None  # type: ignore
    _HAS_SKLEARN = False

# Excel handling
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from openpyxl.styles import PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter

# ------------------- Page Setup -------------------
st.set_page_config(layout="wide", page_title="Review Symptomizer — v7.7 (Fast+Stable)")
st.title("✨ Review Symptomizer — v7.7 (Fast+Stable)")
st.caption(
    "Exact export (K–T dets, U–AD dels) • ETA + presets + overwrite • Undo (optional) • "
    "Product Knowledge Prelearn (recommended) • Strong canonical merging • "
    "Similarity/semantic guard • Evidence-locked labeling • In-session cache • "
    "🟡 Inbox: New Symptoms + Alias Suggestions • ✅ Stability hardening • ⚡ Batch speedups • 🔎 Filters"
)

# ------------------- Global CSS -------------------
st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
      :root { --brand:#7c3aed; --brand2:#06b6d4; --ok:#16a34a; --bad:#dc2626; --muted:#6b7280; }
      html, body, .stApp { font-family: 'Inter', system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
      .stApp { background:
        radial-gradient(1200px 500px at 10% -20%, rgba(124,58,237,.18), transparent 60%),
        radial-gradient(1200px 500px at 100% 0%, rgba(6,182,212,.16), transparent 60%);
      }
      .hero { border-radius: 20px; padding: 18px 22px; color: #0b1020;
        background: linear-gradient(180deg, rgba(255,255,255,.95), rgba(255,255,255,.86));
        border: 1px solid rgba(226,232,240,.9);
        box-shadow: 0 10px 30px rgba(16,24,40,.08), 0 2px 6px rgba(16,24,40,.06) inset; }
      .hero-stats { display:flex; gap:14px; flex-wrap:wrap; margin-top: 6px; }
      .stat { background:#fff; border:1px solid #e6eaf0; border-radius:16px; padding:12px 16px; min-width:160px;
        box-shadow: 0 2px 8px rgba(16,24,40,.05); }
      .stat.accent { border-color: rgba(124,58,237,.35); box-shadow: 0 4px 12px rgba(124,58,237,.15); }
      .stat .label{ font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:#64748b; }
      .stat .value{ font-size:28px; font-weight:800; }
      .chip-wrap {display:flex; flex-wrap:wrap; gap:8px;}
      .chip { padding:6px 10px; border-radius:999px; font-size:12.5px; border:1px solid #e6eaf0; background:#fff; box-shadow: 0 1px 2px rgba(16,24,40,.06); }
      .chip.red { background: #fff1f2; border-color:#fecdd3; }
      .chip.green { background: #ecfdf3; border-color:#bbf7d0; }
      .chip.blue { background: #e0f2fe; border-color:#bae6fd; }
      .chip.yellow { background: #fff7ed; border-color:#fed7aa; }
      .chip.purple { background: #f3e8ff; border-color:#e9d5ff; }
      .muted{ color:#64748b; font-size:12px; }
      .chips-block { margin-bottom: 16px; }
      .stButton > button { height: 40px; border-radius: 10px; font-weight: 600;
        background: linear-gradient(180deg, #ffffff, #f7f8fb);
        border: 1px solid #e6eaf0; box-shadow: 0 1px 2px rgba(16,24,40,.04); }
      .stButton > button:hover { border-color: rgba(124,58,237,.35); box-shadow: 0 2px 6px rgba(124,58,237,.15); }
      div.batch-row .stNumberInput input { height: 40px; font-weight: 700; }
      div.batch-row .stButton > button { border-radius: 999px; height: 36px; font-weight: 700; min-width: 72px;
        background: #fff; border: 1px solid rgba(6,182,212,.45); }
      div.batch-row .stButton > button:hover { background: #f0fdff; border-color: var(--brand2); }
      mark.hl { background: #fde68a; padding: 0 .15em; border-radius: .25em; }
      .tiny { font-size: 12px; color: #64748b; }
      .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ------------------- Utilities -------------------
NON_VALUES = {"<NA>", "NA", "N/A", "NONE", "-", "", "NAN", "NULL"}

def clean_text(x):
    if pd.isna(x):
        return ""
    return str(x).strip()

def is_filled(val) -> bool:
    if pd.isna(val):
        return False
    s = str(val).strip()
    return (s != "") and (s.upper() not in NON_VALUES)

def _fmt_money(x: float) -> str:
    try:
        return f"${x:,.4f}" if x < 1 else f"${x:,.2f}"
    except Exception:
        return "$0.00"

def _fmt_secs(sec: float) -> str:
    sec = float(sec or 0.0)
    if sec < 0:
        sec = 0
    m = int(sec // 60)
    s = int(round(sec - m * 60))
    return f"{m}:{s:02d}"

# ------------------- Column helpers + filter normalization -------------------
def _find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Best-effort resolver for column names (case-insensitive + partial match)."""
    if df is None or df.empty:
        return None
    low = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        key = str(cand).strip().lower()
        if key in low:
            return low[key]
    for cand in candidates:
        key = str(cand).strip().lower()
        for lc, orig in low.items():
            if key and (key == lc or key in lc):
                return orig
    return None

_BOOL_TRUE = {"true","t","yes","y","1","seeded"}
_BOOL_FALSE = {"false","f","no","n","0","non-seeded","nonseeded","not seeded","unseeded"}

def _boolish(x: Any) -> Optional[bool]:
    if pd.isna(x):
        return None
    s = str(x).strip().lower()
    if s in _BOOL_TRUE:
        return True
    if s in _BOOL_FALSE:
        return False
    return None

def _coerce_datetime_inplace(df_in: pd.DataFrame, col: Optional[str]) -> None:
    if col and col in df_in.columns:
        try:
            if not pd.api.types.is_datetime64_any_dtype(df_in[col]):
                df_in[col] = pd.to_datetime(df_in[col], errors="coerce")
        except Exception:
            pass

def _coerce_numeric_inplace(df_in: pd.DataFrame, col: Optional[str]) -> None:
    if col and col in df_in.columns:
        try:
            if not pd.api.types.is_numeric_dtype(df_in[col]):
                df_in[col] = pd.to_numeric(df_in[col], errors="coerce")
        except Exception:
            pass

def _unique_sorted_str(series: pd.Series, limit: int = 5000) -> List[str]:
    if series is None:
        return []
    try:
        vals = series.dropna().astype(str).map(str.strip)
        vals = vals[vals != ""]
        uniq = list(pd.unique(vals))
        if len(uniq) > limit:
            uniq = uniq[:limit]
        uniq.sort()
        return uniq
    except Exception:
        return []

# ------------------- Simple throttle (RPM + estimated TPM) -------------------
def _throttle(kind: str, est_in_tokens: int) -> None:
    """
    Coarse throttling to reduce rate-limit hits.
    Uses session_state:
      - throttle_rpm (0 disables)
      - throttle_tpm (0 disables)
    """
    rpm = int(st.session_state.get("throttle_rpm", 0) or 0)
    tpm = int(st.session_state.get("throttle_tpm", 0) or 0)
    if rpm <= 0 and tpm <= 0:
        return

    now = float(time.time())
    key = f"_throttle_{kind}"
    bucket = st.session_state.get(key) or {"events": []}
    events = bucket.get("events") or []

    pruned = []
    for e in events:
        try:
            ts, tok = float(e[0]), int(e[1])
        except Exception:
            continue
        if (now - ts) < 60.0:
            pruned.append((ts, tok))
    pruned.sort(key=lambda x: x[0])

    if rpm > 0 and len(pruned) >= rpm:
        sleep_sec = 60.0 - (now - pruned[0][0]) + 0.05
        if sleep_sec > 0:
            time.sleep(sleep_sec)
            now = float(time.time())
            pruned = [(ts, tok) for ts, tok in pruned if (now - ts) < 60.0]

    if tpm > 0:
        tok_sum = sum(tok for _, tok in pruned)
        need = int(est_in_tokens)
        if tok_sum + need > tpm and pruned:
            running = tok_sum
            sleep_until_ts: Optional[float] = None
            for ts, tok in pruned:
                running -= tok
                if running + need <= tpm:
                    sleep_until_ts = ts
                    break
            if sleep_until_ts is None:
                sleep_until_ts = pruned[0][0]
            sleep_sec = 60.0 - (now - float(sleep_until_ts)) + 0.05
            if sleep_sec > 0:
                time.sleep(sleep_sec)
                now = float(time.time())
                pruned = [(ts, tok) for ts, tok in pruned if (now - ts) < 60.0]

    pruned.append((now, int(est_in_tokens)))
    bucket["events"] = pruned
    st.session_state[key] = bucket

# ------------------- Pricing & Cost Tracking -------------------
# Prices per 1M tokens (text). Update as needed.
MODEL_PRICING_PER_1M = {
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gpt-4o": {"in": 2.50, "out": 10.00},
    "gpt-4.1": {"in": 2.00, "out": 8.00},
    "gpt-5": {"in": 1.25, "out": 10.00},
}
EMBEDDING_PRICING_PER_1M = {
    "text-embedding-3-small": 0.02,
}

def _price_for_model(model_id: str) -> Tuple[float, float]:
    try:
        ov = st.session_state.get("_pricing_overrides", {}).get("models", {})
        if model_id in ov:
            return (float(ov[model_id].get("in", 0.0)), float(ov[model_id].get("out", 0.0)))
    except Exception:
        pass
    p = MODEL_PRICING_PER_1M.get(model_id, None)
    if not p:
        return (0.0, 0.0)
    return (float(p.get("in", 0.0)), float(p.get("out", 0.0)))

def _price_for_embedding(model_id: str) -> float:
    try:
        ov = st.session_state.get("_pricing_overrides", {}).get("embeddings", {})
        if model_id in ov:
            return float(ov[model_id])
    except Exception:
        pass
    return float(EMBEDDING_PRICING_PER_1M.get(model_id, 0.0))

def _cost_chat(model_id: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = _price_for_model(model_id)
    return (float(input_tokens) * pin + float(output_tokens) * pout) / 1_000_000.0

def _cost_embed(model_id: str, input_tokens: int) -> float:
    p = _price_for_embedding(model_id)
    return float(input_tokens) * p / 1_000_000.0

def _ensure_usage_tracker():
    if "_usage" not in st.session_state:
        st.session_state["_usage"] = {
            "chat_in": 0,
            "chat_out": 0,
            "embed_in": 0,
            "cost_chat": 0.0,
            "cost_embed": 0.0,
            "by_component": {},
        }
    return st.session_state["_usage"]

def _track(component: str, model_id: str, in_tok: int = 0, out_tok: int = 0, embed: bool = False):
    tr = _ensure_usage_tracker()
    component = str(component or "unknown")
    comp = tr["by_component"].setdefault(component, {"chat_in": 0, "chat_out": 0, "embed_in": 0, "cost": 0.0})
    if embed:
        tr["embed_in"] += int(in_tok)
        c = _cost_embed(model_id, int(in_tok))
        tr["cost_embed"] += c
        comp["embed_in"] += int(in_tok)
        comp["cost"] += c
    else:
        tr["chat_in"] += int(in_tok)
        tr["chat_out"] += int(out_tok)
        c = _cost_chat(model_id, int(in_tok), int(out_tok))
        tr["cost_chat"] += c
        comp["chat_in"] += int(in_tok)
        comp["chat_out"] += int(out_tok)
        comp["cost"] += c

def _extract_usage(resp: Any) -> Tuple[int, int]:
    """Best-effort extraction of (prompt/input tokens, completion/output tokens) from OpenAI responses."""
    if resp is None:
        return (0, 0)
    usage = getattr(resp, "usage", None)
    if usage is None:
        try:
            usage = resp.get("usage")  # type: ignore
        except Exception:
            usage = None
    if usage is None:
        return (0, 0)

    def _get(obj, k1, k2):
        if isinstance(obj, dict):
            return obj.get(k1, obj.get(k2, 0)) or 0
        return getattr(obj, k1, getattr(obj, k2, 0)) or 0

    pt = _get(usage, "prompt_tokens", "input_tokens")
    ct = _get(usage, "completion_tokens", "output_tokens")
    if (pt or 0) == 0:
        pt = _get(usage, "total_tokens", "prompt_tokens")
    return (int(pt or 0), int(ct or 0))

def _estimate_tokens(text: str, model_id: str = "gpt-4o-mini") -> int:
    """Rough token estimate; uses tiktoken when available, else ~4 chars per token heuristic."""
    s = str(text or "")
    if not s:
        return 0
    if _HAS_TIKTOKEN:
        try:
            enc_name = "cl100k_base"
            enc = tiktoken.get_encoding(enc_name)
            return int(len(enc.encode(s)))
        except Exception:
            pass
    return int(max(1, math.ceil(len(s) / 4)))

# ------------------- Cached workbook loaders (stability) -------------------
@st.cache_data(show_spinner=False)
def _load_reviews_df(file_bytes: bytes) -> pd.DataFrame:
    bio = io.BytesIO(file_bytes)
    try:
        df0 = pd.read_excel(bio, sheet_name="Star Walk scrubbed verbatims")
    except Exception:
        bio.seek(0)
        df0 = pd.read_excel(bio)
    df0.columns = [str(c).strip() for c in df0.columns]
    return df0

# ------------------- Symptoms sheet parsing -------------------
@st.cache_data(show_spinner=False)
def get_symptom_whitelists(file_bytes: bytes) -> Tuple[List[str], List[str], Dict[str, List[str]]]:
    bio = io.BytesIO(file_bytes)
    try:
        df_sym = pd.read_excel(bio, sheet_name="Symptoms")
    except Exception:
        return [], [], {}
    if df_sym is None or df_sym.empty:
        return [], [], {}

    df_sym.columns = [str(c).strip() for c in df_sym.columns]
    lowcols = {c.lower(): c for c in df_sym.columns}

    alias_col = next((lowcols.get(c) for c in ["aliases", "alias"] if c in lowcols), None)
    label_col = next((lowcols.get(c) for c in ["symptom", "label", "name", "item"] if c in lowcols), None)
    type_col  = next((lowcols.get(c) for c in ["type", "polarity", "category", "side"] if c in lowcols), None)

    POS_TAGS = {"delighter", "delighters", "positive", "pos", "pros"}
    NEG_TAGS = {"detractor", "detractors", "negative", "neg", "cons"}

    def _clean(series: pd.Series) -> List[str]:
        vals = series.dropna().astype(str).map(str.strip)
        out, seen = [], set()
        for v in vals:
            if v and v not in seen:
                seen.add(v); out.append(v)
        return out

    delighters, detractors, alias_map = [], [], {}

    if label_col and type_col:
        df_sym[type_col] = df_sym[type_col].astype(str).str.lower().str.strip()
        delighters = _clean(df_sym.loc[df_sym[type_col].isin(POS_TAGS), label_col])
        detractors = _clean(df_sym.loc[df_sym[type_col].isin(NEG_TAGS), label_col])
        if alias_col:
            for _, r in df_sym.iterrows():
                lbl = str(r.get(label_col, "")).strip()
                als = str(r.get(alias_col, "")).strip()
                if lbl:
                    if als:
                        als_norm = als.replace(",", "|")
                        alias_map[lbl] = [p.strip() for p in als_norm.split("|") if p.strip()]
                    else:
                        alias_map.setdefault(lbl, [])
    else:
        for lc, orig in lowcols.items():
            if ("delight" in lc) or ("positive" in lc) or lc in {"pros"}:
                delighters.extend(_clean(df_sym[orig]))
            if ("detract" in lc) or ("negative" in lc) or lc in {"cons"}:
                detractors.extend(_clean(df_sym[orig]))
        delighters = list(dict.fromkeys(delighters))
        detractors = list(dict.fromkeys(detractors))

    return delighters, detractors, alias_map

# Canonicalization helpers
def _canon(s: str) -> str:
    return " ".join(str(s).split()).lower().strip()

def _canon_simple(s: str) -> str:
    return "".join(ch for ch in _canon(s) if ch.isalnum())

# Evidence highlighting
def highlight_text(text: str, terms: List[str]) -> str:
    safe = html.escape(str(text))
    terms = [t for t in (terms or []) if isinstance(t, str) and len(t.strip()) >= 3]
    if not terms:
        return safe
    uniq = sorted({t.strip() for t in terms}, key=len, reverse=True)
    try:
        pattern = re.compile("|".join(re.escape(t) for t in uniq), re.IGNORECASE)
    except Exception:
        return safe
    return pattern.sub(lambda m: f"<mark class='hl'>{html.escape(m.group(0))}</mark>", safe)

# Schema detection
def detect_symptom_columns(df: pd.DataFrame) -> Dict[str, List[str]]:
    cols = [str(c).strip() for c in df.columns]
    man_det = [f"Symptom {i}" for i in range(1, 11) if f"Symptom {i}" in cols]
    man_del = [f"Symptom {i}" for i in range(11, 21) if f"Symptom {i}" in cols]
    ai_det  = [c for c in cols if c.startswith("AI Symptom Detractor ")]
    ai_del  = [c for c in cols if c.startswith("AI Symptom Delighter ")]
    return {
        "manual_detractors": man_det,
        "manual_delighters": man_del,
        "ai_detractors": ai_det,
        "ai_delighters": ai_del,
    }

# Vectorized missing detection
def _filled_mask(df_in: pd.DataFrame, cols: List[str]) -> pd.Series:
    """Vectorized 'is_filled' across multiple columns."""
    if not cols:
        return pd.Series(False, index=df_in.index)

    mask = pd.Series(False, index=df_in.index)
    for c in cols:
        if c not in df_in.columns:
            continue
        s = df_in[c].fillna("").astype(str).str.strip()
        s_up = s.str.upper()
        mask |= (s != "") & (~s_up.isin(NON_VALUES))
    return mask

def detect_missing(df: pd.DataFrame, colmap: Dict[str, List[str]]) -> pd.DataFrame:
    det_cols = colmap["manual_detractors"] + colmap["ai_detractors"]
    del_cols = colmap["manual_delighters"] + colmap["ai_delighters"]

    out = df.copy()
    out["Has_Detractors"] = _filled_mask(out, det_cols)
    out["Has_Delighters"] = _filled_mask(out, del_cols)
    out["Needs_Detractors"] = ~out["Has_Detractors"]
    out["Needs_Delighters"] = ~out["Has_Delighters"]
    out["Needs_Symptomization"] = out["Needs_Detractors"] & out["Needs_Delighters"]
    return out

# ------------------- Fixed template mapping -------------------
DET_LETTERS = ["K","L","M","N","O","P","Q","R","S","T"]
DEL_LETTERS = ["U","V","W","X","Y","Z","AA","AB","AC","AD"]
DET_INDEXES = [column_index_from_string(c) for c in DET_LETTERS]
DEL_INDEXES = [column_index_from_string(c) for c in DEL_LETTERS]

META_ORDER = [("Safety", "AE"),("Reliability", "AF"),("# of Sessions", "AG")]
META_INDEXES = {name: column_index_from_string(col) for name, col in META_ORDER}

AI_DET_HEADERS = [f"AI Symptom Detractor {i}" for i in range(1, 11)]
AI_DEL_HEADERS = [f"AI Symptom Delighter {i}" for i in range(1, 11)]
AI_META_HEADERS = ["AI Safety", "AI Reliability", "AI # of Sessions"]

def ensure_ai_columns(df_in: pd.DataFrame) -> pd.DataFrame:
    for h in AI_DET_HEADERS + AI_DEL_HEADERS + AI_META_HEADERS:
        if h not in df_in.columns:
            df_in[h] = None
    return df_in

def clear_all_ai_slots_in_df(df_in: pd.DataFrame) -> pd.DataFrame:
    df2 = ensure_ai_columns(df_in.copy())
    for j in range(1, 11):
        df2[f"AI Symptom Detractor {j}"] = None
        df2[f"AI Symptom Delighter {j}"] = None
    df2["AI Safety"] = None
    df2["AI Reliability"] = None
    df2["AI # of Sessions"] = None
    return df2

def build_canonical_maps(delighters: List[str], detractors: List[str], alias_map: Dict[str, List[str]]):
    del_map = {_canon(x): x for x in delighters}
    det_map = {_canon(x): x for x in detractors}
    alias_to_label: Dict[str, str] = {}
    for label, aliases in (alias_map or {}).items():
        for a in aliases:
            alias_to_label[_canon(a)] = label
    return del_map, det_map, alias_to_label

# ---------- LLM helpers ----------
SAFETY_ENUM = ["Not Mentioned", "Concern", "Positive"]
RELIABILITY_ENUM = ["Not Mentioned", "Negative", "Neutral", "Positive"]
SESSIONS_ENUM = ["0", "1", "2–3", "4–9", "10+", "Unknown"]

def _symptom_list_version(delighters: List[str], detractors: List[str], aliases: Dict[str, List[str]]) -> str:
    payload = json.dumps({"del": delighters, "det": detractors, "ali": aliases}, sort_keys=True, ensure_ascii=False)
    try:
        return hashlib.md5(payload.encode("utf-8")).hexdigest()[:10]
    except Exception:
        return str(len(delighters)) + "_" + str(len(detractors))

def _ensure_label_cache():
    if "_label_cache" not in st.session_state:
        st.session_state["_label_cache"] = {}
    return st.session_state["_label_cache"]

# ------------------- Theme normalization (Stronger + plural/synonym handling) -------------------
THEME_RULES = [
    (re.compile(r"\b(pulls?|pulled|pulling).{0,12}\bhair\b|\bhair\s+(?:loss|fall(?:ing)?|coming\s+out|pulled)\b", re.I),
     {"det": "Hair Loss/Pull"}),
    (re.compile(r"\b(snags?|tangles?|catches?)\s+(?:hair|strands?)\b", re.I),
     {"det": "Hair Snag/Tangle"}),
    (re.compile(r"\b(too\s+hot|burns?|scalds?|overheats?)\b", re.I),
     {"det": "Excess Heat"}),
    (re.compile(r"\b(too\s+noisy|loud|whine|high\s+noise)\b", re.I),
     {"det": "High Noise"}),
    (re.compile(r"\b(battery|charge|runtime)\b.+\b(poor|short|bad|low)\b|\b(poor|short|bad|low)\b.+\b(battery|charge|runtime)\b", re.I),
     {"det": "Battery Life: Short"}),

    (re.compile(r"\b(cooling\s+pad)\b.*\b(issue|issues|problem|problems|fail|failed|broken)\b|\b(issue|issues|problem|problems)\b.*\b(cooling\s+pad)\b", re.I),
     {"det": "Cooling Pad Issue"}),
    (re.compile(r"\b(learning\s+curve|hard\s+to\s+learn|takes\s+time\s+to\s+learn|not\s+intuitive)\b", re.I),
     {"det": "Learning Curve"}),
    (re.compile(r"\b(initially|at\s+first)\b.*\b(complicated|confusing|hard)\b|\b(complicated|confusing|hard)\b.*\b(at\s+first|initially)\b", re.I),
     {"det": "Learning Curve"}),

    (re.compile(r"\b(absolutely|totally|really)?\s*love(s|d)?\b|\bworks\s+(amazing|great|fantastic|perfect)\b|\boverall\b.+\b(great|good|positive|happy)\b", re.I),
     {"del": "Overall Satisfaction"}),
    (re.compile(r"\b(easy|quick|simple)\s+to\s+(use|clean|attach|remove)\b|\buser[-\s]?friendly\b|\bintuitive\b", re.I),
     {"del": "Ease Of Use"}),
    (re.compile(r"\b(fast|quick)\s+(dry|drying)\b|\bdries\s+quickly\b", re.I),
     {"del": "Fast Drying"}),
    (re.compile(r"\b(shine|smooth|sleek|frizz\s*(?:free|control))\b", re.I),
     {"del": "Frizz Control/Shine"}),
    (re.compile(r"\b(attachments?|accessories?)\b.+\b(handy|useful|versatile|helpful)\b", re.I),
     {"del": "Attachment Usability"}),

    (re.compile(r"\b(comfortable|comfort|comfy)\b", re.I),
     {"del": "Comfort"}),
]

CANONICAL_SYNONYMS = [
    ("Comfort", "del", [r"\bcomfortable\b", r"\bcomfort\b", r"\bcomfy\b", r"\bconfortable\b", r"\bconfort\b", r"\bcomftable\b", r"\bcomfort\s*(fit|use|design|feel)\b"]),
    ("Cooling Pad Issue", "det", [r"\bcooling\s+pad\b.*\b(issue|problem|broken|fail)", r"\b(issue|problem)s?\b.*\bcooling\s+pad\b"]),
    ("Learning Curve", "det", [r"\blearning\s+curve\b", r"\bnot\s+intuitive\b", r"\bhard\s+to\s+learn\b", r"\bconfusing\s+at\s+first\b", r"\binitially\s+complicated\b"]),
]

SINGULAR_LASTWORD_MAP = {
    "Issues": "Issue",
    "Problems": "Problem",
    "Complaints": "Complaint",
    "Defects": "Defect",
    "Glitches": "Glitch",
}

def _short_title(s: str) -> str:
    s = re.sub(r"[\s\-_/]+", " ", str(s).strip())
    s = re.sub(r"[^\w\s+/]", "", s)
    s = re.sub(r"\s+", " ", s)
    parts = []
    for token in s.split(" "):
        if "/" in token:
            parts.append("/".join([p[:1].upper() + p[1:].lower() if p else "" for p in token.split("/")]))
        else:
            parts.append(token[:1].upper() + token[1:].lower())
    return " ".join(parts).strip()

def _singularize_last_word(label: str) -> str:
    s = str(label).strip()
    if not s:
        return s
    toks = s.split()
    if not toks:
        return s
    last = toks[-1]
    repl = SINGULAR_LASTWORD_MAP.get(last, None)
    if repl:
        toks[-1] = repl
        return " ".join(toks)
    return s

def normalize_theme_label(raw: str, side_hint: str = "", singularize: bool = True) -> str:
    txt = str(raw or "").strip()
    if not txt:
        return ""

    for rx, mapping in THEME_RULES:
        if rx.search(txt):
            if side_hint.lower().startswith("del") and mapping.get("del"):
                out = mapping["del"]
                return _singularize_last_word(out) if singularize else out
            if side_hint.lower().startswith("det") and mapping.get("det"):
                out = mapping["det"]
                return _singularize_last_word(out) if singularize else out
            out = mapping.get("del") or mapping.get("det") or _short_title(txt[:32])
            return _singularize_last_word(out) if singularize else out

    low = txt.lower()
    for canon, side, pats in CANONICAL_SYNONYMS:
        if side_hint.lower().startswith("del") and side != "del":
            continue
        if side_hint.lower().startswith("det") and side != "det":
            continue
        for p in pats:
            if re.search(p, low, flags=re.I):
                return _singularize_last_word(canon) if singularize else canon

    out = _short_title(txt[:60])
    if singularize:
        out = _singularize_last_word(out)

    if out.endswith(" Issues"):
        out = out[:-7] + " Issue"
    return out.strip()

# ------------------- Learned Themes / Candidate Resolution -------------------
def _ensure_learned_store() -> Dict[str, Any]:
    if "learned" not in st.session_state:
        st.session_state["learned"] = {
            "labels": {"Delighter": {}, "Detractor": {}},
            "emb": {"Delighter": {}, "Detractor": {}},
            "keywords": {"Delighter": {}, "Detractor": {}},
            "product_profile": "",
            "glossary_terms": [],
            "version": "",
            "ts": None,
        }
    return st.session_state["learned"]

def _update_learned(side: str, canonical: str, synonym: str = ""):
    ls = _ensure_learned_store()
    side = "Delighter" if str(side).lower().startswith("del") else "Detractor"
    d = ls["labels"][side].setdefault(canonical, {"synonyms": set(), "count": 0})
    d["count"] = int(d.get("count", 0)) + 1
    if synonym and synonym.strip() and _canon_simple(synonym) != _canon_simple(canonical):
        d["synonyms"].add(synonym.strip())

def _known_learned_labels(side: str) -> List[str]:
    ls = _ensure_learned_store()
    side = "Delighter" if str(side).lower().startswith("del") else "Detractor"
    items = list(ls["labels"][side].keys())
    items.sort(key=lambda k: -int(ls["labels"][side].get(k, {}).get("count", 0)))
    return items

def _ensure_embed_cache():
    if "_embed_cache" not in st.session_state:
        st.session_state["_embed_cache"] = {}
    return st.session_state["_embed_cache"]

def _embed_text(text: str, client, model_id: str, component: str) -> Optional[List[float]]:
    if client is None:
        return None
    t = str(text or "").strip()
    if not t:
        return None
    key = (model_id, _canon(t))
    cache = _ensure_embed_cache()
    if key in cache:
        return cache[key]
    try:
        _throttle("embed", _estimate_tokens(t, model_id="gpt-4o-mini"))
        resp = client.embeddings.create(model=model_id, input=[t])
        pt, _ = _extract_usage(resp)
        if pt:
            _track(component, model_id, in_tok=pt, out_tok=0, embed=True)
        vec = resp.data[0].embedding  # type: ignore
        cache[key] = vec
        return vec
    except Exception:
        return None

def _cos_sim(a: List[float], b: List[float]) -> float:
    if (not a) or (not b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += float(x) * float(y)
        na += float(x) * float(x)
        nb += float(y) * float(y)
    if na <= 0 or nb <= 0:
        return 0.0
    return float(dot / (math.sqrt(na) * math.sqrt(nb)))

def _best_semantic_match(
    candidate: str,
    pool: List[str],
    pool_emb: Dict[str, List[float]],
    client,
    embed_model: str,
    component: str,
) -> Tuple[Optional[str], float]:
    cand_vec = _embed_text(candidate, client, embed_model, component=component)
    if not cand_vec:
        return (None, 0.0)
    best_lab, best = None, 0.0
    for lab in pool:
        v = pool_emb.get(lab)
        if not v:
            v = _embed_text(lab, client, embed_model, component=component)
            if v:
                pool_emb[lab] = v
        if not v:
            continue
        s = _cos_sim(cand_vec, v)
        if s > best:
            best = s; best_lab = lab
    return (best_lab, float(best))

def resolve_candidate_to_canonical(
    candidate_raw: str,
    side: str,
    delighters: List[str],
    detractors: List[str],
    alias_to_label: Dict[str, str],
    learned_store: Dict[str, Any],
    sim_threshold_lex: float,
    sim_threshold_sem: float,
    client,
    embed_model: str,
) -> Dict[str, Any]:
    side_norm = "Delighter" if str(side).lower().startswith("del") else "Detractor"
    allowed = delighters if side_norm == "Delighter" else detractors

    cand_theme = normalize_theme_label(candidate_raw, side_norm)
    cand_canon = _canon(cand_theme)
    cand_key = _canon_simple(cand_theme)

    raw_key = _canon_simple(str(candidate_raw or "").strip())
    raw_canon = _canon(str(candidate_raw or "").strip())

    allowed_keys = {_canon_simple(x): x for x in allowed}
    if raw_key in allowed_keys:
        return {"canonical": allowed_keys[raw_key], "kind": "exact_existing", "target": allowed_keys[raw_key], "score": 1.0}
    if cand_key in allowed_keys:
        return {"canonical": allowed_keys[cand_key], "kind": "exact_existing", "target": allowed_keys[cand_key], "score": 1.0}

    if raw_canon in alias_to_label:
        tgt = alias_to_label[raw_canon]
        return {"canonical": tgt, "kind": "alias_to_existing", "target": tgt, "score": 1.0}
    if cand_canon in alias_to_label:
        tgt = alias_to_label[cand_canon]
        return {"canonical": tgt, "kind": "alias_to_existing", "target": tgt, "score": 1.0}

    try:
        m = difflib.get_close_matches(cand_theme, allowed, n=1, cutoff=float(sim_threshold_lex))
        if m:
            tgt = m[0]
            return {"canonical": tgt, "kind": "alias_to_existing", "target": tgt, "score": float(sim_threshold_lex)}
    except Exception:
        pass

    learned_labels = list(learned_store.get("labels", {}).get(side_norm, {}).keys())
    try:
        m2 = difflib.get_close_matches(cand_theme, learned_labels, n=1, cutoff=float(sim_threshold_lex))
        if m2:
            tgt = m2[0]
            return {"canonical": tgt, "kind": "synonym_to_learned", "target": tgt, "score": float(sim_threshold_lex)}
    except Exception:
        pass

    score_used = 0.0
    if client is not None and embed_model:
        pool_emb_allowed = learned_store.setdefault("_emb_allowed", {})
        pool_emb_learned = learned_store.setdefault("_emb_learned", {})
        best_a, score_a = _best_semantic_match(cand_theme, allowed, pool_emb_allowed, client, embed_model, component="embed-merge")
        best_l, score_l = _best_semantic_match(cand_theme, learned_labels, pool_emb_learned, client, embed_model, component="embed-merge")

        best_lab, best_score, best_kind = None, 0.0, ""
        if best_a and score_a >= best_score:
            best_lab, best_score, best_kind = best_a, score_a, "alias_to_existing"
        if best_l and score_l > best_score:
            best_lab, best_score, best_kind = best_l, score_l, "synonym_to_learned"

        if best_lab and best_score >= float(sim_threshold_sem):
            score_used = float(best_score)
            return {"canonical": best_lab, "kind": best_kind, "target": best_lab, "score": score_used}

    return {"canonical": cand_theme, "kind": "new", "target": "", "score": score_used}

# ------------------- Product Knowledge Prelearn -------------------
STOPWORDS = {
    "the","a","an","and","or","but","if","then","this","that","these","those","it","its","i","me","my","we","our","you","your",
    "to","of","in","on","for","with","as","at","by","from","is","are","was","were","be","been","being","have","has","had","do","does","did",
    "so","very","really","just","also","too","not","no","yes","they","them","their","he","she","his","her","him","there","here","when","while",
    "because","into","out","up","down","over","under","again","more","most","less","least","can","could","should","would","will","won't","dont","don't",
    "im","i'm","ive","i've","it's","cant","can't","didnt","didn't","wasnt","wasn't","isnt","isn't","arent","aren't",
}

def _top_terms(texts: List[str], top_n: int = 30) -> List[Tuple[str, int]]:
    counts: Dict[str, int] = {}
    for t in texts:
        toks = re.findall(r"[A-Za-z][A-Za-z']{2,}", str(t).lower())
        toks = [w for w in toks if w not in STOPWORDS]
        for w in toks:
            counts[w] = counts.get(w, 0) + 1
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return items[:top_n]

def _sample_reviews(df_in: pd.DataFrame, n: int, seed: int = 7) -> pd.DataFrame:
    df2 = df_in.copy()
    if n >= len(df2):
        return df2
    if "Star Rating" in df2.columns:
        try:
            rng = random.Random(seed)
            out_idx = []
            for rating, g in df2.groupby("Star Rating"):
                gidx = list(g.index)
                take = max(1, int(round(n * (len(gidx) / len(df2)))))
                rng.shuffle(gidx)
                out_idx.extend(gidx[:take])
            out_idx = out_idx[:n]
            return df2.loc[out_idx]
        except Exception:
            pass
    return df2.sample(n=n, random_state=seed)

# ---------- JSON salvage + retry wrappers (stability) ----------
def _safe_json_load(s: str) -> Dict[str, Any]:
    s = (s or "").strip()
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        pass
    try:
        i = s.find("{")
        j = s.rfind("}")
        if i >= 0 and j > i:
            return json.loads(s[i:j+1])
    except Exception:
        return {}
    return {}

def _chat_json_with_retries(
    client,
    *,
    model: str,
    temperature: float,
    messages: List[Dict[str, str]],
    component: str,
    response_format: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if client is None:
        return {}

    attempts = 1 + int(st.session_state.get("app_json_retries", 2) or 0)
    last_err = None

    for a in range(1, attempts + 1):
        try:
            kwargs: Dict[str, Any] = dict(
                model=model,
                temperature=float(temperature),
                messages=messages,
            )
            if response_format:
                kwargs["response_format"] = response_format

            # Throttle (coarse) to reduce rate-limit hits
            est = 0
            try:
                est = sum(_estimate_tokens(m.get("content", ""), model_id=model) for m in (messages or []))
            except Exception:
                est = 0
            _throttle("chat", est)

            resp = client.chat.completions.create(**kwargs)
            pt, ct = _extract_usage(resp)
            if pt or ct:
                _track(component, model, in_tok=pt, out_tok=ct, embed=False)

            content = resp.choices[0].message.content or "{}"
            data = _safe_json_load(content)
            if data:
                return data

            last_err = RuntimeError("Invalid/empty JSON response")
        except Exception as e:
            last_err = e

        if a < attempts:
            time.sleep(min((2 ** (a - 1)) + random.random(), 20))

    _ = last_err
    return {}

def _prelearn_llm_batch_mine(
    texts: List[str],
    client,
    model: str,
    temperature: float,
    known_themes: Dict[str, List[str]],
    max_themes: int = 30,
) -> Dict[str, Any]:
    if client is None:
        return {"detractors": [], "delighters": [], "product_profile": ""}

    sys = "\n".join([
        "You analyze consumer product reviews to extract CONSISTENT THEMES.",
        "Return STRICT JSON with schema:",
        '{"product_profile":"<1-2 sentence product summary>",'
        ' "detractors":[{"label":"<2-4 words Title Case>", "keywords":["k1","k2","k3"]}],'
        ' "delighters":[{"label":"<2-4 words Title Case>", "keywords":["k1","k2","k3"]}]}',
        "",
        "Rules:",
        "- Labels must be mutually exclusive; avoid near-duplicates and synonyms.",
        "- Use singular nouns when possible (Issue, Problem, Complaint).",
        "- If a concept matches an existing theme, REUSE that exact label (do not invent a variant).",
        f"- Return at most {max_themes//2} detractors and {max_themes//2} delighters.",
        "- Keywords must be short (1-2 words), lowercase.",
    ])

    payload = {
        "reviews": texts[:40],
        "known_detractor_themes": known_themes.get("Detractor", [])[:60],
        "known_delighter_themes": known_themes.get("Delighter", [])[:60],
    }

    data = _chat_json_with_retries(
        client,
        model=model,
        temperature=float(temperature),
        messages=[{"role":"system","content":sys},{"role":"user","content":json.dumps(payload)}],
        component="prelearn-mine",
        response_format={"type":"json_object"},
    )

    return {
        "product_profile": str(data.get("product_profile","") or "").strip(),
        "detractors": data.get("detractors", []) or [],
        "delighters": data.get("delighters", []) or [],
    }

def _merge_theme_dict(
    themes: Dict[str, Dict[str, Any]],
    side: str,
    canonical: str,
    keywords: List[str],
    count_inc: int = 1,
):
    d = themes.setdefault(side, {})
    rec = d.setdefault(canonical, {"count": 0, "keywords": set()})
    rec["count"] = int(rec.get("count", 0)) + int(count_inc)
    for k in (keywords or []):
        kk = str(k).strip().lower()
        if kk and len(kk) <= 32:
            rec["keywords"].add(kk)

def _consolidate_themes_semantic(
    side: str,
    themes: Dict[str, Dict[str, Any]],
    client,
    embed_model: str,
    sem_merge_threshold: float = 0.92,
) -> Dict[str, Dict[str, Any]]:
    if not themes.get(side):
        return themes

    labels = list(themes[side].keys())
    key_to_lab: Dict[str, str] = {}
    for lab in labels:
        k = _canon_simple(normalize_theme_label(lab, side))
        if k in key_to_lab and key_to_lab[k] != lab:
            tgt = key_to_lab[k]
            themes[side][tgt]["count"] += themes[side][lab]["count"]
            themes[side][tgt]["keywords"] |= themes[side][lab]["keywords"]
            del themes[side][lab]
        else:
            key_to_lab[k] = lab

    labels = list(themes[side].keys())
    if client is None or not embed_model or len(labels) < 2:
        return themes

    store = _ensure_learned_store()
    pool_emb = store["emb"][side]
    for lab in labels:
        if lab not in pool_emb:
            v = _embed_text(lab, client, embed_model, component="prelearn-embed-themes")
            if v:
                pool_emb[lab] = v

    labels_sorted = sorted(labels, key=lambda x: -int(themes[side][x]["count"]))
    for i, a in enumerate(labels_sorted):
        if a not in themes[side]:
            continue
        va = pool_emb.get(a)
        if not va:
            continue
        for b in labels_sorted[i+1:]:
            if b not in themes[side]:
                continue
            if b == a:
                continue
            vb = pool_emb.get(b)
            if not vb:
                continue
            sim = _cos_sim(va, vb)
            lex_sub = (_canon(a) in _canon(b)) or (_canon(b) in _canon(a))
            if sim >= float(sem_merge_threshold) or lex_sub:
                themes[side][a]["count"] += themes[side][b]["count"]
                themes[side][a]["keywords"] |= themes[side][b]["keywords"]
                del themes[side][b]
    return themes

def run_prelearn(
    df_in: pd.DataFrame,
    client,
    prelearn_model: str,
    temperature: float,
    embed_model: str,
    sample_n: int,
    batch_size: int,
    sem_merge_threshold: float,
    status_box,
    prog_bar,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    learned = _ensure_learned_store()

    status_box.markdown("🔎 **Prelearn:** Sampling reviews…")
    df_s = _sample_reviews(df_in, n=int(sample_n))
    reviews = [str(x) for x in df_s["Verbatim"].tolist() if str(x).strip()]
    prog_bar.progress(0.05)

    status_box.markdown("🧠 **Prelearn:** Building quick product glossary (no API)…")
    terms = _top_terms(reviews, top_n=40)
    learned["glossary_terms"] = terms
    prog_bar.progress(0.12)

    themes: Dict[str, Dict[str, Any]] = {"Delighter": {}, "Detractor": {}}
    profiles: List[str] = []
    n = len(reviews)
    if n == 0:
        status_box.markdown("⚠️ **Prelearn:** No reviews found to prelearn.")
        prog_bar.progress(1.0)
        return learned

    batches = [reviews[i:i+int(batch_size)] for i in range(0, n, int(batch_size))]
    total_batches = len(batches)
    status_box.markdown(f"🤖 **Prelearn:** Mining themes with LLM… ({total_batches} batches)")
    start_batch_time = time.perf_counter()

    for bi, chunk in enumerate(batches, start=1):
        elapsed = time.perf_counter() - start_batch_time
        avg = (elapsed / max(1, bi-1)) if bi > 1 else 0.0
        rem = (total_batches - bi + 1) * avg
        status_box.markdown(
            f"🤖 **Prelearn:** Batch {bi}/{total_batches} • "
            f"Reviews {((bi-1)*batch_size)+1}-{min(bi*batch_size, n)} of {n} • "
            f"ETA ~ {_fmt_secs(rem)}"
        )

        known = {
            "Delighter": list(themes["Delighter"].keys()),
            "Detractor": list(themes["Detractor"].keys()),
        }
        data = _prelearn_llm_batch_mine(
            texts=chunk,
            client=client,
            model=prelearn_model,
            temperature=temperature,
            known_themes=known,
            max_themes=30,
        )
        if data.get("product_profile"):
            profiles.append(str(data["product_profile"]))

        for obj in data.get("delighters", []) or []:
            lab = normalize_theme_label(obj.get("label",""), "Delighter")
            kws = obj.get("keywords", []) or []
            _merge_theme_dict(themes, "Delighter", lab, kws, count_inc=1)
        for obj in data.get("detractors", []) or []:
            lab = normalize_theme_label(obj.get("label",""), "Detractor")
            kws = obj.get("keywords", []) or []
            _merge_theme_dict(themes, "Detractor", lab, kws, count_inc=1)

        prog = 0.12 + 0.66 * (bi / max(1, total_batches))
        prog_bar.progress(min(0.78, max(0.12, prog)))

        try:
            budget = float(st.session_state.get("budget_limit", 0.0) or 0.0)
        except Exception:
            budget = 0.0
        if budget > 0:
            tr_now = _ensure_usage_tracker()
            session_total = float(tr_now["cost_chat"] + tr_now["cost_embed"])
            if session_total >= budget:
                status_box.markdown(
                    f"⛔ **Budget guard:** session spend {_fmt_money(session_total)} exceeded limit {_fmt_money(budget)}. Stopping prelearn early."
                )
                break

        if bi % 5 == 0:
            gc.collect()

    status_box.markdown("🧩 **Prelearn:** Consolidating themes (dedupe + semantic merge)…")
    themes = _consolidate_themes_semantic("Delighter", themes, client, embed_model, sem_merge_threshold=float(sem_merge_threshold))
    themes = _consolidate_themes_semantic("Detractor", themes, client, embed_model, sem_merge_threshold=float(sem_merge_threshold))
    prog_bar.progress(0.90)

    learned["labels"] = {"Delighter": {}, "Detractor": {}}
    for side in ("Delighter","Detractor"):
        for lab, rec in sorted(themes.get(side, {}).items(), key=lambda kv: (-int(kv[1]["count"]), kv[0])):
            learned["labels"][side][lab] = {"synonyms": set(), "count": int(rec.get("count", 0))}
            learned["keywords"][side][lab] = set(rec.get("keywords", set()))
    learned["product_profile"] = " ".join([p.strip() for p in profiles[:6] if p.strip()])[:600]
    learned["ts"] = time.time()
    learned["version"] = f"prelearn_{int(learned['ts'] or 0)}"

    prog_bar.progress(1.0)
    dt = time.perf_counter() - t0
    status_box.markdown(f"✅ **Prelearn complete** in {_fmt_secs(dt)} • Learned {len(learned['labels']['Delighter'])} delighter themes, {len(learned['labels']['Detractor'])} detractor themes.")
    return learned

# ------------------- Unified Labeler (single) -------------------
def _openai_labeler_unified(
    verbatim: str,
    client,
    model: str,
    temperature: float,
    allowed_delighters: List[str],
    allowed_detractors: List[str],
    known_theme_hints: Dict[str, List[str]],
    max_ev_per_label: int = 2,
    max_ev_chars: int = 120,
) -> Dict[str, Any]:
    if (client is None) or (not verbatim or not verbatim.strip()):
        return {
            "dels": [], "dets": [], "unl_dels": [], "unl_dets": [],
            "ev_del_map": {}, "ev_det_map": {},
            "safety": "Not Mentioned", "reliability": "Not Mentioned", "sessions": "Unknown",
        }

    key = ("lab2", _canon(verbatim), model, f"{float(temperature):.2f}",
           _symptom_list_version(allowed_delighters, allowed_detractors, {}), max_ev_per_label, max_ev_chars,
           json.dumps(known_theme_hints, sort_keys=True)[:2000])
    cache = _ensure_label_cache()
    if key in cache:
        return cache[key]

    sys = "\n".join([
        "You label consumer reviews with predefined symptom lists and extract 3 meta fields.",
        "Return STRICT JSON with this schema:",
        '{'
        '"detractors":[{"label":"<one from allowed detractors>","evidence":["<exact substring from review>", "..."]}],'
        ' "delighters":[{"label":"<one from allowed delighters>","evidence":["<exact substring>", "..."]}],'
        ' "unlisted_detractors":["<THEME>", "..."], "unlisted_delighters":["<THEME>", "..."],'
        ' "safety":"<enum>", "reliability":"<enum>", "sessions":"<enum>"'
        '}',
        "",
        "Rules:",
        f"- Evidence MUST be exact substrings from the review. Each ≤ {max_ev_chars} chars. Up to {max_ev_per_label} per label.",
        "- Only include a label if there is clear textual support in the review.",
        "- Use ONLY allowed lists for 'detractors' and 'delighters'.",
        "- For unlisted_* items, return a SHORT THEME (1–3 words), Title Case, no punctuation except slashes.",
        "- Avoid duplicates and near-duplicates (plural vs singular, synonyms). Prefer reusing known themes if provided.",
        "- Cap to maximum 10 detractors and 10 delighters. Cap to 10 unlisted per side.",
        "",
        "Meta enums:",
        "SAFETY one of: ['Not Mentioned','Concern','Positive']",
        "RELIABILITY one of: ['Not Mentioned','Negative','Neutral','Positive']",
        "SESSIONS one of: ['0','1','2–3','4–9','10+','Unknown']",
    ])

    user_payload = {
        "review": verbatim.strip(),
        "allowed_delighters": allowed_delighters,
        "allowed_detractors": allowed_detractors,
        "known_unlisted_detractor_themes": (known_theme_hints.get("Detractor") or [])[:60],
        "known_unlisted_delighter_themes": (known_theme_hints.get("Delighter") or [])[:60],
    }

    data = _chat_json_with_retries(
        client,
        model=model,
        temperature=float(temperature),
        messages=[{"role": "system", "content": sys},
                  {"role": "user", "content": json.dumps(user_payload)}],
        component="symptomize-label",
        response_format={"type": "json_object"},
    )

    if not data:
        return {
            "dels": [], "dets": [], "unl_dels": [], "unl_dets": [],
            "ev_del_map": {}, "ev_det_map": {},
            "safety": "Not Mentioned", "reliability": "Not Mentioned", "sessions": "Unknown",
        }

    raw_dels = data.get("delighters", []) or []
    raw_dets = data.get("detractors", []) or []
    unl_dels = [x for x in (data.get("unlisted_delighters", []) or []) if isinstance(x, str) and x.strip()][:10]
    unl_dets = [x for x in (data.get("unlisted_detractors", []) or []) if isinstance(x, str) and x.strip()][:10]

    s = str(data.get("safety", "Not Mentioned")).strip()
    r = str(data.get("reliability", "Not Mentioned")).strip()
    n = str(data.get("sessions", "Unknown")).strip()
    s = s if s in SAFETY_ENUM else "Not Mentioned"
    r = r if r in RELIABILITY_ENUM else "Not Mentioned"
    n = n if n in SESSIONS_ENUM else "Unknown"

    def _extract_allowed(objs: Iterable[Any], allowed: List[str]) -> Tuple[List[str], Dict[str, List[str]]]:
        out_labels: List[str] = []
        ev_map: Dict[str, List[str]] = {}
        allowed_set = set(allowed)
        for obj in objs:
            try:
                lbl = str(obj.get("label", "")).strip()
                evs = [str(e)[:max_ev_chars] for e in (obj.get("evidence", []) or []) if isinstance(e, str) and e.strip()]
            except Exception:
                continue
            if lbl in allowed_set and lbl not in out_labels:
                out_labels.append(lbl)
                ev_map[lbl] = evs[:max_ev_per_label]
            if len(out_labels) >= 10:
                break
        return out_labels, ev_map

    dels, ev_del_map = _extract_allowed(raw_dels, allowed_delighters)
    dets, ev_det_map = _extract_allowed(raw_dets, allowed_detractors)

    out = {
        "dels": dels,
        "dets": dets,
        "unl_dels": unl_dels,
        "unl_dets": unl_dets,
        "ev_del_map": ev_del_map,
        "ev_det_map": ev_det_map,
        "safety": s,
        "reliability": r,
        "sessions": n,
    }
    cache[key] = out
    return out

# ------------------- Unified Labeler (BATCH: fast) -------------------
_LABELER_DEFAULT = {
    "dels": [], "dets": [],
    "unl_dels": [], "unl_dets": [],
    "ev_del_map": {}, "ev_det_map": {},
    "safety": "Not Mentioned", "reliability": "Not Mentioned", "sessions": "Unknown",
}

def _label_cache_key(
    verbatim: str,
    model: str,
    temperature: float,
    allowed_delighters: List[str],
    allowed_detractors: List[str],
    known_theme_hints: Dict[str, List[str]],
    max_ev_per_label: int,
    max_ev_chars: int,
) -> Tuple[Any, ...]:
    return (
        "lab2",
        _canon(verbatim),
        model,
        f"{float(temperature):.2f}",
        _symptom_list_version(allowed_delighters, allowed_detractors, {}),
        int(max_ev_per_label),
        int(max_ev_chars),
        json.dumps(known_theme_hints, sort_keys=True)[:2000],
    )

def _normalize_unified_output(
    data: Any,
    allowed_delighters: List[str],
    allowed_detractors: List[str],
    max_ev_per_label: int,
    max_ev_chars: int,
) -> Dict[str, Any]:
    if not isinstance(data, dict):
        data = {}

    raw_dels = data.get("delighters", []) or []
    raw_dets = data.get("detractors", []) or []
    unl_dels = [x for x in (data.get("unlisted_delighters", []) or []) if isinstance(x, str) and x.strip()][:10]
    unl_dets = [x for x in (data.get("unlisted_detractors", []) or []) if isinstance(x, str) and x.strip()][:10]

    s = str(data.get("safety", "Not Mentioned")).strip()
    r = str(data.get("reliability", "Not Mentioned")).strip()
    n = str(data.get("sessions", "Unknown")).strip()
    s = s if s in SAFETY_ENUM else "Not Mentioned"
    r = r if r in RELIABILITY_ENUM else "Not Mentioned"
    n = n if n in SESSIONS_ENUM else "Unknown"

    def _extract_allowed(objs: Iterable[Any], allowed: List[str]) -> Tuple[List[str], Dict[str, List[str]]]:
        out_labels: List[str] = []
        ev_map: Dict[str, List[str]] = {}
        allowed_set = set(allowed)
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            lbl = str(obj.get("label", "")).strip()
            evs_raw = obj.get("evidence", []) or []
            evs = []
            for e in evs_raw:
                if isinstance(e, str) and e.strip():
                    evs.append(str(e)[:max_ev_chars])
            if lbl in allowed_set and lbl not in out_labels:
                out_labels.append(lbl)
                ev_map[lbl] = evs[:max_ev_per_label]
            if len(out_labels) >= 10:
                break
        return out_labels, ev_map

    dels, ev_del_map = _extract_allowed(raw_dels, allowed_delighters)
    dets, ev_det_map = _extract_allowed(raw_dets, allowed_detractors)

    return {
        "dels": dels,
        "dets": dets,
        "unl_dels": unl_dels,
        "unl_dets": unl_dets,
        "ev_del_map": ev_del_map,
        "ev_det_map": ev_det_map,
        "safety": s,
        "reliability": r,
        "sessions": n,
    }

def _openai_labeler_unified_batch(
    items: List[Dict[str, Any]],
    client,
    model: str,
    temperature: float,
    allowed_delighters: List[str],
    allowed_detractors: List[str],
    known_theme_hints: Dict[str, List[str]],
    max_ev_per_label: int = 2,
    max_ev_chars: int = 120,
    product_profile: str = "",
) -> Dict[int, Dict[str, Any]]:
    """
    Batch label N reviews in ONE request.
    items: [{"idx": int, "review": str, "needs_del": bool, "needs_det": bool}, ...]
    Returns: idx -> normalized output dict.
    """
    out_by_idx: Dict[int, Dict[str, Any]] = {}
    if client is None or not items:
        return out_by_idx

    cache = _ensure_label_cache()
    to_send: List[Tuple[int, str, bool, bool, Tuple[Any, ...]]] = []

    for it in items:
        idx = int(it.get("idx"))
        review = str(it.get("review") or "")
        needs_del = bool(it.get("needs_del", True))
        needs_det = bool(it.get("needs_det", True))

        if not review.strip():
            out_by_idx[idx] = dict(_LABELER_DEFAULT)
            continue

        key = _label_cache_key(
            review, model, float(temperature),
            allowed_delighters, allowed_detractors,
            known_theme_hints, int(max_ev_per_label), int(max_ev_chars)
        )
        if key in cache:
            out_by_idx[idx] = cache[key]
        else:
            to_send.append((idx, review, needs_del, needs_det, key))

    if not to_send:
        return out_by_idx

    sys_lines = [
        "You label consumer reviews with predefined symptom lists and extract 3 meta fields.",
        "You will receive MULTIPLE reviews at once; treat each independently.",
        "Return STRICT JSON with schema:",
        '{"items":['
        '{"id":"<id>",'
        '"detractors":[{"label":"<one from allowed detractors>","evidence":["<exact substring>", "..."]}],'
        ' "delighters":[{"label":"<one from allowed delighters>","evidence":["<exact substring>", "..."]}],'
        ' "unlisted_detractors":["<THEME>", "..."], "unlisted_delighters":["<THEME>", "..."],'
        ' "safety":"<enum>", "reliability":"<enum>", "sessions":"<enum>"}'
        ']}',
        "",
        "Rules:",
        f"- Evidence MUST be exact substrings from THAT review. Each ≤ {max_ev_chars} chars. Up to {max_ev_per_label} per label.",
        "- Only include a label if there is clear textual support in the review.",
        "- Use ONLY allowed lists for 'detractors' and 'delighters'.",
        "- For unlisted_* items, return a SHORT THEME (1–3 words), Title Case, no punctuation except slashes.",
        "- Avoid duplicates and near-duplicates (plural vs singular, synonyms). Prefer reusing known themes if provided.",
        "- Cap to maximum 10 detractors and 10 delighters. Cap to 10 unlisted per side.",
        "- Always return ALL keys for every item (use empty lists / Not Mentioned if none).",
        "",
        "Meta enums:",
        "SAFETY one of: ['Not Mentioned','Concern','Positive']",
        "RELIABILITY one of: ['Not Mentioned','Negative','Neutral','Positive']",
        "SESSIONS one of: ['0','1','2–3','4–9','10+','Unknown']",
    ]
    if product_profile and str(product_profile).strip():
        sys_lines.insert(2, f"Product context (brief): {str(product_profile).strip()[:600]}")

    payload = {
        "items": [
            {
                "id": str(idx),
                "review": review,
                "needs_delighters": bool(needs_del),
                "needs_detractors": bool(needs_det),
            }
            for (idx, review, needs_del, needs_det, _) in to_send
        ],
        "allowed_delighters": allowed_delighters,
        "allowed_detractors": allowed_detractors,
        "known_unlisted_detractor_themes": (known_theme_hints.get("Detractor") or [])[:60],
        "known_unlisted_delighter_themes": (known_theme_hints.get("Delighter") or [])[:60],
    }

    data = _chat_json_with_retries(
        client,
        model=model,
        temperature=float(temperature),
        messages=[
            {"role": "system", "content": "\n".join(sys_lines)},
            {"role": "user", "content": json.dumps(payload)}
        ],
        component="symptomize-label-batch",
        response_format={"type": "json_object"},
    )

    items_out = []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items_out = data["items"]
    elif isinstance(data, list):
        items_out = data
    else:
        items_out = []

    by_id: Dict[str, Any] = {}
    for obj in items_out:
        if isinstance(obj, dict) and "id" in obj:
            by_id[str(obj.get("id"))] = obj

    for (idx, review, needs_del, needs_det, key) in to_send:
        obj = by_id.get(str(idx), {}) or {}
        norm = _normalize_unified_output(
            obj, allowed_delighters, allowed_detractors, int(max_ev_per_label), int(max_ev_chars)
        )
        out_by_idx[idx] = norm
        cache[key] = norm

    return out_by_idx

# ------------------- Export helpers (LAZY export: take bytes) -------------------
def generate_template_workbook_bytes(
    original_bytes: bytes,
    updated_df: pd.DataFrame,
    processed_idx: Optional[Set[int]] = None,
    overwrite_processed_slots: bool = False,
) -> bytes:
    wb = load_workbook(io.BytesIO(original_bytes))
    sheet_name = "Star Walk scrubbed verbatims"
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.sheetnames[0]
    ws: Worksheet = wb[sheet_name]

    df2 = ensure_ai_columns(updated_df.copy())

    for name, col in META_ORDER:
        col_idx = column_index_from_string(col)
        if not ws.cell(row=1, column=col_idx).value:
            ws.cell(row=1, column=col_idx, value=name)

    fill_green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    fill_red   = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    fill_yel   = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    fill_blu   = PatternFill(start_color="CFE2F3", end_color="CFE2F3", fill_type="solid")
    fill_pur   = PatternFill(start_color="EAD1DC", end_color="EAD1DC", fill_type="solid")

    pset = set(processed_idx or [])

    def _clear_template_slots(row_i: int):
        for col_idx in DET_INDEXES + DEL_INDEXES + list(META_INDEXES.values()):
            ws.cell(row=row_i, column=col_idx, value=None)

    for i, (rid, r) in enumerate(df2.iterrows(), start=2):
        if overwrite_processed_slots and (int(rid) in pset):
            _clear_template_slots(i)
        for j, col_idx in enumerate(DET_INDEXES, start=1):
            val = r.get(f"AI Symptom Detractor {j}")
            cv = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cv)
            if cv is not None:
                cell.fill = fill_red
        for j, col_idx in enumerate(DEL_INDEXES, start=1):
            val = r.get(f"AI Symptom Delighter {j}")
            cv = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cv)
            if cv is not None:
                cell.fill = fill_green
        safety = r.get("AI Safety"); reliab = r.get("AI Reliability"); sess = r.get("AI # of Sessions")
        if is_filled(safety):
            c = ws.cell(row=i, column=META_INDEXES["Safety"], value=str(safety)); c.fill = fill_yel
        if is_filled(reliab):
            c = ws.cell(row=i, column=META_INDEXES["Reliability"], value=str(reliab)); c.fill = fill_blu
        if is_filled(sess):
            c = ws.cell(row=i, column=META_INDEXES["# of Sessions"], value=str(sess)); c.fill = fill_pur

    for c in DET_INDEXES + DEL_INDEXES + list(META_INDEXES.values()):
        try:
            ws.column_dimensions[get_column_letter(c)].width = 28
        except Exception:
            pass

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

def apply_symptoms_updates_to_workbook(
    original_bytes: bytes,
    new_symptoms: List[Tuple[str, str]],
    alias_additions: List[Tuple[str, str]],
) -> bytes:
    wb = load_workbook(io.BytesIO(original_bytes))

    if "Symptoms" not in wb.sheetnames:
        ws = wb.create_sheet("Symptoms")
    else:
        ws = wb["Symptoms"]

    try:
        headers_row = [c.value for c in ws[1]]
    except Exception:
        headers_row = []
    headers = [str(h).strip() if h is not None else "" for h in headers_row]
    header_map = {str(h).strip().lower(): i + 1 for i, h in enumerate(headers) if str(h).strip()}

    used_cols: Set[int] = set()

    def _ensure_header(name: str, synonyms: List[str], preferred_index: Optional[int] = None) -> int:
        for syn in synonyms:
            idx = header_map.get(str(syn).lower())
            if idx:
                if not ws.cell(row=1, column=idx).value:
                    ws.cell(row=1, column=idx, value=name)
                used_cols.add(idx); return idx
        max_col = int(getattr(ws, "max_column", 0) or 0)
        idx = preferred_index if (preferred_index and preferred_index > 0) else (max_col + 1 if max_col > 0 else 1)
        while idx in used_cols:
            idx += 1
        ws.cell(row=1, column=idx, value=name); used_cols.add(idx); return idx

    col_label = _ensure_header("Symptom", ["symptom", "label", "name", "item"], preferred_index=1)
    col_type  = _ensure_header("Type", ["type", "polarity", "category", "side"], preferred_index=2)
    col_alias = _ensure_header("Aliases", ["aliases", "alias"], preferred_index=3)

    existing_row: Dict[str, int] = {}
    existing_aliases: Dict[str, Set[str]] = {}

    try:
        last_row = int(getattr(ws, "max_row", 0) or 0)
    except Exception:
        last_row = 0

    for r_i in range(2, last_row + 1):
        v = ws.cell(row=r_i, column=col_label).value
        if not v:
            continue
        lab = str(v).strip()
        existing_row[lab] = r_i
        als = ws.cell(row=r_i, column=col_alias).value
        aset: Set[str] = set()
        if als:
            als_norm = str(als).replace(",", "|")
            aset = {a.strip() for a in als_norm.split("|") if a.strip()}
        existing_aliases[lab] = aset

    for label, side in new_symptoms:
        lab = str(label).strip()
        if not lab:
            continue
        if lab in existing_row:
            continue
        rnew = (int(getattr(ws, "max_row", 1) or 1)) + 1
        ws.cell(row=rnew, column=col_label, value=lab)
        ws.cell(row=rnew, column=col_type, value=str(side).strip() or "")
        existing_row[lab] = rnew
        existing_aliases[lab] = set()

    for tgt_label, alias in alias_additions:
        tgt = str(tgt_label).strip()
        als = str(alias).strip()
        if not tgt or not als:
            continue
        if tgt not in existing_row:
            continue
        aset = existing_aliases.setdefault(tgt, set())
        if _canon_simple(als) == _canon_simple(tgt):
            continue
        if any(_canon_simple(als) == _canon_simple(a) for a in aset):
            continue
        aset.add(als)
        r_i = existing_row[tgt]
        ws.cell(row=r_i, column=col_alias, value=" | ".join(sorted(aset)))

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out.getvalue()

# ------------------- File Upload (SESSION-PERSISTED) -------------------
uploaded_file = st.file_uploader("📂 Upload Excel (with 'Star Walk scrubbed verbatims' + 'Symptoms')", type=["xlsx"])
if not uploaded_file:
    st.stop()

uploaded_bytes = uploaded_file.getvalue()
file_sig = hashlib.md5(uploaded_bytes).hexdigest()

if st.session_state.get("_file_sig") != file_sig:
    st.session_state["_file_sig"] = file_sig
    st.session_state["uploaded_bytes"] = uploaded_bytes

    df0 = _load_reviews_df(uploaded_bytes)
    if "Verbatim" not in df0.columns:
        st.error("Missing 'Verbatim' column.")
        st.stop()

    df0["Verbatim"] = df0["Verbatim"].map(clean_text)
    st.session_state["df_work"] = ensure_ai_columns(df0)

    # Normalize common filter columns once per upload
    c_date = _find_col(st.session_state["df_work"], ["Review Date"])
    c_star = _find_col(st.session_state["df_work"], ["Star Rating", "star rating", "Rating"])
    _coerce_datetime_inplace(st.session_state["df_work"], c_date)
    _coerce_numeric_inplace(st.session_state["df_work"], c_star)

    d, t, a = get_symptom_whitelists(uploaded_bytes)
    st.session_state["DELIGHTERS"] = d
    st.session_state["DETRACTORS"] = t
    st.session_state["ALIASES"] = a

    st.session_state["undo_stack"] = []
    st.session_state["processed_rows"] = []
    st.session_state["processed_idx_set"] = set()
    st.session_state["new_symptom_candidates"] = {}
    st.session_state["alias_suggestion_candidates"] = {}
    st.session_state["last_run_processed_count"] = 0
    st.session_state["ev_cov_num"] = 0
    st.session_state["ev_cov_den"] = 0

    st.session_state["_label_cache"] = {}
    st.session_state["_embed_cache"] = {}
    st.session_state["_usage"] = {
        "chat_in": 0, "chat_out": 0, "embed_in": 0,
        "cost_chat": 0.0, "cost_embed": 0.0, "by_component": {}
    }
    st.session_state.pop("export_bytes", None)

    st.session_state.pop("learned", None)
    _ensure_learned_store()

df = st.session_state["df_work"]
DELIGHTERS = st.session_state.get("DELIGHTERS", [])
DETRACTORS = st.session_state.get("DETRACTORS", [])
ALIASES = st.session_state.get("ALIASES", {}) or {}

if not DELIGHTERS and not DETRACTORS:
    st.warning("⚠️ No Symptoms found in 'Symptoms' tab. Prelearn can bootstrap themes.")
else:
    st.success(f"Loaded {len(DELIGHTERS)} Delighters, {len(DETRACTORS)} Detractors from Symptoms tab.")

DEL_MAP, DET_MAP, ALIAS_TO_LABEL = build_canonical_maps(DELIGHTERS, DETRACTORS, ALIASES)

# ------------------- Detection & KPIs -------------------
colmap = detect_symptom_columns(df)
work = detect_missing(df, colmap)

total = len(work)
need_del = int(work["Needs_Delighters"].sum())
need_det = int(work["Needs_Detractors"].sum())
need_both = int(work["Needs_Symptomization"].sum())

st.markdown(f"""
<div class="hero">
  <div class="hero-stats">
    <div class="stat"><div class="label">Total Reviews</div><div class="value">{total:,}</div></div>
    <div class="stat"><div class="label">Need Delighters</div><div class="value">{need_del:,}</div></div>
    <div class="stat"><div class="label">Need Detractors</div><div class="value">{need_det:,}</div></div>
    <div class="stat accent"><div class="label">Missing Both</div><div class="value">{need_both:,}</div></div>
  </div>
</div>
""", unsafe_allow_html=True)

# ------------------- LLM & Similarity Settings -------------------
st.sidebar.header("🤖 LLM Settings")

# Stability controls
st.sidebar.subheader("🛡️ Stability")
request_timeout_s = st.sidebar.number_input("OpenAI request timeout (sec)", 10, 300, 60, 10)
sdk_max_retries = st.sidebar.number_input("OpenAI SDK retries (per request)", 0, 10, 3, 1)
app_json_retries = st.sidebar.number_input("App JSON retries (parse/format)", 0, 6, 2, 1)
ui_log_limit = st.sidebar.slider(
    "Show last N processed reviews in UI",
    0, 200, 40, 10,
    help="0 = fastest (no per-review expanders). Prevents UI from freezing on huge runs."
)
enable_undo = st.sidebar.checkbox("Enable undo snapshots (uses RAM)", value=True)

st.session_state["ui_log_limit"] = int(ui_log_limit)
st.session_state["enable_undo"] = bool(enable_undo)
st.session_state["app_json_retries"] = int(app_json_retries)

MODEL_CHOICES = {
    "Fast – GPT-4o-mini (default)": "gpt-4o-mini",
    "Balanced – GPT-4.1": "gpt-4.1",
    "Balanced – GPT-4o": "gpt-4o",
    "Advanced – GPT-5": "gpt-5",
}
model_label = st.sidebar.selectbox("Model", list(MODEL_CHOICES.keys()), index=0)
selected_model = MODEL_CHOICES[model_label]
temperature = st.sidebar.slider("Creativity (temperature)", 0.0, 1.0, 0.2, 0.1)

def _make_openai_client(api_key: str, timeout_s: float, max_retries: int):
    if not (_HAS_OPENAI and api_key):
        return None
    try:
        return OpenAI(api_key=api_key, timeout=float(timeout_s), max_retries=int(max_retries))
    except TypeError:
        try:
            return OpenAI(api_key=api_key)
        except Exception:
            return None

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
client = _make_openai_client(api_key, request_timeout_s, sdk_max_retries)
if client is None:
    st.sidebar.warning("OpenAI not configured — set OPENAI_API_KEY and install 'openai'.")

# ⚡ Speed / Rate-limit (NEW)
st.sidebar.subheader("⚡ Speed / Rate-limit")

llm_batch_size = st.sidebar.slider(
    "LLM batch size (reviews per request)",
    1, 12, 6, 1,
    help="Batches multiple reviews into one API call. Much faster + fewer requests."
)
batch_token_budget = st.sidebar.slider(
    "Batch token budget (approx prompt tokens)",
    5000, 100000, 35000, 1000,
    help="Caps how much text we pack into a single request (keeps quality stable)."
)

throttle_rpm = st.sidebar.number_input(
    "Throttle: max chat requests/min (0 = off)",
    0, 600, 0, 10,
    help="Set this if you know your org RPM limit. Helps avoid 429s."
)
throttle_tpm = st.sidebar.number_input(
    "Throttle: max estimated input tokens/min (0 = off)",
    0, 2_000_000, 0, 10_000,
    help="Coarse guard for TPM limits; uses estimated prompt tokens."
)

st.session_state["llm_batch_size"] = int(llm_batch_size)
st.session_state["batch_token_budget"] = int(batch_token_budget)
st.session_state["throttle_rpm"] = int(throttle_rpm)
st.session_state["throttle_tpm"] = int(throttle_tpm)

# Similarity guards
st.sidebar.subheader("🧩 Consistency & Dedupe")
sim_threshold_lex = st.sidebar.slider(
    "Lexical similarity guard (difflib)",
    0.80, 0.99, 0.94, 0.01,
    help="Raise to suppress near-duplicates. Used for both New Symptoms & Alias Suggestions."
)
sim_threshold_sem = st.sidebar.slider(
    "Semantic similarity guard (embeddings)",
    0.80, 0.99, 0.92, 0.01,
    help="Used to merge synonyms like 'Learning Curve' ~ 'Initially Complicated'. Requires embeddings + OpenAI."
)

# Evidence settings
st.sidebar.subheader("🧾 Evidence")
require_evidence = st.sidebar.checkbox(
    "Require evidence to write labels",
    value=True,
    help="If ON, a label must include ≥1 exact snippet from the review to be written."
)
max_ev_per_label = st.sidebar.slider("Max evidence per label", 1, 3, 2)
max_ev_chars = st.sidebar.slider("Max evidence length (chars)", 40, 200, 120, 10)

# Embeddings + prelearn settings
st.sidebar.subheader("🧠 Product Knowledge Prelearn (recommended)")
prelearn_enabled = st.sidebar.checkbox(
    "Auto-run Product Knowledge Prelearn before symptomizing",
    value=True,
    help="Runs a fast pre-pass to learn product glossary + canonical themes to reduce duplicates."
)
prelearn_model = st.sidebar.selectbox(
    "Prelearn model (cheap recommended)",
    ["gpt-4o-mini", "gpt-4.1", "gpt-4o", "gpt-5"],
    index=0,
    help="Used only for the prelearn mining step."
)
embed_model = st.sidebar.selectbox(
    "Embedding model",
    ["text-embedding-3-small"],
    index=0,
    help="Used for semantic merging and redundancy suppression."
)
prelearn_sample_n = st.sidebar.slider("Prelearn sample size", 100, 3000, 800, 50)
prelearn_batch_size = st.sidebar.slider("Prelearn batch size", 20, 120, 60, 10)
prelearn_merge_threshold = st.sidebar.slider("Prelearn theme merge threshold", 0.85, 0.99, 0.92, 0.01)

use_learned_as_allowed = st.sidebar.checkbox(
    "Use learned themes as temporary allowed list (helps when Symptoms tab is incomplete)",
    value=True,
    help="When ON, the labeler can tag using learned themes in addition to the Symptoms tab."
)

# Cost panel
st.sidebar.subheader("💰 Cost (this session)")
if "_pricing_overrides" not in st.session_state:
    st.session_state["_pricing_overrides"] = {"models": {}, "embeddings": {}}

with st.sidebar.expander("Pricing overrides (optional)", expanded=False):
    st.caption("Defaults are from OpenAI docs; override here if pricing changes.")
    models_to_show = sorted({selected_model, prelearn_model})
    for mid in models_to_show:
        default = MODEL_PRICING_PER_1M.get(mid, {"in": 0.0, "out": 0.0})
        cur_in = st.session_state["_pricing_overrides"]["models"].get(mid, {}).get("in", default["in"])
        cur_out = st.session_state["_pricing_overrides"]["models"].get(mid, {}).get("out", default["out"])
        in_p = st.number_input(f"{mid} input ($/1M)", value=float(cur_in), step=0.01, key=f"ov_{mid}_in")
        out_p = st.number_input(f"{mid} output ($/1M)", value=float(cur_out), step=0.01, key=f"ov_{mid}_out")
        st.session_state["_pricing_overrides"]["models"][mid] = {"in": float(in_p), "out": float(out_p)}
    emb_default = EMBEDDING_PRICING_PER_1M.get(embed_model, 0.0)
    emb_cur = st.session_state["_pricing_overrides"]["embeddings"].get(embed_model, emb_default)
    emb_p = st.number_input(f"{embed_model} ($/1M tokens)", value=float(emb_cur), step=0.01, key=f"ov_{embed_model}_emb")
    st.session_state["_pricing_overrides"]["embeddings"][embed_model] = float(emb_p)

tr = _ensure_usage_tracker()
pin, pout = _price_for_model(selected_model)
st.sidebar.markdown(
    f"<div class='tiny'>Model <span class='mono'>{selected_model}</span>: "
    f"<b>${pin}</b>/1M input • <b>${pout}</b>/1M output</div>",
    unsafe_allow_html=True,
)
pemb = _price_for_embedding(embed_model)
st.sidebar.markdown(
    f"<div class='tiny'>Embeddings <span class='mono'>{embed_model}</span>: <b>${pemb}</b>/1M tokens</div>",
    unsafe_allow_html=True,
)

st.sidebar.markdown(
    f"<div class='chip-wrap'>"
    f"<span class='chip blue'>Input tokens: {int(tr['chat_in']):,}</span>"
    f"<span class='chip purple'>Output tokens: {int(tr['chat_out']):,}</span>"
    f"<span class='chip yellow'>Embed tokens: {int(tr['embed_in']):,}</span>"
    f"</div>",
    unsafe_allow_html=True,
)
st.sidebar.markdown(
    f"<div class='chip-wrap'>"
    f"<span class='chip green'>Chat cost: {_fmt_money(float(tr['cost_chat']))}</span>"
    f"<span class='chip green'>Embed cost: {_fmt_money(float(tr['cost_embed']))}</span>"
    f"<span class='chip green'><b>Total:</b> {_fmt_money(float(tr['cost_chat']) + float(tr['cost_embed']))}</span>"
    f"</div>",
    unsafe_allow_html=True,
)

budget_limit = st.sidebar.number_input(
    "Stop runs if session spend exceeds (USD)",
    min_value=0.0,
    value=float(st.session_state.get("budget_limit", 0.0)),
    step=1.0,
    help="0 disables. Useful to prevent accidental large runs."
)
st.session_state["budget_limit"] = float(budget_limit)

# ------------------- Prelearn UI -------------------
st.subheader("🧠 Product Knowledge Prelearn")
prelearn_colA, prelearn_colB = st.columns([1.2, 2.8])
with prelearn_colA:
    run_prelearn_btn = st.button("🧠 Run Prelearn now", use_container_width=True, disabled=(client is None))
with prelearn_colB:
    st.markdown(
        "<div class='tiny'>Prelearn builds a product glossary + canonical theme list so the system stops suggesting duplicates "
        "like “Comfortable fit / comfortable use / comfortable design” and merges synonyms into one theme.</div>",
        unsafe_allow_html=True,
    )

prelearn_status = st.empty()
prelearn_prog = st.progress(0.0)

if run_prelearn_btn and client is not None:
    learned = run_prelearn(
        df_in=df,
        client=client,
        prelearn_model=prelearn_model,
        temperature=0.0,
        embed_model=embed_model,
        sample_n=int(prelearn_sample_n),
        batch_size=int(prelearn_batch_size),
        sem_merge_threshold=float(prelearn_merge_threshold),
        status_box=prelearn_status,
        prog_bar=prelearn_prog,
    )
    st.session_state["learned"] = learned

learned_store = _ensure_learned_store()
if learned_store.get("labels", {}).get("Delighter") or learned_store.get("labels", {}).get("Detractor"):
    with st.expander("See learned product knowledge", expanded=False):
        st.markdown("**Product profile (learned)**")
        st.write(learned_store.get("product_profile","") or "(none)")
        st.markdown("**Top glossary terms (no-API)**")
        if learned_store.get("glossary_terms"):
            chips = "<div class='chip-wrap'>" + "".join(
                [f"<span class='chip blue'>{html.escape(w)} · {c}</span>" for w,c in learned_store["glossary_terms"][:30]]
            ) + "</div>"
            st.markdown(chips, unsafe_allow_html=True)
        else:
            st.write("(none)")
        st.markdown("**Learned themes (Delighters)**")
        d1 = list(learned_store.get("labels", {}).get("Delighter", {}).keys())[:40]
        st.markdown("<div class='chip-wrap'>" + "".join([f"<span class='chip green'>{html.escape(x)}</span>" for x in d1]) + "</div>", unsafe_allow_html=True)
        st.markdown("**Learned themes (Detractors)**")
        d2 = list(learned_store.get("labels", {}).get("Detractor", {}).keys())[:40]
        st.markdown("<div class='chip-wrap'>" + "".join([f"<span class='chip red'>{html.escape(x)}</span>" for x in d2]) + "</div>", unsafe_allow_html=True)

# ------------------- Review Filters (subset selection) -------------------
st.subheader("🔎 Review Filters (optional)")

# Resolve columns best-effort
c_source  = _find_col(work, ["Source"])
c_model   = _find_col(work, ["Model (SKU)", "Model", "SKU"])
c_seeded  = _find_col(work, ["Seeded"])
c_country = _find_col(work, ["Country", "Region"])
c_newrev  = _find_col(work, ["New Review", "New"])
c_rdate   = _find_col(work, ["Review Date"])
c_rating  = _find_col(work, ["Star Rating", "star rating", "Rating"])

_coerce_datetime_inplace(work, c_rdate)
_coerce_numeric_inplace(work, c_rating)

with st.expander("Choose a subset to symptomize (Source / SKU / Seeded / Country / New Review / Date / Rating)", expanded=False):
    row1 = st.columns(3)
    with row1[0]:
        if c_source:
            st.multiselect("Source", options=_unique_sorted_str(work[c_source]), key="f_source_sel")
        else:
            st.caption("Source: (column not found)")
    with row1[1]:
        if c_model:
            st.multiselect("Model (SKU)", options=_unique_sorted_str(work[c_model]), key="f_model_sel")
        else:
            st.caption("Model (SKU): (column not found)")
    with row1[2]:
        if c_country:
            st.multiselect("Country", options=_unique_sorted_str(work[c_country]), key="f_country_sel")
        else:
            st.caption("Country: (column not found)")

    row2 = st.columns(3)
    with row2[0]:
        if c_seeded:
            st.selectbox("Seeded", ["All", "Seeded only", "Non-seeded only"], index=0, key="f_seeded_mode")
        else:
            st.caption("Seeded: (column not found)")
    with row2[1]:
        if c_newrev:
            st.selectbox("New Review", ["All", "New only", "Not new only"], index=0, key="f_new_mode")
        else:
            st.caption("New Review: (column not found)")
    with row2[2]:
        if c_rating:
            vals = work[c_rating].dropna().tolist()
            opts = sorted({int(v) if isinstance(v, (int, float)) and float(v).is_integer() else v for v in vals})
            if opts:
                st.multiselect("Star Rating", options=opts, default=opts, key="f_rating_sel")
            else:
                st.caption("Star Rating: (no values)")
        else:
            st.caption("Star Rating: (column not found)")

    if c_rdate and work[c_rdate].notna().any():
        try:
            dmin = pd.to_datetime(work[c_rdate].min()).date()
            dmax = pd.to_datetime(work[c_rdate].max()).date()
            st.date_input("Review Date range", value=(dmin, dmax), key="f_date_range")
        except Exception:
            st.caption("Review Date: (could not parse dates)")

def _apply_filters_to_work(work_df: pd.DataFrame) -> pd.DataFrame:
    out = work_df

    c_source  = _find_col(out, ["Source"])
    c_model   = _find_col(out, ["Model (SKU)", "Model", "SKU"])
    c_seeded  = _find_col(out, ["Seeded"])
    c_country = _find_col(out, ["Country", "Region"])
    c_newrev  = _find_col(out, ["New Review", "New"])
    c_rdate   = _find_col(out, ["Review Date"])
    c_rating  = _find_col(out, ["Star Rating", "star rating", "Rating"])

    src_sel = st.session_state.get("f_source_sel", []) or []
    if c_source and src_sel:
        out = out[out[c_source].astype(str).isin([str(x) for x in src_sel])]

    model_sel = st.session_state.get("f_model_sel", []) or []
    if c_model and model_sel:
        out = out[out[c_model].astype(str).isin([str(x) for x in model_sel])]

    country_sel = st.session_state.get("f_country_sel", []) or []
    if c_country and country_sel:
        out = out[out[c_country].astype(str).isin([str(x) for x in country_sel])]

    seeded_mode = str(st.session_state.get("f_seeded_mode", "All"))
    if c_seeded and seeded_mode != "All":
        b = out[c_seeded].map(_boolish)
        if seeded_mode == "Seeded only":
            out = out[b == True]
        elif seeded_mode == "Non-seeded only":
            out = out[b == False]

    new_mode = str(st.session_state.get("f_new_mode", "All"))
    if c_newrev and new_mode != "All":
        b = out[c_newrev].map(_boolish)
        if new_mode == "New only":
            out = out[b == True]
        elif new_mode == "Not new only":
            out = out[b == False]

    date_range = st.session_state.get("f_date_range", None)
    if c_rdate and date_range and isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        try:
            _coerce_datetime_inplace(out, c_rdate)
            start = pd.Timestamp(date_range[0])
            end = pd.Timestamp(date_range[1]) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
            out = out[(out[c_rdate] >= start) & (out[c_rdate] <= end)]
        except Exception:
            pass

    rating_sel = st.session_state.get("f_rating_sel", None)
    if c_rating and rating_sel is not None and len(rating_sel) > 0:
        try:
            _coerce_numeric_inplace(out, c_rating)
            out = out[out[c_rating].isin(list(rating_sel))]
        except Exception:
            out = out[out[c_rating].astype(str).isin([str(x) for x in rating_sel])]

    return out

work_filtered = _apply_filters_to_work(work)
st.caption(f"Filters active: **{len(work_filtered):,} / {len(work):,}** reviews eligible.")

# ------------------- Scope & Preview -------------------
st.subheader("🧪 Symptomize")
scope = st.selectbox(
    "Choose scope",
    ["Missing both", "Any missing", "Missing delighters only", "Missing detractors only"],
    index=0,
)

base = work_filtered

if scope == "Missing both":
    target = base[(base["Needs_Delighters"]) & (base["Needs_Detractors"])]
elif scope == "Missing delighters only":
    target = base[(base["Needs_Delighters"]) & (~base["Needs_Detractors"])]
elif scope == "Missing detractors only":
    target = base[(~base["Needs_Delighters"]) & (base["Needs_Detractors"])]
else:
    target = base[(base["Needs_Delighters"]) | (base["Needs_Detractors"])]

st.write(f"🔎 **{len(target):,} reviews** match the selected scope (and filters).")
with st.expander("Preview in-scope rows", expanded=False):
    preview_cols = ["Verbatim", "Has_Delighters", "Has_Detractors", "Needs_Delighters", "Needs_Detractors"]
    extras = [c for c in ["Star Rating", "Review Date", "Source"] if c in target.columns]
    st.dataframe(target[preview_cols + extras].head(200), use_container_width=True)

# ------------------- Controls (SESSION-PERSISTED) -------------------
st.session_state.setdefault("processed_rows", [])
st.session_state.setdefault("processed_idx_set", set())
st.session_state.setdefault("new_symptom_candidates", {})
st.session_state.setdefault("alias_suggestion_candidates", {})
st.session_state.setdefault("undo_stack", [])
st.session_state.setdefault("last_run_processed_count", 0)
st.session_state.setdefault("ev_cov_num", 0)
st.session_state.setdefault("ev_cov_den", 0)

processed_rows: List[Dict[str, Any]] = st.session_state["processed_rows"]
processed_idx_set: Set[int] = st.session_state["processed_idx_set"]

new_symptom_candidates: Dict[Tuple[str, str], Dict[str, Any]] = st.session_state["new_symptom_candidates"]
alias_suggestion_candidates: Dict[Tuple[str, str, str], Dict[str, Any]] = st.session_state["alias_suggestion_candidates"]

def _agg_candidate(d: Dict, key: Tuple, idx: int, max_refs: int = 50):
    rec = d.setdefault(key, {"count": 0, "refs": []})
    rec["count"] = int(rec.get("count", 0)) + 1
    refs = rec.get("refs", [])
    if isinstance(refs, list) and len(refs) < max_refs:
        refs.append(int(idx))
        rec["refs"] = refs

# Row 1: actions
r1a, r1b, r1c, r1d, r1e = st.columns([1.4, 1.4, 1.8, 1.8, 1.2])
with r1a: run_n_btn = st.button("▶️ Symptomize N", use_container_width=True)
with r1b: run_all_btn = st.button("🚀 Symptomize All (current scope)", use_container_width=True)
with r1c: overwrite_btn = st.button("🧹 Overwrite & Symptomize ALL (start at row 1)", use_container_width=True)
with r1d: run_missing_both_btn = st.button("✨ Missing-Both One-Click", use_container_width=True)
with r1e: undo_btn = st.button("↩️ Undo last run", use_container_width=True)

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

# Row 2: batch size + presets
st.markdown("<div class='batch-row'></div>", unsafe_allow_html=True)
cA, cB, cC, cD, cE = st.columns([1.0, 0.5, 0.5, 0.5, 0.5])

with cA:
    bound_min = 1
    bound_max = max(1, len(target))
    if "n_to_process" not in st.session_state:
        st.session_state["n_to_process"] = min(10, bound_max)
    cur = int(st.session_state.get("n_to_process", 1))
    if cur < bound_min:
        st.session_state["n_to_process"] = bound_min
    elif cur > bound_max:
        st.session_state["n_to_process"] = bound_max

    n_to_process = st.number_input(
        "How many to symptomize (from top of scope)",
        min_value=bound_min,
        max_value=bound_max,
        step=1,
        key="n_to_process",
    )

def _set_n(v: int):
    st.session_state["n_to_process"] = min(max(int(v), 1), max(1, len(target)))
    st.rerun()

with cB: st.button("10",  use_container_width=True, on_click=_set_n, args=(10,))
with cC: st.button("25",  use_container_width=True, on_click=_set_n, args=(25,))
with cD: st.button("50",  use_container_width=True, on_click=_set_n, args=(50,))
with cE: st.button("100", use_container_width=True, on_click=_set_n, args=(100,))

# ------------------- Runner helpers -------------------
def _active_allowed_lists() -> Tuple[List[str], List[str]]:
    dels = list(DELIGHTERS)
    dets = list(DETRACTORS)

    if use_learned_as_allowed:
        learned_dels = _known_learned_labels("Delighter")[:60]
        learned_dets = _known_learned_labels("Detractor")[:60]
        for x in learned_dels:
            if x not in dels:
                dels.append(x)
        for x in learned_dets:
            if x not in dets:
                dets.append(x)
    return dels, dets

# ------------------- FAST batched runner -------------------
def _run_symptomize(rows_df: pd.DataFrame, overwrite_mode: bool = False):
    global df, new_symptom_candidates, alias_suggestion_candidates

    st.session_state["processed_rows"] = []
    st.session_state["processed_idx_set"] = set()
    st.session_state["new_symptom_candidates"] = {}
    st.session_state["alias_suggestion_candidates"] = {}
    st.session_state["last_run_processed_count"] = 0
    st.session_state["ev_cov_num"] = 0
    st.session_state["ev_cov_den"] = 0
    st.session_state.pop("export_bytes", None)

    processed_rows_local: List[Dict[str, Any]] = st.session_state["processed_rows"]
    processed_idx_set_local: Set[int] = st.session_state["processed_idx_set"]
    new_symptom_candidates = st.session_state["new_symptom_candidates"]
    alias_suggestion_candidates = st.session_state["alias_suggestion_candidates"]

    prog = st.progress(0.0)
    eta_box = st.empty()
    status_box = st.empty()

    # Auto prelearn
    if prelearn_enabled and client is not None:
        ls = _ensure_learned_store()
        if not (ls.get("labels", {}).get("Delighter") or ls.get("labels", {}).get("Detractor")):
            status_box.markdown("🧠 Auto-running Prelearn (recommended)…")
            pre_box = st.empty()
            pre_prog = st.progress(0.0)
            run_prelearn(
                df_in=df,
                client=client,
                prelearn_model=prelearn_model,
                temperature=0.0,
                embed_model=embed_model,
                sample_n=int(prelearn_sample_n),
                batch_size=int(prelearn_batch_size),
                sem_merge_threshold=float(prelearn_merge_threshold),
                status_box=pre_box,
                prog_bar=pre_prog,
            )
            pre_box.empty()
            pre_prog.empty()

    df = ensure_ai_columns(df)

    snapshot: List[Tuple[int, Dict[str, Optional[str]]]] = []
    do_undo = bool(st.session_state.get("enable_undo", True))

    if overwrite_mode:
        idxs = rows_df.index.tolist()
        for idx_clear in idxs:
            if do_undo:
                old_vals = {f"AI Symptom Detractor {j}": df.loc[idx_clear, f"AI Symptom Detractor {j}"] for j in range(1, 11)}
                old_vals.update({f"AI Symptom Delighter {j}": df.loc[idx_clear, f"AI Symptom Delighter {j}"] for j in range(1, 11)})
                old_vals.update({
                    "AI Safety": df.loc[idx_clear, "AI Safety"],
                    "AI Reliability": df.loc[idx_clear, "AI Reliability"],
                    "AI # of Sessions": df.loc[idx_clear, "AI # of Sessions"],
                })
                snapshot.append((int(idx_clear), old_vals))
            for j in range(1, 11):
                df.loc[idx_clear, f"AI Symptom Detractor {j}"] = None
                df.loc[idx_clear, f"AI Symptom Delighter {j}"] = None
            df.loc[idx_clear, "AI Safety"] = None
            df.loc[idx_clear, "AI Reliability"] = None
            df.loc[idx_clear, "AI # of Sessions"] = None

    total_n = max(1, len(rows_df))
    t0 = time.perf_counter()
    cost_start = float(_ensure_usage_tracker()["cost_chat"] + _ensure_usage_tracker()["cost_embed"])
    cov_num = 0
    cov_den = 0
    ui_keep = int(st.session_state.get("ui_log_limit", 40))

    allowed_dels, allowed_dets = _active_allowed_lists()
    known_hints = {
        "Delighter": _known_learned_labels("Delighter")[:60],
        "Detractor": _known_learned_labels("Detractor")[:60],
    }
    product_profile = str(_ensure_learned_store().get("product_profile", "") or "").strip()

    batch_size = int(st.session_state.get("llm_batch_size", 6) or 1)
    batch_budget = int(st.session_state.get("batch_token_budget", 35000) or 35000)

    try:
        overhead_est = _estimate_tokens(
            json.dumps(
                {
                    "allowed_delighters": allowed_dels,
                    "allowed_detractors": allowed_dets,
                    "known": known_hints,
                    "profile": product_profile[:600],
                }
            ),
            model_id=selected_model,
        ) + 800
    except Exception:
        overhead_est = 2000

    rows_list = list(rows_df.iterrows())
    batches: List[List[Tuple[int, pd.Series]]] = []
    cur: List[Tuple[int, pd.Series]] = []
    cur_tok = 0

    for idx, row in rows_list:
        vb = str(row.get("Verbatim", "") or "")
        try:
            t_est = _estimate_tokens(vb, model_id=selected_model)
        except Exception:
            t_est = max(1, len(vb) // 4)

        if cur and ((len(cur) >= batch_size) or (overhead_est + cur_tok + t_est > batch_budget)):
            batches.append(cur)
            cur = []
            cur_tok = 0

        cur.append((int(idx), row))
        cur_tok += int(t_est)

        if overhead_est + cur_tok > batch_budget and len(cur) == 1:
            batches.append(cur)
            cur = []
            cur_tok = 0

    if cur:
        batches.append(cur)

    done = 0

    for bi, batch_rows in enumerate(batches, start=1):
        items = []
        for idx, row in batch_rows:
            vb = str(row.get("Verbatim", "") or "")
            needs_del = bool(row.get("Needs_Delighters", False))
            needs_det = bool(row.get("Needs_Detractors", False))
            items.append({"idx": int(idx), "review": vb, "needs_del": needs_del, "needs_det": needs_det})

            if not overwrite_mode and do_undo:
                old_vals = {f"AI Symptom Detractor {j}": df.loc[idx, f"AI Symptom Detractor {j}"] for j in range(1, 11)}
                old_vals.update({f"AI Symptom Delighter {j}": df.loc[idx, f"AI Symptom Delighter {j}"] for j in range(1, 11)})
                old_vals.update({
                    "AI Safety": df.loc[idx, "AI Safety"],
                    "AI Reliability": df.loc[idx, "AI Reliability"],
                    "AI # of Sessions": df.loc[idx, "AI # of Sessions"],
                })
                snapshot.append((int(idx), old_vals))

        status_box.markdown(f"🔄 **Batch {bi}/{len(batches)}** • labeling + meta…")

        outs_by_idx = {}
        if client:
            outs_by_idx = _openai_labeler_unified_batch(
                items=items,
                client=client,
                model=selected_model,
                temperature=temperature,
                allowed_delighters=allowed_dels,
                allowed_detractors=allowed_dets,
                known_theme_hints=known_hints,
                max_ev_per_label=max_ev_per_label,
                max_ev_chars=max_ev_chars,
                product_profile=product_profile,
            )

        for it in items:
            idx = int(it["idx"])
            vb = str(it["review"] or "")
            needs_deli = bool(it["needs_del"])
            needs_detr = bool(it["needs_det"])

            out = outs_by_idx.get(idx, dict(_LABELER_DEFAULT))

            dels = out.get("dels", []) or []
            dets = out.get("dets", []) or []
            unl_dels = out.get("unl_dels", []) or []
            unl_dets = out.get("unl_dets", []) or []
            ev_del_map = out.get("ev_del_map", {}) or {}
            ev_det_map = out.get("ev_det_map", {}) or {}
            safety = out.get("safety", "Not Mentioned")
            reliability = out.get("reliability", "Not Mentioned")
            sessions = out.get("sessions", "Unknown")

            wrote_dets, wrote_dels = [], []
            ev_written_det: Dict[str, List[str]] = {}
            ev_written_del: Dict[str, List[str]] = {}

            def _label_allowed(label: str, side: str) -> bool:
                if not require_evidence:
                    return True
                evs = (ev_det_map if side == "det" else ev_del_map).get(label, [])
                return len(evs) > 0

            if needs_detr and dets:
                dets_to_write = [lab for lab in dets if _label_allowed(lab, "det")][:10]
                for j, lab in enumerate(dets_to_write):
                    df.loc[idx, f"AI Symptom Detractor {j+1}"] = lab
                    ev_written_det[lab] = ev_det_map.get(lab, [])
                wrote_dets = dets_to_write

            if needs_deli and dels:
                dels_to_write = [lab for lab in dels if _label_allowed(lab, "del")][:10]
                for j, lab in enumerate(dels_to_write):
                    df.loc[idx, f"AI Symptom Delighter {j+1}"] = lab
                    ev_written_del[lab] = ev_del_map.get(lab, [])
                wrote_dels = dels_to_write

            df.loc[idx, "AI Safety"] = safety
            df.loc[idx, "AI Reliability"] = reliability
            df.loc[idx, "AI # of Sessions"] = sessions

            learned = _ensure_learned_store()
            new_unl_dels: List[str] = []
            new_unl_dets: List[str] = []
            alias_sugs_for_row: List[Tuple[str, str, str, float]] = []

            def _handle_unlisted_list(items2: List[str], side_label: str):
                nonlocal new_unl_dels, new_unl_dets, alias_sugs_for_row
                for raw in items2 or []:
                    raw2 = str(raw).strip()
                    if not raw2:
                        continue
                    res = resolve_candidate_to_canonical(
                        candidate_raw=raw2,
                        side=side_label,
                        delighters=DELIGHTERS,
                        detractors=DETRACTORS,
                        alias_to_label=ALIAS_TO_LABEL,
                        learned_store=learned,
                        sim_threshold_lex=float(sim_threshold_lex),
                        sim_threshold_sem=float(sim_threshold_sem),
                        client=client if client is not None else None,
                        embed_model=embed_model,
                    )
                    canon = str(res["canonical"]).strip()
                    kind = res.get("kind", "new")
                    tgt = str(res.get("target", "") or "").strip()
                    score = float(res.get("score", 0.0) or 0.0)

                    _update_learned(side_label, canon, synonym=raw2)

                    if kind in {"exact_existing", "alias_to_existing"} and tgt:
                        alias_sugs_for_row.append((tgt, raw2, side_label, score))
                    elif kind == "synonym_to_learned":
                        pass
                    else:
                        if side_label.lower().startswith("del"):
                            new_unl_dels.append(canon)
                        else:
                            new_unl_dets.append(canon)

            _handle_unlisted_list(unl_dels, "Delighter")
            _handle_unlisted_list(unl_dets, "Detractor")

            def _dedupe_keep_order(lst: List[str]) -> List[str]:
                out2, seen = [], set()
                for x in lst:
                    k2 = _canon_simple(x)
                    if not x or k2 in seen:
                        continue
                    seen.add(k2); out2.append(x)
                return out2

            new_unl_dels = _dedupe_keep_order([normalize_theme_label(x, "Delighter") for x in new_unl_dels])
            new_unl_dets = _dedupe_keep_order([normalize_theme_label(x, "Detractor") for x in new_unl_dets])

            for lab in new_unl_dels:
                _agg_candidate(new_symptom_candidates, (lab, "Delighter"), int(idx))
            for lab in new_unl_dets:
                _agg_candidate(new_symptom_candidates, (lab, "Detractor"), int(idx))
            for tgt, alias, side_label, score in alias_sugs_for_row:
                side_norm = "Delighter" if side_label.lower().startswith("del") else "Detractor"
                _agg_candidate(alias_suggestion_candidates, (tgt, alias, side_norm), int(idx))

            total_labels = len(wrote_dets) + len(wrote_dels)
            labels_with_ev = sum(1 for lab in wrote_dets if len(ev_written_det.get(lab, [])) > 0) + \
                             sum(1 for lab in wrote_dels if len(ev_written_del.get(lab, [])) > 0)
            cov_num += labels_with_ev
            cov_den += total_labels
            row_ev_cov = (labels_with_ev / total_labels) if total_labels else 0.0

            if ui_keep > 0:
                processed_rows_local.append({
                    "Index": int(idx),
                    "Verbatim": str(vb)[:4000],
                    "Added_Detractors": wrote_dets,
                    "Added_Delighters": wrote_dels,
                    "Evidence_Detractors": ev_written_det,
                    "Evidence_Delighters": ev_written_del,
                    "NewCand_Detractors": new_unl_dets,
                    "NewCand_Delighters": new_unl_dels,
                    "AliasSuggestions": alias_sugs_for_row,
                    ">10 Detractors Detected": len(dets) > 10,
                    ">10 Delighters Detected": len(dels) > 10,
                    "Safety": safety,
                    "Reliability": reliability,
                    "Sessions": sessions,
                    "Evidence_Coverage": row_ev_cov,
                })
                if len(processed_rows_local) > ui_keep:
                    del processed_rows_local[:len(processed_rows_local) - ui_keep]

            processed_idx_set_local.add(int(idx))
            done += 1

        prog.progress(done / total_n)

        elapsed = time.perf_counter() - t0
        rate = (done / elapsed) if elapsed > 0 else 0.0
        rem = total_n - done
        eta_sec = (rem / rate) if rate > 0 else 0.0

        tr2 = _ensure_usage_tracker()
        spent = float(tr2["cost_chat"] + tr2["cost_embed"]) - cost_start
        avg_per = (spent / done) if done else 0.0
        est_total = avg_per * total_n

        eta_box.markdown(
            f"**Progress:** {done}/{total_n} • **ETA:** ~ {_fmt_secs(eta_sec)} • **Speed:** {rate*60:.1f} rev/min • "
            f"**Spend:** {_fmt_money(spent)} • **Est total:** {_fmt_money(est_total)}"
        )

        try:
            budget = float(st.session_state.get("budget_limit", 0.0) or 0.0)
        except Exception:
            budget = 0.0
        session_total = float(tr2["cost_chat"] + tr2["cost_embed"])
        if budget > 0 and session_total >= budget:
            status_box.markdown(
                f"⛔ **Budget guard:** session spend {_fmt_money(session_total)} exceeded limit {_fmt_money(budget)}. Stopping early."
            )
            break

        if done % 50 == 0:
            gc.collect()

    status_box.markdown("✅ Done.")

    if do_undo and snapshot:
        st.session_state["undo_stack"].append({"rows": snapshot})

    st.session_state["processed_rows"] = processed_rows_local
    st.session_state["processed_idx_set"] = processed_idx_set_local
    st.session_state["new_symptom_candidates"] = new_symptom_candidates
    st.session_state["alias_suggestion_candidates"] = alias_suggestion_candidates
    st.session_state["last_run_processed_count"] = len(processed_idx_set_local)
    st.session_state["ev_cov_num"] = int(cov_num)
    st.session_state["ev_cov_den"] = int(cov_den)

    st.session_state["df_work"] = df
    st.session_state.pop("export_bytes", None)

# ------------------- Execute by buttons -------------------
if client is not None and (run_n_btn or run_all_btn or overwrite_btn or run_missing_both_btn):
    if run_missing_both_btn:
        rows_iter = work_filtered[(work_filtered["Needs_Delighters"]) & (work_filtered["Needs_Detractors"])].sort_index()
        _run_symptomize(rows_iter, overwrite_mode=False)

    elif overwrite_btn:
        df = clear_all_ai_slots_in_df(df)
        st.session_state["df_work"] = df
        colmap = detect_symptom_columns(df)
        work = detect_missing(df, colmap)
        work = _apply_filters_to_work(work)   # ✅ respect filters
        rows_iter = work.sort_index()
        _run_symptomize(rows_iter, overwrite_mode=False)

    else:
        if run_all_btn:
            rows_iter = target.sort_index()
        else:
            rows_iter = target.sort_index().head(int(st.session_state.get("n_to_process", 10)))
        _run_symptomize(rows_iter, overwrite_mode=False)

    st.success(f"Symptomized {int(st.session_state.get('last_run_processed_count', 0))} review(s).")

# Undo last run
def _undo_last_run():
    global df
    if not st.session_state.get("undo_stack"):
        st.info("Nothing to undo.")
        return
    snap = st.session_state["undo_stack"].pop()
    for idx, old_vals in snap.get("rows", []):
        for col, val in old_vals.items():
            if col not in df.columns:
                df[col] = None
            df.loc[idx, col] = val
    st.session_state["df_work"] = df
    st.session_state.pop("export_bytes", None)
    st.success("Reverted last run.")

if undo_btn:
    _undo_last_run()

# ------------------- Processed Reviews (chips + highlighted evidence) -------------------
processed_rows = st.session_state.get("processed_rows", []) or []
if processed_rows and int(st.session_state.get("ui_log_limit", 40)) > 0:
    st.subheader("🧾 Processed Reviews (this run — UI-capped)")
    cov_num = int(st.session_state.get("ev_cov_num", 0) or 0)
    cov_den = int(st.session_state.get("ev_cov_den", 0) or 0)
    overall_cov = (cov_num / cov_den) if cov_den else 0.0
    st.caption(f"**Evidence Coverage (this run):** {overall_cov*100:.1f}% of written labels include ≥1 snippet.")

    def _esc(s: str) -> str:
        return (str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))

    for rec in processed_rows:
        head = f"Row {rec['Index']} — Dets: {len(rec['Added_Detractors'])} • Dels: {len(rec['Added_Delighters'])}"
        if rec[">10 Detractors Detected"] or rec[">10 Delighters Detected"]:
            head += " • ⚠︎ >10 detected (trimmed to 10)"
        with st.expander(head):
            evidence_terms: List[str] = []
            for _, evs in (rec.get("Evidence_Detractors", {}) or {}).items():
                evidence_terms.extend(evs or [])
            for _, evs in (rec.get("Evidence_Delighters", {}) or {}).items():
                evidence_terms.extend(evs or [])

            st.markdown("**Verbatim (evidence highlighted)**")
            st.markdown(highlight_text(rec["Verbatim"], evidence_terms), unsafe_allow_html=True)

            meta_html = (
                "<div class='chips-block chip-wrap'>"
                f"<span class='chip yellow'>Safety: {_esc(rec.get('Safety','Not Mentioned'))}</span>"
                f"<span class='chip blue'>Reliability: {_esc(rec.get('Reliability','Not Mentioned'))}</span>"
                f"<span class='chip purple'># Sessions: {_esc(rec.get('Sessions','Unknown'))}</span>"
                "</div>"
            )
            st.markdown(meta_html, unsafe_allow_html=True)

            st.markdown("**Detractors added**")
            det_html = "<div class='chips-block chip-wrap'>"
            for lab in rec["Added_Detractors"]:
                k = len((rec.get("Evidence_Detractors", {}) or {}).get(lab, []))
                det_html += f"<span class='chip red'>{html.escape(lab)} · Evidence: {k}</span>"
            det_html += "</div>"
            st.markdown(det_html, unsafe_allow_html=True)

            st.markdown("**Delighters added**")
            del_html = "<div class='chips-block chip-wrap'>"
            for lab in rec["Added_Delighters"]:
                k = len((rec.get("Evidence_Delighters", {}) or {}).get(lab, []))
                del_html += f"<span class='chip green'>{html.escape(lab)} · Evidence: {k}</span>"
            del_html += "</div>"
            st.markdown(del_html, unsafe_allow_html=True)

            if rec.get("NewCand_Delighters") or rec.get("NewCand_Detractors"):
                st.markdown("**New symptom candidates (deduped & canonicalized)**")
                chips = "<div class='chip-wrap'>"
                for x in rec.get("NewCand_Delighters", []) or []:
                    chips += f"<span class='chip green'>{html.escape(x)}</span>"
                for x in rec.get("NewCand_Detractors", []) or []:
                    chips += f"<span class='chip red'>{html.escape(x)}</span>"
                chips += "</div>"
                st.markdown(chips, unsafe_allow_html=True)

            if rec.get("AliasSuggestions"):
                st.markdown("**Alias suggestions (routes near-duplicates to existing labels)**")
                chips = "<div class='chip-wrap'>"
                for tgt, alias, side, score in rec["AliasSuggestions"]:
                    chips += f"<span class='chip yellow'>{html.escape(alias)} → {html.escape(tgt)}</span>"
                chips += "</div>"
                st.markdown(chips, unsafe_allow_html=True)

            with st.expander("See evidence snippets", expanded=False):
                if rec.get("Evidence_Detractors"):
                    st.markdown("**Detractor evidence**")
                    for lab, evs in rec["Evidence_Detractors"].items():
                        for e in evs:
                            st.write(f"- {e}")
                if rec.get("Evidence_Delighters"):
                    st.markdown("**Delighter evidence**")
                    for lab, evs in rec["Evidence_Delighters"].items():
                        for e in evs:
                            st.write(f"- {e}")
elif int(st.session_state.get("ui_log_limit", 40)) == 0:
    st.info("UI log is disabled (Show last N processed reviews in UI = 0). This is the fastest mode for very large runs.")

# ------------------- 🟡 Inbox: New Symptoms + Alias Suggestions -------------------
whitelist_all = set(DELIGHTERS + DETRACTORS)
alias_all = set([a for lst in ALIASES.values() for a in lst]) if ALIASES else set()
wl_canon = {_canon_simple(x) for x in whitelist_all}
ali_canon = {_canon_simple(x) for x in alias_all}

def _is_existing_label_or_alias(s: str) -> bool:
    k = _canon_simple(s)
    return (k in wl_canon) or (k in ali_canon)

def _filter_new_symptom_candidates(
    cands: Dict[Tuple[str, str], Dict[str, Any]]
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for (lab, side), rec in cands.items():
        lab2 = normalize_theme_label(lab, side)
        if not lab2:
            continue
        if _is_existing_label_or_alias(lab2):
            continue
        out[(lab2, side)] = {"count": int(rec.get("count", 0)), "refs": list(rec.get("refs", []))}

    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    seen: Dict[Tuple[str, str], str] = {}
    for (lab, side), rec in out.items():
        key = _canon_simple(lab)
        k2 = (key, side)
        if k2 in seen:
            prev_lab = seen[k2]
            mrec = merged.setdefault((prev_lab, side), {"count": 0, "refs": []})
            mrec["count"] += int(rec.get("count", 0))
            for ridx in rec.get("refs", [])[:50]:
                if len(mrec["refs"]) < 50 and ridx not in mrec["refs"]:
                    mrec["refs"].append(ridx)
        else:
            merged[(lab, side)] = {"count": int(rec.get("count", 0)), "refs": list(rec.get("refs", []))}
            seen[k2] = lab

    final: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for (lab, side), rec in merged.items():
        try:
            m = difflib.get_close_matches(lab, list(whitelist_all), n=1, cutoff=float(sim_threshold_lex))
            if m:
                continue
        except Exception:
            pass
        final[(lab, side)] = rec
    return final

def _filter_alias_candidates(
    cands: Dict[Tuple[str, str, str], Dict[str, Any]]
) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for (tgt, alias, side), rec in cands.items():
        tgt2 = str(tgt).strip()
        alias2 = normalize_theme_label(alias, side, singularize=True)
        if not tgt2 or not alias2:
            continue
        if _canon_simple(alias2) == _canon_simple(tgt2):
            continue
        if _is_existing_label_or_alias(alias2):
            continue
        out[(tgt2, alias2, side)] = {"count": int(rec.get("count", 0)), "refs": list(rec.get("refs", []))}

    merged: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    seen: Set[Tuple[str, str, str]] = set()
    for (tgt, alias, side), rec in out.items():
        key = (tgt, _canon_simple(alias), side)
        if key in seen:
            for kk in list(merged.keys()):
                if kk[0] == tgt and _canon_simple(kk[1]) == _canon_simple(alias) and kk[2] == side:
                    merged[kk]["count"] += int(rec.get("count", 0))
                    for ridx in rec.get("refs", [])[:50]:
                        if len(merged[kk]["refs"]) < 50 and ridx not in merged[kk]["refs"]:
                            merged[kk]["refs"].append(ridx)
                    break
        else:
            merged[(tgt, alias, side)] = {"count": int(rec.get("count", 0)), "refs": list(rec.get("refs", []))}
            seen.add(key)
    return merged

new_symptom_candidates_f = _filter_new_symptom_candidates(st.session_state.get("new_symptom_candidates", {}) or {})
alias_suggestion_candidates_f = _filter_alias_candidates(st.session_state.get("alias_suggestion_candidates", {}) or {})

if new_symptom_candidates_f or alias_suggestion_candidates_f:
    st.subheader("🟡 Inbox: New Symptoms + Alias Suggestions")
    tabs_inbox = st.tabs(["New Symptoms", "Alias Suggestions"])

    def _mk_examples(refs: List[int], n: int = 3) -> str:
        ex = []
        for ridx in refs[:n]:
            try:
                ex.append(str(df.loc[ridx, "Verbatim"])[:200])
            except Exception:
                pass
        return " | ".join(["— " + e for e in ex])

    with tabs_inbox[0]:
        st.markdown("**New symptom candidates** (deduped, canonical, excludes existing Symptoms + existing Aliases)")
        rows_tbl = []
        for (lab, side), rec in sorted(new_symptom_candidates_f.items(), key=lambda kv: (-int(kv[1].get("count", 0)), kv[0][0])):
            rows_tbl.append({
                "Add": False,
                "Label": lab,
                "Side": side,
                "Count": int(rec.get("count", 0)),
                "Examples": _mk_examples(list(rec.get("refs", []))),
            })
        tbl_new = pd.DataFrame(rows_tbl) if rows_tbl else pd.DataFrame(columns=["Add","Label","Side","Count","Examples"])
        editor_new = st.data_editor(
            tbl_new,
            num_rows="fixed",
            use_container_width=True,
            column_config={
                "Add": st.column_config.CheckboxColumn(help="Check to add as a NEW symptom"),
                "Label": st.column_config.TextColumn(),
                "Side": st.column_config.SelectboxColumn(options=["Delighter","Detractor"]),
                "Count": st.column_config.NumberColumn(format="%d"),
                "Examples": st.column_config.TextColumn(width="large"),
            },
            key="inbox_new_editor",
        )

    with tabs_inbox[1]:
        st.markdown("**Alias suggestions** (routes near-duplicate phrasing to an existing symptom label)")
        rows_tbl2 = []
        for (tgt, alias, side), rec in sorted(alias_suggestion_candidates_f.items(), key=lambda kv: (-int(kv[1].get("count", 0)), kv[0][0], kv[0][1])):
            rows_tbl2.append({
                "Add": False,
                "Target Symptom": tgt,
                "Alias": alias,
                "Side": side,
                "Count": int(rec.get("count", 0)),
                "Examples": _mk_examples(list(rec.get("refs", []))),
            })
        tbl_alias = pd.DataFrame(rows_tbl2) if rows_tbl2 else pd.DataFrame(columns=["Add","Target Symptom","Alias","Side","Count","Examples"])
        editor_alias = st.data_editor(
            tbl_alias,
            num_rows="fixed",
            use_container_width=True,
            column_config={
                "Add": st.column_config.CheckboxColumn(help="Check to add this Alias to the target symptom"),
                "Target Symptom": st.column_config.TextColumn(disabled=True),
                "Alias": st.column_config.TextColumn(),
                "Side": st.column_config.SelectboxColumn(options=["Delighter","Detractor"]),
                "Count": st.column_config.NumberColumn(format="%d"),
                "Examples": st.column_config.TextColumn(width="large"),
            },
            key="inbox_alias_editor",
        )

    with st.form("apply_inbox_updates_form", clear_on_submit=False):
        st.markdown("When you submit, we will update the **Symptoms** sheet and provide a download.")
        apply_btn = st.form_submit_button("✅ Apply selected updates to Symptoms & Download")

    if apply_btn:
        new_to_add: List[Tuple[str, str]] = []
        alias_to_add: List[Tuple[str, str]] = []

        try:
            if isinstance(editor_new, pd.DataFrame) and not editor_new.empty:
                for _, r_ in editor_new.iterrows():
                    if bool(r_.get("Add", False)) and str(r_.get("Label","")).strip():
                        lab = normalize_theme_label(str(r_["Label"]).strip(), str(r_.get("Side","Delighter")))
                        side = str(r_.get("Side","Delighter")).strip()
                        if lab and not _is_existing_label_or_alias(lab):
                            new_to_add.append((lab, side))
        except Exception:
            pass

        try:
            if isinstance(editor_alias, pd.DataFrame) and not editor_alias.empty:
                for _, r_ in editor_alias.iterrows():
                    if bool(r_.get("Add", False)) and str(r_.get("Alias","")).strip() and str(r_.get("Target Symptom","")).strip():
                        tgt = str(r_["Target Symptom"]).strip()
                        als = normalize_theme_label(str(r_["Alias"]).strip(), str(r_.get("Side","Detractor")))
                        if tgt and als and not _is_existing_label_or_alias(als) and _canon_simple(als) != _canon_simple(tgt):
                            alias_to_add.append((tgt, als))
        except Exception:
            pass

        if new_to_add or alias_to_add:
            updated_bytes = apply_symptoms_updates_to_workbook(
                st.session_state["uploaded_bytes"],
                new_symptoms=new_to_add,
                alias_additions=alias_to_add,
            )
            st.download_button(
                "⬇️ Download 'Symptoms' (updated)",
                data=updated_bytes,
                file_name="Symptoms_Updated.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            st.success(f"Applied {len(new_to_add)} new symptom(s) and {len(alias_to_add)} alias addition(s).")
        else:
            st.info("No updates selected.")

# ------------------- Download Symptomized Workbook (LAZY BUILD) -------------------
st.subheader("📦 Download Symptomized Workbook")
try:
    file_base = os.path.splitext(getattr(uploaded_file, 'name', 'Reviews'))[0]
except Exception:
    file_base = 'Reviews'

prep = st.button("🧾 Prepare XLSX export (build file now)", use_container_width=True)
if prep:
    with st.spinner("Building XLSX export..."):
        st.session_state["export_bytes"] = generate_template_workbook_bytes(
            st.session_state["uploaded_bytes"],
            df,
            processed_idx=st.session_state.get("processed_idx_set", set()) or None,
            overwrite_processed_slots=False,
        )
    st.success("Export prepared — click download below.")

export_bytes = st.session_state.get("export_bytes", None)
st.download_button(
    "⬇️ Download symptomized workbook (XLSX)",
    data=(export_bytes or b""),
    file_name=f"{file_base}_Symptomized.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    disabled=(export_bytes is None),
)

# ------------------- Symptoms Catalog quick export -------------------
st.subheader("🗂️ Download Symptoms Catalog")
sym_df = pd.DataFrame({
    "Symptom": (DELIGHTERS + DETRACTORS),
    "Type":    ["Delighter"]*len(DELIGHTERS) + ["Detractor"]*len(DETRACTORS)
})
if ALIASES:
    alias_rows = [{"Symptom": k, "Aliases": " | ".join(v)} for k, v in ALIASES.items()]
    alias_df = pd.DataFrame(alias_rows)
    sym_df = sym_df.merge(alias_df, how="left", on="Symptom")

sym_bytes = io.BytesIO()
with pd.ExcelWriter(sym_bytes, engine="openpyxl") as xw:
    sym_df.to_excel(xw, index=False, sheet_name="Symptoms")
sym_bytes.seek(0)
st.download_button(
    "⬇️ Download Symptoms Catalog (XLSX)",
    sym_bytes.getvalue(),
    file_name=f"{file_base}_Symptoms_Catalog.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# Footer
st.divider()
st.caption(
    "v7.7 (Fast+Stable) — Evidence-locked labeling + Product Knowledge Prelearn + canonical merging + "
    "🟡 Inbox: New Symptoms + Alias Suggestions + ⚡ Batch symptomization + 🔎 Subset filters + Optional throttle. "
    "Exports: K–T/U–AD, meta: AE/AF/AG. Stability: session persistence + vectorized detection + retries + lazy export."
)






