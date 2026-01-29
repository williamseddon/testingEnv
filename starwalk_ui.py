
# starwalk_ui_v7_5_knowledge_plus.py — Evidence-Locked Labeling + Product Knowledge Prelearn + Canonical Theme Merge
# Adds: Prelearn w/ ETA + real-time status, semantic merge, alias-suggestion inbox, stronger dedupe/singularization, cost tracking
# Requirements: streamlit>=1.28, pandas, openpyxl, openai (optional)
# Optional: numpy, scikit-learn (for better clustering), tiktoken (for better token counts)

import streamlit as st
import pandas as pd
import io, os, json, difflib, time, re, html, math, random
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
st.set_page_config(layout="wide", page_title="Review Symptomizer — v7.5")
st.title("✨ Review Symptomizer — v7.5")
st.caption(
    "Exact export (K–T dets, U–AD dels) • ETA + presets + overwrite • Undo • "
    "Product Knowledge Prelearn (recommended) • Strong canonical merging • "
    "Similarity/semantic guard • Evidence-locked labeling • In-session cache • "
    "🟡 Inbox: New Symptoms + Alias Suggestions"
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
    if sec < 0: sec = 0
    m = int(sec // 60)
    s = int(round(sec - m * 60))
    return f"{m}:{s:02d}"

# ------------------- Pricing & Cost Tracking -------------------
# Prices per 1M tokens (text). Source-of-truth should be OpenAI model pages; update as needed.
# Defaults (as of Jan 2026) pulled from OpenAI model pages.
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
    # Allow runtime overrides (pricing can change over time)
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
            "by_component": {},  # component -> dict tokens/cost
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
        # Sometimes responses nest usage differently
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
    # embeddings often only have prompt_tokens / total_tokens
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
    if not terms: return safe
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

def row_has_any(row: pd.Series, columns: List[str]) -> bool:
    if not columns: return False
    for c in columns:
        if c in row and is_filled(row[c]): return True
    return False

def detect_missing(df: pd.DataFrame, colmap: Dict[str, List[str]]) -> pd.DataFrame:
    det_cols = colmap["manual_detractors"] + colmap["ai_detractors"]
    del_cols = colmap["manual_delighters"] + colmap["ai_delighters"]
    out = df.copy()
    out["Has_Detractors"] = out.apply(lambda r: row_has_any(r, det_cols), axis=1)
    out["Has_Delighters"] = out.apply(lambda r: row_has_any(r, del_cols), axis=1)
    out["Needs_Detractors"] = ~out["Has_Detractors"]
    out["Needs_Delighters"] = ~out["Has_Delighters"]
    out["Needs_Symptomization"] = out["Needs_Detractors"] & out["Needs_Delighters"]
    return out

# ------------------- Fixed template mapping -------------------
DET_LETTERS = ["K","L","M","N","O","P","Q","R","S","T"]
DEL_LETTERS = ["U","V","W","X","Y","Z","AA","AB","AC","AD"]
DET_INDEXES = [column_index_from_string(c) for c in DET_LETTERS]
DEL_INDEXES = [column_index_from_string(c) for c in DEL_LETTERS]

# Optional meta columns after AD (headers only if blank)
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
    """
    Hard-reset ALL AI symptom and meta columns across the entire dataframe.
    Returns a new dataframe with cleared columns.
    """
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
        import hashlib
        return hashlib.md5(payload.encode("utf-8")).hexdigest()[:10]
    except Exception:
        return str(len(delighters)) + "_" + str(len(detractors))

def _ensure_label_cache():
    if "_label_cache" not in st.session_state:
        st.session_state["_label_cache"] = {}
    return st.session_state["_label_cache"]

# ------------------- Theme normalization (Stronger + plural/synonym handling) -------------------
# Goal: stop "Comfortable fit" vs "Comfortable design" vs "Comfortable use" and "issue/issues".
THEME_RULES = [
    # detractors (hair/heat/noise/battery from v7.3)
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

    # NEW detractor normalizers
    (re.compile(r"\b(cooling\s+pad)\b.*\b(issue|issues|problem|problems|fail|failed|broken)\b|\b(issue|issues|problem|problems)\b.*\b(cooling\s+pad)\b", re.I),
     {"det": "Cooling Pad Issue"}),
    (re.compile(r"\b(learning\s+curve|hard\s+to\s+learn|takes\s+time\s+to\s+learn|not\s+intuitive)\b", re.I),
     {"det": "Learning Curve"}),
    (re.compile(r"\b(initially|at\s+first)\b.*\b(complicated|confusing|hard)\b|\b(complicated|confusing|hard)\b.*\b(at\s+first|initially)\b", re.I),
     {"det": "Learning Curve"}),

    # delighters
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

    # NEW delighter normalizers
    (re.compile(r"\b(comfortable|comfort|comfy)\b", re.I),
     {"del": "Comfort"}),
]

# Extra canonical synonym map (deterministic). Helps unify variants that slip through.
CANONICAL_SYNONYMS = [
    # canonical, side, patterns (any hit -> canonical)
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
    # Title Case but keep slashes segments
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
    """Deterministically normalize candidate labels to reduce duplicates."""
    txt = str(raw or "").strip()
    if not txt:
        return ""

    # 1) Apply rules first (fast)
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

    # 2) Canonical synonyms (regex)
    low = txt.lower()
    for canon, side, pats in CANONICAL_SYNONYMS:
        if side_hint.lower().startswith("del") and side != "del":
            continue
        if side_hint.lower().startswith("det") and side != "det":
            continue
        for p in pats:
            if re.search(p, low, flags=re.I):
                return _singularize_last_word(canon) if singularize else canon

    # 3) Heuristic cleanup
    out = _short_title(txt[:60])
    if singularize:
        out = _singularize_last_word(out)

    # 4) Last-mile: unify trailing "Issue(s)" forms
    if out.endswith(" Issues"):
        out = out[:-7] + " Issue"
    return out.strip()

# ------------------- Learned Themes / Candidate Resolution -------------------
def _ensure_learned_store() -> Dict[str, Any]:
    """Session store for learned themes & embeddings, used to merge new symptom phrases as we go."""
    if "learned" not in st.session_state:
        st.session_state["learned"] = {
            "labels": {"Delighter": {}, "Detractor": {}},  # canonical -> {"synonyms": set(), "count": int}
            "emb": {"Delighter": {}, "Detractor": {}},     # canonical -> embedding vector
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
    # highest frequency first
    items.sort(key=lambda k: -int(ls["labels"][side].get(k, {}).get("count", 0)))
    return items

def _ensure_embed_cache():
    if "_embed_cache" not in st.session_state:
        st.session_state["_embed_cache"] = {}
    return st.session_state["_embed_cache"]

def _embed_text(text: str, client, model_id: str, component: str) -> Optional[List[float]]:
    """Embedding with in-session cache."""
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
        resp = client.embeddings.create(model=model_id, input=[t])
        # usage may show prompt_tokens
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
    # Manual cosine to avoid numpy dependency
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
    """Return (best_label, similarity) using embeddings."""
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
    """
    Canonicalize a candidate phrase.
    Returns dict:
      {
        "canonical": <label>,
        "kind": "new" | "alias_to_existing" | "synonym_to_learned" | "exact_existing",
        "target": <existing label if alias>,
        "score": float (semantic score if used else 0),
      }
    """
    side_norm = "Delighter" if str(side).lower().startswith("del") else "Detractor"
    allowed = delighters if side_norm == "Delighter" else detractors

    cand_theme = normalize_theme_label(candidate_raw, side_norm)
    cand_canon = _canon(cand_theme)
    cand_key = _canon_simple(cand_theme)

    # Also consider the raw phrase BEFORE normalization for exact matches
    raw_key = _canon_simple(str(candidate_raw or "").strip())
    raw_canon = _canon(str(candidate_raw or "").strip())

    # 0) exact existing (raw or normalized)
    allowed_keys = {_canon_simple(x): x for x in allowed}
    if raw_key in allowed_keys:
        return {"canonical": allowed_keys[raw_key], "kind": "exact_existing", "target": allowed_keys[raw_key], "score": 1.0}
    if cand_key in allowed_keys:
        return {"canonical": allowed_keys[cand_key], "kind": "exact_existing", "target": allowed_keys[cand_key], "score": 1.0}

    # 1) alias match (raw or normalized)
    if raw_canon in alias_to_label:
        tgt = alias_to_label[raw_canon]
        return {"canonical": tgt, "kind": "alias_to_existing", "target": tgt, "score": 1.0}
    if cand_canon in alias_to_label:
        tgt = alias_to_label[cand_canon]
        return {"canonical": tgt, "kind": "alias_to_existing", "target": tgt, "score": 1.0}

    # 2) lexical similarity to allowed
    try:
        m = difflib.get_close_matches(cand_theme, allowed, n=1, cutoff=float(sim_threshold_lex))
        if m:
            tgt = m[0]
            return {"canonical": tgt, "kind": "alias_to_existing", "target": tgt, "score": float(sim_threshold_lex)}
    except Exception:
        pass

    # 3) lexical similarity to learned labels (within-run)
    learned_labels = list(learned_store.get("labels", {}).get(side_norm, {}).keys())
    try:
        m2 = difflib.get_close_matches(cand_theme, learned_labels, n=1, cutoff=float(sim_threshold_lex))
        if m2:
            tgt = m2[0]
            return {"canonical": tgt, "kind": "synonym_to_learned", "target": tgt, "score": float(sim_threshold_lex)}
    except Exception:
        pass

    # 4) semantic similarity (optional) to allowed, then learned
    score_used = 0.0
    if client is not None and embed_model:
        # Prepare embedding pools
        pool_emb_allowed = learned_store.setdefault("_emb_allowed", {})
        pool_emb_learned = learned_store.setdefault("_emb_learned", {})
        best_a, score_a = _best_semantic_match(cand_theme, allowed, pool_emb_allowed, client, embed_model, component="embed-merge")
        best_l, score_l = _best_semantic_match(cand_theme, learned_labels, pool_emb_learned, client, embed_model, component="embed-merge")

        # pick best
        best_lab, best_score, best_kind = None, 0.0, ""
        if best_a and score_a >= best_score:
            best_lab, best_score, best_kind = best_a, score_a, "alias_to_existing"
        if best_l and score_l > best_score:
            best_lab, best_score, best_kind = best_l, score_l, "synonym_to_learned"

        if best_lab and best_score >= float(sim_threshold_sem):
            score_used = float(best_score)
            return {"canonical": best_lab, "kind": best_kind, "target": best_lab, "score": score_used}

    # 5) new theme
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
    # stratify by star rating if present
    if "Star Rating" in df2.columns:
        try:
            rng = random.Random(seed)
            out_idx = []
            for rating, g in df2.groupby("Star Rating"):
                gidx = list(g.index)
                take = max(1, int(round(n * (len(gidx) / len(df2)))))
                rng.shuffle(gidx)
                out_idx.extend(gidx[:take])
            # trim if overshoot
            out_idx = out_idx[:n]
            return df2.loc[out_idx]
        except Exception:
            pass
    return df2.sample(n=n, random_state=seed)

def _prelearn_llm_batch_mine(
    texts: List[str],
    client,
    model: str,
    temperature: float,
    known_themes: Dict[str, List[str]],
    max_themes: int = 30,
) -> Dict[str, Any]:
    """
    Mine themes from a batch of reviews.
    Returns dict {detractors:[{label,keywords}], delighters:[...], product_profile:"..."}
    """
    if client is None:
        return {"detractors": [], "delighters": [], "product_profile": ""}

    # Keep prompts compact – use known themes to discourage duplicates
    sys = "\n".join([
        "You analyze consumer product reviews to extract CONSISTENT THEMES.",
        "Return STRICT JSON with schema:",
        '{"product_profile":"<1-2 sentence product summary>",',
        ' "detractors":[{"label":"<2-4 words Title Case>", "keywords":["k1","k2","k3"]}],',
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
        "reviews": texts[:40],  # cap per batch
        "known_detractor_themes": known_themes.get("Detractor", [])[:60],
        "known_delighter_themes": known_themes.get("Delighter", [])[:60],
    }
    resp = client.chat.completions.create(
        model=model,
        temperature=float(temperature),
        messages=[{"role":"system","content":sys},{"role":"user","content":json.dumps(payload)}],
        response_format={"type":"json_object"},
    )
    pt, ct = _extract_usage(resp)
    if pt or ct:
        _track("prelearn-mine", model, in_tok=pt, out_tok=ct, embed=False)

    try:
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception:
        data = {}
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
    """
    Merge very similar theme labels into one canonical label using embeddings + lexical heuristic.
    """
    if not themes.get(side):
        return themes

    labels = list(themes[side].keys())
    # quick lexical merge by canon_simple (handles Cooling Pad Issue(s) etc after normalization)
    key_to_lab: Dict[str, str] = {}
    for lab in labels:
        k = _canon_simple(normalize_theme_label(lab, side))
        if k in key_to_lab and key_to_lab[k] != lab:
            # merge into existing
            tgt = key_to_lab[k]
            themes[side][tgt]["count"] += themes[side][lab]["count"]
            themes[side][tgt]["keywords"] |= themes[side][lab]["keywords"]
            del themes[side][lab]
        else:
            key_to_lab[k] = lab

    labels = list(themes[side].keys())
    if client is None or not embed_model or len(labels) < 2:
        return themes

    # compute embeddings for labels
    store = _ensure_learned_store()
    pool_emb = store["emb"][side]  # reuse store
    for lab in labels:
        if lab not in pool_emb:
            v = _embed_text(lab, client, embed_model, component="prelearn-embed-themes")
            if v:
                pool_emb[lab] = v

    # greedy merge: sort by count desc; merge smaller into larger if similar
    labels_sorted = sorted(labels, key=lambda x: -int(themes[side][x]["count"]))
    merged_into: Dict[str, str] = {}

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
            # also merge if one label is substring of another (strong lexical)
            lex_sub = (_canon(a) in _canon(b)) or (_canon(b) in _canon(a))
            if sim >= float(sem_merge_threshold) or lex_sub:
                # merge b -> a (keep bigger count label 'a')
                themes[side][a]["count"] += themes[side][b]["count"]
                themes[side][a]["keywords"] |= themes[side][b]["keywords"]
                merged_into[b] = a
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
    """
    Full prelearn pipeline with real-time status + ETA.
    """
    t0 = time.perf_counter()
    learned = _ensure_learned_store()

    # 1) Sample
    status_box.markdown("🔎 **Prelearn:** Sampling reviews…")
    df_s = _sample_reviews(df_in, n=int(sample_n))
    reviews = [str(x) for x in df_s["Verbatim"].tolist() if str(x).strip()]
    prog_bar.progress(0.05)

    # 2) Cheap glossary
    status_box.markdown("🧠 **Prelearn:** Building quick product glossary (no API)…")
    terms = _top_terms(reviews, top_n=40)
    learned["glossary_terms"] = terms
    prog_bar.progress(0.12)

    # 3) Batch theme mining with ETA
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
        # ETA based on average batch duration
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
        # merge mined
        for obj in data.get("delighters", []) or []:
            lab = normalize_theme_label(obj.get("label",""), "Delighter")
            kws = obj.get("keywords", []) or []
            _merge_theme_dict(themes, "Delighter", lab, kws, count_inc=1)
        for obj in data.get("detractors", []) or []:
            lab = normalize_theme_label(obj.get("label",""), "Detractor")
            kws = obj.get("keywords", []) or []
            _merge_theme_dict(themes, "Detractor", lab, kws, count_inc=1)

        # progress (0.12 -> 0.78)
        prog = 0.12 + 0.66 * (bi / max(1, total_batches))
        prog_bar.progress(min(0.78, max(0.12, prog)))

        # Budget guard (session-level)
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

    # 4) Consolidate themes to be mutually exclusive
    status_box.markdown("🧩 **Prelearn:** Consolidating themes (dedupe + semantic merge)…")
    themes = _consolidate_themes_semantic("Delighter", themes, client, embed_model, sem_merge_threshold=float(sem_merge_threshold))
    themes = _consolidate_themes_semantic("Detractor", themes, client, embed_model, sem_merge_threshold=float(sem_merge_threshold))
    prog_bar.progress(0.90)

    # 5) Save into learned store
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

# ------------------- Unified Labeler (labels + meta in one call) -------------------
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
    """
    Evidence-locked + meta. Returns dict with:
      dels, dets, unl_dels, unl_dets, ev_del_map, ev_det_map, safety, reliability, sessions
    """
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

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=float(temperature),
            messages=[{"role": "system", "content": sys},
                      {"role": "user", "content": json.dumps(user_payload)}],
            response_format={"type": "json_object"},
        )
        pt, ct = _extract_usage(resp)
        if pt or ct:
            _track("symptomize-label", model, in_tok=pt, out_tok=ct, embed=False)

        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
    except Exception:
        return {
            "dels": [], "dets": [], "unl_dels": [], "unl_dets": [],
            "ev_del_map": {}, "ev_det_map": {},
            "safety": "Not Mentioned", "reliability": "Not Mentioned", "sessions": "Unknown",
        }

    raw_dels = data.get("delighters", []) or []
    raw_dets = data.get("detractors", []) or []
    unl_dels = [x for x in (data.get("unlisted_delighters", []) or []) if isinstance(x, str) and x.strip()][:10]
    unl_dets = [x for x in (data.get("unlisted_detractors", []) or []) if isinstance(x, str) and x.strip()][:10]

    # meta
    s = str(data.get("safety", "Not Mentioned")).strip()
    r = str(data.get("reliability", "Not Mentioned")).strip()
    n = str(data.get("sessions", "Unknown")).strip()
    s = s if s in SAFETY_ENUM else "Not Mentioned"
    r = r if r in RELIABILITY_ENUM else "Not Mentioned"
    n = n if n in SESSIONS_ENUM else "Unknown"

    # normalize allowed-label evidence maps
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

# ------------------- Export helpers -------------------
def generate_template_workbook_bytes(
    original_file,
    updated_df: pd.DataFrame,
    processed_idx: Optional[Set[int]] = None,
    overwrite_processed_slots: bool = False,
) -> bytes:
    """Return workbook bytes with K–T (dets), U–AD (dels), and AE/AF/AG meta (headers preserved)."""
    original_file.seek(0)
    wb = load_workbook(original_file)
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
            if cv is not None: cell.fill = fill_red
        for j, col_idx in enumerate(DEL_INDEXES, start=1):
            val = r.get(f"AI Symptom Delighter {j}")
            cv = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cv)
            if cv is not None: cell.fill = fill_green
        safety = r.get("AI Safety"); reliab = r.get("AI Reliability"); sess = r.get("AI # of Sessions")
        if is_filled(safety):
            c = ws.cell(row=i, column=META_INDEXES["Safety"], value=str(safety)); c.fill = fill_yel
        if is_filled(reliab):
            c = ws.cell(row=i, column=META_INDEXES["Reliability"], value=str(reliab)); c.fill = fill_blu
        if is_filled(sess):
            c = ws.cell(row=i, column=META_INDEXES["# of Sessions"], value=str(sess)); c.fill = fill_pur

    for c in DET_INDEXES + DEL_INDEXES + list(META_INDEXES.values()):
        try: ws.column_dimensions[get_column_letter(c)].width = 28
        except Exception: pass

    out = io.BytesIO(); wb.save(out)
    return out.getvalue()

# ------------------- Helpers: apply inbox updates (new symptoms + alias additions) -------------------
def apply_symptoms_updates_to_workbook(
    original_file,
    new_symptoms: List[Tuple[str, str]],
    alias_additions: List[Tuple[str, str]],
) -> bytes:
    """
    Update the 'Symptoms' sheet:
      - append new symptom rows (label, type)
      - append alias strings to existing label rows
    """
    original_file.seek(0)
    wb = load_workbook(original_file)

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
        while idx in used_cols: idx += 1
        ws.cell(row=1, column=idx, value=name); used_cols.add(idx); return idx

    col_label = _ensure_header("Symptom", ["symptom", "label", "name", "item"], preferred_index=1)
    col_type  = _ensure_header("Type", ["type", "polarity", "category", "side"], preferred_index=2)
    col_alias = _ensure_header("Aliases", ["aliases", "alias"], preferred_index=3)

    # Build lookup of existing labels -> row index and existing aliases
    existing_row: Dict[str, int] = {}
    existing_aliases: Dict[str, Set[str]] = {}

    try: last_row = int(getattr(ws, "max_row", 0) or 0)
    except Exception: last_row = 0

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

    # 1) new symptoms
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

    # 2) alias additions
    for tgt_label, alias in alias_additions:
        tgt = str(tgt_label).strip()
        als = str(alias).strip()
        if not tgt or not als:
            continue
        if tgt not in existing_row:
            continue
        aset = existing_aliases.setdefault(tgt, set())
        # suppress exact/near duplicates (case-insensitive)
        if _canon_simple(als) == _canon_simple(tgt):
            continue
        if any(_canon_simple(als) == _canon_simple(a) for a in aset):
            continue
        aset.add(als)
        r_i = existing_row[tgt]
        # write back as " | " list
        ws.cell(row=r_i, column=col_alias, value=" | ".join(sorted(aset)))

    out = io.BytesIO(); wb.save(out); out.seek(0)
    return out.getvalue()

# ------------------- File Upload -------------------
uploaded_file = st.file_uploader("📂 Upload Excel (with 'Star Walk scrubbed verbatims' + 'Symptoms')", type=["xlsx"])
if not uploaded_file:
    st.stop()

uploaded_bytes = uploaded_file.read(); uploaded_file.seek(0)
try:
    df = pd.read_excel(uploaded_file, sheet_name="Star Walk scrubbed verbatims")
except ValueError:
    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file)

if "Verbatim" not in df.columns:
    st.error("Missing 'Verbatim' column.")
    st.stop()

# Normalize
df.columns = [str(c).strip() for c in df.columns]
df["Verbatim"] = df["Verbatim"].map(clean_text)

# Load Symptoms
DELIGHTERS, DETRACTORS, ALIASES = get_symptom_whitelists(uploaded_bytes)
if not DELIGHTERS and not DETRACTORS:
    st.warning("⚠️ No Symptoms found in 'Symptoms' tab. Prelearn can bootstrap themes.")
else:
    st.success(f"Loaded {len(DELIGHTERS)} Delighters, {len(DETRACTORS)} Detractors from Symptoms tab.")

# Canonical maps
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

MODEL_CHOICES = {
    "Fast – GPT-4o-mini (default)": "gpt-4o-mini",
    "Balanced – GPT-4.1": "gpt-4.1",
    "Balanced – GPT-4o": "gpt-4o",
    "Advanced – GPT-5": "gpt-5",
}
# Default: mini
model_label = st.sidebar.selectbox("Model", list(MODEL_CHOICES.keys()), index=0)
selected_model = MODEL_CHOICES[model_label]
temperature = st.sidebar.slider("Creativity (temperature)", 0.0, 1.0, 0.2, 0.1)

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
client = OpenAI(api_key=api_key) if (_HAS_OPENAI and api_key) else None
if client is None:
    st.sidebar.warning("OpenAI not configured — set OPENAI_API_KEY and install 'openai'.")

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
    help="Runs a fast pre-pass to learn product glossary + canonical themes to reduce duplicates (Comfortable fit/use/design etc.)."
)
prelearn_model = st.sidebar.selectbox(
    "Prelearn model (cheap recommended)",
    ["gpt-4o-mini", "gpt-4.1", "gpt-4o", "gpt-5"],
    index=0,
    help="This model is used only for the prelearn mining step."
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
    help="When ON, the labeler can tag using learned themes in addition to the Symptoms tab. Keeps output consistent."
)

# Cost panel
st.sidebar.subheader("💰 Cost (this session)")
# Allow optional runtime overrides in case OpenAI pricing changes
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

# Optional budget guard
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
        "like “Comfortable fit / comfortable use / comfortable design” and merges synonyms into one theme. "
        "It also improves the 🟡 Inbox by routing near-duplicates as <b>Alias Suggestions</b> instead of new symptoms.</div>",
        unsafe_allow_html=True,
    )

prelearn_status = st.empty()
prelearn_prog = st.progress(0.0)

if run_prelearn_btn and client is not None:
    learned = run_prelearn(
        df_in=df,
        client=client,
        prelearn_model=prelearn_model,
        temperature=0.0,  # deterministic
        embed_model=embed_model,
        sample_n=int(prelearn_sample_n),
        batch_size=int(prelearn_batch_size),
        sem_merge_threshold=float(prelearn_merge_threshold),
        status_box=prelearn_status,
        prog_bar=prelearn_prog,
    )
    st.session_state["learned"] = learned

# Show current learned summary (if exists)
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

# ------------------- Scope & Preview -------------------
st.subheader("🧪 Symptomize")
scope = st.selectbox(
    "Choose scope",
    ["Missing both", "Any missing", "Missing delighters only", "Missing detractors only"],
    index=0,
)

if scope == "Missing both":
    target = work[(work["Needs_Delighters"]) & (work["Needs_Detractors"])]
elif scope == "Missing delighters only":
    target = work[(work["Needs_Delighters"]) & (~work["Needs_Detractors"])]
elif scope == "Missing detractors only":
    target = work[(~work["Needs_Delighters"]) & (work["Needs_Detractors"])]
else:
    target = work[(work["Needs_Delighters"]) | (work["Needs_Detractors"])]

st.write(f"🔎 **{len(target):,} reviews** match the selected scope.")
with st.expander("Preview in-scope rows", expanded=False):
    preview_cols = ["Verbatim", "Has_Delighters", "Has_Detractors", "Needs_Delighters", "Needs_Detractors"]
    extras = [c for c in ["Star Rating", "Review Date", "Source"] if c in target.columns]
    st.dataframe(target[preview_cols + extras].head(200), use_container_width=True)

# ------------------- Controls -------------------
processed_rows: List[Dict] = []
processed_idx_set: Set[int] = set()

# Global maps for inbox aggregation
new_symptom_candidates: Dict[Tuple[str, str], List[int]] = {}      # (label, side) -> [row_idx...]
alias_suggestion_candidates: Dict[Tuple[str, str, str], List[int]] = {}  # (target_label, alias, side) -> [row_idx...]

if "undo_stack" not in st.session_state:
    st.session_state["undo_stack"] = []

# Row 1: actions
r1a, r1b, r1c, r1d, r1e = st.columns([1.4, 1.4, 1.8, 1.8, 1.2])
with r1a: run_n_btn = st.button("▶️ Symptomize N", use_container_width=True)
with r1b: run_all_btn = st.button("🚀 Symptomize All (current scope)", use_container_width=True)
with r1c: overwrite_btn = st.button("🧹 Overwrite & Symptomize ALL (start at row 1)", use_container_width=True)
with r1d: run_missing_both_btn = st.button("✨ Missing-Both One-Click", use_container_width=True)
with r1e: undo_btn = st.button("↩️ Undo last run", use_container_width=True)

# Small spacer
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

# ------------------- Runner -------------------
def _active_allowed_lists() -> Tuple[List[str], List[str]]:
    """
    Return allowed lists that the labeler will use.
    If use_learned_as_allowed is on, we union learned themes (top N) in addition to Symptoms tab.
    """
    dels = list(DELIGHTERS)
    dets = list(DETRACTORS)

    if use_learned_as_allowed:
        ls = _ensure_learned_store()
        # top themes only (avoid runaway)
        learned_dels = _known_learned_labels("Delighter")[:60]
        learned_dets = _known_learned_labels("Detractor")[:60]
        # union preserving order
        for x in learned_dels:
            if x not in dels:
                dels.append(x)
        for x in learned_dets:
            if x not in dets:
                dets.append(x)
    return dels, dets

def _run_symptomize(rows_df: pd.DataFrame, overwrite_mode: bool = False):
    global df
    prog = st.progress(0.0)
    eta_box = st.empty()
    status_box = st.empty()

    # Auto prelearn if enabled and not yet run
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
            pre_box.empty(); pre_prog.empty()

    # snapshot for undo
    snapshot: List[Tuple[int, Dict[str, Optional[str]]]] = []

    if overwrite_mode:
        df = ensure_ai_columns(df)
        idxs = rows_df.index.tolist()
        for idx_clear in idxs:
            old_vals = {f"AI Symptom Detractor {j}": df.loc[idx_clear, f"AI Symptom Detractor {j}"] if f"AI Symptom Detractor {j}" in df.columns else None for j in range(1,11)}
            old_vals.update({f"AI Symptom Delighter {j}": df.loc[idx_clear, f"AI Symptom Delighter {j}"] if f"AI Symptom Delighter {j}" in df.columns else None for j in range(1,11)})
            old_vals.update({"AI Safety": df.loc[idx_clear, "AI Safety"] if "AI Safety" in df.columns else None,
                             "AI Reliability": df.loc[idx_clear, "AI Reliability"] if "AI Reliability" in df.columns else None,
                             "AI # of Sessions": df.loc[idx_clear, "AI # of Sessions"] if "AI # of Sessions" in df.columns else None})
            snapshot.append((int(idx_clear), old_vals))
            for j in range(1, 11):
                df.loc[idx_clear, f"AI Symptom Detractor {j}"] = None
                df.loc[idx_clear, f"AI Symptom Delighter {j}"] = None

    total_n = max(1, len(rows_df))
    t0 = time.perf_counter()

    # for dynamic cost estimate
    cost_start = float(_ensure_usage_tracker()["cost_chat"] + _ensure_usage_tracker()["cost_embed"])

    for k, (idx, row) in enumerate(rows_df.iterrows(), start=1):
        vb = row.get("Verbatim", "")
        needs_deli = bool(row.get("Needs_Delighters", False))
        needs_detr = bool(row.get("Needs_Detractors", False))

        if not overwrite_mode:
            old_vals = {f"AI Symptom Detractor {j}": df.loc[idx, f"AI Symptom Detractor {j}"] if f"AI Symptom Detractor {j}" in df.columns else None for j in range(1,11)}
            old_vals.update({f"AI Symptom Delighter {j}": df.loc[idx, f"AI Symptom Delighter {j}"] if f"AI Symptom Delighter {j}" in df.columns else None for j in range(1,11)})
            old_vals.update({"AI Safety": df.loc[idx, "AI Safety"] if "AI Safety" in df.columns else None,
                             "AI Reliability": df.loc[idx, "AI Reliability"] if "AI Reliability" in df.columns else None,
                             "AI # of Sessions": df.loc[idx, "AI # of Sessions"] if "AI # of Sessions" in df.columns else None})
            snapshot.append((int(idx), old_vals))

        status_box.markdown(f"🔄 **Row {int(idx)}** • labeling + extracting meta…")

        allowed_dels, allowed_dets = _active_allowed_lists()
        known_hints = {
            "Delighter": _known_learned_labels("Delighter")[:60],
            "Detractor": _known_learned_labels("Detractor")[:60],
        }

        try:
            out = _openai_labeler_unified(
                verbatim=vb,
                client=client,
                model=selected_model,
                temperature=temperature,
                allowed_delighters=allowed_dels,
                allowed_detractors=allowed_dets,
                known_theme_hints=known_hints,
                max_ev_per_label=max_ev_per_label,
                max_ev_chars=max_ev_chars,
            ) if client else {
                "dels": [], "dets": [], "unl_dels": [], "unl_dets": [],
                "ev_del_map": {}, "ev_det_map": {},
                "safety": "Not Mentioned", "reliability": "Not Mentioned", "sessions": "Unknown",
            }
        except Exception:
            out = {
                "dels": [], "dets": [], "unl_dels": [], "unl_dets": [],
                "ev_del_map": {}, "ev_det_map": {},
                "safety": "Not Mentioned", "reliability": "Not Mentioned", "sessions": "Unknown",
            }

        dels = out["dels"]; dets = out["dets"]
        unl_dels = out["unl_dels"]; unl_dets = out["unl_dets"]
        ev_del_map = out["ev_del_map"]; ev_det_map = out["ev_det_map"]
        safety, reliability, sessions = out["safety"], out["reliability"], out["sessions"]

        df = ensure_ai_columns(df)
        wrote_dets, wrote_dels = [], []
        ev_written_det: Dict[str, List[str]] = {}
        ev_written_del: Dict[str, List[str]] = {}

        def _label_allowed(label: str, side: str) -> bool:
            if not require_evidence:
                return True
            evs = (ev_det_map if side == "det" else ev_del_map).get(label, [])
            return len(evs) > 0

        # Write allowed labels only (from active list)
        if needs_detr and dets:
            dets_to_write = [lab for lab in dets if _label_allowed(lab, "det")][:10]
            for j, lab in enumerate(dets_to_write):
                col = f"AI Symptom Detractor {j+1}"
                df.loc[idx, col] = lab
                ev_written_det[lab] = ev_det_map.get(lab, [])
            wrote_dets = dets_to_write

        if needs_deli and dels:
            dels_to_write = [lab for lab in dels if _label_allowed(lab, "del")][:10]
            for j, lab in enumerate(dels_to_write):
                col = f"AI Symptom Delighter {j+1}"
                df.loc[idx, col] = lab
                ev_written_del[lab] = ev_del_map.get(lab, [])
            wrote_dels = dels_to_write

        df.loc[idx, "AI Safety"] = safety
        df.loc[idx, "AI Reliability"] = reliability
        df.loc[idx, "AI # of Sessions"] = sessions

        # Handle unlisted: canonicalize + route into New Symptoms vs Alias Suggestions
        learned = _ensure_learned_store()
        new_unl_dels: List[str] = []
        new_unl_dets: List[str] = []
        alias_sugs_for_row: List[Tuple[str, str, str, float]] = []  # (target, alias, side, score)

        def _handle_unlisted_list(items: List[str], side_label: str):
            nonlocal new_unl_dels, new_unl_dets, alias_sugs_for_row
            for raw in items or []:
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

                # Update learned store to improve future merges
                _update_learned(side_label, canon, synonym=raw2)

                if kind in {"exact_existing", "alias_to_existing"} and tgt:
                    # alias suggestion (only if alias isn't already present)
                    if tgt in (DELIGHTERS + DETRACTORS):
                        alias_sugs_for_row.append((tgt, raw2, side_label, score))
                elif kind == "synonym_to_learned":
                    # don't create a new candidate
                    pass
                else:
                    # new canonical symptom candidate
                    if side_label.lower().startswith("del"):
                        new_unl_dels.append(canon)
                    else:
                        new_unl_dets.append(canon)

        _handle_unlisted_list(unl_dels, "Delighter")
        _handle_unlisted_list(unl_dets, "Detractor")

        # Deduplicate per row (canon_simple)
        def _dedupe_keep_order(lst: List[str]) -> List[str]:
            out, seen = [], set()
            for x in lst:
                k = _canon_simple(x)
                if not x or k in seen:
                    continue
                seen.add(k); out.append(x)
            return out

        new_unl_dels = _dedupe_keep_order([normalize_theme_label(x, "Delighter") for x in new_unl_dels])
        new_unl_dets = _dedupe_keep_order([normalize_theme_label(x, "Detractor") for x in new_unl_dets])

        # Aggregate for inbox (counts + examples)
        for lab in new_unl_dels:
            new_symptom_candidates.setdefault((lab, "Delighter"), []).append(int(idx))
        for lab in new_unl_dets:
            new_symptom_candidates.setdefault((lab, "Detractor"), []).append(int(idx))
        for tgt, alias, side_label, score in alias_sugs_for_row:
            alias_suggestion_candidates.setdefault((tgt, alias, "Delighter" if side_label.lower().startswith("del") else "Detractor"), []).append(int(idx))

        # Evidence coverage for this row
        total_labels = len(wrote_dets) + len(wrote_dels)
        labels_with_ev = sum(1 for lab in wrote_dets if len(ev_written_det.get(lab, []))>0) + \
                         sum(1 for lab in wrote_dels if len(ev_written_del.get(lab, []))>0)
        row_ev_cov = (labels_with_ev / total_labels) if total_labels else 0.0

        processed_rows.append({
            "Index": int(idx),
            "Verbatim": str(vb),
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
        processed_idx_set.add(int(idx))

        prog.progress(k/total_n)

        # ETA + cost update
        elapsed = time.perf_counter() - t0
        rate = (k / elapsed) if elapsed > 0 else 0.0
        rem = total_n - k
        eta_sec = (rem / rate) if rate > 0 else 0.0

        tr2 = _ensure_usage_tracker()
        spent = float(tr2["cost_chat"] + tr2["cost_embed"]) - cost_start
        avg_per = (spent / k) if k else 0.0
        est_total = avg_per * total_n

        eta_box.markdown(
            f"**Progress:** {k}/{total_n} • **ETA:** ~ {_fmt_secs(eta_sec)} • **Speed:** {rate*60:.1f} rev/min • "
            f"**Spend:** {_fmt_money(spent)} • **Est total:** {_fmt_money(est_total)}"
        )

        # Budget guard (session-level)
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


    status_box.markdown("✅ Done.")
    st.session_state["undo_stack"].append({"rows": snapshot})

# ------------------- Execute by buttons (updated overwrite-all logic) -------------------
if client is not None and (run_n_btn or run_all_btn or overwrite_btn or run_missing_both_btn):
    if run_missing_both_btn:
        rows_iter = work[(work["Needs_Delighters"]) & (work["Needs_Detractors"])].sort_index()
        _run_symptomize(rows_iter, overwrite_mode=False)

    elif overwrite_btn:
        # FULL RESET: clear every AI slot in the entire df, recompute, then process ALL rows from row 1
        df = clear_all_ai_slots_in_df(df)

        # Recompute column map and needs after clearing
        colmap = detect_symptom_columns(df)
        work = detect_missing(df, colmap)

        # Process ALL rows, top-to-bottom (index order)
        rows_iter = work.sort_index()
        _run_symptomize(rows_iter, overwrite_mode=False)

    else:
        if run_all_btn:
            rows_iter = target.sort_index()
        else:
            rows_iter = target.sort_index().head(int(st.session_state.get("n_to_process", 10)))
        _run_symptomize(rows_iter, overwrite_mode=False)

    st.success(f"Symptomized {len(processed_rows)} review(s).")

# Undo last run
def _undo_last_run():
    global df
    if not st.session_state["undo_stack"]:
        st.info("Nothing to undo."); return
    snap = st.session_state["undo_stack"].pop()
    for idx, old_vals in snap.get("rows", []):
        for col, val in old_vals.items():
            if col not in df.columns:
                df[col] = None
            df.loc[idx, col] = val
    st.success("Reverted last run.")

if undo_btn:
    _undo_last_run()

# ------------------- Processed Reviews (chips + highlighted evidence) -------------------
if processed_rows:
    st.subheader("🧾 Processed Reviews (this run)")
    ev_cov_vals = [float(r.get("Evidence_Coverage", 0.0)) for r in processed_rows]
    overall_cov = (sum(ev_cov_vals)/len(ev_cov_vals)) if ev_cov_vals else 0.0
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

            # New candidates + alias suggestions
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
                        for e in evs: st.write(f"- {e}")
                if rec.get("Evidence_Delighters"):
                    st.markdown("**Delighter evidence**")
                    for lab, evs in rec["Evidence_Delighters"].items():
                        for e in evs: st.write(f"- {e}")

# ------------------- 🟡 Inbox: New Symptoms + Alias Suggestions -------------------
# Strengthen: suppress items already in Symptoms or aliases (case-insensitive canon), plus near-dupes in both inboxes.
whitelist_all = set(DELIGHTERS + DETRACTORS)
alias_all = set([a for lst in ALIASES.values() for a in lst]) if ALIASES else set()
wl_canon = {_canon_simple(x) for x in whitelist_all}
ali_canon = {_canon_simple(x) for x in alias_all}

def _is_existing_label_or_alias(s: str) -> bool:
    k = _canon_simple(s)
    return (k in wl_canon) or (k in ali_canon)

def _filter_new_symptom_candidates(cands: Dict[Tuple[str, str], List[int]]) -> Dict[Tuple[str, str], List[int]]:
    out: Dict[Tuple[str, str], List[int]] = {}
    for (lab, side), refs in cands.items():
        lab2 = normalize_theme_label(lab, side)
        if not lab2:
            continue
        if _is_existing_label_or_alias(lab2):
            # existing already; don't show as new symptom
            continue
        out.setdefault((lab2, side), []).extend(refs)
    # merge same canon_simple key (singular/plural etc)
    merged: Dict[Tuple[str, str], List[int]] = {}
    seen: Dict[Tuple[str, str], str] = {}
    for (lab, side), refs in out.items():
        key = _canon_simple(lab)
        k2 = (key, side)
        if k2 in seen:
            merged[(seen[k2], side)].extend(refs)
        else:
            merged[(lab, side)] = list(refs); seen[k2] = lab
    # near-dupe suppression vs whitelist
    final: Dict[Tuple[str, str], List[int]] = {}
    for (lab, side), refs in merged.items():
        try:
            m = difflib.get_close_matches(lab, list(whitelist_all), n=1, cutoff=float(sim_threshold_lex))
            if m:
                # route to alias suggestions instead of new symptom (handled separately)
                continue
        except Exception:
            pass
        final[(lab, side)] = refs
    return final

def _filter_alias_candidates(cands: Dict[Tuple[str, str, str], List[int]]) -> Dict[Tuple[str, str, str], List[int]]:
    out: Dict[Tuple[str, str, str], List[int]] = {}
    for (tgt, alias, side), refs in cands.items():
        tgt2 = str(tgt).strip()
        alias2 = normalize_theme_label(alias, side, singularize=True)  # normalize alias phrase too
        if not tgt2 or not alias2:
            continue
        # suppress if alias equals label or already an alias
        if _canon_simple(alias2) == _canon_simple(tgt2):
            continue
        if _is_existing_label_or_alias(alias2):
            # already exists as label or alias somewhere; don't add
            continue
        out.setdefault((tgt2, alias2, side), []).extend(refs)
    # merge identical canon alias per target
    merged: Dict[Tuple[str, str, str], List[int]] = {}
    seen: Set[Tuple[str, str, str]] = set()
    for k, refs in out.items():
        tgt, alias, side = k
        key = (tgt, _canon_simple(alias), side)
        if key in seen:
            # find existing
            for kk in list(merged.keys()):
                if kk[0] == tgt and _canon_simple(kk[1]) == _canon_simple(alias) and kk[2] == side:
                    merged[kk].extend(refs)
                    break
        else:
            merged[k] = list(refs); seen.add(key)
    return merged

new_symptom_candidates_f = _filter_new_symptom_candidates(new_symptom_candidates)
alias_suggestion_candidates_f = _filter_alias_candidates(alias_suggestion_candidates)

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
        for (lab, side), refs in sorted(new_symptom_candidates_f.items(), key=lambda kv: (-len(kv[1]), kv[0][0])):
            rows_tbl.append({
                "Add": False,
                "Label": lab,
                "Side": side,
                "Count": int(len(refs)),
                "Examples": _mk_examples(refs),
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
        for (tgt, alias, side), refs in sorted(alias_suggestion_candidates_f.items(), key=lambda kv: (-len(kv[1]), kv[0][0], kv[0][1])):
            rows_tbl2.append({
                "Add": False,
                "Target Symptom": tgt,
                "Alias": alias,
                "Side": side,
                "Count": int(len(refs)),
                "Examples": _mk_examples(refs),
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
        # new
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
        # alias
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
                uploaded_file,
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

# ------------------- Download Symptomized Workbook -------------------
st.subheader("📦 Download Symptomized Workbook")
try:
    file_base = os.path.splitext(getattr(uploaded_file, 'name', 'Reviews'))[0]
except Exception:
    file_base = 'Reviews'

export_bytes = generate_template_workbook_bytes(
    uploaded_file,
    df,
    processed_idx=processed_idx_set if processed_idx_set else None,
    overwrite_processed_slots=False,
)

st.download_button(
    "⬇️ Download symptomized workbook (XLSX)",
    data=export_bytes,
    file_name=f"{file_base}_Symptomized.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
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

# ------------------- View Symptoms from Excel Workbook (expander) -------------------
st.subheader("📘 View Symptoms from Excel Workbook")
with st.expander("📘 View Symptoms from Excel Workbook", expanded=False):
    st.markdown("This reflects the **Symptoms** sheet as loaded; use the inbox above to propose additions.")

    tabs = st.tabs(["Delighters", "Detractors", "Aliases", "Meta", "Cost Breakdown"])

    def _esc2(s: str) -> str:
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _chips(items, color: str):
        items_sorted = sorted({str(x).strip() for x in (items or []) if str(x).strip()})
        if not items_sorted:
            st.write("(none)")
        else:
            htmlchips = "<div class='chip-wrap'>" + "".join([f"<span class='chip {color}'>{_esc2(x)}</span>" for x in items_sorted]) + "</div>"
            st.markdown(htmlchips, unsafe_allow_html=True)

    with tabs[0]:
        st.markdown("**Delighter labels from workbook**")
        _chips(DELIGHTERS, "green")
    with tabs[1]:
        st.markdown("**Detractor labels from workbook**")
        _chips(DETRACTORS, "red")
    with tabs[2]:
        st.markdown("**Aliases (if present)**")
        if ALIASES:
            alias_rows = [{"Label": k, "Aliases": " | ".join(v)} for k, v in sorted(ALIASES.items())]
            st.dataframe(pd.DataFrame(alias_rows), use_container_width=True, hide_index=True)
        else:
            st.write("(no aliases defined)")
    with tabs[3]:
        st.markdown("**Meta fields usage (from this dataset)**")
        df_meta = ensure_ai_columns(df.copy())

        def _count(col: str, order: List[str]) -> pd.DataFrame:
            if col not in df_meta.columns:
                return pd.DataFrame({"Value": order, "Count": [0] * len(order)})
            vc = df_meta[col].fillna("Not Mentioned").astype(str).value_counts().reindex(order, fill_value=0)
            return vc.rename_axis("Value").reset_index(name="Count")

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Safety**")
            df_s = _count("AI Safety", SAFETY_ENUM)
            st.bar_chart(df_s.set_index("Value")["Count"])
        with c2:
            st.markdown("**Reliability**")
            df_r = _count("AI Reliability", RELIABILITY_ENUM)
            st.bar_chart(df_r.set_index("Value")["Count"])
        with c3:
            st.markdown("**# of Sessions**")
            df_n = _count("AI # of Sessions", SESSIONS_ENUM)
            st.bar_chart(df_n.set_index("Value")["Count"])
    with tabs[4]:
        st.markdown("**OpenAI cost breakdown (this session)**")
        tr = _ensure_usage_tracker()
        st.write({
            "chat_input_tokens": int(tr["chat_in"]),
            "chat_output_tokens": int(tr["chat_out"]),
            "embedding_tokens": int(tr["embed_in"]),
            "chat_cost_usd": float(tr["cost_chat"]),
            "embedding_cost_usd": float(tr["cost_embed"]),
            "total_cost_usd": float(tr["cost_chat"] + tr["cost_embed"]),
        })
        comp_rows = []
        for comp, d in (tr.get("by_component", {}) or {}).items():
            comp_rows.append({
                "Component": comp,
                "Chat in": int(d.get("chat_in", 0)),
                "Chat out": int(d.get("chat_out", 0)),
                "Embed in": int(d.get("embed_in", 0)),
                "Cost (USD)": float(d.get("cost", 0.0)),
            })
        if comp_rows:
            st.dataframe(pd.DataFrame(comp_rows).sort_values("Cost (USD)", ascending=False), use_container_width=True, hide_index=True)
        else:
            st.write("(no usage recorded)")

# Footer
st.divider()
st.caption(
    "v7.5 — Evidence-locked labeling + Product Knowledge Prelearn + canonical merging + 🟡 Inbox: New Symptoms + Alias Suggestions. "
    "Exports: K–T/U–AD, meta: AE/AF/AG."
)




