# starwalk_ui_v8_3_catalog_first_better_output.py
# v8.3 — catalog-first symptomization with stronger provided-symptom detection
#
# Highlights:
#   - Catalog-first matching of provided Symptoms + aliases
#   - Stronger rescue for supplied labels before falling back to learned labels
#   - Clearer processed-review log: detected vs written
#   - Safer overwrite semantics, filters, inbox, export, undo, throttling, cost tracking
#   - Tuned defaults for better real-world output

import gc
import hashlib
import html
import io
import json
import math
import os
import random
import re
import time
import difflib
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd
import streamlit as st
from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

try:
    from openai import OpenAI  # type: ignore
    _HAS_OPENAI = True
except Exception:
    OpenAI = None  # type: ignore
    _HAS_OPENAI = False

try:
    import tiktoken  # type: ignore
    _HAS_TIKTOKEN = True
except Exception:
    tiktoken = None  # type: ignore
    _HAS_TIKTOKEN = False


# ----------------------------- Page setup -----------------------------
st.set_page_config(layout="wide", page_title="Review Symptomizer — v8.3 Catalog-First")
st.title("✨ Review Symptomizer — v8.3 Catalog-First")
st.caption(
    "Catalog-first detection • stronger provided-symptom matching • clearer review log • tuned defaults • safer overwrite • exact K–T / U–AD export • inbox + aliases • filters"
)

st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
      :root {
        --bg1: rgba(124, 58, 237, .12);
        --bg2: rgba(6, 182, 212, .10);
        --line: #e5e7eb;
        --muted: #667085;
        --text: #101828;
        --brand: #7c3aed;
        --brand-2: #06b6d4;
        --good: #16a34a;
        --bad: #dc2626;
        --warn: #d97706;
      }
      html, body, .stApp {
        font-family: 'Inter', system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
        color: var(--text);
      }
      .stApp {
        background:
          radial-gradient(1000px 420px at 0% -10%, var(--bg1), transparent 60%),
          radial-gradient(900px 420px at 100% 0%, var(--bg2), transparent 60%);
      }
      .hero-shell {
        background: linear-gradient(180deg, rgba(255,255,255,.97), rgba(255,255,255,.90));
        border: 1px solid rgba(226,232,240,.95);
        border-radius: 22px;
        padding: 18px 20px 16px 20px;
        box-shadow: 0 10px 30px rgba(16,24,40,.07);
      }
      .hero-top {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 16px;
        flex-wrap: wrap;
      }
      .hero-title {
        font-size: 22px;
        font-weight: 800;
        letter-spacing: -.02em;
      }
      .hero-sub {
        color: var(--muted);
        font-size: 13px;
        margin-top: 4px;
      }
      .badge-row, .chip-wrap {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
      }
      .badge, .chip {
        padding: 6px 10px;
        border-radius: 999px;
        font-size: 12.5px;
        line-height: 1;
        border: 1px solid #e5e7eb;
        background: #fff;
        box-shadow: 0 1px 2px rgba(16,24,40,.04);
      }
      .chip.blue { background: #eff6ff; border-color: #bfdbfe; }
      .chip.green { background: #ecfdf3; border-color: #bbf7d0; }
      .chip.red { background: #fff1f2; border-color: #fecdd3; }
      .chip.yellow { background: #fffbeb; border-color: #fde68a; }
      .chip.purple { background: #f5f3ff; border-color: #ddd6fe; }
      .chip.gray { background: #f8fafc; border-color: #e2e8f0; }
      .hero-grid {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 12px;
        margin-top: 14px;
      }
      .hero-stat {
        background: #fff;
        border: 1px solid #e5e7eb;
        border-radius: 18px;
        padding: 14px 16px;
        box-shadow: 0 2px 8px rgba(16,24,40,.05);
      }
      .hero-stat.accent {
        border-color: rgba(124,58,237,.35);
        box-shadow: 0 6px 18px rgba(124,58,237,.14);
      }
      .hero-stat .label {
        color: #64748b;
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: .08em;
      }
      .hero-stat .value {
        font-size: 28px;
        font-weight: 800;
        margin-top: 2px;
      }
      .section-title {
        font-size: 18px;
        font-weight: 800;
        margin: 6px 0 10px 0;
      }
      .section-sub {
        color: var(--muted);
        font-size: 13px;
        margin: -2px 0 12px 0;
      }
      .info-card {
        background: linear-gradient(180deg, rgba(255,255,255,.98), rgba(255,255,255,.93));
        border: 1px solid #e5e7eb;
        border-radius: 18px;
        padding: 14px 16px;
        box-shadow: 0 4px 14px rgba(16,24,40,.05);
      }
      .info-card.tight { padding: 12px 14px; }
      .info-card .title { font-size: 13px; color: #475467; font-weight: 700; }
      .info-card .big { font-size: 30px; font-weight: 800; line-height: 1.1; margin-top: 4px; }
      .info-card .muted { color: #667085; font-size: 12.5px; margin-top: 4px; }
      .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
      .run-plan {
        display: grid;
        grid-template-columns: repeat(2, minmax(0,1fr));
        gap: 10px;
      }
      .kv {
        background: #fff;
        border: 1px solid #e5e7eb;
        border-radius: 14px;
        padding: 12px 14px;
      }
      .kv .k { color: #667085; font-size: 11px; text-transform: uppercase; letter-spacing: .06em; }
      .kv .v { font-size: 20px; font-weight: 800; margin-top: 2px; }
      .danger-box {
        background: linear-gradient(180deg, rgba(254,242,242,.92), rgba(254,226,226,.72));
        border: 1px solid #fecaca;
        border-radius: 16px;
        padding: 12px 14px;
      }
      .good-box {
        background: linear-gradient(180deg, rgba(236,253,243,.92), rgba(220,252,231,.72));
        border: 1px solid #bbf7d0;
        border-radius: 16px;
        padding: 12px 14px;
      }
      .tiny { font-size: 12px; color: #667085; }
      mark.hl { background: #fde68a; padding: 0 .16em; border-radius: .25em; }
      .stButton > button {
        height: 42px;
        border-radius: 12px;
        font-weight: 700;
        border: 1px solid #d0d5dd;
        background: linear-gradient(180deg, #ffffff, #f8fafc);
        box-shadow: 0 1px 2px rgba(16,24,40,.04);
      }
      .stButton > button:hover {
        border-color: rgba(124,58,237,.35);
        box-shadow: 0 4px 12px rgba(124,58,237,.14);
      }
      .stNumberInput input, .stTextInput input, .stDateInput input {
        border-radius: 12px !important;
      }
      @media (max-width: 1100px) {
        .hero-grid { grid-template-columns: repeat(2, minmax(0,1fr)); }
        .run-plan { grid-template-columns: 1fr; }
      }
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------- Utilities -----------------------------
NON_VALUES = {"<NA>", "NA", "N/A", "NONE", "-", "", "NAN", "NULL"}


def clean_text(x: Any) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip()


def is_filled(val: Any) -> bool:
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


def _safe(s: Any) -> str:
    return html.escape(str(s or ""))


def _canon(s: str) -> str:
    return " ".join(str(s).split()).lower().strip()


def _canon_simple(s: str) -> str:
    return "".join(ch for ch in _canon(s) if ch.isalnum())


def _find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
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


_BOOL_TRUE = {"true", "t", "yes", "y", "1", "seeded"}
_BOOL_FALSE = {"false", "f", "no", "n", "0", "non-seeded", "nonseeded", "not seeded", "unseeded"}


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


def _unique_sorted_str(series: Optional[pd.Series], limit: int = 5000) -> List[str]:
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


def _chip_html(items: List[Tuple[str, str]]) -> str:
    if not items:
        return "<span class='chip gray'>No active filters</span>"
    out = ["<div class='chip-wrap'>"]
    for text, color in items:
        out.append(f"<span class='chip {color}'>{_safe(text)}</span>")
    out.append("</div>")
    return "".join(out)


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


# -------------------------- Throttling / costs --------------------------
def _throttle(kind: str, est_in_tokens: int) -> None:
    rpm = int(st.session_state.get("throttle_rpm", 0) or 0)
    tpm = int(st.session_state.get("throttle_tpm", 0) or 0)
    if rpm <= 0 and tpm <= 0:
        return

    now = float(time.time())
    key = f"_throttle_{kind}"
    bucket = st.session_state.get(key) or {"events": []}
    events = bucket.get("events") or []

    pruned: List[Tuple[float, int]] = []
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


def _ensure_usage_tracker() -> Dict[str, Any]:
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


def _track(component: str, model_id: str, in_tok: int = 0, out_tok: int = 0, embed: bool = False) -> None:
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

    def _get(obj: Any, k1: str, k2: str) -> int:
        if isinstance(obj, dict):
            return obj.get(k1, obj.get(k2, 0)) or 0
        return getattr(obj, k1, getattr(obj, k2, 0)) or 0

    pt = _get(usage, "prompt_tokens", "input_tokens")
    ct = _get(usage, "completion_tokens", "output_tokens")
    if (pt or 0) == 0:
        pt = _get(usage, "total_tokens", "prompt_tokens")
    return (int(pt or 0), int(ct or 0))


def _estimate_tokens(text: str, model_id: str = "gpt-4o-mini") -> int:
    s = str(text or "")
    if not s:
        return 0
    if _HAS_TIKTOKEN:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return int(len(enc.encode(s)))
        except Exception:
            pass
    return int(max(1, math.ceil(len(s) / 4)))


# ------------------------------ Loaders ------------------------------
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
    type_col = next((lowcols.get(c) for c in ["type", "polarity", "category", "side"] if c in lowcols), None)

    pos_tags = {"delighter", "delighters", "positive", "pos", "pros"}
    neg_tags = {"detractor", "detractors", "negative", "neg", "cons"}

    def _clean(series: pd.Series) -> List[str]:
        vals = series.dropna().astype(str).map(str.strip)
        out: List[str] = []
        seen: Set[str] = set()
        for v in vals:
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    delighters: List[str] = []
    detractors: List[str] = []
    alias_map: Dict[str, List[str]] = {}

    if label_col and type_col:
        df_sym[type_col] = df_sym[type_col].astype(str).str.lower().str.strip()
        delighters = _clean(df_sym.loc[df_sym[type_col].isin(pos_tags), label_col])
        detractors = _clean(df_sym.loc[df_sym[type_col].isin(neg_tags), label_col])
        if alias_col:
            for _, row in df_sym.iterrows():
                lbl = str(row.get(label_col, "")).strip()
                als = str(row.get(alias_col, "")).strip()
                if lbl:
                    if als:
                        alias_map[lbl] = [p.strip() for p in als.replace(",", "|").split("|") if p.strip()]
                    else:
                        alias_map.setdefault(lbl, [])
    else:
        for lc, orig in lowcols.items():
            if ("delight" in lc) or ("positive" in lc) or lc == "pros":
                delighters.extend(_clean(df_sym[orig]))
            if ("detract" in lc) or ("negative" in lc) or lc == "cons":
                detractors.extend(_clean(df_sym[orig]))
        delighters = list(dict.fromkeys(delighters))
        detractors = list(dict.fromkeys(detractors))

    return delighters, detractors, alias_map

# ------------------------- Symptom schema helpers -------------------------
def detect_symptom_columns(df: pd.DataFrame) -> Dict[str, List[str]]:
    cols = [str(c).strip() for c in df.columns]
    man_det = [f"Symptom {i}" for i in range(1, 11) if f"Symptom {i}" in cols]
    man_del = [f"Symptom {i}" for i in range(11, 21) if f"Symptom {i}" in cols]
    ai_det = [c for c in cols if c.startswith("AI Symptom Detractor ")]
    ai_del = [c for c in cols if c.startswith("AI Symptom Delighter ")]
    return {
        "manual_detractors": man_det,
        "manual_delighters": man_del,
        "ai_detractors": ai_det,
        "ai_delighters": ai_del,
    }


def _filled_mask(df_in: pd.DataFrame, cols: List[str]) -> pd.Series:
    if not cols:
        return pd.Series(False, index=df_in.index)
    mask = pd.Series(False, index=df_in.index)
    for c in cols:
        if c not in df_in.columns:
            continue
        s = df_in[c].fillna("").astype(str).str.strip()
        mask |= (s != "") & (~s.str.upper().isin(NON_VALUES))
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


DET_LETTERS = ["K", "L", "M", "N", "O", "P", "Q", "R", "S", "T"]
DEL_LETTERS = ["U", "V", "W", "X", "Y", "Z", "AA", "AB", "AC", "AD"]
DET_INDEXES = [column_index_from_string(c) for c in DET_LETTERS]
DEL_INDEXES = [column_index_from_string(c) for c in DEL_LETTERS]
META_ORDER = [("Safety", "AE"), ("Reliability", "AF"), ("# of Sessions", "AG")]
META_INDEXES = {name: column_index_from_string(col) for name, col in META_ORDER}

AI_DET_HEADERS = [f"AI Symptom Detractor {i}" for i in range(1, 11)]
AI_DEL_HEADERS = [f"AI Symptom Delighter {i}" for i in range(1, 11)]
AI_META_HEADERS = ["AI Safety", "AI Reliability", "AI # of Sessions"]


def ensure_ai_columns(df_in: pd.DataFrame) -> pd.DataFrame:
    for h in AI_DET_HEADERS + AI_DEL_HEADERS + AI_META_HEADERS:
        if h not in df_in.columns:
            df_in[h] = None
    return df_in


def clear_ai_slots_for_indices(df_in: pd.DataFrame, indices: Iterable[int]) -> pd.DataFrame:
    df2 = ensure_ai_columns(df_in)
    for idx in indices:
        for j in range(1, 11):
            df2.loc[idx, f"AI Symptom Detractor {j}"] = None
            df2.loc[idx, f"AI Symptom Delighter {j}"] = None
        df2.loc[idx, "AI Safety"] = None
        df2.loc[idx, "AI Reliability"] = None
        df2.loc[idx, "AI # of Sessions"] = None
    return df2


def build_canonical_maps(
    delighters: List[str], detractors: List[str], alias_map: Dict[str, List[str]]
) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    del_map = {_canon(x): x for x in delighters}
    det_map = {_canon(x): x for x in detractors}
    alias_to_label: Dict[str, str] = {}
    for label, aliases in (alias_map or {}).items():
        for a in aliases:
            alias_to_label[_canon(a)] = label
    return del_map, det_map, alias_to_label


SAFETY_ENUM = ["Not Mentioned", "Concern", "Positive"]
RELIABILITY_ENUM = ["Not Mentioned", "Negative", "Neutral", "Positive"]
SESSIONS_ENUM = ["0", "1", "2–3", "4–9", "10+", "Unknown"]


def _symptom_list_version(delighters: List[str], detractors: List[str], aliases: Dict[str, List[str]]) -> str:
    payload = json.dumps({"del": delighters, "det": detractors, "ali": aliases}, sort_keys=True, ensure_ascii=False)
    try:
        return hashlib.md5(payload.encode("utf-8")).hexdigest()[:10]
    except Exception:
        return f"{len(delighters)}_{len(detractors)}"


def _ensure_label_cache() -> Dict[Any, Any]:
    if "_label_cache" not in st.session_state:
        st.session_state["_label_cache"] = {}
    return st.session_state["_label_cache"]


# -------------------------- Theme normalization --------------------------
THEME_RULES = [
    (
        re.compile(r"\b(pulls?|pulled|pulling).{0,12}\bhair\b|\bhair\s+(?:loss|fall(?:ing)?|coming\s+out|pulled)\b", re.I),
        {"det": "Hair Loss/Pull"},
    ),
    (re.compile(r"\b(snags?|tangles?|catches?)\s+(?:hair|strands?)\b", re.I), {"det": "Hair Snag/Tangle"}),
    (re.compile(r"\b(too\s+hot|burns?|scalds?|overheats?)\b", re.I), {"det": "Excess Heat"}),
    (re.compile(r"\b(too\s+noisy|loud|whine|high\s+noise)\b", re.I), {"det": "High Noise"}),
    (
        re.compile(r"\b(battery|charge|runtime)\b.+\b(poor|short|bad|low)\b|\b(poor|short|bad|low)\b.+\b(battery|charge|runtime)\b", re.I),
        {"det": "Battery Life: Short"},
    ),
    (
        re.compile(r"\b(cooling\s+pad)\b.*\b(issue|issues|problem|problems|fail|failed|broken)\b|\b(issue|issues|problem|problems)\b.*\b(cooling\s+pad)\b", re.I),
        {"det": "Cooling Pad Issue"},
    ),
    (
        re.compile(r"\b(learning\s+curve|hard\s+to\s+learn|takes\s+time\s+to\s+learn|not\s+intuitive)\b", re.I),
        {"det": "Learning Curve"},
    ),
    (
        re.compile(r"\b(initially|at\s+first)\b.*\b(complicated|confusing|hard)\b|\b(complicated|confusing|hard)\b.*\b(at\s+first|initially)\b", re.I),
        {"det": "Learning Curve"},
    ),
    (
        re.compile(r"\b(absolutely|totally|really)?\s*love(s|d)?\b|\bworks\s+(amazing|great|fantastic|perfect)\b|\boverall\b.+\b(great|good|positive|happy)\b", re.I),
        {"del": "Overall Satisfaction"},
    ),
    (
        re.compile(r"\b(easy|quick|simple)\s+to\s+(use|clean|attach|remove)\b|\buser[-\s]?friendly\b|\bintuitive\b", re.I),
        {"del": "Ease Of Use"},
    ),
    (re.compile(r"\b(fast|quick)\s+(dry|drying)\b|\bdries\s+quickly\b", re.I), {"del": "Fast Drying"}),
    (re.compile(r"\b(shine|smooth|sleek|frizz\s*(?:free|control))\b", re.I), {"del": "Frizz Control/Shine"}),
    (
        re.compile(r"\b(attachments?|accessories?)\b.+\b(handy|useful|versatile|helpful)\b", re.I),
        {"del": "Attachment Usability"},
    ),
    (re.compile(r"\b(comfortable|comfort|comfy)\b", re.I), {"del": "Comfort"}),
]

CANONICAL_SYNONYMS = [
    (
        "Comfort",
        "del",
        [
            r"\bcomfortable\b",
            r"\bcomfort\b",
            r"\bcomfy\b",
            r"\bconfortable\b",
            r"\bconfort\b",
            r"\bcomftable\b",
            r"\bcomfort\s*(fit|use|design|feel)\b",
        ],
    ),
    (
        "Cooling Pad Issue",
        "det",
        [r"\bcooling\s+pad\b.*\b(issue|problem|broken|fail)", r"\b(issue|problem)s?\b.*\bcooling\s+pad\b"],
    ),
    (
        "Learning Curve",
        "det",
        [r"\blearning\s+curve\b", r"\bnot\s+intuitive\b", r"\bhard\s+to\s+learn\b", r"\bconfusing\s+at\s+first\b", r"\binitially\s+complicated\b"],
    ),
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
    parts: List[str] = []
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
    repl = SINGULAR_LASTWORD_MAP.get(last)
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


# --------------------------- Learned theme store ---------------------------
def _ensure_learned_store() -> Dict[str, Any]:
    if "learned" not in st.session_state:
        st.session_state["learned"] = {
            "labels": {"Delighter": {}, "Detractor": {}},
            "emb": {"Delighter": {}, "Detractor": {}},
            "keywords": {"Delighter": {}, "Detractor": {}},
            "families": {"Delighter": {}, "Detractor": {}},
            "product_profile": "",
            "product_category": "",
            "product_parts": [],
            "use_cases": [],
            "glossary_terms": [],
            "version": "",
            "ts": None,
        }
    ls = st.session_state["learned"]
    ls.setdefault("labels", {"Delighter": {}, "Detractor": {}})
    ls.setdefault("emb", {"Delighter": {}, "Detractor": {}})
    ls.setdefault("keywords", {"Delighter": {}, "Detractor": {}})
    ls.setdefault("families", {"Delighter": {}, "Detractor": {}})
    ls.setdefault("product_profile", "")
    ls.setdefault("product_category", "")
    ls.setdefault("product_parts", [])
    ls.setdefault("use_cases", [])
    ls.setdefault("glossary_terms", [])
    ls.setdefault("version", "")
    ls.setdefault("ts", None)
    return ls


def _update_learned(side: str, canonical: str, synonym: str = "") -> None:
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


def _ensure_embed_cache() -> Dict[Any, Any]:
    if "_embed_cache" not in st.session_state:
        st.session_state["_embed_cache"] = {}
    return st.session_state["_embed_cache"]


def _embed_text(text: str, client: Any, model_id: str, component: str) -> Optional[List[float]]:
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
    client: Any,
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
            best = s
            best_lab = lab
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
    client: Any,
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


# --------------------------- Prelearn utilities ---------------------------
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "this", "that", "these", "those", "it", "its", "i", "me", "my",
    "we", "our", "you", "your", "to", "of", "in", "on", "for", "with", "as", "at", "by", "from", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "so", "very", "really", "just", "also", "too",
    "not", "no", "yes", "they", "them", "their", "he", "she", "his", "her", "him", "there", "here", "when", "while",
    "because", "into", "out", "up", "down", "over", "under", "again", "more", "most", "less", "least", "can", "could",
    "should", "would", "will", "won't", "dont", "don't", "im", "i'm", "ive", "i've", "it's", "cant", "can't", "didnt",
    "didn't", "wasnt", "wasn't", "isnt", "isn't", "arent", "aren't",
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
    if n >= len(df_in):
        return df_in.copy()
    df2 = df_in.copy()
    if "Star Rating" in df2.columns:
        try:
            rng = random.Random(seed)
            out_idx: List[int] = []
            for _, g in df2.groupby("Star Rating"):
                gidx = list(g.index)
                take = max(1, int(round(n * (len(gidx) / len(df2)))))
                rng.shuffle(gidx)
                out_idx.extend(gidx[:take])
            out_idx = out_idx[:n]
            return df2.loc[out_idx]
        except Exception:
            pass
    return df2.sample(n=n, random_state=seed)




# ---------------------- Universal backbone + routing ----------------------
ROUTER_STOPWORDS = set(STOPWORDS) | {
    "product", "item", "thing", "device", "review", "reviews", "using", "used", "works", "working",
    "one", "two", "first", "really", "super", "pretty", "quite", "much", "lot", "lots", "also",
}

DEFAULT_PRIORITY_DELIGHTERS = [
    "Overall Satisfaction",
    "Ease Of Use",
    "Effective Results",
    "Visible Improvement",
    "Time Saver",
    "Comfort",
    "Value",
    "Reliability",
]
DEFAULT_PRIORITY_DETRACTORS = [
    "Poor Results",
    "Ease Of Use",
    "Reliability Issue",
    "High Cost",
    "Irritation",
    "Battery Problem",
    "High Noise",
    "Cleaning Difficulty",
    "Setup Issue",
    "Connectivity Issue",
    "Safety Concern",
]

UNIVERSAL_ASPECT_BACKBONE = {
    "Overall Satisfaction": {
        "positive_patterns": [
            r"\bbest\b", r"\bfavorite\b", r"\blove(?:d)?\b", r"\bamazing\b", r"\bexcellent\b",
            r"\bhighly recommend\b", r"\bcould not recommend more\b", r"\bwould recommend\b",
            r"\bworth it\b", r"\bvery happy\b", r"\bso happy\b", r"\bglad i bought\b",
            r"\bimpressed\b", r"\bgame changer\b",
        ],
        "negative_patterns": [
            r"\bdo not recommend\b", r"\bwould not recommend\b", r"\bregret\b", r"\bwaste of money\b",
            r"\bvery disappointed\b", r"\bso disappointed\b", r"\breturn(?:ed|ing)?\b",
        ],
        "positive_labels": ["Overall Satisfaction"],
        "negative_labels": ["Overall Dissatisfaction", "Poor Results"],
    },
    "Effective Results": {
        "positive_patterns": [
            r"\bworks? (?:great|well|amazing(?:ly)?|perfectly)\b", r"\bactually works?\b", r"\bdid what (?:it|this) (?:says|promised?)\b",
            r"\bmade a difference\b", r"\bnoticeable difference\b", r"\bnoticeable results?\b",
            r"\beffective\b", r"\bhelped\b", r"\bgot results?\b", r"\bdoes the job\b",
            r"\bimproved?\b", r"\bbetter than\b", r"\bremoved?\b", r"\bcleaned?\b",
        ],
        "negative_patterns": [
            r"\bdidn['’]t work\b", r"\bdoesn['’]t work\b", r"\bnot effective\b", r"\bno results?\b",
            r"\bno difference\b", r"\bunderwhelming\b", r"\bpoor results?\b", r"\bdoes very little\b",
            r"\bexpected more\b", r"\bbarely works?\b",
        ],
        "positive_labels": ["Effective Results", "Performance", "Works Well"],
        "negative_labels": ["Poor Results", "Performance Issue", "Poor Performance"],
    },
    "Visible Improvement": {
        "positive_patterns": [
            r"\bglow(?:ing)?\b", r"\bsmoother\b", r"\bsofter\b", r"\bbrighter\b", r"\bclearer\b",
            r"\bshinier\b", r"\bshine\b", r"\bnoticeable improvement\b", r"\blooks better\b", r"\bhealthier\b",
            r"\bless frizz\b", r"\bwithout frizz\b", r"\bfrizz[- ]?free\b", r"\bsalon look\b", r"\bdead skin gone\b", r"\bskin was glowing\b",
        ],
        "negative_patterns": [],
        "positive_labels": ["Visible Improvement", "Improved Appearance"],
        "negative_labels": [],
    },
    "Ease Of Use": {
        "positive_patterns": [
            r"\beasy to (?:use|clean|attach|remove|assemble|set up|setup|operate)\b", r"\bsimple to use\b",
            r"\buser[- ]?friendly\b", r"\bintuitive\b", r"\bstraightforward\b", r"\beffortless\b",
            r"\bquick to (?:use|set up|setup)\b",
        ],
        "negative_patterns": [
            r"\bhard to (?:use|clean|attach|remove|assemble|set up|setup|operate)\b", r"\bdifficult to (?:use|clean|assemble|set up|setup)\b",
            r"\bconfusing\b", r"\bcomplicated\b", r"\bnot intuitive\b", r"\blearning curve\b",
            r"\bfrustrating\b", r"\bcumbersome\b", r"\binstructions? (?:are )?(?:bad|unclear|confusing)\b",
        ],
        "positive_labels": ["Ease Of Use", "Easy Setup", "Easy To Clean"],
        "negative_labels": ["Ease Of Use", "Difficult To Use", "Setup Issue", "Learning Curve", "Cleaning Difficulty"],
    },
    "Time Saver": {
        "positive_patterns": [
            r"\bsaves? time\b", r"\bquick(?:er)?\b", r"\bfaster\b", r"\bcut my .* time\b", r"\bsped up\b",
            r"\btakes less time\b", r"\bso much quicker\b", r"\bin minutes?\b",
        ],
        "negative_patterns": [r"\btoo slow\b", r"\btakes forever\b", r"\btime consuming\b"],
        "positive_labels": ["Time Saver", "Fast Results"],
        "negative_labels": ["Slow Performance", "Time Consuming"],
    },
    "Comfort": {
        "positive_patterns": [
            r"\bcomfortable\b", r"\bcomfort\b", r"\blightweight\b", r"\bergonomic\b", r"\bfeels good\b",
            r"\beasy on my (?:hand|arms|ears|skin)\b",
        ],
        "negative_patterns": [
            r"\buncomfortable\b", r"\bheavy\b", r"\bawkward\b", r"\bbulky\b", r"\bhurts?\b",
            r"\bsore\b", r"\btiring to hold\b",
        ],
        "positive_labels": ["Comfort"],
        "negative_labels": ["Comfort Issue", "Size Issue"],
    },
    "Value": {
        "positive_patterns": [
            r"\bworth (?:it|the money|every penny)\b", r"\bgood value\b", r"\bgreat value\b", r"\breasonably priced\b", r"\bsaves? me money\b", r"\bwithout spending money\b",
        ],
        "negative_patterns": [
            r"\bexpensive\b", r"\boverpriced\b", r"\btoo pricey\b", r"\btoo expensive\b", r"\bnot worth\b",
            r"\bpricey\b", r"\bcosts? too much\b",
        ],
        "positive_labels": ["Value", "Worth The Price"],
        "negative_labels": ["High Cost", "Value Issue"],
    },
    "Reliability": {
        "positive_patterns": [
            r"\breliable\b", r"\bsturdy\b", r"\bdurable\b", r"\bwell built\b", r"\bholds up\b",
            r"\bstill working\b", r"\bsolid build\b",
        ],
        "negative_patterns": [
            r"\bbroke(?:n)?\b", r"\bstopped working\b", r"\bdefective\b", r"\bfaulty\b", r"\bmalfunction(?:ed|ing)?\b",
            r"\bdead on arrival\b", r"\bleaks?\b", r"\bcracked\b", r"\bnot durable\b", r"\bquit working\b",
            r"\bproduct failure\b",
        ],
        "positive_labels": ["Reliability", "Durability"],
        "negative_labels": ["Reliability Issue", "Product Failure", "Reliability"],
    },
    "Battery": {
        "positive_patterns": [r"\blong battery life\b", r"\bbattery lasts?\b", r"\bholds a charge\b", r"\blasts all day\b"],
        "negative_patterns": [
            r"\bbattery dies?\b", r"\bshort battery life\b", r"\bwon['’]t charge\b", r"\bcharging issue\b",
            r"\bbattery problem\b", r"\bdrains? quickly\b",
        ],
        "positive_labels": ["Battery Life"],
        "negative_labels": ["Battery Problem", "Short Battery Life", "Battery Life"],
    },
    "Noise": {
        "positive_patterns": [r"\bquiet\b", r"\bnot noisy\b", r"\bsurprisingly quiet\b"],
        "negative_patterns": [r"\bloud\b", r"\bnoisy\b", r"\bhigh[- ]pitched\b", r"\bwhin(?:e|y)\b"],
        "positive_labels": ["Quiet Operation"],
        "negative_labels": ["High Noise", "Noise"],
    },
    "Cleaning / Maintenance": {
        "positive_patterns": [r"\beasy to clean\b", r"\blow maintenance\b", r"\bsimple to clean\b"],
        "negative_patterns": [
            r"\bhard to clean\b", r"\bdifficult to clean\b", r"\bmessy\b", r"\bmaintenance hassle\b",
            r"\bclogs?\b", r"\bresidue\b", r"\bwater spill(?:age)?\b",
        ],
        "positive_labels": ["Easy To Clean"],
        "negative_labels": ["Cleaning Difficulty", "Clogged Pores", "Maintenance Issue"],
    },
    "Connectivity": {
        "positive_patterns": [r"\bconnects? quickly\b", r"\bpairs? easily\b", r"\bapp works?\b"],
        "negative_patterns": [
            r"\bwon['’]t connect\b", r"\bpairing issue\b", r"\bapp issue\b", r"\bdisconnects?\b",
            r"\bbluetooth issue\b", r"\bsync issue\b",
        ],
        "positive_labels": ["Connectivity", "App Experience"],
        "negative_labels": ["Connectivity Issue", "App Issue"],
    },
    "Design": {
        "positive_patterns": [r"\bcompact\b", r"\bsleek\b", r"\bwell designed\b", r"\bportable\b", r"\bgreat design\b"],
        "negative_patterns": [r"\bbulky\b", r"\btoo big\b", r"\btoo small\b", r"\bpoor design\b", r"\bflimsy\b"],
        "positive_labels": ["Design", "Compact Design", "Portability"],
        "negative_labels": ["Design Issue", "Size Issue"],
    },
    "Attachments / Versatility": {
        "positive_patterns": [
            r"\battachments?\b.*\b(?:useful|helpful|versatile|handy|great)\b", r"\bversatile\b", r"\bmultiple uses\b", r"\bwet and dry\b", r"\bwet or dry\b", r"\bcan be used on wet and dry\b", r"\bworks? on (?:curly|straight|thick|fine) hair\b",
        ],
        "negative_patterns": [r"\battachment issue\b", r"\bmissing attachment\b", r"\blimited functionality\b"],
        "positive_labels": ["Attachment Usability", "Versatility"],
        "negative_labels": ["Attachment Issue", "Limited Functionality"],
    },
    "Safety": {
        "positive_patterns": [r"\bfeels safe\b", r"\bsafe to use\b", r"\bgentle\b", r"\bno irritation\b"],
        "negative_patterns": [
            r"\bburn(?:ed|s|ing)?\b", r"\birritat(?:ed|es|ing|ion)\b", r"\bhurt\b", r"\bunsafe\b",
            r"\bconcerning\b", r"\btoo hot\b", r"\bheat(?:ing)? issue\b",
        ],
        "positive_labels": ["Safety"],
        "negative_labels": ["Safety Concern", "Irritation", "Excess Heat"],
    },
}

UNIVERSAL_L2_TEMPLATES = {
    "Overall Satisfaction": {
        "Delighter": [
            {"label": "Strong Recommendation", "patterns": [r"\bhighly recommend\b", r"\bcould not recommend more\b", r"\bwould recommend\b", r"\brecommend(?: it)? to anyone\b"], "boost": 0.54},
            {"label": "Favorite Product", "patterns": [r"\bfavorite\b", r"\bbest product\b", r"\bgo[- ]to\b"], "boost": 0.50},
        ],
        "Detractor": [
            {"label": "Would Not Recommend", "patterns": [r"\bdo not recommend\b", r"\bwould not recommend\b", r"\bcannot recommend\b"], "boost": 0.60},
            {"label": "Return Intent", "patterns": [r"\breturn(?:ed|ing)?\b", r"\bsending it back\b", r"\bgot a refund\b"], "boost": 0.56},
            {"label": "Buyer Remorse", "patterns": [r"\bregret(?:ted)?\b", r"\bwish i had(?: not|n't)\b", r"\bwaste of money\b"], "boost": 0.54},
        ],
    },
    "Effective Results": {
        "Delighter": [
            {"label": "Visible Improvement", "patterns": [r"\bglow(?:ing)?\b", r"\bsmoother\b", r"\bclearer\b", r"\bbrighter\b", r"\bnoticeable improvement\b", r"\blooks better\b"], "boost": 0.64},
            {"label": "Fast Results", "patterns": [r"\bafter (?:one|1) use\b", r"\bimmediate(?:ly)?\b", r"\binstant(?:ly)?\b", r"\bright away\b", r"\bquick results?\b"], "boost": 0.58},
            {"label": "Consistent Results", "patterns": [r"\bevery time\b", r"\bconsisten(?:t|cy)\b", r"\breliable results?\b"], "boost": 0.50},
            {"label": "Strong Performance", "patterns": [r"\bpowerful\b", r"\bstrong performance\b", r"\bdoes the job\b", r"\bworks? really well\b"], "boost": 0.48},
        ],
        "Detractor": [
            {"label": "Poor Results", "patterns": [r"\bno results?\b", r"\bno difference\b", r"\bdidn['’]t work\b", r"\bdoesn['’]t work\b", r"\bnot effective\b"], "boost": 0.62},
            {"label": "Slow Results", "patterns": [r"\btakes? too long\b", r"\bslow results?\b", r"\bnot quick\b"], "boost": 0.48},
            {"label": "Inconsistent Results", "patterns": [r"\binconsistent\b", r"\bworks? sometimes\b", r"\bhit or miss\b", r"\bnot always\b"], "boost": 0.54},
            {"label": "Weak Performance", "patterns": [r"\bweak\b", r"\bunderspowered\b", r"\bbarely works?\b"], "boost": 0.44},
        ],
    },
    "Ease Of Use": {
        "Delighter": [
            {"label": "Easy Setup", "patterns": [r"\beasy to set up\b", r"\bsetup was easy\b", r"\bquick setup\b", r"\beasy to assemble\b"], "boost": 0.60},
            {"label": "Intuitive Controls", "patterns": [r"\bintuitive\b", r"\bstraightforward\b", r"\buser[- ]?friendly\b", r"\beasy to navigate\b"], "boost": 0.54},
            {"label": "Easy To Clean", "patterns": [r"\beasy to clean\b", r"\bsimple to clean\b", r"\bcleans up easily\b"], "boost": 0.56},
        ],
        "Detractor": [
            {"label": "Setup Issue", "patterns": [r"\bhard to set up\b", r"\bdifficult to set up\b", r"\bsetup issue\b", r"\bassembly issue\b"], "boost": 0.62},
            {"label": "Confusing Instructions", "patterns": [r"\binstructions? (?:are )?(?:bad|unclear|confusing)\b", r"\bmanual (?:is )?(?:bad|unclear|confusing)\b"], "boost": 0.58},
            {"label": "Learning Curve", "patterns": [r"\blearning curve\b", r"\bnot intuitive\b", r"\bconfusing\b", r"\bcomplicated\b"], "boost": 0.54},
            {"label": "Cleaning Difficulty", "patterns": [r"\bhard to clean\b", r"\bdifficult to clean\b", r"\bannoying to clean\b"], "boost": 0.56},
        ],
    },
    "Time Saver": {
        "Delighter": [
            {"label": "Time Saver", "patterns": [r"\bsaves? time\b", r"\bso much quicker\b", r"\bsped up\b"], "boost": 0.56},
            {"label": "Fast Results", "patterns": [r"\bquick(?:er)?\b", r"\bfaster\b", r"\bworks fast\b"], "boost": 0.48},
        ],
        "Detractor": [
            {"label": "Time Consuming", "patterns": [r"\btime consuming\b", r"\btakes forever\b", r"\btoo slow\b"], "boost": 0.58},
            {"label": "Slow Performance", "patterns": [r"\bslow\b", r"\bnot fast\b"], "boost": 0.46},
        ],
    },
    "Comfort": {
        "Delighter": [
            {"label": "Comfortable Fit", "patterns": [r"\bcomfortable\b", r"\bfits? well\b", r"\bfeels good\b"], "boost": 0.54},
            {"label": "Lightweight Design", "patterns": [r"\blightweight\b", r"\bnot heavy\b", r"\beasy to hold\b"], "boost": 0.48},
        ],
        "Detractor": [
            {"label": "Comfort Issue", "patterns": [r"\buncomfortable\b", r"\bhurts?\b", r"\bsore\b"], "boost": 0.58},
            {"label": "Heavy/Bulky Feel", "patterns": [r"\bheavy\b", r"\bbulky\b", r"\bawkward\b", r"\btiring to hold\b"], "boost": 0.50},
        ],
    },
    "Value": {
        "Delighter": [
            {"label": "Worth The Price", "patterns": [r"\bworth (?:it|the money|every penny)\b", r"\bworth the price\b"], "boost": 0.60},
            {"label": "Good Value", "patterns": [r"\bgood value\b", r"\bgreat value\b", r"\bpriced fairly\b"], "boost": 0.54},
        ],
        "Detractor": [
            {"label": "High Cost", "patterns": [r"\btoo expensive\b", r"\boverpriced\b", r"\bpricey\b", r"\bcosts? too much\b"], "boost": 0.62},
            {"label": "Not Worth The Price", "patterns": [r"\bnot worth\b", r"\bnot worth the price\b", r"\bnot worth the money\b"], "boost": 0.58},
        ],
    },
    "Reliability": {
        "Delighter": [
            {"label": "Durable Build", "patterns": [r"\bdurable\b", r"\bsturdy\b", r"\bwell built\b", r"\bsolid build\b"], "boost": 0.56},
            {"label": "Reliable Performance", "patterns": [r"\breliable\b", r"\bstill working\b", r"\bholds up\b"], "boost": 0.50},
        ],
        "Detractor": [
            {"label": "Product Failure", "patterns": [r"\bstopped working\b", r"\bquit working\b", r"\bproduct failure\b", r"\bbroke(?:n)?\b"], "boost": 0.66},
            {"label": "Defective Unit", "patterns": [r"\bdefective\b", r"\bfaulty\b", r"\bdead on arrival\b"], "boost": 0.62},
            {"label": "Short Lifespan", "patterns": [r"\bafter (?:a few|few) uses?\b", r"\bdidn['’]t last\b", r"\bstopped working after\b"], "boost": 0.54},
            {"label": "Leaking/Spillage", "patterns": [r"\bleaks?\b", r"\bspills?\b", r"\bleaking\b"], "boost": 0.52},
        ],
    },
    "Battery": {
        "Delighter": [
            {"label": "Long Battery Life", "patterns": [r"\blong battery life\b", r"\bbattery lasts?\b", r"\blasts all day\b"], "boost": 0.58},
            {"label": "Fast Charging", "patterns": [r"\bcharges? quickly\b", r"\bfast charging\b"], "boost": 0.50},
        ],
        "Detractor": [
            {"label": "Short Battery Life", "patterns": [r"\bshort battery life\b", r"\bdrains? quickly\b", r"\bbattery dies?\b"], "boost": 0.62},
            {"label": "Charging Issue", "patterns": [r"\bwon['’]t charge\b", r"\bcharging issue\b", r"\bdoesn['’]t charge\b"], "boost": 0.58},
        ],
    },
    "Noise": {
        "Delighter": [
            {"label": "Quiet Operation", "patterns": [r"\bquiet\b", r"\bsurprisingly quiet\b", r"\bnot noisy\b"], "boost": 0.54},
        ],
        "Detractor": [
            {"label": "High Noise", "patterns": [r"\bloud\b", r"\bnoisy\b", r"\bhigh[- ]pitched\b", r"\bwhin(?:e|y)\b"], "boost": 0.62},
        ],
    },
    "Cleaning / Maintenance": {
        "Delighter": [
            {"label": "Easy To Clean", "patterns": [r"\beasy to clean\b", r"\blow maintenance\b", r"\bsimple to clean\b"], "boost": 0.56},
            {"label": "Low Maintenance", "patterns": [r"\blow maintenance\b", r"\bminimal maintenance\b"], "boost": 0.48},
        ],
        "Detractor": [
            {"label": "Cleaning Difficulty", "patterns": [r"\bhard to clean\b", r"\bdifficult to clean\b", r"\bmessy\b"], "boost": 0.60},
            {"label": "Residue Build-Up", "patterns": [r"\bresidue\b", r"\bbuildup\b", r"\bleaves? behind\b"], "boost": 0.46},
            {"label": "Clogging", "patterns": [r"\bclogs?\b", r"\bclogged\b"], "boost": 0.52},
        ],
    },
    "Connectivity": {
        "Delighter": [
            {"label": "Easy Pairing", "patterns": [r"\bpairs? easily\b", r"\bconnects? quickly\b"], "boost": 0.56},
            {"label": "App Experience", "patterns": [r"\bapp works?\b", r"\bapp is easy\b"], "boost": 0.48},
        ],
        "Detractor": [
            {"label": "Pairing Issue", "patterns": [r"\bpairing issue\b", r"\bwon['’]t pair\b", r"\bwon['’]t connect\b"], "boost": 0.62},
            {"label": "App Issue", "patterns": [r"\bapp issue\b", r"\bapp crashes?\b", r"\bapp doesn['’]t work\b"], "boost": 0.58},
            {"label": "Sync Issue", "patterns": [r"\bsync issue\b", r"\bdisconnects?\b", r"\bbluetooth issue\b"], "boost": 0.54},
        ],
    },
    "Design": {
        "Delighter": [
            {"label": "Compact Design", "patterns": [r"\bcompact\b", r"\bdoesn['’]t take up much space\b"], "boost": 0.54},
            {"label": "Portable Design", "patterns": [r"\bportable\b", r"\beasy to travel with\b"], "boost": 0.48},
            {"label": "Premium Design", "patterns": [r"\bwell designed\b", r"\bgreat design\b", r"\bpremium feel\b", r"\bsleek\b"], "boost": 0.46},
        ],
        "Detractor": [
            {"label": "Bulky Design", "patterns": [r"\bbulky\b", r"\btoo big\b", r"\btakes up too much space\b"], "boost": 0.56},
            {"label": "Flimsy Build", "patterns": [r"\bflimsy\b", r"\bcheaply made\b", r"\bpoor design\b"], "boost": 0.52},
            {"label": "Size Issue", "patterns": [r"\btoo small\b", r"\btoo big\b", r"\bwrong size\b"], "boost": 0.48},
        ],
    },
    "Attachments / Versatility": {
        "Delighter": [
            {"label": "Versatile Attachments", "patterns": [r"\battachments?\b.*\b(?:useful|helpful|versatile|great)\b", r"\bmultiple uses\b", r"\bversatile\b"], "boost": 0.56},
            {"label": "Attachment Usability", "patterns": [r"\battachments?\b.*\b(?:easy|simple|handy)\b", r"\beasy to switch\b"], "boost": 0.50},
            {"label": "Multi-Use", "patterns": [r"\bmultiple uses\b", r"\bcan use it for\b"], "boost": 0.46},
        ],
        "Detractor": [
            {"label": "Attachment Issue", "patterns": [r"\battachment issue\b", r"\battachments?\b.*\b(?:don['’]t|do not) fit\b", r"\bhard to attach\b"], "boost": 0.58},
            {"label": "Missing Attachment", "patterns": [r"\bmissing attachment\b", r"\bdidn['’]t come with\b"], "boost": 0.54},
            {"label": "Limited Functionality", "patterns": [r"\blimited functionality\b", r"\bnot versatile\b", r"\bonly does one thing\b"], "boost": 0.52},
        ],
    },
    "Safety": {
        "Delighter": [
            {"label": "Safe To Use", "patterns": [r"\bsafe to use\b", r"\bfeels safe\b", r"\bgentle\b", r"\bno irritation\b"], "boost": 0.54},
        ],
        "Detractor": [
            {"label": "Irritation", "patterns": [r"\birritat(?:ed|es|ing|ion)\b", r"\bredness\b", r"\brash\b"], "boost": 0.60},
            {"label": "Excess Heat", "patterns": [r"\btoo hot\b", r"\bburn(?:ed|s|ing)?\b", r"\bheat issue\b"], "boost": 0.58},
            {"label": "Safety Concern", "patterns": [r"\bunsafe\b", r"\bconcerning\b", r"\bworried about safety\b"], "boost": 0.54},
        ],
    },
}



def _compile_backbone_rules() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for aspect, spec in UNIVERSAL_ASPECT_BACKBONE.items():
        out[aspect] = dict(spec)
        out[aspect]["positive_patterns"] = [re.compile(p, re.I) for p in spec.get("positive_patterns", [])]
        out[aspect]["negative_patterns"] = [re.compile(p, re.I) for p in spec.get("negative_patterns", [])]
    return out


UNIVERSAL_ASPECT_BACKBONE_COMPILED = _compile_backbone_rules()


def _dedupe_keep_order_str(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for v in values:
        s = str(v or "").strip()
        if not s:
            continue
        k = _canon_simple(s)
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def _compile_l2_templates() -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    out: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for family, by_side in UNIVERSAL_L2_TEMPLATES.items():
        out[family] = {}
        for side, specs in by_side.items():
            compiled_specs: List[Dict[str, Any]] = []
            for spec in specs:
                spec2 = dict(spec)
                spec2["patterns"] = [re.compile(p, re.I) for p in spec.get("patterns", [])]
                compiled_specs.append(spec2)
            out[family][side] = compiled_specs
    return out


UNIVERSAL_L2_TEMPLATES_COMPILED = _compile_l2_templates()

FAMILY_TOKEN_HINTS = {
    "Overall Satisfaction": ["satisfaction", "recommend", "favorite", "best", "happy", "impressed", "regret", "disappointed", "return"],
    "Effective Results": ["result", "results", "performance", "effective", "works", "working", "improvement", "difference", "quality", "output", "glow", "smooth", "smoother", "shine", "frizz", "curly", "curl", "salon"],
    "Ease Of Use": ["easy", "setup", "clean", "instructions", "intuitive", "simple", "user", "friendly", "complicated"],
    "Time Saver": ["time", "quick", "quicker", "fast", "faster", "slow", "forever", "minutes"],
    "Comfort": ["comfort", "comfortable", "lightweight", "ergonomic", "heavy", "bulky", "awkward", "fit"],
    "Value": ["value", "worth", "price", "priced", "cost", "expensive", "overpriced", "pricey", "money", "affordable"],
    "Reliability": ["reliable", "durable", "sturdy", "broke", "broken", "defective", "faulty", "failure", "leak", "lasting"],
    "Battery": ["battery", "charge", "charging", "runtime", "power"],
    "Noise": ["noise", "noisy", "loud", "quiet", "whine"],
    "Cleaning / Maintenance": ["clean", "cleaning", "maintenance", "messy", "clog", "residue", "buildup"],
    "Connectivity": ["connect", "connected", "pair", "pairing", "sync", "app", "bluetooth", "wifi"],
    "Design": ["design", "compact", "portable", "size", "bulky", "flimsy", "sleek", "premium"],
    "Attachments / Versatility": ["attachment", "attachments", "accessory", "versatile", "multi", "tool", "nozzle", "wet", "dry", "curly", "straight"],
    "Safety": ["safe", "safety", "burn", "irritation", "rash", "hot", "heat", "gentle"],
}


def _build_family_seed_labels() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for family, spec in UNIVERSAL_ASPECT_BACKBONE.items():
        labels: List[str] = [family]
        labels.extend(list(spec.get("positive_labels", []) or []))
        labels.extend(list(spec.get("negative_labels", []) or []))
        for side in ("Delighter", "Detractor"):
            for tmpl in (UNIVERSAL_L2_TEMPLATES.get(family, {}) or {}).get(side, []) or []:
                labels.append(str(tmpl.get("label", "") or ""))
        out[family] = _dedupe_keep_order_str([normalize_theme_label(x) for x in labels if str(x).strip()])
    return out


FAMILY_SEED_LABELS = _build_family_seed_labels()


def _infer_l1_family_from_label(label: str, side: str = "") -> str:
    raw = normalize_theme_label(label, side)
    if not raw:
        return ""
    raw_key = _canon_simple(raw)
    raw_tokens = set(_label_tokens(raw))

    for family, seeds in FAMILY_SEED_LABELS.items():
        seed_keys = {_canon_simple(family)} | {_canon_simple(s) for s in seeds}
        if raw_key in seed_keys:
            return family

    best_family = ""
    best_score = 0.0
    for family, seeds in FAMILY_SEED_LABELS.items():
        score = 0.0
        family_tokens = set(_label_tokens(family))
        hint_tokens = set(FAMILY_TOKEN_HINTS.get(family, []))
        seed_tokens: Set[str] = set()
        for s in seeds[:16]:
            seed_tokens |= set(_label_tokens(s))
        if raw_tokens & family_tokens:
            score += 1.25 * len(raw_tokens & family_tokens)
        if raw_tokens & hint_tokens:
            score += 0.85 * len(raw_tokens & hint_tokens)
        if raw_tokens & seed_tokens:
            score += 0.55 * len(raw_tokens & seed_tokens)
        try:
            close = max([difflib.SequenceMatcher(None, _canon(raw), _canon(s)).ratio() for s in ([family] + seeds[:12])])
        except Exception:
            close = 0.0
        score += 0.9 * close
        if score > best_score:
            best_score = score
            best_family = family
    if best_score >= 1.75:
        return best_family
    return ""


def _get_label_family(label: str, side: str, learned_store: Optional[Dict[str, Any]] = None) -> str:
    side_norm = "Delighter" if str(side).lower().startswith("del") else "Detractor"
    cache = st.session_state.setdefault("_label_family_cache", {})
    key = (side_norm, _canon_simple(label))
    if key in cache:
        return str(cache.get(key) or "")
    ls = learned_store or _ensure_learned_store()
    fam = str(((ls.get("families", {}) or {}).get(side_norm, {}) or {}).get(label, "") or "")
    if not fam:
        fam = _infer_l1_family_from_label(label, side_norm)
    cache[key] = fam
    if fam:
        ls.setdefault("families", {}).setdefault(side_norm, {})[label] = fam
    return fam


def _group_labels_by_family(side: str, labels: Iterable[str], learned_store: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for lab in labels:
        fam = _get_label_family(str(lab), side, learned_store) or "Other"
        out.setdefault(fam, [])
        if str(lab) not in out[fam]:
            out[fam].append(str(lab))
    return out


def _route_l2_template_candidates(
    review_s: str,
    side: str,
    allowed_labels: List[str],
    matched_families: Set[str],
    max_ev_chars: int = 120,
) -> Tuple[Dict[str, float], Dict[str, List[str]], List[str]]:
    scores: Dict[str, float] = {}
    ev_map: Dict[str, List[str]] = {}
    raw_labels: List[str] = []
    if not bool(st.session_state.get("enable_deep_l2_routing", True)):
        return scores, ev_map, raw_labels

    specificity_bias = float(st.session_state.get("l2_specificity_bias", 0.16) or 0.16)
    max_per_family = int(st.session_state.get("l2_candidate_top_n", 3) or 3)

    for family in matched_families:
        picked_in_family = 0
        for spec in (UNIVERSAL_L2_TEMPLATES_COMPILED.get(family, {}) or {}).get(side, []) or []:
            hits = _regex_snippets(review_s, spec.get("patterns", []) or [], max_hits=2, max_chars=max_ev_chars)
            if not hits:
                continue
            candidate_names = [str(spec.get("label", "") or "")] + list(spec.get("aliases", []) or [])
            resolved = _resolve_available_label(candidate_names, allowed_labels, side)
            if not resolved:
                continue
            score = float(spec.get("boost", 0.52) or 0.52) + specificity_bias + (0.06 * len(hits))
            if resolved not in scores or score > scores[resolved]:
                scores[resolved] = score
            ev_map.setdefault(resolved, [])
            ev_map[resolved] = _dedupe_keep_order_str(ev_map[resolved] + hits)[:2]
            raw_labels.append(resolved)
            picked_in_family += 1
            if picked_in_family >= max_per_family:
                break
    return scores, ev_map, _dedupe_keep_order_str(raw_labels)


def _parse_label_textarea(text_in: Any) -> List[str]:
    s = str(text_in or "")
    raw = re.split(r"[\n,;|]+", s)
    vals = [normalize_theme_label(x.strip()) for x in raw if str(x).strip()]
    return _dedupe_keep_order_str(vals)


def _provided_catalog_labels(side: str) -> List[str]:
    side_norm = "Delighter" if str(side).lower().startswith("del") else "Detractor"
    vals = DELIGHTERS if side_norm == "Delighter" else DETRACTORS
    return [str(x).strip() for x in (vals or []) if str(x).strip()]


def _provided_catalog_keyset(side: str) -> Set[str]:
    return {_canon_simple(x) for x in _provided_catalog_labels(side)}


def _label_phrase_parts(label: str) -> List[str]:
    raw_parts = re.split(r"\s*-\s*|[/|,&]+", str(label or ""))
    parts: List[str] = []
    for p in raw_parts:
        ps = str(p or "").strip()
        if not ps:
            continue
        if len(ps) < 4:
            continue
        if _canon_simple(ps) == _canon_simple(label):
            continue
        parts.append(ps)
    return _dedupe_keep_order_str(parts)


def _priority_theme_labels(side: str) -> List[str]:
    side_norm = "Delighter" if str(side).lower().startswith("del") else "Detractor"
    defaults = DEFAULT_PRIORITY_DELIGHTERS if side_norm == "Delighter" else DEFAULT_PRIORITY_DETRACTORS
    key = "priority_delighters_text" if side_norm == "Delighter" else "priority_detractors_text"
    custom = _parse_label_textarea(st.session_state.get(key, ""))
    if not custom:
        custom = list(defaults)
    return _dedupe_keep_order_str(list(defaults) + custom)


def _route_config_signature() -> str:
    payload = {
        "universal": bool(st.session_state.get("use_universal_l1_backbone", True)),
        "recall": bool(st.session_state.get("high_recall_labeling", True)),
        "deep_l2": bool(st.session_state.get("enable_deep_l2_routing", True)),
        "top_n": int(st.session_state.get("router_candidate_top_n", 6) or 6),
        "l2_top_n": int(st.session_state.get("l2_candidate_top_n", 3) or 3),
        "threshold": float(st.session_state.get("router_rescue_threshold", 0.95) or 0.95),
        "l2_threshold": float(st.session_state.get("l2_rescue_threshold", 1.08) or 1.08),
        "family_boost": float(st.session_state.get("router_family_boost", 0.38) or 0.38),
        "l2_bias": float(st.session_state.get("l2_specificity_bias", 0.16) or 0.16),
        "pd": _priority_theme_labels("Delighter"),
        "pt": _priority_theme_labels("Detractor"),
    }
    try:
        return hashlib.md5(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    except Exception:
        return str(payload)


def _label_tokens(label: str) -> List[str]:
    toks = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9+/']*", str(label or ""))]
    return [t for t in toks if t not in ROUTER_STOPWORDS and len(t) >= 3]


def _phrase_snippets(text_in: str, phrases: Iterable[str], max_hits: int = 2, max_chars: int = 120) -> List[str]:
    text_s = str(text_in or "")
    out: List[str] = []
    seen: Set[str] = set()
    for ph in phrases:
        phs = str(ph or "").strip()
        if not phs:
            continue
        try:
            rx = re.compile(r"(?<!\w)" + re.escape(phs) + r"(?!\w)", re.I)
        except Exception:
            continue
        for m in rx.finditer(text_s):
            hit = text_s[m.start():m.end()][:max_chars].strip()
            if hit and hit.lower() not in seen:
                seen.add(hit.lower())
                out.append(hit)
                if len(out) >= max_hits:
                    return out
    return out


def _regex_snippets(text_in: str, patterns: Iterable[Any], max_hits: int = 2, max_chars: int = 120) -> List[str]:
    text_s = str(text_in or "")
    out: List[str] = []
    seen: Set[str] = set()
    for p in patterns:
        rx = p if hasattr(p, "finditer") else re.compile(str(p), re.I)
        for m in rx.finditer(text_s):
            hit = text_s[m.start():m.end()][:max_chars].strip(" .,;:-")
            if hit and hit.lower() not in seen:
                seen.add(hit.lower())
                out.append(hit[:max_chars])
                if len(out) >= max_hits:
                    return out
    return out


def _keyword_snippets(text_in: str, keywords: Iterable[str], max_hits: int = 2, max_chars: int = 120) -> List[str]:
    text_s = str(text_in or "")
    out: List[str] = []
    seen: Set[str] = set()
    for kw in keywords:
        k = str(kw or "").strip().lower()
        if not k:
            continue
        if (len(k) < 4) and (" " not in k):
            continue
        try:
            if " " in k:
                rx = re.compile(re.escape(k), re.I)
            else:
                rx = re.compile(r"(?<!\w)" + re.escape(k) + r"(?!\w)", re.I)
        except Exception:
            continue
        for m in rx.finditer(text_s):
            hit = text_s[m.start():m.end()][:max_chars].strip()
            if hit and hit.lower() not in seen:
                seen.add(hit.lower())
                out.append(hit)
                if len(out) >= max_hits:
                    return out
    return out


def _resolve_available_label(candidate_names: List[str], allowed_labels: List[str], side: str) -> Optional[str]:
    clean_allowed = [str(x).strip() for x in (allowed_labels or []) if str(x).strip()]
    if not clean_allowed:
        return candidate_names[0] if bool(st.session_state.get("use_universal_l1_backbone", True)) and candidate_names else None

    exact = {_canon_simple(x): x for x in clean_allowed}
    for cand in candidate_names:
        c = str(cand or "").strip()
        if not c:
            continue
        k = _canon_simple(c)
        if k in exact:
            return exact[k]
        norm = normalize_theme_label(c, side)
        kn = _canon_simple(norm)
        if kn in exact:
            return exact[kn]
        tgt = ALIAS_TO_LABEL.get(_canon(c))
        if tgt and _canon_simple(tgt) in exact:
            return exact[_canon_simple(tgt)]
    cutoff = max(0.84, float(st.session_state.get("sim_threshold_lex", 0.94)) - 0.06)
    for cand in candidate_names:
        c = normalize_theme_label(cand, side)
        m = difflib.get_close_matches(c, clean_allowed, n=1, cutoff=cutoff)
        if m:
            return m[0]
    if bool(st.session_state.get("use_universal_l1_backbone", True)) and candidate_names:
        return normalize_theme_label(candidate_names[0], side)
    return None


def _coerce_label_to_allowed(raw_label: str, allowed_labels: List[str], side: str) -> Optional[str]:
    raw = str(raw_label or "").strip()
    if not raw:
        return None
    exact = {_canon_simple(x): x for x in allowed_labels}
    for cand in [raw, normalize_theme_label(raw, side)]:
        if _canon_simple(cand) in exact:
            return exact[_canon_simple(cand)]
        tgt = ALIAS_TO_LABEL.get(_canon(cand))
        if tgt and _canon_simple(tgt) in exact:
            return exact[_canon_simple(tgt)]
    cutoff = max(0.84, float(st.session_state.get("sim_threshold_lex", 0.94)) - 0.06)
    candidates = difflib.get_close_matches(normalize_theme_label(raw, side), list(allowed_labels), n=1, cutoff=cutoff)
    if candidates:
        return candidates[0]
    raw_tokens = set(_label_tokens(raw))
    if raw_tokens:
        best = None
        best_score = 0.0
        for lab in allowed_labels:
            toks = set(_label_tokens(lab))
            if not toks:
                continue
            inter = len(raw_tokens & toks)
            if inter <= 0:
                continue
            score = inter / max(1, len(raw_tokens | toks))
            if score > best_score:
                best_score = score
                best = lab
        if best and best_score >= 0.5:
            return best
    return None


def _score_existing_label(
    review: str,
    label: str,
    side: str,
    learned_store: Dict[str, Any],
    matched_families: Optional[Set[str]] = None,
    max_ev_chars: int = 120,
) -> Tuple[float, List[str]]:
    score = 0.0
    evs: List[str] = []
    catalog_keys = _provided_catalog_keyset(side)
    label_key = _canon_simple(label)
    if label in _priority_theme_labels(side):
        score += 0.08
    if label_key in catalog_keys:
        score += 0.12

    family = _get_label_family(label, side, learned_store)
    if family and matched_families and family in matched_families:
        score += float(st.session_state.get("router_family_boost", 0.38) or 0.38)

    label_evs = _phrase_snippets(review, [label], max_hits=2, max_chars=max_ev_chars)
    if label_evs:
        score += 1.00
        evs.extend(label_evs)

    part_hits = _phrase_snippets(review, _label_phrase_parts(label)[:4], max_hits=2, max_chars=max_ev_chars)
    if part_hits:
        score += 0.28 + (0.06 * min(2, len(part_hits)))
        evs.extend(part_hits)

    for alias in (ALIASES.get(label, []) or [])[:12]:
        hit = _phrase_snippets(review, [alias], max_hits=2, max_chars=max_ev_chars)
        if hit:
            score += 0.85
            evs.extend(hit)
            break

    kw_pool = list((learned_store.get("keywords", {}).get(side, {}).get(label, set()) or set()))[:12]
    kw_hits = _keyword_snippets(review, kw_pool, max_hits=3, max_chars=max_ev_chars)
    if kw_hits:
        score += 0.34 * min(3, len(kw_hits))
        if family and matched_families and family in matched_families:
            score += 0.14
        evs.extend(kw_hits)

    rtoks = set(re.findall(r"[A-Za-z][A-Za-z0-9+/']*", str(review or "").lower()))
    ltoks = [t for t in _label_tokens(label) if t not in ROUTER_STOPWORDS]
    overlap = [t for t in ltoks if t in rtoks]
    if len(overlap) >= 2:
        score += 0.42 + (0.08 * min(3, len(overlap)))
        evs.extend(_phrase_snippets(review, overlap[:2], max_hits=2, max_chars=max_ev_chars))
        if label_key in catalog_keys:
            score += 0.12
    elif len(overlap) == 1 and len(ltoks) >= 1 and len(overlap[0]) >= 5:
        score += 0.22
        evs.extend(_phrase_snippets(review, overlap[:1], max_hits=1, max_chars=max_ev_chars))

    if family:
        fam_kw_hits = _keyword_snippets(review, list(FAMILY_TOKEN_HINTS.get(family, []))[:12], max_hits=2, max_chars=max_ev_chars)
        if fam_kw_hits and label_key in catalog_keys:
            score += 0.12 * min(2, len(fam_kw_hits))
            evs.extend(fam_kw_hits)

    if family and matched_families and family in matched_families:
        is_specific_l2 = _canon_simple(label) != _canon_simple(family)
        if is_specific_l2:
            score += float(st.session_state.get("l2_specificity_bias", 0.16) or 0.16)
        family_hits = _regex_snippets(
            review,
            (UNIVERSAL_ASPECT_BACKBONE_COMPILED.get(family, {}) or {}).get(
                "positive_patterns" if str(side).lower().startswith("del") else "negative_patterns", []
            ) or [],
            max_hits=2,
            max_chars=max_ev_chars,
        )
        if family_hits:
            evs.extend(family_hits)
            score += 0.10 * len(family_hits)

    return (score, _dedupe_keep_order_str(evs)[:2])

def _route_review_candidates(
    review: str,
    allowed_delighters: List[str],
    allowed_detractors: List[str],
    learned_store: Dict[str, Any],
    top_n: int = 6,
    max_ev_chars: int = 120,
) -> Dict[str, Any]:
    review_s = str(review or "").strip()
    out = {
        "dels": [],
        "dets": [],
        "ev_del_map": {},
        "ev_det_map": {},
        "del_scores": {},
        "det_scores": {},
        "matched_del_families": [],
        "matched_det_families": [],
        "l2_dels": [],
        "l2_dets": [],
    }
    if not review_s:
        return out

    side_cfg = {
        "Delighter": {
            "allowed": allowed_delighters,
            "labels_key": "positive_labels",
            "patterns_key": "positive_patterns",
            "out_labels": "dels",
            "out_ev": "ev_del_map",
            "out_scores": "del_scores",
            "out_families": "matched_del_families",
            "out_l2": "l2_dels",
        },
        "Detractor": {
            "allowed": allowed_detractors,
            "labels_key": "negative_labels",
            "patterns_key": "negative_patterns",
            "out_labels": "dets",
            "out_ev": "ev_det_map",
            "out_scores": "det_scores",
            "out_families": "matched_det_families",
            "out_l2": "l2_dets",
        },
    }

    for side, cfg in side_cfg.items():
        allowed = list(cfg["allowed"] or [])
        scores: Dict[str, float] = {}
        ev_map: Dict[str, List[str]] = {}
        priority = set(_priority_theme_labels(side))
        provided_catalog_keys = _provided_catalog_keyset(side)

        aspect_hits: Dict[str, List[str]] = {}
        for aspect, spec in UNIVERSAL_ASPECT_BACKBONE_COMPILED.items():
            pats = spec.get(cfg["patterns_key"], []) or []
            hits = _regex_snippets(review_s, pats, max_hits=2, max_chars=max_ev_chars)
            if not hits:
                continue
            aspect_hits[aspect] = hits
            names = list(spec.get(cfg["labels_key"], []) or []) + [aspect]
            resolved = _resolve_available_label(names, allowed, side)
            if not resolved:
                continue
            base = 1.15 if resolved in priority else 0.95
            if _canon_simple(resolved) in provided_catalog_keys:
                base += 0.18
            scores[resolved] = scores.get(resolved, 0.0) + base + (0.10 * len(hits))
            ev_map.setdefault(resolved, [])
            ev_map[resolved] = _dedupe_keep_order_str(ev_map[resolved] + hits)[:2]

        matched_families = set(aspect_hits.keys())
        out[cfg["out_families"]] = sorted(matched_families)

        l2_scores, l2_ev_map, l2_raw_labels = _route_l2_template_candidates(
            review_s,
            side=side,
            allowed_labels=allowed,
            matched_families=matched_families,
            max_ev_chars=max_ev_chars,
        )
        out[cfg["out_l2"]] = list(l2_raw_labels)[: max(1, int(st.session_state.get("l2_candidate_top_n", 3) or 3) * 3)]
        for lab, sc in l2_scores.items():
            sc2 = float(sc) + (0.12 if _canon_simple(lab) in provided_catalog_keys else 0.0)
            scores[lab] = max(scores.get(lab, 0.0), sc2)
        for lab, evs in l2_ev_map.items():
            ev_map.setdefault(lab, [])
            ev_map[lab] = _dedupe_keep_order_str(ev_map[lab] + list(evs or []))[:2]

        if side == "Delighter" and aspect_hits.get("Visible Improvement"):
            eff_label = _resolve_available_label(["Effective Results", "Performance", "Works Well"], allowed, side)
            if eff_label:
                eff_hits = list(aspect_hits.get("Visible Improvement", []))
                scores[eff_label] = max(scores.get(eff_label, 0.0), 0.92 + (0.08 * len(eff_hits)))
                if eff_hits:
                    ev_map.setdefault(eff_label, [])
                    ev_map[eff_label] = _dedupe_keep_order_str(ev_map[eff_label] + eff_hits)[:2]
                matched_families.add("Effective Results")
                out[cfg["out_families"]] = sorted(matched_families)

        pool = sorted(list(allowed), key=lambda lab: (0 if _canon_simple(lab) in provided_catalog_keys else 1, str(lab)))
        for lab in pool:
            sc, evs = _score_existing_label(
                review_s,
                lab,
                side,
                learned_store,
                matched_families=matched_families,
                max_ev_chars=max_ev_chars,
            )
            if sc <= 0:
                continue
            if _canon_simple(lab) in provided_catalog_keys:
                sc += 0.12
            family = _get_label_family(lab, side, learned_store)
            is_specific_l2 = bool(family) and (_canon_simple(lab) != _canon_simple(family))
            if sc >= 0.35 or lab in priority or (is_specific_l2 and family in matched_families):
                scores[lab] = max(scores.get(lab, 0.0), sc if lab not in scores else scores[lab] + (0.22 * sc))
                if evs:
                    ev_map.setdefault(lab, [])
                    ev_map[lab] = _dedupe_keep_order_str(ev_map[lab] + evs)[:2]

        ordered = sorted(scores.items(), key=lambda kv: (0 if _canon_simple(kv[0]) in provided_catalog_keys else 1, -kv[1], kv[0]))
        picked: List[str] = []
        picked_specific: List[str] = []
        for lab, sc in ordered:
            family = _get_label_family(lab, side, learned_store)
            is_specific_l2 = bool(family) and (_canon_simple(lab) != _canon_simple(family))
            is_catalog = _canon_simple(lab) in provided_catalog_keys
            min_score = 0.45 if lab not in priority else 0.30
            if is_catalog and ev_map.get(lab):
                min_score = min(min_score, 0.28 if not is_specific_l2 else 0.32)
            elif is_catalog:
                min_score = min(min_score, 0.32 if not is_specific_l2 else 0.36)
            if is_specific_l2 and family in matched_families:
                min_score = min(min_score, 0.40)
            if sc < min_score:
                continue
            picked.append(lab)
            if is_specific_l2:
                picked_specific.append(lab)
            if len(picked) >= int(top_n):
                break

        out[cfg["out_labels"]] = picked
        out[cfg["out_ev"]] = {lab: ev_map.get(lab, [])[:2] for lab in picked if ev_map.get(lab)}
        out[cfg["out_scores"]] = {lab: float(scores.get(lab, 0.0)) for lab in picked}
        out[cfg["out_l2"]] = _dedupe_keep_order_str((out.get(cfg["out_l2"], []) or []) + picked_specific)[: max(1, int(st.session_state.get("l2_candidate_top_n", 3) or 3) * 3)]

    return out

def _apply_router_rescue(
    norm: Dict[str, Any],
    router: Dict[str, Any],
    needs_del: bool,
    needs_det: bool,
    require_evidence_flag: bool,
    max_ev_per_label: int,
) -> Dict[str, Any]:
    base_threshold = float(st.session_state.get("router_rescue_threshold", 0.95) or 0.95)
    l2_threshold = float(st.session_state.get("l2_rescue_threshold", max(base_threshold + 0.10, 1.05)) or max(base_threshold + 0.10, 1.05))

    def _merge(side_key: str, ev_key: str, route_key: str, route_ev_key: str, score_key: str, needs_side: bool, side_name: str) -> None:
        if not needs_side:
            return
        labs = list(norm.get(side_key, []) or [])
        ev_map = dict(norm.get(ev_key, {}) or {})
        route_labs = list(router.get(route_key, []) or [])
        route_evs = dict(router.get(route_ev_key, {}) or {})
        scores = dict(router.get(score_key, {}) or {})

        for lab in route_labs:
            sc = float(scores.get(lab, 0.0) or 0.0)
            evs = list(route_evs.get(lab, []) or [])[:max_ev_per_label]
            family = _get_label_family(lab, side_name, _ensure_learned_store())
            is_specific_l2 = bool(family) and (_canon_simple(lab) != _canon_simple(family))
            is_catalog = _canon_simple(lab) in _provided_catalog_keyset(side_name)
            required_score = l2_threshold if is_specific_l2 else base_threshold
            if is_catalog:
                required_score = min(required_score, max(0.62, base_threshold - (0.14 if is_specific_l2 else 0.22)))
            if lab in labs:
                if evs and not ev_map.get(lab):
                    ev_map[lab] = evs
                continue
            if sc < required_score:
                continue
            if require_evidence_flag and not evs:
                continue
            labs.append(lab)
            if evs:
                ev_map[lab] = evs
            if len(labs) >= 10:
                break

        norm[side_key] = labs[:10]
        norm[ev_key] = {k: list(v)[:max_ev_per_label] for k, v in ev_map.items() if k in norm[side_key]}

    _merge("dels", "ev_del_map", "dels", "ev_del_map", "del_scores", needs_del, "Delighter")
    _merge("dets", "ev_det_map", "dets", "ev_det_map", "det_scores", needs_det, "Detractor")
    return norm

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
            return json.loads(s[i:j + 1])
    except Exception:
        return {}
    return {}


def _chat_json_with_retries(
    client: Any,
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
    last_err: Optional[Exception] = None
    for a in range(1, attempts + 1):
        try:
            kwargs: Dict[str, Any] = {
                "model": model,
                "temperature": float(temperature),
                "messages": messages,
            }
            if response_format:
                kwargs["response_format"] = response_format
            est = sum(_estimate_tokens(m.get("content", ""), model_id=model) for m in (messages or []))
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
    client: Any,
    model: str,
    temperature: float,
    known_themes: Dict[str, List[str]],
    max_themes: int = 30,
) -> Dict[str, Any]:
    if client is None:
        return {"detractors": [], "delighters": [], "product_profile": "", "product_category": "", "product_parts": [], "use_cases": []}

    l1_options = list(UNIVERSAL_ASPECT_BACKBONE.keys())
    sys = "\n".join([
        "You analyze consumer product reviews to extract CONSISTENT, reusable symptom themes across ANY product category.",
        "Return STRICT JSON with schema:",
        '{"product_profile":"<1-2 sentence product summary>", "product_category":"<short category>", "product_parts":["part1","part2"], "use_cases":["use1","use2"], "detractors":[{"label":"<2-4 words Title Case>","parent_l1":"<one L1 aspect>","keywords":["k1","k2","k3"]}], "delighters":[{"label":"<2-4 words Title Case>","parent_l1":"<one L1 aspect>","keywords":["k1","k2","k3"]}]}',
        "",
        "Rules:",
        "- Labels must be mutually exclusive, reusable, and not too tiny or one-off.",
        "- Prefer a broad L1 + reusable L2 style theme over very narrow phrasing.",
        "- Reuse an existing theme exactly if it already exists.",
        "- Use singular nouns when possible (Issue, Problem, Failure).",
        f"- parent_l1 must be one of: {', '.join(l1_options)}",
        f"- Return at most {max_themes // 2} detractors and {max_themes // 2} delighters.",
        "- Keywords should be short and literal (1-2 words), lowercase.",
        "- product_parts should be concrete parts / components if obvious.",
        "- use_cases should be broad reusable jobs-to-be-done.",
    ])

    payload = {
        "reviews": texts[:40],
        "known_detractor_themes": known_themes.get("Detractor", [])[:60],
        "known_delighter_themes": known_themes.get("Delighter", [])[:60],
        "priority_delighter_backbone": _priority_theme_labels("Delighter")[:20],
        "priority_detractor_backbone": _priority_theme_labels("Detractor")[:20],
        "available_l1_aspects": l1_options,
    }

    data = _chat_json_with_retries(
        client,
        model=model,
        temperature=float(temperature),
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": json.dumps(payload)}],
        component="prelearn-mine",
        response_format={"type": "json_object"},
    )
    return {
        "product_profile": str(data.get("product_profile", "") or "").strip(),
        "product_category": str(data.get("product_category", "") or "").strip(),
        "product_parts": [str(x).strip() for x in (data.get("product_parts", []) or []) if str(x).strip()][:16],
        "use_cases": [str(x).strip() for x in (data.get("use_cases", []) or []) if str(x).strip()][:16],
        "detractors": data.get("detractors", []) or [],
        "delighters": data.get("delighters", []) or [],
    }

def _merge_theme_dict(themes: Dict[str, Dict[str, Any]], side: str, canonical: str, keywords: List[str], count_inc: int = 1) -> None:
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
    client: Any,
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
        for b in labels_sorted[i + 1:]:
            if b not in themes[side] or b == a:
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
    client: Any,
    prelearn_model: str,
    temperature: float,
    embed_model: str,
    sample_n: int,
    batch_size: int,
    sem_merge_threshold: float,
    status_box: Any,
    prog_bar: Any,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    learned = _ensure_learned_store()

    status_box.markdown("🔎 **Prelearn:** sampling reviews…")
    df_s = _sample_reviews(df_in, n=int(sample_n))
    reviews = [str(x) for x in df_s["Verbatim"].tolist() if str(x).strip()]
    prog_bar.progress(0.05)

    status_box.markdown("🧠 **Prelearn:** building quick product glossary…")
    learned["glossary_terms"] = _top_terms(reviews, top_n=40)
    prog_bar.progress(0.12)

    themes: Dict[str, Dict[str, Any]] = {"Delighter": {}, "Detractor": {}}
    family_votes: Dict[str, Dict[str, Dict[str, int]]] = {"Delighter": {}, "Detractor": {}}
    profiles: List[str] = []
    categories: List[str] = []
    parts_accum: List[str] = []
    use_cases_accum: List[str] = []
    n = len(reviews)
    if n == 0:
        status_box.markdown("⚠️ **Prelearn:** no reviews found.")
        prog_bar.progress(1.0)
        return learned

    batches = [reviews[i:i + int(batch_size)] for i in range(0, n, int(batch_size))]
    total_batches = len(batches)
    status_box.markdown(f"🤖 **Prelearn:** mining themes with LLM… ({total_batches} batches)")
    start_batch_time = time.perf_counter()

    def _note_family(side_name: str, label_name: str, family_name: str) -> None:
        fam = str(family_name or "").strip()
        if fam not in UNIVERSAL_ASPECT_BACKBONE:
            fam = _infer_l1_family_from_label(label_name, side_name)
        if not fam:
            return
        slot = family_votes.setdefault(side_name, {}).setdefault(label_name, {})
        slot[fam] = int(slot.get(fam, 0)) + 1

    for bi, chunk in enumerate(batches, start=1):
        elapsed = time.perf_counter() - start_batch_time
        avg = (elapsed / max(1, bi - 1)) if bi > 1 else 0.0
        rem = (total_batches - bi + 1) * avg
        status_box.markdown(
            f"🤖 **Prelearn:** batch {bi}/{total_batches} • reviews {((bi - 1) * batch_size) + 1}-{min(bi * batch_size, n)} of {n} • ETA ~ {_fmt_secs(rem)}"
        )
        known = {"Delighter": list(themes["Delighter"].keys()), "Detractor": list(themes["Detractor"].keys())}
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
        if data.get("product_category"):
            categories.append(str(data["product_category"]))
        parts_accum.extend([str(x).strip() for x in (data.get("product_parts", []) or []) if str(x).strip()])
        use_cases_accum.extend([str(x).strip() for x in (data.get("use_cases", []) or []) if str(x).strip()])

        for obj in data.get("delighters", []) or []:
            lab = normalize_theme_label(obj.get("label", ""), "Delighter")
            kws = obj.get("keywords", []) or []
            if not lab:
                continue
            _merge_theme_dict(themes, "Delighter", lab, kws, count_inc=1)
            _note_family("Delighter", lab, str(obj.get("parent_l1", "") or ""))
        for obj in data.get("detractors", []) or []:
            lab = normalize_theme_label(obj.get("label", ""), "Detractor")
            kws = obj.get("keywords", []) or []
            if not lab:
                continue
            _merge_theme_dict(themes, "Detractor", lab, kws, count_inc=1)
            _note_family("Detractor", lab, str(obj.get("parent_l1", "") or ""))

        prog_bar.progress(min(0.78, 0.12 + 0.66 * (bi / max(1, total_batches))))
        budget = float(st.session_state.get("budget_limit", 0.0) or 0.0)
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

    status_box.markdown("🧩 **Prelearn:** consolidating themes…")
    themes = _consolidate_themes_semantic("Delighter", themes, client, embed_model, sem_merge_threshold=float(sem_merge_threshold))
    themes = _consolidate_themes_semantic("Detractor", themes, client, embed_model, sem_merge_threshold=float(sem_merge_threshold))
    prog_bar.progress(0.90)

    learned["labels"] = {"Delighter": {}, "Detractor": {}}
    learned["keywords"] = {"Delighter": {}, "Detractor": {}}
    learned["families"] = {"Delighter": {}, "Detractor": {}}
    for side in ("Delighter", "Detractor"):
        for lab, rec in sorted(themes.get(side, {}).items(), key=lambda kv: (-int(kv[1]["count"]), kv[0])):
            learned["labels"][side][lab] = {"synonyms": set(), "count": int(rec.get("count", 0))}
            learned["keywords"][side][lab] = set(rec.get("keywords", set()))
            fam_votes = (family_votes.get(side, {}) or {}).get(lab, {}) or {}
            family = ""
            if fam_votes:
                family = sorted(fam_votes.items(), key=lambda kv: (-int(kv[1]), kv[0]))[0][0]
            if not family:
                family = _infer_l1_family_from_label(lab, side)
            if family:
                learned["families"][side][lab] = family

    learned["product_profile"] = " ".join([p.strip() for p in profiles[:6] if p.strip()])[:600]
    learned["product_category"] = sorted([c for c in categories if c], key=lambda x: (-categories.count(x), x))[0] if categories else ""
    learned["product_parts"] = _dedupe_keep_order_str(parts_accum)[:20]
    learned["use_cases"] = _dedupe_keep_order_str(use_cases_accum)[:20]
    learned["ts"] = time.time()
    learned["version"] = f"prelearn_{int(learned['ts'] or 0)}"

    prog_bar.progress(1.0)
    status_box.markdown(
        f"✅ **Prelearn complete** in {_fmt_secs(time.perf_counter() - t0)} • learned {len(learned['labels']['Delighter'])} delighter themes and {len(learned['labels']['Detractor'])} detractor themes."
    )
    return learned

# ------------------------------ Batch labeler ------------------------------
_LABELER_DEFAULT = {
    "dels": [],
    "dets": [],
    "unl_dels": [],
    "unl_dets": [],
    "ev_del_map": {},
    "ev_det_map": {},
    "safety": "Not Mentioned",
    "reliability": "Not Mentioned",
    "sessions": "Unknown",
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
        "lab4",
        _canon(verbatim),
        model,
        f"{float(temperature):.2f}",
        _symptom_list_version(allowed_delighters, allowed_detractors, {}),
        int(max_ev_per_label),
        int(max_ev_chars),
        json.dumps(known_theme_hints, sort_keys=True)[:2000],
        _route_config_signature(),
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

    def _extract_allowed(objs: Iterable[Any], allowed: List[str], side: str) -> Tuple[List[str], Dict[str, List[str]]]:
        out_labels: List[str] = []
        ev_map: Dict[str, List[str]] = {}
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            raw_lbl = str(obj.get("label", "")).strip()
            lbl = _coerce_label_to_allowed(raw_lbl, allowed, side)
            if not lbl:
                continue
            evs_raw = obj.get("evidence", []) or []
            evs = []
            for e in evs_raw:
                if isinstance(e, str) and e.strip():
                    evs.append(str(e)[:max_ev_chars])
            if lbl not in out_labels:
                out_labels.append(lbl)
                ev_map[lbl] = _dedupe_keep_order_str(evs)[:max_ev_per_label]
            elif evs:
                ev_map[lbl] = _dedupe_keep_order_str((ev_map.get(lbl, []) or []) + evs)[:max_ev_per_label]
            if len(out_labels) >= 10:
                break
        return out_labels, ev_map

    dels, ev_del_map = _extract_allowed(raw_dels, allowed_delighters, "Delighter")
    dets, ev_det_map = _extract_allowed(raw_dets, allowed_detractors, "Detractor")
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
    client: Any,
    model: str,
    temperature: float,
    allowed_delighters: List[str],
    allowed_detractors: List[str],
    known_theme_hints: Dict[str, List[str]],
    max_ev_per_label: int = 2,
    max_ev_chars: int = 120,
    product_profile: str = "",
) -> Dict[int, Dict[str, Any]]:
    out_by_idx: Dict[int, Dict[str, Any]] = {}
    if not items:
        return out_by_idx

    cache = _ensure_label_cache()
    to_send: List[Tuple[int, str, bool, bool, Tuple[Any, ...]]] = []
    router_by_idx: Dict[int, Dict[str, Any]] = {}
    learned_store = _ensure_learned_store()
    router_top_n = int(st.session_state.get("router_candidate_top_n", 6) or 6)

    for it in items:
        idx = int(it.get("idx"))
        review = str(it.get("review") or "")
        needs_del = bool(it.get("needs_del", True))
        needs_det = bool(it.get("needs_det", True))
        if not review.strip():
            out_by_idx[idx] = dict(_LABELER_DEFAULT)
            continue

        key = _label_cache_key(
            review,
            model,
            float(temperature),
            allowed_delighters,
            allowed_detractors,
            known_theme_hints,
            int(max_ev_per_label),
            int(max_ev_chars),
        )
        router = _route_review_candidates(
            review,
            allowed_delighters=allowed_delighters,
            allowed_detractors=allowed_detractors,
            learned_store=learned_store,
            top_n=router_top_n,
            max_ev_chars=max_ev_chars,
        )
        router_by_idx[idx] = router
        if key in cache:
            cached = dict(cache[key])
            if bool(st.session_state.get("high_recall_labeling", True)):
                cached = _apply_router_rescue(
                    cached,
                    router,
                    needs_del=needs_del,
                    needs_det=needs_det,
                    require_evidence_flag=bool(st.session_state.get("require_evidence_flag", True)),
                    max_ev_per_label=int(max_ev_per_label),
                )
            out_by_idx[idx] = cached
        else:
            to_send.append((idx, review, needs_del, needs_det, key))

    if not to_send:
        return out_by_idx

    sys_lines = [
        "You are a high-recall but disciplined review symptomizer for consumer products across MANY product categories.",
        "Start broad: reason in reusable L1 product aspects first (Overall Satisfaction, Effective Results, Ease Of Use, Time Saver, Comfort, Value, Reliability, Battery, Noise, Cleaning / Maintenance, Connectivity, Design, Attachments / Versatility, Safety).",
        "Then go one level deeper: when the evidence is direct, add a reusable L2 subtheme such as Easy Setup, Confusing Instructions, Visible Improvement, Fast Results, Strong Recommendation, Product Failure, Short Battery Life, Pairing Issue, Compact Design, High Cost, or Irritation.",
        "Candidate labels, matched aspect families, and evidence supplied per review are strong hints derived from rules, aliases, the provided Symptoms sheet, learned keywords, and prelearned knowledge. Reuse them whenever supported.",
        "Prefer labels from the provided Symptoms sheet first. Only fall back to universal backbone labels or learned temporary labels when no provided label fits as well.",
        "Do not be overly sparse. If the same review clearly supports multiple broad labels, include all of them. If it clearly supports both a broad L1 and a reusable L2 under that L1, include both when available.",
        "Return STRICT JSON with schema:",
        '{"items":[{"id":"<id>","detractors":[{"label":"<one from allowed detractors>","evidence":["<exact substring>", "..."]}], "delighters":[{"label":"<one from allowed delighters>","evidence":["<exact substring>", "..."]}], "unlisted_detractors":["<THEME>", "..."], "unlisted_delighters":["<THEME>", "..."], "safety":"<enum>", "reliability":"<enum>", "sessions":"<enum>"}]}',
        "",
        "Rules:",
        f"- Evidence MUST be exact substrings from THAT review. Each ≤ {max_ev_chars} chars. Up to {max_ev_per_label} per label.",
        "- Use ONLY allowed lists for delighters and detractors. Prefer provided catalog labels first, then broad stable labels plus reusable L2 themes over tiny wording variants.",
        "- If a review clearly shows strong recommendation, strong results, visible improvement, ease, comfort, value, reliability, setup experience, connectivity experience, or safety, tag it. Do not leave obvious broad labels blank.",
        "- Only include a label if there is textual support in the review.",
        "- For unlisted_* items, return a SHORT reusable THEME (1–3 words), Title Case, no punctuation except slashes.",
        "- Avoid duplicates and near-duplicates.",
        "- Cap to maximum 10 detractors and 10 delighters. Cap to 10 unlisted per side.",
        "- Always return ALL keys for every item (use empty lists / Not Mentioned if none).",
        "",
        "Meta enums:",
        "SAFETY one of: ['Not Mentioned','Concern','Positive']",
        "RELIABILITY one of: ['Not Mentioned','Negative','Neutral','Positive']",
        "SESSIONS one of: ['0','1','2–3','4–9','10+','Unknown']",
    ]
    if product_profile and str(product_profile).strip():
        sys_lines.insert(1, f"Product context (brief): {str(product_profile).strip()[:600]}")

    payload = {
        "provided_catalog_delighters": _provided_catalog_labels("Delighter")[:200],
        "provided_catalog_detractors": _provided_catalog_labels("Detractor")[:200],
        "fallback_delighters": [x for x in allowed_delighters if _canon_simple(x) not in _provided_catalog_keyset("Delighter")][:120],
        "fallback_detractors": [x for x in allowed_detractors if _canon_simple(x) not in _provided_catalog_keyset("Detractor")][:120],
        "priority_delighter_themes": _priority_theme_labels("Delighter")[:20],
        "priority_detractor_themes": _priority_theme_labels("Detractor")[:20],
        "allowed_delighters": allowed_delighters,
        "allowed_detractors": allowed_detractors,
        "known_unlisted_detractor_themes": (known_theme_hints.get("Detractor") or [])[:60],
        "known_unlisted_delighter_themes": (known_theme_hints.get("Delighter") or [])[:60],
        "items": [
            {
                "id": str(idx),
                "review": review,
                "needs_delighters": bool(needs_del),
                "needs_detractors": bool(needs_det),
                "matched_positive_aspects": (router_by_idx.get(idx, {}) or {}).get("matched_del_families", []),
                "matched_negative_aspects": (router_by_idx.get(idx, {}) or {}).get("matched_det_families", []),
                "candidate_delighters": (router_by_idx.get(idx, {}) or {}).get("dels", [])[:router_top_n],
                "candidate_detractors": (router_by_idx.get(idx, {}) or {}).get("dets", [])[:router_top_n],
                "candidate_specific_delighters": (router_by_idx.get(idx, {}) or {}).get("l2_dels", [])[: max(router_top_n, 6)],
                "candidate_specific_detractors": (router_by_idx.get(idx, {}) or {}).get("l2_dets", [])[: max(router_top_n, 6)],
                "candidate_delighter_evidence": (router_by_idx.get(idx, {}) or {}).get("ev_del_map", {}),
                "candidate_detractor_evidence": (router_by_idx.get(idx, {}) or {}).get("ev_det_map", {}),
            }
            for (idx, review, needs_del, needs_det, _) in to_send
        ],
    }
    data = {}
    if client is not None:
        data = _chat_json_with_retries(
            client,
            model=model,
            temperature=float(temperature),
            messages=[
                {"role": "system", "content": "\n".join(sys_lines)},
                {"role": "user", "content": json.dumps(payload)},
            ],
            component="symptomize-label-batch",
            response_format={"type": "json_object"},
        )

    items_out: List[Any] = []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        items_out = data["items"]
    elif isinstance(data, list):
        items_out = data

    by_id: Dict[str, Any] = {}
    for obj in items_out:
        if isinstance(obj, dict) and "id" in obj:
            by_id[str(obj.get("id"))] = obj

    for (idx, _, needs_del, needs_det, key) in to_send:
        obj = by_id.get(str(idx), {}) or {}
        norm = _normalize_unified_output(obj, allowed_delighters, allowed_detractors, int(max_ev_per_label), int(max_ev_chars))
        router = router_by_idx.get(idx, {}) or {}
        if bool(st.session_state.get("high_recall_labeling", True)):
            norm = _apply_router_rescue(
                norm,
                router,
                needs_del=needs_del,
                needs_det=needs_det,
                require_evidence_flag=bool(st.session_state.get("require_evidence_flag", True)),
                max_ev_per_label=int(max_ev_per_label),
            )
        out_by_idx[idx] = norm
        cache[key] = dict(norm)

    return out_by_idx


# ------------------------------ Export helpers ------------------------------
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
    fill_red = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    fill_yel = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
    fill_blu = PatternFill(start_color="CFE2F3", end_color="CFE2F3", fill_type="solid")
    fill_pur = PatternFill(start_color="EAD1DC", end_color="EAD1DC", fill_type="solid")

    pset = set(processed_idx or [])

    def _clear_template_slots(row_i: int) -> None:
        for col_idx in DET_INDEXES + DEL_INDEXES + list(META_INDEXES.values()):
            ws.cell(row=row_i, column=col_idx, value=None)

    for i, (rid, row) in enumerate(df2.iterrows(), start=2):
        if overwrite_processed_slots and (int(rid) in pset):
            _clear_template_slots(i)
        for j, col_idx in enumerate(DET_INDEXES, start=1):
            val = row.get(f"AI Symptom Detractor {j}")
            cv = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cv)
            if cv is not None:
                cell.fill = fill_red
        for j, col_idx in enumerate(DEL_INDEXES, start=1):
            val = row.get(f"AI Symptom Delighter {j}")
            cv = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cv)
            if cv is not None:
                cell.fill = fill_green
        safety = row.get("AI Safety")
        reliab = row.get("AI Reliability")
        sess = row.get("AI # of Sessions")
        if is_filled(safety):
            c = ws.cell(row=i, column=META_INDEXES["Safety"], value=str(safety))
            c.fill = fill_yel
        if is_filled(reliab):
            c = ws.cell(row=i, column=META_INDEXES["Reliability"], value=str(reliab))
            c.fill = fill_blu
        if is_filled(sess):
            c = ws.cell(row=i, column=META_INDEXES["# of Sessions"], value=str(sess))
            c.fill = fill_pur

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

    headers_row = [c.value for c in ws[1]] if ws.max_row >= 1 else []
    headers = [str(h).strip() if h is not None else "" for h in headers_row]
    header_map = {str(h).strip().lower(): i + 1 for i, h in enumerate(headers) if str(h).strip()}
    used_cols: Set[int] = set()

    def _ensure_header(name: str, synonyms: List[str], preferred_index: Optional[int] = None) -> int:
        for syn in synonyms:
            idx = header_map.get(str(syn).lower())
            if idx:
                if not ws.cell(row=1, column=idx).value:
                    ws.cell(row=1, column=idx, value=name)
                used_cols.add(idx)
                return idx
        max_col = int(getattr(ws, "max_column", 0) or 0)
        idx = preferred_index if (preferred_index and preferred_index > 0) else (max_col + 1 if max_col > 0 else 1)
        while idx in used_cols:
            idx += 1
        ws.cell(row=1, column=idx, value=name)
        used_cols.add(idx)
        return idx

    col_label = _ensure_header("Symptom", ["symptom", "label", "name", "item"], preferred_index=1)
    col_type = _ensure_header("Type", ["type", "polarity", "category", "side"], preferred_index=2)
    col_alias = _ensure_header("Aliases", ["aliases", "alias"], preferred_index=3)

    existing_row: Dict[str, int] = {}
    existing_aliases: Dict[str, Set[str]] = {}
    last_row = int(getattr(ws, "max_row", 0) or 0)
    for r_i in range(2, last_row + 1):
        v = ws.cell(row=r_i, column=col_label).value
        if not v:
            continue
        lab = str(v).strip()
        existing_row[lab] = r_i
        als = ws.cell(row=r_i, column=col_alias).value
        aset: Set[str] = set()
        if als:
            aset = {a.strip() for a in str(als).replace(",", "|").split("|") if a.strip()}
        existing_aliases[lab] = aset

    for label, side in new_symptoms:
        lab = str(label).strip()
        if not lab or lab in existing_row:
            continue
        rnew = (int(getattr(ws, "max_row", 1) or 1)) + 1
        ws.cell(row=rnew, column=col_label, value=lab)
        ws.cell(row=rnew, column=col_type, value=str(side).strip() or "")
        existing_row[lab] = rnew
        existing_aliases[lab] = set()

    for tgt_label, alias in alias_additions:
        tgt = str(tgt_label).strip()
        als = str(alias).strip()
        if not tgt or not als or tgt not in existing_row:
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

# ----------------------------- Session bootstrap -----------------------------
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


def _reset_filter_state() -> None:
    for k in [
        "f_source_sel",
        "f_model_sel",
        "f_country_sel",
        "f_seeded_mode",
        "f_new_mode",
        "f_rating_sel",
        "f_date_range",
    ]:
        st.session_state.pop(k, None)


def _reset_run_outputs() -> None:
    st.session_state["processed_rows"] = []
    st.session_state["processed_idx_set"] = set()
    st.session_state["new_symptom_candidates"] = {}
    st.session_state["alias_suggestion_candidates"] = {}
    st.session_state["last_run_processed_count"] = 0
    st.session_state["ev_cov_num"] = 0
    st.session_state["ev_cov_den"] = 0
    st.session_state.pop("export_bytes", None)


def _suggest_autotune_settings(
    df_in: pd.DataFrame,
    work_in: Optional[pd.DataFrame],
    delighters: List[str],
    detractors: List[str],
    learned_store: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], str]:
    ls = learned_store or _ensure_learned_store()
    review_lengths = pd.Series(dtype="float64")
    if df_in is not None and not df_in.empty and "Verbatim" in df_in.columns:
        try:
            review_lengths = df_in["Verbatim"].fillna("").astype(str).str.len()
        except Exception:
            review_lengths = pd.Series(dtype="float64")

    n_reviews = int(len(df_in) if df_in is not None else 0)
    median_chars = int(review_lengths.median()) if len(review_lengths) else 0
    p90_chars = int(review_lengths.quantile(0.90)) if len(review_lengths) else median_chars
    catalog_total = int(len(delighters) + len(detractors))
    learned_total = int(len((ls.get("labels", {}) or {}).get("Delighter", {})) + len((ls.get("labels", {}) or {}).get("Detractor", {})))

    missing_both_rate = 1.0
    any_missing_rate = 1.0
    if isinstance(work_in, pd.DataFrame) and not work_in.empty and "Needs_Symptomization" in work_in.columns:
        try:
            missing_both_rate = float(work_in["Needs_Symptomization"].mean())
            any_missing_rate = float(((work_in["Needs_Delighters"]) | (work_in["Needs_Detractors"])).mean())
        except Exception:
            pass

    sparse_catalog = catalog_total < 60
    deep_catalog = catalog_total >= 140
    long_reviews = median_chars >= 260 or p90_chars >= 700
    heavy_gap = missing_both_rate >= 0.35 or any_missing_rate >= 0.60

    def _top_family_priorities(labels_in: List[str], side_name: str) -> List[str]:
        counts: Dict[str, int] = {}
        for lab in labels_in:
            fam = _infer_l1_family_from_label(lab, side_name)
            if fam:
                counts[fam] = counts.get(fam, 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [fam for fam, _ in ordered[:5]]

    del_priority = _dedupe_keep_order_str(DEFAULT_PRIORITY_DELIGHTERS + _top_family_priorities(delighters, "Delighter"))
    det_priority = _dedupe_keep_order_str(DEFAULT_PRIORITY_DETRACTORS + _top_family_priorities(detractors, "Detractor"))

    router_candidate_top_n = 10 if sparse_catalog else 8 if catalog_total < 140 else 7
    l2_candidate_top_n = 4 if (sparse_catalog or learned_total > 0) else 3
    router_rescue_threshold = 0.82 if (sparse_catalog and heavy_gap) else 0.86 if sparse_catalog else 0.92 if deep_catalog else 0.88
    l2_rescue_threshold = min(1.35, router_rescue_threshold + (0.16 if deep_catalog else 0.12))
    llm_batch_size = 4 if long_reviews else 6 if median_chars < 240 else 5
    batch_token_budget = 28000 if long_reviews else 36000 if median_chars < 260 else 32000
    prelearn_sample_n = int(min(1600, max(350, round(min(max(n_reviews, 1), 3000) * (0.45 if sparse_catalog else 0.35)))))

    cfg = {
        "auto_tune_on_upload": True,
        "temperature_setting": 0.0 if (sparse_catalog or heavy_gap) else 0.1,
        "require_evidence_flag": True,
        "max_ev_per_label": 2,
        "max_ev_chars": 140 if long_reviews else 120,
        "prelearn_enabled": True,
        "prelearn_sample_n": prelearn_sample_n,
        "prelearn_batch_size": 80 if n_reviews >= 1200 else 60,
        "prelearn_merge_threshold": 0.91 if (sparse_catalog or heavy_gap) else 0.93,
        "use_learned_as_allowed": (catalog_total < 45 and learned_total > 0),
        "use_universal_l1_backbone": True,
        "high_recall_labeling": True,
        "enable_deep_l2_routing": True,
        "router_candidate_top_n": router_candidate_top_n,
        "l2_candidate_top_n": l2_candidate_top_n,
        "router_family_boost": 0.45 if sparse_catalog else 0.38,
        "l2_specificity_bias": 0.20 if (sparse_catalog or heavy_gap) else 0.16,
        "router_rescue_threshold": router_rescue_threshold,
        "l2_rescue_threshold": l2_rescue_threshold,
        "sim_threshold_lex": 0.95 if deep_catalog else 0.93,
        "sim_threshold_sem": 0.92,
        "llm_batch_size": llm_batch_size,
        "batch_token_budget": batch_token_budget,
        "priority_delighters_text": ", ".join(del_priority[:10]),
        "priority_detractors_text": ", ".join(det_priority[:10]),
    }

    notes: List[str] = []
    if sparse_catalog:
        notes.append("smaller symptom catalog → more candidate tags and stronger family boosts")
    if heavy_gap:
        notes.append("many rows still unlabeled → lower rescue threshold for better recall")
    if long_reviews:
        notes.append("longer reviews → smaller batches and slightly longer evidence windows")
    if learned_total > 0:
        notes.append("prelearned themes available → deeper reusable L2 routing enabled")
    if not notes:
        notes.append("balanced settings for broad coverage plus reusable L2 specificity")

    summary = "Auto-tune: " + "; ".join(notes) + "."
    return cfg, summary


def _apply_autotune_settings(cfg: Dict[str, Any], summary: str = "", source: str = "manual") -> None:
    for k, v in (cfg or {}).items():
        st.session_state[k] = v
    if summary:
        st.session_state["_autotune_summary"] = str(summary)
    st.session_state["_autotune_last_source"] = str(source)
    st.session_state["_router_sig"] = _route_config_signature()


def _init_defaults() -> None:
    defaults = {
        "scope_choice": "Missing both",
        "run_mode": "First N in current scope",
        "overwrite_target": "Current scope only",
        "confirm_overwrite": False,
        "n_to_process": 10,
        "ui_log_limit": 40,
        "throttle_rpm": 0,
        "throttle_tpm": 0,
        "llm_batch_size": 6,
        "batch_token_budget": 35000,
        "budget_limit": 0.0,
        "app_json_retries": 2,
        "enable_undo": True,
        "auto_tune_on_upload": True,
        "temperature_setting": 0.1,
        "require_evidence_flag": True,
        "max_ev_per_label": 2,
        "max_ev_chars": 120,
        "prelearn_enabled": True,
        "prelearn_model_setting": "gpt-4o-mini",
        "prelearn_sample_n": 800,
        "prelearn_batch_size": 60,
        "prelearn_merge_threshold": 0.92,
        "use_learned_as_allowed": False,
        "use_universal_l1_backbone": True,
        "high_recall_labeling": True,
        "enable_deep_l2_routing": True,
        "router_candidate_top_n": 6,
        "l2_candidate_top_n": 3,
        "router_family_boost": 0.38,
        "l2_specificity_bias": 0.16,
        "router_rescue_threshold": 0.95,
        "l2_rescue_threshold": 1.08,
        "priority_delighters_text": ", ".join(DEFAULT_PRIORITY_DELIGHTERS),
        "priority_detractors_text": ", ".join(DEFAULT_PRIORITY_DETRACTORS),
        "sim_threshold_lex": 0.94,
        "sim_threshold_sem": 0.92,
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


_init_defaults()

uploaded_file = st.file_uploader(
    "📂 Upload Excel workbook (reviews sheet + optional Symptoms sheet)",
    type=["xlsx"],
    help="Expected review sheet: 'Star Walk scrubbed verbatims'. Symptoms sheet is optional but recommended.",
)
if not uploaded_file:
    st.stop()

uploaded_bytes = uploaded_file.getvalue()
file_sig = hashlib.md5(uploaded_bytes).hexdigest()

if st.session_state.get("_file_sig") != file_sig:
    st.session_state["_file_sig"] = file_sig
    st.session_state["uploaded_bytes"] = uploaded_bytes
    st.session_state["uploaded_name"] = getattr(uploaded_file, "name", "reviews.xlsx")

    df0 = _load_reviews_df(uploaded_bytes)
    if "Verbatim" not in df0.columns:
        st.error("Missing required 'Verbatim' column.")
        st.stop()

    df0["Verbatim"] = df0["Verbatim"].map(clean_text)
    st.session_state["df_work"] = ensure_ai_columns(df0)

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
        "chat_in": 0,
        "chat_out": 0,
        "embed_in": 0,
        "cost_chat": 0.0,
        "cost_embed": 0.0,
        "by_component": {},
    }
    st.session_state.pop("export_bytes", None)
    st.session_state.pop("learned", None)
    _ensure_learned_store()

flash_msg = st.session_state.pop("_flash_msg", None)
if flash_msg and isinstance(flash_msg, (tuple, list)) and len(flash_msg) == 2:
    level, msg = flash_msg
    if level == "success":
        st.success(str(msg))
    elif level == "warning":
        st.warning(str(msg))
    else:
        st.info(str(msg))


# ----------------------------- Core data state -----------------------------
df = st.session_state["df_work"]
DELIGHTERS = st.session_state.get("DELIGHTERS", []) or []
DETRACTORS = st.session_state.get("DETRACTORS", []) or []
ALIASES = st.session_state.get("ALIASES", {}) or {}
_, _, ALIAS_TO_LABEL = build_canonical_maps(DELIGHTERS, DETRACTORS, ALIASES)

if not DELIGHTERS and not DETRACTORS:
    st.warning("No Symptoms sheet was found, or it was empty. Prelearn can bootstrap themes, and learned themes can be used temporarily.")
else:
    st.success(f"Loaded {len(DELIGHTERS)} delighters and {len(DETRACTORS)} detractors from the Symptoms sheet.")

_autotune_probe_work = detect_missing(df, detect_symptom_columns(df))
_autotune_cfg_preview, _autotune_summary_preview = _suggest_autotune_settings(
    df,
    _autotune_probe_work,
    DELIGHTERS,
    DETRACTORS,
    _ensure_learned_store(),
)
st.session_state["_autotune_preview"] = dict(_autotune_cfg_preview)
st.session_state["_autotune_summary"] = str(_autotune_summary_preview or st.session_state.get("_autotune_summary", ""))
if bool(st.session_state.get("auto_tune_on_upload", True)) and st.session_state.get("_autotuned_file_sig") != file_sig:
    _apply_autotune_settings(_autotune_cfg_preview, summary=_autotune_summary_preview, source="upload")
    st.session_state["_autotuned_file_sig"] = file_sig


# ----------------------------- Sidebar controls -----------------------------
st.sidebar.header("⚙️ Controls")

st.sidebar.subheader("🪄 Auto-tune")
auto_tune_on_upload = st.sidebar.checkbox(
    "Auto-tune on new workbook",
    value=bool(st.session_state.get("auto_tune_on_upload", True)),
    help="Applies recommended settings for better broad coverage plus disciplined reusable L2 specificity.",
)
st.session_state["auto_tune_on_upload"] = bool(auto_tune_on_upload)
st.sidebar.caption(str(st.session_state.get("_autotune_summary", "")))
_preview_cfg = st.session_state.get("_autotune_preview", {}) or {}
if _preview_cfg:
    st.sidebar.markdown(
        _chip_html([
            ("Router", f"{int(_preview_cfg.get('router_candidate_top_n', 0) or 0)} tags"),
            ("Deep L2", f"{int(_preview_cfg.get('l2_candidate_top_n', 0) or 0)}/family"),
            ("Rescue", f"{float(_preview_cfg.get('router_rescue_threshold', 0.0) or 0.0):.2f}"),
            ("Batch", f"{int(_preview_cfg.get('llm_batch_size', 0) or 0)} req"),
        ]),
        unsafe_allow_html=True,
    )
if st.sidebar.button("Apply tuned settings now", use_container_width=True, key="apply_tuned_settings_now"):
    cfg_now, summary_now = _suggest_autotune_settings(
        df,
        detect_missing(df, detect_symptom_columns(df)),
        DELIGHTERS,
        DETRACTORS,
        _ensure_learned_store(),
    )
    _apply_autotune_settings(cfg_now, summary=summary_now, source="manual")
    st.rerun()

st.sidebar.subheader("🤖 Model")
MODEL_CHOICES = {
    "Fast — GPT-4o-mini": "gpt-4o-mini",
    "Balanced — GPT-4.1": "gpt-4.1",
    "Balanced — GPT-4o": "gpt-4o",
    "Advanced — GPT-5": "gpt-5",
}
_model_labels = list(MODEL_CHOICES.keys())
_selected_model_label_default = str(st.session_state.get("selected_model_label", _model_labels[0]))
if _selected_model_label_default not in MODEL_CHOICES:
    _selected_model_label_default = _model_labels[0]
selected_model_label = st.sidebar.selectbox("Run model", _model_labels, index=_model_labels.index(_selected_model_label_default))
st.session_state["selected_model_label"] = selected_model_label
selected_model = MODEL_CHOICES[selected_model_label]
temperature = st.sidebar.slider(
    "Creativity (temperature)",
    0.0,
    1.0,
    float(st.session_state.get("temperature_setting", 0.1) or 0.1),
    0.1,
)
st.session_state["temperature_setting"] = float(temperature)

st.sidebar.subheader("🛡️ Stability")
request_timeout_s = st.sidebar.number_input("Request timeout (sec)", 10, 300, 60, 10)
sdk_max_retries = st.sidebar.number_input("SDK retries", 0, 10, 3, 1)
app_json_retries = st.sidebar.number_input("JSON retries", 0, 6, int(st.session_state.get("app_json_retries", 2)), 1)
ui_log_limit = st.sidebar.slider("Show last N processed reviews", 0, 200, int(st.session_state.get("ui_log_limit", 40)), 10)
enable_undo = st.sidebar.checkbox("Keep undo snapshots", value=bool(st.session_state.get("enable_undo", True)))
st.session_state["app_json_retries"] = int(app_json_retries)
st.session_state["ui_log_limit"] = int(ui_log_limit)
st.session_state["enable_undo"] = bool(enable_undo)

st.sidebar.subheader("⚡ Throughput")
llm_batch_size = st.sidebar.slider("LLM batch size", 1, 12, int(st.session_state.get("llm_batch_size", 6)), 1)
batch_token_budget = st.sidebar.slider("Batch token budget", 5000, 100000, int(st.session_state.get("batch_token_budget", 35000)), 1000)
throttle_rpm = st.sidebar.number_input("Throttle chat requests/min (0 = off)", 0, 600, int(st.session_state.get("throttle_rpm", 0)), 10)
throttle_tpm = st.sidebar.number_input("Throttle input tokens/min (0 = off)", 0, 2_000_000, int(st.session_state.get("throttle_tpm", 0)), 10_000)
st.session_state["llm_batch_size"] = int(llm_batch_size)
st.session_state["batch_token_budget"] = int(batch_token_budget)
st.session_state["throttle_rpm"] = int(throttle_rpm)
st.session_state["throttle_tpm"] = int(throttle_tpm)

st.sidebar.subheader("🧾 Evidence")
require_evidence = st.sidebar.checkbox(
    "Require evidence to write labels",
    value=bool(st.session_state.get("require_evidence_flag", True)),
)
max_ev_per_label = st.sidebar.slider(
    "Max evidence snippets per label",
    1,
    3,
    int(st.session_state.get("max_ev_per_label", 2) or 2),
)
max_ev_chars = st.sidebar.slider(
    "Max evidence length",
    40,
    200,
    int(st.session_state.get("max_ev_chars", 120) or 120),
    10,
)
st.session_state["require_evidence_flag"] = bool(require_evidence)
st.session_state["max_ev_per_label"] = int(max_ev_per_label)
st.session_state["max_ev_chars"] = int(max_ev_chars)

st.sidebar.subheader("🧠 Prelearn")
prelearn_enabled = st.sidebar.checkbox(
    "Auto-run prelearn before symptomizing",
    value=bool(st.session_state.get("prelearn_enabled", True)),
)
_PRELEARN_MODELS = ["gpt-4o-mini", "gpt-4.1", "gpt-4o", "gpt-5"]
_prelearn_model_default = str(st.session_state.get("prelearn_model_setting", "gpt-4o-mini"))
if _prelearn_model_default not in _PRELEARN_MODELS:
    _prelearn_model_default = _PRELEARN_MODELS[0]
prelearn_model = st.sidebar.selectbox("Prelearn model", _PRELEARN_MODELS, index=_PRELEARN_MODELS.index(_prelearn_model_default))
st.session_state["prelearn_model_setting"] = prelearn_model
embed_model = st.sidebar.selectbox("Embedding model", ["text-embedding-3-small"], index=0)
prelearn_sample_n = st.sidebar.slider("Prelearn sample size", 100, 3000, int(st.session_state.get("prelearn_sample_n", 800)), 50)
prelearn_batch_size = st.sidebar.slider("Prelearn batch size", 20, 120, int(st.session_state.get("prelearn_batch_size", 60)), 10)
prelearn_merge_threshold = st.sidebar.slider(
    "Prelearn merge threshold",
    0.85,
    0.99,
    float(st.session_state.get("prelearn_merge_threshold", 0.92) or 0.92),
    0.01,
)
use_learned_as_allowed = st.sidebar.checkbox(
    "Use learned themes as temporary allowed labels",
    value=bool(st.session_state.get("use_learned_as_allowed", False)),
    help="Leave this OFF when your Symptoms sheet is already strong. Turn it ON only when you want learned fallback labels to supplement a sparse catalog.",
)
st.session_state["prelearn_enabled"] = bool(prelearn_enabled)
st.session_state["prelearn_sample_n"] = int(prelearn_sample_n)
st.session_state["prelearn_batch_size"] = int(prelearn_batch_size)
st.session_state["prelearn_merge_threshold"] = float(prelearn_merge_threshold)
st.session_state["use_learned_as_allowed"] = bool(use_learned_as_allowed)

st.sidebar.subheader("🎯 Universal tagging")
use_universal_l1_backbone = st.sidebar.checkbox(
    "Use universal L1 backbone (works for any product)",
    value=bool(st.session_state.get("use_universal_l1_backbone", True)),
    help="Always makes broad reusable themes like Ease Of Use, Effective Results, Overall Satisfaction, Reliability, Value, and Safety available as temporary labels.",
)
high_recall_labeling = st.sidebar.checkbox(
    "High-recall symptomization",
    value=bool(st.session_state.get("high_recall_labeling", True)),
    help="Blends LLM output with rule, alias, learned keyword, and aspect-family routing so obvious broad tags are not missed.",
)
enable_deep_l2_routing = st.sidebar.checkbox(
    "Deeper reusable L2 routing",
    value=bool(st.session_state.get("enable_deep_l2_routing", True)),
    help="Pushes the router to surface slightly more specific reusable L2 themes like Easy Setup, Product Failure, Short Battery Life, or Strong Recommendation when evidence is direct.",
)
router_candidate_top_n = st.sidebar.slider(
    "Candidate tags per review",
    3,
    12,
    int(st.session_state.get("router_candidate_top_n", 6) or 6),
    1,
    help="How many routed tags the model sees for each review.",
)
l2_candidate_top_n = st.sidebar.slider(
    "Deep L2 candidates per family",
    1,
    6,
    int(st.session_state.get("l2_candidate_top_n", 3) or 3),
    1,
    help="How many reusable L2 candidates the router can surface inside each matched L1 family.",
)
router_family_boost = st.sidebar.slider(
    "L1→L2 family boost",
    0.0,
    1.0,
    float(st.session_state.get("router_family_boost", 0.38) or 0.38),
    0.05,
    help="Boosts existing labels whose L1 family is already strongly supported in the review.",
)
l2_specificity_bias = st.sidebar.slider(
    "L2 specificity bias",
    0.0,
    0.50,
    float(st.session_state.get("l2_specificity_bias", 0.16) or 0.16),
    0.02,
    help="Extra preference for reusable L2 themes with direct evidence.",
)
router_rescue_threshold = st.sidebar.slider(
    "Broad rescue threshold",
    0.40,
    1.80,
    float(st.session_state.get("router_rescue_threshold", 0.95) or 0.95),
    0.05,
    help="Lower values add more high-confidence routed broad tags when the model misses them.",
)
l2_rescue_threshold = st.sidebar.slider(
    "Deep L2 rescue threshold",
    0.60,
    1.80,
    float(st.session_state.get("l2_rescue_threshold", 1.08) or 1.08),
    0.05,
    help="Use a slightly higher threshold to keep L2 rescue specific and disciplined.",
)
priority_delighters_text = st.sidebar.text_area(
    "Priority delighter themes",
    value=str(st.session_state.get("priority_delighters_text", ", ".join(DEFAULT_PRIORITY_DELIGHTERS))),
    height=90,
    help="Broad L1 themes to always prioritize when supported by evidence.",
)
priority_detractors_text = st.sidebar.text_area(
    "Priority detractor themes",
    value=str(st.session_state.get("priority_detractors_text", ", ".join(DEFAULT_PRIORITY_DETRACTORS))),
    height=90,
    help="Broad L1 themes to always prioritize when supported by evidence.",
)
st.session_state["use_universal_l1_backbone"] = bool(use_universal_l1_backbone)
st.session_state["high_recall_labeling"] = bool(high_recall_labeling)
st.session_state["enable_deep_l2_routing"] = bool(enable_deep_l2_routing)
st.session_state["router_candidate_top_n"] = int(router_candidate_top_n)
st.session_state["l2_candidate_top_n"] = int(l2_candidate_top_n)
st.session_state["router_family_boost"] = float(router_family_boost)
st.session_state["l2_specificity_bias"] = float(l2_specificity_bias)
st.session_state["router_rescue_threshold"] = float(router_rescue_threshold)
st.session_state["l2_rescue_threshold"] = float(l2_rescue_threshold)
st.session_state["priority_delighters_text"] = str(priority_delighters_text or "")
st.session_state["priority_detractors_text"] = str(priority_detractors_text or "")

st.sidebar.subheader("🧩 Consistency")
sim_threshold_lex = st.sidebar.slider(
    "Lexical similarity guard",
    0.80,
    0.99,
    float(st.session_state.get("sim_threshold_lex", 0.94) or 0.94),
    0.01,
)
sim_threshold_sem = st.sidebar.slider(
    "Semantic similarity guard",
    0.80,
    0.99,
    float(st.session_state.get("sim_threshold_sem", 0.92) or 0.92),
    0.01,
)
st.session_state["sim_threshold_lex"] = float(sim_threshold_lex)
st.session_state["sim_threshold_sem"] = float(sim_threshold_sem)
st.session_state["_router_sig"] = _route_config_signature()

st.sidebar.subheader("💰 Budget")
if "_pricing_overrides" not in st.session_state:
    st.session_state["_pricing_overrides"] = {"models": {}, "embeddings": {}}
with st.sidebar.expander("Pricing overrides", expanded=False):
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

budget_limit = st.sidebar.number_input(
    "Stop runs if session spend exceeds (USD)",
    min_value=0.0,
    value=float(st.session_state.get("budget_limit", 0.0)),
    step=1.0,
)
st.session_state["budget_limit"] = float(budget_limit)

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
client = _make_openai_client(api_key, request_timeout_s, sdk_max_retries)
if client is None:
    st.sidebar.warning("OpenAI is not configured. Upload / preview / export still work, but symptomization and prelearn require OPENAI_API_KEY.")

tr = _ensure_usage_tracker()
pin, pout = _price_for_model(selected_model)
pemb = _price_for_embedding(embed_model)
st.sidebar.markdown(
    f"<div class='tiny'>Model <span class='mono'>{selected_model}</span>: <b>${pin}</b>/1M input • <b>${pout}</b>/1M output</div>",
    unsafe_allow_html=True,
)
st.sidebar.markdown(
    f"<div class='tiny'>Embeddings <span class='mono'>{embed_model}</span>: <b>${pemb}</b>/1M tokens</div>",
    unsafe_allow_html=True,
)
st.sidebar.markdown(
    f"<div class='chip-wrap'>"
    f"<span class='chip blue'>Input {int(tr['chat_in']):,}</span>"
    f"<span class='chip purple'>Output {int(tr['chat_out']):,}</span>"
    f"<span class='chip yellow'>Embed {int(tr['embed_in']):,}</span>"
    f"</div>",
    unsafe_allow_html=True,
)
st.sidebar.markdown(
    f"<div class='chip-wrap'>"
    f"<span class='chip green'>Chat {_fmt_money(float(tr['cost_chat']))}</span>"
    f"<span class='chip green'>Embed {_fmt_money(float(tr['cost_embed']))}</span>"
    f"<span class='chip green'><b>Total {_fmt_money(float(tr['cost_chat']) + float(tr['cost_embed']))}</b></span>"
    f"</div>",
    unsafe_allow_html=True,
)


# ----------------------------- Filtering logic -----------------------------
colmap = detect_symptom_columns(df)
work = detect_missing(df, colmap)

c_source = _find_col(work, ["Source"])
c_model = _find_col(work, ["Model (SKU)", "Model", "SKU"])
c_seeded = _find_col(work, ["Seeded"])
c_country = _find_col(work, ["Country", "Region"])
c_newrev = _find_col(work, ["New Review", "New"])
c_rdate = _find_col(work, ["Review Date"])
c_rating = _find_col(work, ["Star Rating", "star rating", "Rating"])
_coerce_datetime_inplace(work, c_rdate)
_coerce_numeric_inplace(work, c_rating)


def _apply_filters_to_work(work_df: pd.DataFrame) -> pd.DataFrame:
    out = work_df.copy()

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
    if c_rdate and date_range and isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        try:
            start = pd.Timestamp(date_range[0])
            end = pd.Timestamp(date_range[1]) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
            out = out[(out[c_rdate] >= start) & (out[c_rdate] <= end)]
        except Exception:
            pass

    rating_sel = st.session_state.get("f_rating_sel", None)
    if c_rating and rating_sel is not None and len(rating_sel) > 0:
        try:
            out = out[out[c_rating].isin(list(rating_sel))]
        except Exception:
            out = out[out[c_rating].astype(str).isin([str(x) for x in rating_sel])]

    return out


work_filtered = _apply_filters_to_work(work)
scope_choice = st.session_state.get("scope_choice", "Missing both")
if scope_choice == "Missing both":
    target = work_filtered[(work_filtered["Needs_Delighters"]) & (work_filtered["Needs_Detractors"])]
elif scope_choice == "Any missing":
    target = work_filtered[(work_filtered["Needs_Delighters"]) | (work_filtered["Needs_Detractors"])]
elif scope_choice == "Missing delighters only":
    target = work_filtered[(work_filtered["Needs_Delighters"]) & (~work_filtered["Needs_Detractors"])]
else:
    target = work_filtered[(~work_filtered["Needs_Delighters"]) & (work_filtered["Needs_Detractors"])]


def _active_filter_items() -> List[Tuple[str, str]]:
    items: List[Tuple[str, str]] = []
    src_sel = st.session_state.get("f_source_sel", []) or []
    model_sel = st.session_state.get("f_model_sel", []) or []
    country_sel = st.session_state.get("f_country_sel", []) or []
    seeded_mode = str(st.session_state.get("f_seeded_mode", "All"))
    new_mode = str(st.session_state.get("f_new_mode", "All"))
    rating_sel = st.session_state.get("f_rating_sel", []) or []
    date_range = st.session_state.get("f_date_range", None)
    if src_sel:
        items.append((f"Source: {len(src_sel)}", "blue"))
    if model_sel:
        items.append((f"SKU: {len(model_sel)}", "purple"))
    if country_sel:
        items.append((f"Country: {len(country_sel)}", "blue"))
    if seeded_mode != "All":
        items.append((seeded_mode, "yellow"))
    if new_mode != "All":
        items.append((new_mode, "yellow"))
    if rating_sel:
        items.append((f"Rating: {', '.join([str(x) for x in rating_sel])}", "green"))
    if date_range and isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        items.append((f"Date: {date_range[0]} → {date_range[1]}", "gray"))
    return items


# ----------------------------- Run helpers -----------------------------
processed_rows: List[Dict[str, Any]] = st.session_state.get("processed_rows", []) or []
processed_idx_set: Set[int] = st.session_state.get("processed_idx_set", set()) or set()
new_symptom_candidates: Dict[Tuple[str, str], Dict[str, Any]] = st.session_state.get("new_symptom_candidates", {}) or {}
alias_suggestion_candidates: Dict[Tuple[str, str, str], Dict[str, Any]] = st.session_state.get("alias_suggestion_candidates", {}) or {}


def _agg_candidate(d: Dict[Any, Any], key: Tuple[Any, ...], idx: int, max_refs: int = 50) -> None:
    rec = d.setdefault(key, {"count": 0, "refs": []})
    rec["count"] = int(rec.get("count", 0)) + 1
    refs = rec.get("refs", [])
    if isinstance(refs, list) and len(refs) < max_refs:
        refs.append(int(idx))
        rec["refs"] = refs


def _active_allowed_lists() -> Tuple[List[str], List[str]]:
    dels = list(DELIGHTERS)
    dets = list(DETRACTORS)
    if bool(st.session_state.get("use_universal_l1_backbone", True)):
        for x in _priority_theme_labels("Delighter"):
            if x not in dels:
                dels.append(x)
        for x in _priority_theme_labels("Detractor"):
            if x not in dets:
                dets.append(x)
    if bool(st.session_state.get("use_learned_as_allowed", False)):
        for x in _known_learned_labels("Delighter")[:80]:
            if x not in dels:
                dels.append(x)
        for x in _known_learned_labels("Detractor")[:80]:
            if x not in dets:
                dets.append(x)
    return dels, dets


def _build_run_selection() -> Tuple[pd.DataFrame, bool, str]:
    run_mode = st.session_state.get("run_mode", "First N in current scope")
    overwrite_mode = False
    label = run_mode
    if run_mode == "First N in current scope":
        rows_iter = target.sort_index().head(int(st.session_state.get("n_to_process", 10) or 10))
        label = f"First {len(rows_iter):,} rows in current scope"
    elif run_mode == "All reviews in current scope":
        rows_iter = target.sort_index()
        label = f"All {len(rows_iter):,} rows in current scope"
    elif run_mode == "Missing-both fast pass":
        rows_iter = work_filtered[(work_filtered["Needs_Delighters"]) & (work_filtered["Needs_Detractors"])].sort_index()
        label = f"Missing-both within current filters ({len(rows_iter):,} rows)"
    else:
        overwrite_mode = True
        if st.session_state.get("overwrite_target", "Current scope only") == "Current scope only":
            rows_iter = target.sort_index()
            label = f"Rebuild current scope from scratch ({len(rows_iter):,} rows)"
        else:
            rows_iter = work_filtered.sort_index()
            label = f"Rebuild all filtered rows from scratch ({len(rows_iter):,} rows)"
    return rows_iter, overwrite_mode, label


def _run_button_label(rows_df: pd.DataFrame, overwrite_mode: bool) -> str:
    run_mode = st.session_state.get("run_mode", "First N in current scope")
    n = len(rows_df)
    if overwrite_mode:
        return f"🧹 Rebuild {n:,} row(s)"
    if run_mode == "Missing-both fast pass":
        return f"✨ Run missing-both on {n:,} row(s)"
    if run_mode == "All reviews in current scope":
        return f"🚀 Run all {n:,} in scope"
    return f"▶️ Run first {n:,} row(s)"


def _run_symptomize(rows_df: pd.DataFrame, overwrite_mode: bool = False) -> None:
    global df

    _reset_run_outputs()
    processed_rows_local: List[Dict[str, Any]] = st.session_state["processed_rows"]
    processed_idx_set_local: Set[int] = st.session_state["processed_idx_set"]
    new_symptom_candidates_local: Dict[Tuple[str, str], Dict[str, Any]] = st.session_state["new_symptom_candidates"]
    alias_suggestion_candidates_local: Dict[Tuple[str, str, str], Dict[str, Any]] = st.session_state["alias_suggestion_candidates"]
    existing_catalog_keys: Set[str] = {_canon_simple(x) for x in (DELIGHTERS + DETRACTORS)}
    for _label, _aliases in (ALIASES or {}).items():
        existing_catalog_keys.add(_canon_simple(_label))
        for _a in (_aliases or []):
            existing_catalog_keys.add(_canon_simple(_a))

    prog = st.progress(0.0)
    eta_box = st.empty()
    status_box = st.empty()

    if prelearn_enabled and client is not None:
        ls = _ensure_learned_store()
        if not (ls.get("labels", {}).get("Delighter") or ls.get("labels", {}).get("Detractor")):
            status_box.markdown("🧠 Auto-running prelearn…")
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
        if do_undo:
            for idx_clear in idxs:
                old_vals = {f"AI Symptom Detractor {j}": df.loc[idx_clear, f"AI Symptom Detractor {j}"] for j in range(1, 11)}
                old_vals.update({f"AI Symptom Delighter {j}": df.loc[idx_clear, f"AI Symptom Delighter {j}"] for j in range(1, 11)})
                old_vals.update({
                    "AI Safety": df.loc[idx_clear, "AI Safety"],
                    "AI Reliability": df.loc[idx_clear, "AI Reliability"],
                    "AI # of Sessions": df.loc[idx_clear, "AI # of Sessions"],
                })
                snapshot.append((int(idx_clear), old_vals))
        df = clear_ai_slots_for_indices(df, idxs)
    elif do_undo:
        for idx_keep in rows_df.index.tolist():
            old_vals = {f"AI Symptom Detractor {j}": df.loc[idx_keep, f"AI Symptom Detractor {j}"] for j in range(1, 11)}
            old_vals.update({f"AI Symptom Delighter {j}": df.loc[idx_keep, f"AI Symptom Delighter {j}"] for j in range(1, 11)})
            old_vals.update({
                "AI Safety": df.loc[idx_keep, "AI Safety"],
                "AI Reliability": df.loc[idx_keep, "AI Reliability"],
                "AI # of Sessions": df.loc[idx_keep, "AI # of Sessions"],
            })
            snapshot.append((int(idx_keep), old_vals))

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
    overhead_est = _estimate_tokens(
        json.dumps({"allowed_delighters": allowed_dels, "allowed_detractors": allowed_dets, "known": known_hints, "profile": product_profile[:600]}),
        model_id=selected_model,
    ) + 800

    rows_list = list(rows_df.iterrows())
    batches: List[List[Tuple[int, pd.Series]]] = []
    cur: List[Tuple[int, pd.Series]] = []
    cur_tok = 0
    for idx, row in rows_list:
        vb = str(row.get("Verbatim", "") or "")
        t_est = _estimate_tokens(vb, model_id=selected_model)
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
        items: List[Dict[str, Any]] = []
        for idx, row in batch_rows:
            vb = str(row.get("Verbatim", "") or "")
            if overwrite_mode:
                needs_del = True
                needs_det = True
            else:
                needs_del = bool(row.get("Needs_Delighters", False))
                needs_det = bool(row.get("Needs_Detractors", False))
            items.append({"idx": int(idx), "review": vb, "needs_del": needs_del, "needs_det": needs_det})

        status_box.markdown(f"🔄 **Batch {bi}/{len(batches)}** — labeling + meta…")
        outs_by_idx: Dict[int, Dict[str, Any]] = {}
        if client is not None:
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

            wrote_dets: List[str] = []
            wrote_dels: List[str] = []
            ev_written_det: Dict[str, List[str]] = {}
            ev_written_del: Dict[str, List[str]] = {}

            def _label_allowed(label: str, side: str) -> bool:
                if not require_evidence:
                    return True
                evs = (ev_det_map if side == "det" else ev_del_map).get(label, [])
                return len(evs) > 0

            if overwrite_mode:
                for j in range(1, 11):
                    df.loc[idx, f"AI Symptom Detractor {j}"] = None
                    df.loc[idx, f"AI Symptom Delighter {j}"] = None

            if needs_detr and dets:
                dets_to_write = [lab for lab in dets if _label_allowed(lab, "det")][:10]
                for j, lab in enumerate(dets_to_write):
                    df.loc[idx, f"AI Symptom Detractor {j + 1}"] = lab
                    ev_written_det[lab] = ev_det_map.get(lab, [])
                wrote_dets = dets_to_write
            if needs_deli and dels:
                dels_to_write = [lab for lab in dels if _label_allowed(lab, "del")][:10]
                for j, lab in enumerate(dels_to_write):
                    df.loc[idx, f"AI Symptom Delighter {j + 1}"] = lab
                    ev_written_del[lab] = ev_del_map.get(lab, [])
                wrote_dels = dels_to_write

            df.loc[idx, "AI Safety"] = safety
            df.loc[idx, "AI Reliability"] = reliability
            df.loc[idx, "AI # of Sessions"] = sessions

            learned = _ensure_learned_store()
            new_unl_dels: List[str] = []
            new_unl_dets: List[str] = []
            alias_sugs_for_row: List[Tuple[str, str, str, float]] = []

            def _handle_unlisted_list(items2: List[str], side_label: str) -> None:
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
                        client=client,
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
                out2: List[str] = []
                seen: Set[str] = set()
                for x in lst:
                    k2 = _canon_simple(x)
                    if not x or k2 in seen:
                        continue
                    seen.add(k2)
                    out2.append(x)
                return out2

            new_unl_dels = _dedupe_keep_order([normalize_theme_label(x, "Delighter") for x in new_unl_dels])
            new_unl_dets = _dedupe_keep_order([normalize_theme_label(x, "Detractor") for x in new_unl_dets])

            for lab in new_unl_dels:
                _agg_candidate(new_symptom_candidates_local, (lab, "Delighter"), int(idx))
            for lab in new_unl_dets:
                _agg_candidate(new_symptom_candidates_local, (lab, "Detractor"), int(idx))
            for tgt, alias, side_label, score in alias_sugs_for_row:
                side_norm = "Delighter" if side_label.lower().startswith("del") else "Detractor"
                _agg_candidate(alias_suggestion_candidates_local, (tgt, alias, side_norm), int(idx))

            for lab in wrote_dels:
                if _canon_simple(lab) not in existing_catalog_keys:
                    _agg_candidate(new_symptom_candidates_local, (normalize_theme_label(lab, "Delighter"), "Delighter"), int(idx))
            for lab in wrote_dets:
                if _canon_simple(lab) not in existing_catalog_keys:
                    _agg_candidate(new_symptom_candidates_local, (normalize_theme_label(lab, "Detractor"), "Detractor"), int(idx))

            total_labels = len(wrote_dets) + len(wrote_dels)
            labels_with_ev = sum(1 for lab in wrote_dets if len(ev_written_det.get(lab, [])) > 0) + sum(1 for lab in wrote_dels if len(ev_written_del.get(lab, [])) > 0)
            cov_num += labels_with_ev
            cov_den += total_labels
            row_ev_cov = (labels_with_ev / total_labels) if total_labels else 0.0

            if ui_keep > 0:
                detected_dets = list(dets)[:10]
                detected_dels = list(dels)[:10]
                detected_ev_det = {lab: list((ev_det_map.get(lab, []) or []))[:max_ev_per_label] for lab in detected_dets if ev_det_map.get(lab)}
                detected_ev_del = {lab: list((ev_del_map.get(lab, []) or []))[:max_ev_per_label] for lab in detected_dels if ev_del_map.get(lab)}
                det_status = "Written to workbook" if wrote_dets else ("Detected but not written because this row already had detractors." if (detected_dets and not needs_detr) else ("Detected but filtered out by the evidence guard." if (detected_dets and needs_detr) else "No strong detractor match found."))
                del_status = "Written to workbook" if wrote_dels else ("Detected but not written because this row already had delighters." if (detected_dels and not needs_deli) else ("Detected but filtered out by the evidence guard." if (detected_dels and needs_deli) else "No strong delighter match found."))
                processed_rows_local.append({
                    "Index": int(idx),
                    "Verbatim": str(vb)[:4000],
                    "Needs_Detractors": needs_detr,
                    "Needs_Delighters": needs_deli,
                    "Detected_Detractors": detected_dets,
                    "Detected_Delighters": detected_dels,
                    "Detected_Evidence_Detractors": detected_ev_det,
                    "Detected_Evidence_Delighters": detected_ev_del,
                    "Added_Detractors": wrote_dets,
                    "Added_Delighters": wrote_dels,
                    "Evidence_Detractors": ev_written_det,
                    "Evidence_Delighters": ev_written_del,
                    "Detractor_Status": det_status,
                    "Delighter_Status": del_status,
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
            f"**Progress:** {done}/{total_n} • **ETA:** ~ {_fmt_secs(eta_sec)} • **Speed:** {rate * 60:.1f} rev/min • **Spend:** {_fmt_money(spent)} • **Est total:** {_fmt_money(est_total)}"
        )
        budget = float(st.session_state.get("budget_limit", 0.0) or 0.0)
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
    st.session_state["new_symptom_candidates"] = new_symptom_candidates_local
    st.session_state["alias_suggestion_candidates"] = alias_suggestion_candidates_local
    st.session_state["last_run_processed_count"] = len(processed_idx_set_local)
    st.session_state["ev_cov_num"] = int(cov_num)
    st.session_state["ev_cov_den"] = int(cov_den)
    st.session_state["df_work"] = df
    st.session_state.pop("export_bytes", None)


def _undo_last_run() -> Tuple[bool, str]:
    global df
    if not st.session_state.get("undo_stack"):
        return (False, "Nothing to undo.")
    snap = st.session_state["undo_stack"].pop()
    for idx, old_vals in snap.get("rows", []):
        for col, val in old_vals.items():
            if col not in df.columns:
                df[col] = None
            df.loc[idx, col] = val
    st.session_state["df_work"] = df
    st.session_state.pop("export_bytes", None)
    return (True, "Reverted the last run.")

# ----------------------------- Main dashboard -----------------------------
learned_store = _ensure_learned_store()
file_name = st.session_state.get("uploaded_name", getattr(uploaded_file, "name", "reviews.xlsx"))
file_base = os.path.splitext(file_name)[0]
need_del = int(work["Needs_Delighters"].sum())
need_det = int(work["Needs_Detractors"].sum())
need_both = int(work["Needs_Symptomization"].sum())
rows_for_run, overwrite_mode_selected, run_selection_label = _build_run_selection()
estimated_batches = max(1, math.ceil(len(rows_for_run) / max(1, int(st.session_state.get("llm_batch_size", 6))))) if len(rows_for_run) else 0

a1, a2 = st.columns([2.5, 1.2])
with a1:
    st.markdown(
        f"""
        <div class='hero-shell'>
          <div class='hero-top'>
            <div>
              <div class='hero-title'>Review workspace</div>
              <div class='hero-sub'>Workbook: <span class='mono'>{_safe(file_name)}</span></div>
            </div>
            <div class='badge-row'>
              <span class='badge'>Symptoms: {len(DELIGHTERS) + len(DETRACTORS):,}</span>
              <span class='badge'>Aliases: {sum(len(v) for v in ALIASES.values()) if ALIASES else 0:,}</span>
              <span class='badge'>Learned themes: {len(learned_store.get('labels', {}).get('Delighter', {})) + len(learned_store.get('labels', {}).get('Detractor', {})):,}</span>
            </div>
          </div>
          <div class='hero-grid'>
            <div class='hero-stat'><div class='label'>Total reviews</div><div class='value'>{len(work):,}</div></div>
            <div class='hero-stat'><div class='label'>Need delighters</div><div class='value'>{need_del:,}</div></div>
            <div class='hero-stat'><div class='label'>Need detractors</div><div class='value'>{need_det:,}</div></div>
            <div class='hero-stat accent'><div class='label'>Missing both</div><div class='value'>{need_both:,}</div></div>
            <div class='hero-stat'><div class='label'>Filtered eligible</div><div class='value'>{len(work_filtered):,}</div></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with a2:
    st.markdown(
        f"""
        <div class='info-card'>
          <div class='title'>Current run plan</div>
          <div class='big'>{len(rows_for_run):,}</div>
          <div class='muted'>{_safe(run_selection_label)}</div>
          <div class='muted' style='margin-top:8px;'>Estimated batches: <b>{estimated_batches:,}</b><br/>Overwrite mode: <b>{'Yes' if overwrite_mode_selected else 'No'}</b></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


tab_dash, tab_run, tab_log, tab_inbox, tab_export = st.tabs([
    "📊 Dashboard",
    "🚀 Run Center",
    "🧾 Review Log",
    "🟡 Inbox",
    "📦 Exports",
])

with tab_dash:
    st.markdown("<div class='section-title'>Filter reviews and inspect scope</div>", unsafe_allow_html=True)
    st.markdown("<div class='section-sub'>The filters below decide which rows are eligible. The scope decides which of those rows are in your current working set.</div>", unsafe_allow_html=True)

    f1, f2 = st.columns([5, 1])
    with f1:
        st.markdown(_chip_html(_active_filter_items()), unsafe_allow_html=True)
    with f2:
        st.button("Clear filters", use_container_width=True, on_click=_reset_filter_state, key="clear_filters_btn")

    row1 = st.columns(3)
    with row1[0]:
        if c_source:
            st.multiselect("Source", options=_unique_sorted_str(work[c_source]), key="f_source_sel")
        else:
            st.caption("Source column not found")
    with row1[1]:
        if c_model:
            st.multiselect("Model (SKU)", options=_unique_sorted_str(work[c_model]), key="f_model_sel")
        else:
            st.caption("Model / SKU column not found")
    with row1[2]:
        if c_country:
            st.multiselect("Country", options=_unique_sorted_str(work[c_country]), key="f_country_sel")
        else:
            st.caption("Country column not found")

    row2 = st.columns(3)
    with row2[0]:
        if c_seeded:
            st.selectbox("Seeded", ["All", "Seeded only", "Non-seeded only"], key="f_seeded_mode")
        else:
            st.caption("Seeded column not found")
    with row2[1]:
        if c_newrev:
            st.selectbox("New Review", ["All", "New only", "Not new only"], key="f_new_mode")
        else:
            st.caption("New Review column not found")
    with row2[2]:
        if c_rating:
            vals = work[c_rating].dropna().tolist()
            opts = sorted({int(v) if isinstance(v, (int, float)) and float(v).is_integer() else v for v in vals})
            st.multiselect("Star Rating", options=opts, key="f_rating_sel", help="Leave empty to include all ratings.")
        else:
            st.caption("Star Rating column not found")

    if c_rdate and work[c_rdate].notna().any():
        try:
            dmin = pd.to_datetime(work[c_rdate].min()).date()
            dmax = pd.to_datetime(work[c_rdate].max()).date()
            current_date_value = st.session_state.get("f_date_range", (dmin, dmax))
            if not isinstance(current_date_value, (tuple, list)) or len(current_date_value) != 2:
                current_date_value = (dmin, dmax)
            st.date_input("Review Date range", value=current_date_value, key="f_date_range")
        except Exception:
            st.caption("Review Date could not be parsed")

    st.divider()
    p1, p2, p3 = st.columns(3)
    with p1:
        st.metric("Filtered eligible", f"{len(work_filtered):,}")
    with p2:
        st.metric("Current scope", f"{len(target):,}")
    with p3:
        st.metric("Selected for next run", f"{len(rows_for_run):,}")

    with st.expander("Preview in-scope rows", expanded=False):
        preview_cols = ["Verbatim", "Has_Delighters", "Has_Detractors", "Needs_Delighters", "Needs_Detractors"]
        extras = [c for c in [c_rating, c_rdate, c_source, c_model, c_country] if c and c in target.columns]
        st.dataframe(target[preview_cols + extras].head(200), use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("<div class='section-title'>Product Knowledge Prelearn</div>", unsafe_allow_html=True)
    pc1, pc2 = st.columns([1.2, 2.8])
    with pc1:
        run_prelearn_btn = st.button("🧠 Run prelearn now", use_container_width=True, disabled=(client is None), key="run_prelearn_now")
    with pc2:
        st.markdown(
            "<div class='tiny'>Prelearn builds a product glossary and canonical theme hints so the labeler stops inventing tiny wording variants.</div>",
            unsafe_allow_html=True,
        )

    prelearn_status = st.empty()
    prelearn_prog_box = st.empty()
    if run_prelearn_btn and client is not None:
        prelearn_prog = prelearn_prog_box.progress(0.0)
        learned_store = run_prelearn(
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
        st.session_state["learned"] = learned_store
    else:
        prelearn_prog_box.empty()

    learned_store = _ensure_learned_store()
    if learned_store.get("labels", {}).get("Delighter") or learned_store.get("labels", {}).get("Detractor"):
        with st.expander("Show learned product knowledge", expanded=False):
            if learned_store.get("product_category"):
                st.markdown(f"**Product category**: {_safe(learned_store.get('product_category', ''))}", unsafe_allow_html=True)
            st.markdown("**Product profile**")
            st.write(learned_store.get("product_profile", "") or "(none)")
            if learned_store.get("product_parts"):
                st.markdown("**Likely product parts / components**")
                st.markdown(
                    "<div class='chip-wrap'>" + "".join([f"<span class='chip gray'>{_safe(x)}</span>" for x in learned_store.get("product_parts", [])[:20]]) + "</div>",
                    unsafe_allow_html=True,
                )
            if learned_store.get("use_cases"):
                st.markdown("**Likely use cases / jobs-to-be-done**")
                st.markdown(
                    "<div class='chip-wrap'>" + "".join([f"<span class='chip yellow'>{_safe(x)}</span>" for x in learned_store.get("use_cases", [])[:20]]) + "</div>",
                    unsafe_allow_html=True,
                )
            st.markdown("**Glossary terms**")
            if learned_store.get("glossary_terms"):
                st.markdown(
                    "<div class='chip-wrap'>" + "".join([f"<span class='chip blue'>{_safe(w)} · {c}</span>" for w, c in learned_store["glossary_terms"][:30]]) + "</div>",
                    unsafe_allow_html=True,
                )
            grouped_del = _group_labels_by_family("Delighter", list(learned_store.get("labels", {}).get("Delighter", {}).keys())[:80], learned_store)
            grouped_det = _group_labels_by_family("Detractor", list(learned_store.get("labels", {}).get("Detractor", {}).keys())[:80], learned_store)
            st.markdown("**Learned delighters by L1 family**")
            for fam, labs in grouped_del.items():
                st.markdown(f"<div class='tiny'><b>{_safe(fam)}</b></div>", unsafe_allow_html=True)
                st.markdown("<div class='chip-wrap'>" + "".join([f"<span class='chip green'>{_safe(x)}</span>" for x in labs[:20]]) + "</div>", unsafe_allow_html=True)
            st.markdown("**Learned detractors by L1 family**")
            for fam, labs in grouped_det.items():
                st.markdown(f"<div class='tiny'><b>{_safe(fam)}</b></div>", unsafe_allow_html=True)
                st.markdown("<div class='chip-wrap'>" + "".join([f"<span class='chip red'>{_safe(x)}</span>" for x in labs[:20]]) + "</div>", unsafe_allow_html=True)

with tab_run:
    st.markdown("<div class='section-title'>Run Center</div>", unsafe_allow_html=True)
    st.markdown("<div class='section-sub'>One place to choose scope, choose run mode, inspect the plan, and start safely.</div>", unsafe_allow_html=True)

    rleft, rright = st.columns([1.45, 1.05])
    with rleft:
        st.selectbox(
            "Scope",
            ["Missing both", "Any missing", "Missing delighters only", "Missing detractors only"],
            key="scope_choice",
            help="Current scope is used for normal runs. Missing-both fast pass ignores this selector and always uses missing-both within the current filters.",
        )
        st.radio(
            "Run mode",
            [
                "First N in current scope",
                "All reviews in current scope",
                "Missing-both fast pass",
                "Rebuild selected rows (overwrite AI output)",
            ],
            key="run_mode",
        )

        if st.session_state.get("run_mode") == "First N in current scope":
            n_cols = st.columns([1.3, 1, 1, 1, 1])
            with n_cols[0]:
                bound_max = max(1, len(target))
                cur_n = int(st.session_state.get("n_to_process", 10) or 10)
                st.session_state["n_to_process"] = min(max(cur_n, 1), bound_max)
                st.number_input(
                    "How many rows from the top of scope?",
                    min_value=1,
                    max_value=bound_max,
                    step=1,
                    key="n_to_process",
                )

            def _set_n(v: int) -> None:
                st.session_state["n_to_process"] = min(max(int(v), 1), max(1, len(target)))

            with n_cols[1]:
                st.button("10", use_container_width=True, on_click=_set_n, args=(10,), key="set_n_10")
            with n_cols[2]:
                st.button("25", use_container_width=True, on_click=_set_n, args=(25,), key="set_n_25")
            with n_cols[3]:
                st.button("50", use_container_width=True, on_click=_set_n, args=(50,), key="set_n_50")
            with n_cols[4]:
                st.button("100", use_container_width=True, on_click=_set_n, args=(100,), key="set_n_100")

        if st.session_state.get("run_mode") == "Rebuild selected rows (overwrite AI output)":
            st.markdown(
                "<div class='danger-box'><b>Overwrite mode</b><br/>Selected AI slots will be cleared for the chosen rows, then rebuilt from scratch. Manual symptom columns are not touched.</div>",
                unsafe_allow_html=True,
            )
            st.radio(
                "Overwrite target",
                ["Current scope only", "All filtered reviews"],
                key="overwrite_target",
                horizontal=True,
            )
            st.checkbox("I understand this will replace existing AI output for the selected rows", key="confirm_overwrite")
        else:
            st.session_state["confirm_overwrite"] = False

    with rright:
        rows_for_run, overwrite_mode_selected, run_selection_label = _build_run_selection()
        estimated_batches = max(1, math.ceil(len(rows_for_run) / max(1, int(st.session_state.get("llm_batch_size", 6))))) if len(rows_for_run) else 0
        st.markdown(
            f"""
            <div class='info-card'>
              <div class='title'>Run summary</div>
              <div class='run-plan'>
                <div class='kv'><div class='k'>Filtered eligible</div><div class='v'>{len(work_filtered):,}</div></div>
                <div class='kv'><div class='k'>Current scope</div><div class='v'>{len(target):,}</div></div>
                <div class='kv'><div class='k'>Selected now</div><div class='v'>{len(rows_for_run):,}</div></div>
                <div class='kv'><div class='k'>Estimated batches</div><div class='v'>{estimated_batches:,}</div></div>
              </div>
              <div class='muted' style='margin-top:10px;'>{_safe(run_selection_label)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if client is None:
            st.warning("OpenAI is not configured, so runs are disabled.")
        elif len(rows_for_run) == 0:
            st.info("No rows match the current filters / scope / run mode.")
        elif overwrite_mode_selected:
            st.info("Overwrite rebuilds both sides for selected rows, even if they were not previously missing.")

        btn_cols = st.columns([1.5, 1])
        run_disabled = (client is None) or (len(rows_for_run) == 0) or (overwrite_mode_selected and not st.session_state.get("confirm_overwrite", False))
        with btn_cols[0]:
            start_run_btn = st.button(_run_button_label(rows_for_run, overwrite_mode_selected), use_container_width=True, disabled=run_disabled, key="start_run_btn")
        with btn_cols[1]:
            undo_btn = st.button("↩️ Undo last run", use_container_width=True, key="undo_last_run_btn")

        if overwrite_mode_selected and not st.session_state.get("confirm_overwrite", False):
            st.caption("Check the confirmation box to enable overwrite mode.")

    if start_run_btn:
        _run_symptomize(rows_for_run, overwrite_mode=overwrite_mode_selected)
        st.session_state["_flash_msg"] = ("success", f"Processed {int(st.session_state.get('last_run_processed_count', 0)):,} review(s).")
        st.rerun()
    if undo_btn:
        ok, msg = _undo_last_run()
        st.session_state["_flash_msg"] = ("success" if ok else "info", msg)
        st.rerun()

with tab_log:
    st.markdown("<div class='section-title'>Processed reviews</div>", unsafe_allow_html=True)
    current_processed_rows = st.session_state.get("processed_rows", []) or []
    if current_processed_rows and int(st.session_state.get("ui_log_limit", 40)) > 0:
        cov_num = int(st.session_state.get("ev_cov_num", 0) or 0)
        cov_den = int(st.session_state.get("ev_cov_den", 0) or 0)
        overall_cov = (cov_num / cov_den) if cov_den else 0.0
        st.caption(f"Evidence coverage for the latest run: {overall_cov * 100:.1f}% of written labels include at least one snippet.")
        for rec in current_processed_rows:
            head = f"Row {rec['Index']} — written Dets: {len(rec['Added_Detractors'])} • written Dels: {len(rec['Added_Delighters'])}"
            detected_total = len(rec.get("Detected_Detractors", []) or []) + len(rec.get("Detected_Delighters", []) or [])
            if detected_total:
                head += f" • detected total: {detected_total}"
            if rec[">10 Detractors Detected"] or rec[">10 Delighters Detected"]:
                head += " • ⚠ trimmed to 10"
            with st.expander(head):
                evidence_terms: List[str] = []
                for _, evs in (rec.get("Detected_Evidence_Detractors", {}) or {}).items():
                    evidence_terms.extend(evs or [])
                for _, evs in (rec.get("Detected_Evidence_Delighters", {}) or {}).items():
                    evidence_terms.extend(evs or [])

                st.markdown("**Verbatim**")
                st.markdown(highlight_text(rec["Verbatim"], evidence_terms), unsafe_allow_html=True)
                st.markdown(
                    "<div class='chip-wrap'>"
                    f"<span class='chip gray'>Needed detractors: {'Yes' if rec.get('Needs_Detractors', False) else 'No'}</span>"
                    f"<span class='chip gray'>Needed delighters: {'Yes' if rec.get('Needs_Delighters', False) else 'No'}</span>"
                    f"<span class='chip yellow'>Safety: {_safe(rec.get('Safety', 'Not Mentioned'))}</span>"
                    f"<span class='chip blue'>Reliability: {_safe(rec.get('Reliability', 'Not Mentioned'))}</span>"
                    f"<span class='chip purple'># Sessions: {_safe(rec.get('Sessions', 'Unknown'))}</span>"
                    "</div>",
                    unsafe_allow_html=True,
                )

                st.markdown("**Detractors detected**")
                st.markdown(
                    "<div class='chip-wrap'>" + "".join([f"<span class='chip red'>{_safe(lab)} · evidence {len((rec.get('Detected_Evidence_Detractors', {}) or {}).get(lab, []))}</span>" for lab in rec.get("Detected_Detractors", [])]) + "</div>",
                    unsafe_allow_html=True,
                )
                st.caption(str(rec.get("Detractor_Status", "")))

                st.markdown("**Delighters detected**")
                st.markdown(
                    "<div class='chip-wrap'>" + "".join([f"<span class='chip green'>{_safe(lab)} · evidence {len((rec.get('Detected_Evidence_Delighters', {}) or {}).get(lab, []))}</span>" for lab in rec.get("Detected_Delighters", [])]) + "</div>",
                    unsafe_allow_html=True,
                )
                st.caption(str(rec.get("Delighter_Status", "")))

                st.markdown("**Written to workbook**")
                st.markdown(
                    "<div class='chip-wrap'>"
                    + "".join([f"<span class='chip red'>{_safe(lab)}</span>" for lab in rec.get("Added_Detractors", [])])
                    + "".join([f"<span class='chip green'>{_safe(lab)}</span>" for lab in rec.get("Added_Delighters", [])])
                    + "</div>",
                    unsafe_allow_html=True,
                )
                if rec.get("NewCand_Delighters") or rec.get("NewCand_Detractors"):
                    st.markdown("**New symptom candidates**")
                    chips = "<div class='chip-wrap'>"
                    for x in rec.get("NewCand_Delighters", []) or []:
                        chips += f"<span class='chip green'>{_safe(x)}</span>"
                    for x in rec.get("NewCand_Detractors", []) or []:
                        chips += f"<span class='chip red'>{_safe(x)}</span>"
                    chips += "</div>"
                    st.markdown(chips, unsafe_allow_html=True)
                if rec.get("AliasSuggestions"):
                    st.markdown("**Alias suggestions**")
                    chips = "<div class='chip-wrap'>"
                    for tgt, alias, _, _ in rec["AliasSuggestions"]:
                        chips += f"<span class='chip yellow'>{_safe(alias)} → {_safe(tgt)}</span>"
                    chips += "</div>"
                    st.markdown(chips, unsafe_allow_html=True)
                with st.expander("Evidence snippets", expanded=False):
                    if rec.get("Detected_Evidence_Detractors"):
                        st.markdown("**Detractor evidence**")
                        for lab, evs in rec["Detected_Evidence_Detractors"].items():
                            for e in evs:
                                st.write(f"- {lab}: {e}")
                    if rec.get("Detected_Evidence_Delighters"):
                        st.markdown("**Delighter evidence**")
                        for lab, evs in rec["Detected_Evidence_Delighters"].items():
                            for e in evs:
                                st.write(f"- {lab}: {e}")
    elif int(st.session_state.get("ui_log_limit", 40)) == 0:
        st.info("Review log is disabled (fastest mode). Increase 'Show last N processed reviews' in the sidebar to see row-level details.")
    else:
        st.info("No processed reviews yet in this session.")

with tab_inbox:
    st.markdown("<div class='section-title'>Inbox: new symptoms and alias suggestions</div>", unsafe_allow_html=True)
    whitelist_all = set(DELIGHTERS + DETRACTORS)
    alias_all = set([a for lst in ALIASES.values() for a in lst]) if ALIASES else set()
    wl_canon = {_canon_simple(x) for x in whitelist_all}
    ali_canon = {_canon_simple(x) for x in alias_all}

    def _is_existing_label_or_alias(s: str) -> bool:
        k = _canon_simple(s)
        return (k in wl_canon) or (k in ali_canon)

    def _filter_new_symptom_candidates(cands: Dict[Tuple[str, str], Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
        out: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for (lab, side), rec in cands.items():
            lab2 = normalize_theme_label(lab, side)
            if not lab2 or _is_existing_label_or_alias(lab2):
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

    def _filter_alias_candidates(cands: Dict[Tuple[str, str, str], Dict[str, Any]]) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
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

    if not new_symptom_candidates_f and not alias_suggestion_candidates_f:
        st.info("No inbox items yet. Run symptomization first, then review new symptom candidates and alias suggestions here.")
    else:
        tabs_inbox = st.tabs(["New Symptoms", "Alias Suggestions"])

        def _mk_examples(refs: List[int], n: int = 3) -> str:
            ex: List[str] = []
            for ridx in refs[:n]:
                try:
                    ex.append(str(df.loc[ridx, "Verbatim"])[:200])
                except Exception:
                    pass
            return " | ".join(["— " + e for e in ex])

        with tabs_inbox[0]:
            rows_tbl = []
            for (lab, side), rec in sorted(new_symptom_candidates_f.items(), key=lambda kv: (-int(kv[1].get("count", 0)), kv[0][0])):
                rows_tbl.append({"Add": False, "Label": lab, "Side": side, "Count": int(rec.get("count", 0)), "Examples": _mk_examples(list(rec.get("refs", [])))})
            tbl_new = pd.DataFrame(rows_tbl) if rows_tbl else pd.DataFrame(columns=["Add", "Label", "Side", "Count", "Examples"])
            editor_new = st.data_editor(
                tbl_new,
                num_rows="fixed",
                use_container_width=True,
                column_config={
                    "Add": st.column_config.CheckboxColumn(help="Select to add as a new symptom"),
                    "Label": st.column_config.TextColumn(),
                    "Side": st.column_config.SelectboxColumn(options=["Delighter", "Detractor"]),
                    "Count": st.column_config.NumberColumn(format="%d"),
                    "Examples": st.column_config.TextColumn(width="large"),
                },
                key="inbox_new_editor",
            )

        with tabs_inbox[1]:
            rows_tbl2 = []
            for (tgt, alias, side), rec in sorted(alias_suggestion_candidates_f.items(), key=lambda kv: (-int(kv[1].get("count", 0)), kv[0][0], kv[0][1])):
                rows_tbl2.append({"Add": False, "Target Symptom": tgt, "Alias": alias, "Side": side, "Count": int(rec.get("count", 0)), "Examples": _mk_examples(list(rec.get("refs", [])))})
            tbl_alias = pd.DataFrame(rows_tbl2) if rows_tbl2 else pd.DataFrame(columns=["Add", "Target Symptom", "Alias", "Side", "Count", "Examples"])
            editor_alias = st.data_editor(
                tbl_alias,
                num_rows="fixed",
                use_container_width=True,
                column_config={
                    "Add": st.column_config.CheckboxColumn(help="Select to add as an alias"),
                    "Target Symptom": st.column_config.TextColumn(disabled=True),
                    "Alias": st.column_config.TextColumn(),
                    "Side": st.column_config.SelectboxColumn(options=["Delighter", "Detractor"]),
                    "Count": st.column_config.NumberColumn(format="%d"),
                    "Examples": st.column_config.TextColumn(width="large"),
                },
                key="inbox_alias_editor",
            )

        with st.form("apply_inbox_updates_form", clear_on_submit=False):
            st.markdown("Apply the selected updates to the **Symptoms** sheet and download the updated workbook.")
            apply_btn = st.form_submit_button("✅ Apply selected updates")

        if apply_btn:
            new_to_add: List[Tuple[str, str]] = []
            alias_to_add: List[Tuple[str, str]] = []
            try:
                if isinstance(editor_new, pd.DataFrame) and not editor_new.empty:
                    for _, r_ in editor_new.iterrows():
                        if bool(r_.get("Add", False)) and str(r_.get("Label", "")).strip():
                            lab = normalize_theme_label(str(r_["Label"]).strip(), str(r_.get("Side", "Delighter")))
                            side = str(r_.get("Side", "Delighter")).strip()
                            if lab and not _is_existing_label_or_alias(lab):
                                new_to_add.append((lab, side))
            except Exception:
                pass
            try:
                if isinstance(editor_alias, pd.DataFrame) and not editor_alias.empty:
                    for _, r_ in editor_alias.iterrows():
                        if bool(r_.get("Add", False)) and str(r_.get("Alias", "")).strip() and str(r_.get("Target Symptom", "")).strip():
                            tgt = str(r_["Target Symptom"]).strip()
                            als = normalize_theme_label(str(r_["Alias"]).strip(), str(r_.get("Side", "Detractor")))
                            if tgt and als and not _is_existing_label_or_alias(als) and _canon_simple(als) != _canon_simple(tgt):
                                alias_to_add.append((tgt, als))
            except Exception:
                pass

            if new_to_add or alias_to_add:
                updated_bytes = apply_symptoms_updates_to_workbook(st.session_state["uploaded_bytes"], new_symptoms=new_to_add, alias_additions=alias_to_add)
                st.download_button(
                    "⬇️ Download updated Symptoms workbook",
                    data=updated_bytes,
                    file_name="Symptoms_Updated.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                st.success(f"Applied {len(new_to_add)} new symptom(s) and {len(alias_to_add)} alias addition(s).")
            else:
                st.info("No updates were selected.")

with tab_export:
    st.markdown("<div class='section-title'>Exports</div>", unsafe_allow_html=True)
    ex1, ex2 = st.columns(2)
    with ex1:
        st.markdown("<div class='info-card tight'><div class='title'>Symptomized workbook</div><div class='muted'>Build the XLSX once, then download it. The export preserves your original workbook and writes AI output to the exact template columns.</div></div>", unsafe_allow_html=True)
        prep = st.button("🧾 Prepare XLSX export", use_container_width=True, key="prepare_export_btn")
        if prep:
            with st.spinner("Building XLSX export…"):
                st.session_state["export_bytes"] = generate_template_workbook_bytes(
                    st.session_state["uploaded_bytes"],
                    st.session_state["df_work"],
                    processed_idx=st.session_state.get("processed_idx_set", set()) or None,
                    overwrite_processed_slots=False,
                )
            st.success("Export prepared.")
        export_bytes = st.session_state.get("export_bytes", None)
        st.download_button(
            "⬇️ Download symptomized workbook",
            data=(export_bytes or b""),
            file_name=f"{file_base}_Symptomized.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            disabled=(export_bytes is None),
        )

    with ex2:
        st.markdown("<div class='info-card tight'><div class='title'>Symptoms catalog</div><div class='muted'>Quick export of the current Symptoms sheet, including aliases when present.</div></div>", unsafe_allow_html=True)
        sym_df = pd.DataFrame({
            "Symptom": (DELIGHTERS + DETRACTORS),
            "Type": ["Delighter"] * len(DELIGHTERS) + ["Detractor"] * len(DETRACTORS),
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
            "⬇️ Download symptoms catalog",
            sym_bytes.getvalue(),
            file_name=f"{file_base}_Symptoms_Catalog.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

st.divider()
st.caption(
    "v8.0 — rewritten for a smoother and more accurate workflow. Safer overwrite, cleaner run center, batch symptomization, prelearn, inbox, exact template export, and session persistence."
)
