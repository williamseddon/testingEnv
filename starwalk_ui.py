# starwalk_ui_v7_4_prelearn_canonical.py
# Evidence-Locked Labeling + PRELEARN product knowledge + canonical merge for new symptoms
# Keeps ALL v7.3 features: ETA, presets, overwrite, undo, similarity guard, polished UI,
# evidence highlighting, theme-focused inbox, overwrite & symptomize ALL (row 1),
# and exact template export (K–T detractors, U–AD delighters, AE/AF/AG meta).
#
# New in v7.4:
# - Prelearn (recommended, pre-selected): product knowledge + learned symptom themes library
# - Canonical merge: unlisted candidates map to existing/learned symptoms (ex: Learning Curve -> Initially Complicated)
# - Alias Suggestions Inbox (optional): approve alias updates into Symptoms sheet
# - One-call LLM: symptoms + meta in one request (faster)
# - Evidence verification: ensure snippets are truly substrings of the review
# - Persist learned knowledge in exported workbook (AI Learned sheet)

import streamlit as st
import pandas as pd
import io, os, json, difflib, time, re, html, math
from typing import List, Dict, Tuple, Optional, Set, Any

# Optional: numpy for faster cosine similarity if embeddings are used
try:
    import numpy as np
    _HAS_NUMPY = True
except Exception:
    np = None  # type: ignore
    _HAS_NUMPY = False

# Optional: OpenAI
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    OpenAI = None  # type: ignore
    _HAS_OPENAI = False

# Excel handling
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from openpyxl.styles import PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter

# ------------------- App Setup -------------------
APP_VERSION = "v7.4"
LEARNED_SHEET_NAME = "AI Learned"
st.set_page_config(layout="wide", page_title=f"Review Symptomizer — {APP_VERSION}")
st.title(f"✨ Review Symptomizer — {APP_VERSION}")
st.caption(
    "Exact export (K–T dets, U–AD dels, AE/AF/AG meta) • ETA + presets + overwrite • Undo • "
    "New-symptom inbox • Tiles UI • Similarity guard • Evidence-locked labeling • "
    "Prelearn (product knowledge + learned themes) • Canonical merge + Alias Suggestions • "
    "Persist learned knowledge"
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
      .kcard { border-radius: 16px; padding: 14px 16px; background: rgba(255,255,255,.92); border: 1px solid #e6eaf0; }
      .kcard h4 { margin: 0 0 8px 0; }
      .kgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
      .small { font-size: 12px; color:#475569; }
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

def _canon(s: str) -> str:
    return " ".join(str(s).split()).lower().strip()

def _canon_simple(s: str) -> str:
    return "".join(ch for ch in _canon(s) if ch.isalnum())

def _safe_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default

def truncate_text(s: str, max_chars: int = 600) -> str:
    t = str(s or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_chars]

def md5_short(payload: str) -> str:
    try:
        import hashlib
        return hashlib.md5(payload.encode("utf-8")).hexdigest()[:10]
    except Exception:
        return str(len(payload))

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

def evidence_is_valid(verbatim: str, ev: str, min_len: int = 3) -> bool:
    if not ev or len(ev.strip()) < min_len:
        return False
    return ev.strip().lower() in str(verbatim or "").lower()

# ------------------- Load Symptoms sheet -------------------
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
                if lbl and als:
                    als_norm = als.replace(",", "|")
                    alias_map[lbl] = [p.strip() for p in als_norm.split("|") if p.strip()]
    else:
        for lc, orig in lowcols.items():
            if ("delight" in lc) or ("positive" in lc) or lc in {"pros"}:
                delighters.extend(_clean(df_sym[orig]))
            if ("detract" in lc) or ("negative" in lc) or lc in {"cons"}:
                detractors.extend(_clean(df_sym[orig]))
        delighters = list(dict.fromkeys(delighters))
        detractors = list(dict.fromkeys(detractors))

    return delighters, detractors, alias_map

def build_canonical_maps(delighters: List[str], detractors: List[str], alias_map: Dict[str, List[str]]):
    del_map = {_canon(x): x for x in delighters}
    det_map = {_canon(x): x for x in detractors}
    alias_to_label: Dict[str, str] = {}
    for label, aliases in (alias_map or {}).items():
        for a in aliases:
            alias_to_label[_canon(a)] = label
    return del_map, det_map, alias_to_label

# ------------------- Schema detection -------------------
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

# ------------------- Template mapping -------------------
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

# ------------------- Enums -------------------
SAFETY_ENUM = ["Not Mentioned", "Concern", "Positive"]
RELIABILITY_ENUM = ["Not Mentioned", "Negative", "Neutral", "Positive"]
SESSIONS_ENUM = ["0", "1", "2–3", "4–9", "10+", "Unknown"]

# ------------------- Theme normalization -------------------
THEME_RULES = [
    # detractors
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
    # NEW: complexity / learning curve
    (re.compile(r"\b(learning\s+curve|hard\s+to\s+learn|not\s+intuitive|confusing|complicated|initially\s+complicated|takes?\s+time\s+to\s+(learn|get\s+used\s+to))\b", re.I),
     {"det": "Learning Curve"}),

    # delighters
    (re.compile(r"\b(absolutely|totally|really)?\s*love(s|d)?\b|\bworks\s+(amazing|great|fantastic|perfect)\b|\boverall\b.+\b(great|good|positive|happy)\b", re.I),
     {"del": "Overall Satisfaction"}),
    (re.compile(r"\b(easy|quick|simple)\s+to\s+(use|clean|attach|remove)\b|\buser[-\s]?friendly\b", re.I),
     {"del": "Ease Of Use"}),
    (re.compile(r"\b(fast|quick)\s+(dry|drying)\b|\bdries\s+quickly\b", re.I),
     {"del": "Fast Drying"}),
    (re.compile(r"\b(shine|smooth|sleek|frizz\s*(?:free|control))\b", re.I),
     {"del": "Frizz Control/Shine"}),
    (re.compile(r"\b(attachments?|accessories?)\b.+\b(handy|useful|versatile|helpful)\b", re.I),
     {"del": "Attachment Usability"}),
]

def _short_title(s: str) -> str:
    s = re.sub(r"[\s\-_/]+", " ", s.strip())
    s = re.sub(r"[^\w\s+/]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.title()

def thematize_label(raw: str, side_hint: str = "", use_llm_fallback: bool = False) -> str:
    """Convert a raw phrase into a short, reusable theme label."""
    txt = str(raw or "").strip()
    if not txt:
        return ""
    # 1) Regex rules first
    for rx, mapping in THEME_RULES:
        if rx.search(txt):
            if side_hint.lower().startswith("del") and mapping.get("del"):
                return mapping["del"]
            if side_hint.lower().startswith("det") and mapping.get("det"):
                return mapping["det"]
            return mapping.get("del") or mapping.get("det") or _short_title(txt[:32])

    # 2) Optional LLM fallback (OFF by default for speed/cost)
    if use_llm_fallback and 'client' in globals() and globals().get('client') is not None:
        try:
            sys = (
                "You convert raw customer phrases into VERY SHORT theme labels.\n"
                "Return ONLY a concise noun phrase (1–3 words), Title Case, no punctuation or emojis.\n"
                "Slashes are allowed (e.g., Hair Loss/Pull)."
            )
            u = f"Raw phrase: {txt}\nSide hint: {side_hint or 'Unknown'}"
            resp = client.chat.completions.create(
                model=globals().get('selected_model', 'gpt-4o'),
                temperature=0,
                messages=[{"role":"system","content":sys},{"role":"user","content":u}],
            )
            candidate = (resp.choices[0].message.content or "").strip()
            if 1 <= len(candidate) <= 40 and re.search(r"[A-Za-z]", candidate):
                return candidate
        except Exception:
            pass

    # 3) Heuristic fallback
    return _short_title(txt[:40])

# ------------------- Session state -------------------
def _ensure_state():
    if "_label_cache" not in st.session_state:
        st.session_state["_label_cache"] = {}
    if "_learned" not in st.session_state:
        st.session_state["_learned"] = {
            "fingerprint": None,
            "knowledge": None,  # dict
            "themes": {"Delighter": {}, "Detractor": {}},  # label -> {count:int, examples:list, aliases:set}
            "alias_suggestions": {},  # canonical_label -> set(alias)
            "embedding_index": {
                "hash": None,
                "model": None,
                "side": None,
                "labels": [],
                "vectors": None,  # np.ndarray or list
            }
        }
    if "undo_stack" not in st.session_state:
        st.session_state["undo_stack"] = []
    return st.session_state

STATE = _ensure_state()

def _symptom_list_version(delighters: List[str], detractors: List[str], aliases: Dict[str, List[str]]) -> str:
    payload = json.dumps({"del": delighters, "det": detractors, "ali": aliases}, sort_keys=True, ensure_ascii=False)
    return md5_short(payload)

def _dataset_fingerprint(df: pd.DataFrame) -> str:
    # stable-ish fingerprint: size + first/last few verbatims
    parts = [str(len(df))]
    if "Verbatim" in df.columns and len(df) > 0:
        head = "|".join(df["Verbatim"].head(5).astype(str).tolist())
        tail = "|".join(df["Verbatim"].tail(5).astype(str).tolist())
        parts.append(head)
        parts.append(tail)
    return md5_short("::".join(parts))

# ------------------- Embedding-based canonical resolver -------------------
def cosine_sim(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    if _HAS_NUMPY and isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        va = np.array(a, dtype=float)
        vb = np.array(b, dtype=float)
        na = float(np.linalg.norm(va))
        nb = float(np.linalg.norm(vb))
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))
    # fallback pure python
    dot = sum(float(x)*float(y) for x, y in zip(a, b))
    na = math.sqrt(sum(float(x)*float(x) for x in a))
    nb = math.sqrt(sum(float(y)*float(y) for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return float(dot / (na * nb))

def build_known_catalog(
    approved_del: List[str], approved_det: List[str],
    approved_aliases: Dict[str, List[str]],
    learned: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Returns a catalog for resolution:
    {
      "Delighter": {"labels":[...], "alias_to_label":{canon:label}},
      "Detractor": {...}
    }
    """
    out = {}
    for side, base_labels in [("Delighter", approved_del), ("Detractor", approved_det)]:
        labels = list(dict.fromkeys([x for x in (base_labels or []) if str(x).strip()]))
        # learned canonical themes
        learned_labels = list((learned.get("themes", {}).get(side, {}) or {}).keys())
        for ll in learned_labels:
            if ll and ll not in labels:
                labels.append(ll)

        alias_to_label = {}
        # approved aliases
        for lbl, als in (approved_aliases or {}).items():
            if lbl in labels:
                for a in (als or []):
                    alias_to_label[_canon_simple(a)] = lbl

        # learned aliases (stored inside learned themes)
        for lbl, meta in (learned.get("themes", {}).get(side, {}) or {}).items():
            for a in (meta.get("aliases") or set()):
                alias_to_label[_canon_simple(a)] = lbl

        out[side] = {"labels": labels, "alias_to_label": alias_to_label}
    return out

def maybe_build_embedding_index(
    client,
    embed_model: str,
    side: str,
    labels: List[str],
    force: bool = False,
) -> None:
    """Build or reuse an embedding index for a specific side+label list."""
    if client is None:
        return
    labels = [x for x in (labels or []) if str(x).strip()]
    if not labels:
        return

    h = md5_short(json.dumps({"side": side, "labels": labels, "model": embed_model}, ensure_ascii=False))
    idx = STATE["_learned"]["embedding_index"]
    if (not force) and idx.get("hash") == h and idx.get("side") == side and idx.get("model") == embed_model:
        return

    try:
        resp = client.embeddings.create(model=embed_model, input=labels)
        vecs = [d.embedding for d in resp.data]
        idx.update({"hash": h, "side": side, "model": embed_model, "labels": labels, "vectors": vecs})
    except Exception:
        # disable vectors if fails
        idx.update({"hash": h, "side": side, "model": embed_model, "labels": labels, "vectors": None})

def resolve_candidate_to_canonical(
    candidate: str,
    side: str,
    catalog: Dict[str, Any],
    prefer_existing: bool = True,
    string_merge_threshold: float = 0.94,
    semantic_merge_threshold: float = 0.86,
    use_semantic: bool = True,
    client=None,
    embed_model: str = "text-embedding-3-small",
) -> Dict[str, Any]:
    """
    Map candidate to an existing/learned canonical label to avoid sprawl.
    Returns:
      {
        "canonical": <label>,
        "action": "keep" | "merge",
        "match_type": "exact"|"alias"|"fuzzy"|"semantic"|"none",
        "score": float,
        "alias": <candidate_if_merged_else_"">
      }
    """
    cand = str(candidate or "").strip()
    if not cand:
        return {"canonical": "", "action": "keep", "match_type": "none", "score": 0.0, "alias": ""}

    cand_theme = thematize_label(cand, side, use_llm_fallback=False)
    cand_key = _canon_simple(cand_theme)

    labels = (catalog.get(side, {}) or {}).get("labels", []) or []
    alias_to_label = (catalog.get(side, {}) or {}).get("alias_to_label", {}) or {}

    # 1) Alias/exact match
    for lbl in labels:
        if _canon_simple(lbl) == cand_key:
            return {"canonical": lbl, "action": "merge" if lbl != cand_theme else "keep",
                    "match_type": "exact", "score": 1.0, "alias": cand_theme if lbl != cand_theme else ""}
    if cand_key in alias_to_label:
        lbl = alias_to_label[cand_key]
        return {"canonical": lbl, "action": "merge", "match_type": "alias", "score": 1.0, "alias": cand_theme}

    # 2) Fuzzy string match to labels
    best_lbl = ""
    best_score = 0.0
    for lbl in labels:
        sc = difflib.SequenceMatcher(None, _canon(lbl), _canon(cand_theme)).ratio()
        if sc > best_score:
            best_score = sc; best_lbl = lbl
    if best_lbl and best_score >= string_merge_threshold:
        return {"canonical": best_lbl, "action": "merge", "match_type": "fuzzy", "score": float(best_score), "alias": cand_theme}

    # 3) Semantic (embeddings) match (optional)
    if use_semantic and client is not None and labels:
        maybe_build_embedding_index(client, embed_model, side, labels)
        idx = STATE["_learned"]["embedding_index"]
        vecs = idx.get("vectors")
        idx_labels = idx.get("labels", [])
        if vecs is not None and idx_labels:
            try:
                v = client.embeddings.create(model=embed_model, input=[cand_theme]).data[0].embedding
                best_i, best_s = -1, 0.0
                for i, base in enumerate(vecs):
                    s = cosine_sim(v, base)
                    if s > best_s:
                        best_s = s; best_i = i
                if best_i >= 0 and best_s >= semantic_merge_threshold:
                    return {"canonical": idx_labels[best_i], "action": "merge", "match_type": "semantic",
                            "score": float(best_s), "alias": cand_theme}
            except Exception:
                pass

    # 4) Keep as truly new
    return {"canonical": cand_theme, "action": "keep", "match_type": "none", "score": float(best_score), "alias": ""}

# ------------------- LLM labeler (ONE CALL: symptoms + meta) -------------------
def _ensure_label_cache():
    return STATE["_label_cache"]

def format_knowledge_hint(knowledge: Optional[Dict[str, Any]], max_chars: int = 900) -> str:
    if not knowledge:
        return ""
    bits = []
    for k in ["what_it_is", "key_parts_and_terms", "usage_patterns", "common_failures", "common_delights", "care_and_maintenance", "safety_considerations"]:
        v = knowledge.get(k)
        if not v:
            continue
        if isinstance(v, list):
            v2 = "; ".join([str(x) for x in v[:10] if str(x).strip()])
        else:
            v2 = str(v).strip()
        if v2:
            bits.append(f"{k.replace('_',' ').title()}: {v2}")
    txt = "\n".join(bits).strip()
    txt = truncate_text(txt, max_chars=max_chars)
    return txt

def _openai_labeler_onecall(
    verbatim: str,
    client,
    model: str,
    temperature: float,
    delighters: List[str],
    detractors: List[str],
    alias_map: Dict[str, List[str]],
    del_map: Dict[str, str],
    det_map: Dict[str, str],
    alias_to_label: Dict[str, str],
    known_theme_hints: Optional[List[str]] = None,
    knowledge_hint: str = "",
    require_evidence: bool = True,
    verify_evidence: bool = True,
    max_ev_per_label: int = 2,
    max_ev_chars: int = 120,
) -> Tuple[List[str], List[str], List[str], List[str], Dict[str, List[str]], Dict[str, List[str]], str, str, str]:
    """
    Returns:
      dels, dets, unl_dels, unl_dets, ev_del_map, ev_det_map, safety, reliability, sessions
    """
    if (client is None) or (not verbatim or not verbatim.strip()):
        return [], [], [], [], {}, {}, "Not Mentioned", "Not Mentioned", "Unknown"

    # Cache key
    v = _symptom_list_version(delighters, detractors, alias_map)
    hints_hash = md5_short(json.dumps({"h": known_theme_hints or [], "k": knowledge_hint[:500]}, ensure_ascii=False))
    key = ("lab_meta", _canon(verbatim), model, f"{float(temperature):.2f}", v, hints_hash,
           require_evidence, verify_evidence, max_ev_per_label, max_ev_chars)
    cache = _ensure_label_cache()
    if key in cache:
        return cache[key]

    # Keep known_theme_hints small to avoid token blowups
    hints = [x for x in (known_theme_hints or []) if str(x).strip()]
    hints = list(dict.fromkeys(hints))[:120]

    sys = "\n".join([
        "You label consumer reviews with predefined symptom lists.",
        "Return STRICT JSON with this schema:",
        '{"meta":{"safety":"<enum>","reliability":"<enum>","sessions":"<enum>"},'
        ' "detractors":[{"label":"<allowed detractor label>","evidence":["<exact substring>", "..."]}],'
        ' "delighters":[{"label":"<allowed delighter label>","evidence":["<exact substring>", "..."]}],'
        ' "unlisted_detractors":["<theme>", "..."], "unlisted_delighters":["<theme>", "..."]}',
        "",
        "Meta enums:",
        f"- safety one of {SAFETY_ENUM}",
        f"- reliability one of {RELIABILITY_ENUM}",
        f"- sessions one of {SESSIONS_ENUM}",
        "",
        "Rules:",
        f"- Evidence MUST be exact substrings from the review. Each ≤ {max_ev_chars} chars. Up to {max_ev_per_label} per label.",
        "- Only include a label if there is clear textual support in the review.",
        "- Use the allowed lists for detractors/delighters. If close wording appears, pick the closest allowed label.",
        "- For unlisted_* items, RETURN A THEME (1–3 word noun phrase, Title Case). Slashes allowed. No emojis.",
        "- Before proposing an unlisted theme, try to reuse an existing known theme if it matches the meaning.",
        "- Cap to maximum 10 detractors and 10 delighters.",
        "",
        "Important:",
        "- If you include a label, provide at least one evidence snippet that directly supports it.",
    ])

    user_payload = {
        "review": verbatim.strip(),
        "allowed_delighters": delighters,
        "allowed_detractors": detractors,
        "alias_map": alias_map,               # helps model reuse existing label names
        "known_themes": hints,                # helps avoid new variants (learning curve vs initially complicated)
        "product_knowledge_hint": knowledge_hint[:900] if knowledge_hint else ""
    }

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=float(temperature),
            messages=[{"role": "system", "content": sys},
                      {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}],
            response_format={"type": "json_object"}
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
    except Exception:
        out = ([], [], [], [], {}, {}, "Not Mentioned", "Not Mentioned", "Unknown")
        cache[key] = out
        return out

    raw_meta = data.get("meta", {}) or {}
    safety = str(raw_meta.get("safety", "Not Mentioned")).strip()
    reliability = str(raw_meta.get("reliability", "Not Mentioned")).strip()
    sessions = str(raw_meta.get("sessions", "Unknown")).strip()
    safety = safety if safety in SAFETY_ENUM else "Not Mentioned"
    reliability = reliability if reliability in RELIABILITY_ENUM else "Not Mentioned"
    sessions = sessions if sessions in SESSIONS_ENUM else "Unknown"

    raw_dels = data.get("delighters", []) or []
    raw_dets = data.get("detractors", []) or []
    unl_dels = [x for x in (data.get("unlisted_delighters", []) or [])][:10]
    unl_dets = [x for x in (data.get("unlisted_detractors", []) or [])][:10]

    def _canon_map_item(obj: Any, side: str) -> Tuple[Optional[str], List[str]]:
        try:
            lbl_raw = str(obj.get("label", "")).strip()
            evs = [str(e)[:max_ev_chars] for e in (obj.get("evidence", []) or [])
                   if isinstance(e, str) and e.strip()]
        except Exception:
            lbl_raw, evs = "", []
        key2 = _canon(lbl_raw)

        # map through canonical maps + alias-to-label for label name normalization
        if side == "del":
            label = del_map.get(key2) or alias_to_label.get(key2)
            allowed = set(delighters)
        else:
            label = det_map.get(key2) or alias_to_label.get(key2)
            allowed = set(detractors)

        if not label or label not in allowed:
            return None, []

        # evidence verification / requirement
        if verify_evidence:
            evs = [e for e in evs if evidence_is_valid(verbatim, e)]
        evs = evs[:max_ev_per_label]

        if require_evidence and len(evs) == 0:
            return None, []
        return label, evs

    dels: List[str] = []
    dets: List[str] = []
    ev_del_map: Dict[str, List[str]] = {}
    ev_det_map: Dict[str, List[str]] = {}

    for obj in raw_dels:
        label, evs = _canon_map_item(obj, side="del")
        if label and (label not in dels):
            dels.append(label)
            ev_del_map[label] = evs
        if len(dels) >= 10:
            break

    for obj in raw_dets:
        label, evs = _canon_map_item(obj, side="det")
        if label and (label not in dets):
            dets.append(label)
            ev_det_map[label] = evs
        if len(dets) >= 10:
            break

    out = (dels, dets, unl_dels, unl_dets, ev_del_map, ev_det_map, safety, reliability, sessions)
    cache[key] = out
    return out

# ------------------- Prelearn: product knowledge + learned themes -------------------
def stratified_sample_indices(df: pd.DataFrame, n: int, rating_col: str = "Star Rating") -> List[int]:
    if n <= 0:
        return []
    if rating_col not in df.columns:
        return df.index.tolist()[:n]
    # bucket ratings into 1..5 if possible
    buckets: Dict[int, List[int]] = {1: [], 2: [], 3: [], 4: [], 5: []}
    for idx, val in df[rating_col].items():
        try:
            r = int(float(val))
        except Exception:
            continue
        if r in buckets:
            buckets[r].append(idx)
    # allocate evenly
    per = max(1, n // 5)
    picks = []
    for r in [1,2,3,4,5]:
        picks.extend(buckets[r][:per])
    # fill remainder
    if len(picks) < n:
        remaining = [i for i in df.index.tolist() if i not in set(picks)]
        picks.extend(remaining[:(n - len(picks))])
    return picks[:n]

def llm_prelearn_batch(
    client,
    model: str,
    temperature: float,
    reviews: List[Dict[str, Any]],
    max_themes_each: int = 12,
) -> Dict[str, Any]:
    """
    reviews: [{ "text": "...", "star": 5, "idx": 123 }, ...]
    """
    if client is None or not reviews:
        return {"knowledge": None, "themes": {"Delighter": [], "Detractor": []}}

    sys = "\n".join([
        "You are a product-insight miner for consumer reviews.",
        "Given a batch of reviews, you will extract:",
        "1) A compact product knowledge card (facts inferred from reviews).",
        "2) A list of recurring delighter themes and detractor themes.",
        "",
        "Return STRICT JSON with schema:",
        '{"knowledge":{"what_it_is":"...","key_parts_and_terms":["..."],"usage_patterns":["..."],'
        '"common_failures":["..."],"common_delights":["..."],"care_and_maintenance":["..."],"safety_considerations":["..."]},'
        '"themes":{"Delighter":[{"label":"<1–3 words Title Case>","aliases":["..."],"examples":["..."]}],'
        '"Detractor":[{"label":"<1–3 words Title Case>","aliases":["..."],"examples":["..."]}]}}',
        "",
        "Rules:",
        f"- Provide at most {max_themes_each} themes per side.",
        "- Theme labels must be concise (1–3 words), Title Case; slashes allowed; no emojis.",
        "- Examples should be short excerpts (not necessarily exact substrings).",
        "- Use consistent wording across themes; avoid near-duplicates.",
    ])

    # keep payload small
    payload = []
    for r in reviews:
        payload.append({
            "idx": _safe_int(r.get("idx", -1)),
            "star": r.get("star", None),
            "text": truncate_text(r.get("text", ""), 450)
        })

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=float(temperature),
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": json.dumps({"reviews": payload}, ensure_ascii=False)}
            ],
            response_format={"type": "json_object"}
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        return {
            "knowledge": data.get("knowledge", None),
            "themes": data.get("themes", {"Delighter": [], "Detractor": []}) or {"Delighter": [], "Detractor": []}
        }
    except Exception:
        return {"knowledge": None, "themes": {"Delighter": [], "Detractor": []}}

def merge_knowledge_cards(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    # light heuristic merge
    out: Dict[str, Any] = {
        "what_it_is": "",
        "key_parts_and_terms": [],
        "usage_patterns": [],
        "common_failures": [],
        "common_delights": [],
        "care_and_maintenance": [],
        "safety_considerations": [],
    }
    if not cards:
        return out
    for c in cards:
        if not isinstance(c, dict):
            continue
        if not out["what_it_is"] and c.get("what_it_is"):
            out["what_it_is"] = str(c.get("what_it_is")).strip()
        for k in ["key_parts_and_terms","usage_patterns","common_failures","common_delights","care_and_maintenance","safety_considerations"]:
            v = c.get(k, [])
            if isinstance(v, list):
                out[k].extend([str(x).strip() for x in v if str(x).strip()])
            elif isinstance(v, str) and v.strip():
                out[k].append(v.strip())

    # dedupe lists
    for k in ["key_parts_and_terms","usage_patterns","common_failures","common_delights","care_and_maintenance","safety_considerations"]:
        seen = set()
        dedup = []
        for x in out[k]:
            cx = _canon_simple(x)
            if cx and cx not in seen:
                seen.add(cx); dedup.append(x)
        out[k] = dedup[:20]
    out["what_it_is"] = truncate_text(out["what_it_is"], 280)
    return out

def prelearn_from_df(
    df: pd.DataFrame,
    client,
    model: str,
    temperature: float,
    max_reviews: int = 800,
    batch_size: int = 60,
    stratify_by_rating: bool = True,
) -> None:
    """
    Populates STATE["_learned"]["knowledge"] and STATE["_learned"]["themes"].
    """
    fp = _dataset_fingerprint(df)
    STATE["_learned"]["fingerprint"] = fp

    idxs = []
    if stratify_by_rating and "Star Rating" in df.columns:
        idxs = stratified_sample_indices(df, min(max_reviews, len(df)), rating_col="Star Rating")
    else:
        idxs = df.index.tolist()[:min(max_reviews, len(df))]

    reviews = []
    for idx in idxs:
        txt = str(df.loc[idx, "Verbatim"]) if "Verbatim" in df.columns else ""
        star = df.loc[idx, "Star Rating"] if "Star Rating" in df.columns else None
        reviews.append({"idx": int(idx), "text": txt, "star": star})

    # batch
    batches = [reviews[i:i+batch_size] for i in range(0, len(reviews), batch_size)]
    prog = st.progress(0.0)
    kb_cards = []
    themes_acc = {"Delighter": {}, "Detractor": {}}

    for bi, b in enumerate(batches, start=1):
        out = llm_prelearn_batch(client, model=model, temperature=max(0.0, min(0.3, float(temperature))), reviews=b)
        if isinstance(out.get("knowledge"), dict):
            kb_cards.append(out["knowledge"])

        themes = out.get("themes", {}) or {}
        for side in ["Delighter","Detractor"]:
            for item in (themes.get(side, []) or []):
                label = thematize_label(str(item.get("label","")), side, use_llm_fallback=False)
                if not label:
                    continue
                aliases = [str(a).strip() for a in (item.get("aliases", []) or []) if str(a).strip()]
                examples = [truncate_text(str(e), 180) for e in (item.get("examples", []) or []) if str(e).strip()]
                bucket = themes_acc[side].setdefault(label, {"count": 0, "examples": [], "aliases": set()})
                bucket["count"] += 1
                for a in aliases[:8]:
                    bucket["aliases"].add(a)
                for e in examples[:3]:
                    if e and e not in bucket["examples"]:
                        bucket["examples"].append(e)

        prog.progress(bi / max(1, len(batches)))

    knowledge = merge_knowledge_cards(kb_cards)
    STATE["_learned"]["knowledge"] = knowledge
    STATE["_learned"]["themes"] = themes_acc

# ------------------- Load/Save learned sheet in workbook -------------------
def load_learned_from_workbook(file_bytes: bytes) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Reads AI Learned sheet if present and returns (knowledge, themes_dict).
    themes_dict same format as STATE["_learned"]["themes"].
    """
    bio = io.BytesIO(file_bytes)
    try:
        wb = load_workbook(bio, data_only=True)
    except Exception:
        return None, {"Delighter": {}, "Detractor": {}}
    if LEARNED_SHEET_NAME not in wb.sheetnames:
        return None, {"Delighter": {}, "Detractor": {}}

    ws = wb[LEARNED_SHEET_NAME]
    # Expect:
    # A1: "Section", B1:"Key", C1:"Value"
    # Knowledge rows: Section="Knowledge"
    # Themes rows: Section="Theme" with Key=Side, Value=Label, plus extra columns for count/aliases/examples
    knowledge: Dict[str, Any] = {}
    themes: Dict[str, Any] = {"Delighter": {}, "Detractor": {}}

    try:
        for r in ws.iter_rows(min_row=2, values_only=True):
            section = str(r[0] or "").strip()
            key = str(r[1] or "").strip()
            val = r[2]
            if section.lower() == "knowledge" and key:
                if isinstance(val, str) and val.strip().startswith("[") and val.strip().endswith("]"):
                    # naive list parse
                    try:
                        knowledge[key] = json.loads(val)
                    except Exception:
                        knowledge[key] = val
                else:
                    knowledge[key] = val
            elif section.lower() == "theme":
                side = str(r[1] or "").strip()
                label = str(r[2] or "").strip()
                count = _safe_int(r[3], 0) if len(r) > 3 else 0
                aliases = str(r[4] or "").strip() if len(r) > 4 else ""
                examples = str(r[5] or "").strip() if len(r) > 5 else ""
                if side in themes and label:
                    bucket = themes[side].setdefault(label, {"count": 0, "examples": [], "aliases": set()})
                    bucket["count"] += count
                    for a in [x.strip() for x in aliases.replace(",", "|").split("|") if x.strip()]:
                        bucket["aliases"].add(a)
                    for e in [x.strip() for x in examples.split(" || ") if x.strip()]:
                        if e and e not in bucket["examples"]:
                            bucket["examples"].append(truncate_text(e, 180))
    except Exception:
        pass

    return (knowledge if knowledge else None), themes

def write_learned_to_workbook(wb, knowledge: Optional[Dict[str, Any]], themes: Dict[str, Any]) -> None:
    # Remove old sheet if exists
    if LEARNED_SHEET_NAME in wb.sheetnames:
        ws_old = wb[LEARNED_SHEET_NAME]
        wb.remove(ws_old)
    ws = wb.create_sheet(LEARNED_SHEET_NAME)

    # Headers
    ws.cell(row=1, column=1, value="Section")
    ws.cell(row=1, column=2, value="Key")
    ws.cell(row=1, column=3, value="Value")
    ws.cell(row=1, column=4, value="Count")
    ws.cell(row=1, column=5, value="Aliases")
    ws.cell(row=1, column=6, value="Examples")

    r = 2
    # Knowledge
    if knowledge:
        for k, v in knowledge.items():
            ws.cell(row=r, column=1, value="Knowledge")
            ws.cell(row=r, column=2, value=str(k))
            if isinstance(v, list):
                ws.cell(row=r, column=3, value=json.dumps(v, ensure_ascii=False))
            else:
                ws.cell(row=r, column=3, value=str(v))
            r += 1

    # Spacer
    r += 1

    # Themes
    for side in ["Delighter","Detractor"]:
        items = (themes or {}).get(side, {}) or {}
        # Sort by count descending then label
        sorted_items = sorted(items.items(), key=lambda kv: (-_safe_int(kv[1].get("count", 0), 0), kv[0]))
        for label, meta in sorted_items:
            ws.cell(row=r, column=1, value="Theme")
            ws.cell(row=r, column=2, value=side)
            ws.cell(row=r, column=3, value=str(label))
            ws.cell(row=r, column=4, value=_safe_int(meta.get("count", 0), 0))
            als = " | ".join(sorted({str(a).strip() for a in (meta.get("aliases") or set()) if str(a).strip()}))
            exs = " || ".join([truncate_text(str(e), 180) for e in (meta.get("examples") or []) if str(e).strip()][:5])
            ws.cell(row=r, column=5, value=als)
            ws.cell(row=r, column=6, value=exs)
            r += 1

    # Formatting widths
    for col, w in [(1,14),(2,22),(3,40),(4,10),(5,45),(6,60)]:
        ws.column_dimensions[get_column_letter(col)].width = w

# ------------------- Workbook update helpers (Symptoms + Aliases) -------------------
def upsert_symptoms_and_aliases(wb, new_symptoms: List[Tuple[str, str]], alias_additions: Dict[str, Set[str]]) -> None:
    """
    new_symptoms: [(label, side), ...]
    alias_additions: {canonical_label: set(aliases_to_add)}
    """
    if "Symptoms" not in wb.sheetnames:
        ws = wb.create_sheet("Symptoms")
    else:
        ws = wb["Symptoms"]

    # Read/ensure headers
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
    col_type  = _ensure_header("Type", ["type", "polarity", "category", "side"], preferred_index=2)
    col_alias = _ensure_header("Aliases", ["aliases", "alias"], preferred_index=3)

    # Build row index of existing labels
    label_to_row: Dict[str, int] = {}
    try:
        last_row = int(getattr(ws, "max_row", 0) or 0)
    except Exception:
        last_row = 0

    for r_i in range(2, last_row + 1):
        v = ws.cell(row=r_i, column=col_label).value
        if v:
            label_to_row[str(v).strip()] = r_i

    # Insert new symptoms
    for label, side in (new_symptoms or []):
        lab = str(label).strip()
        if not lab:
            continue
        if lab in label_to_row:
            continue
        rnew = (int(getattr(ws, "max_row", 1) or 1)) + 1
        ws.cell(row=rnew, column=col_label, value=lab)
        ws.cell(row=rnew, column=col_type, value=str(side).strip() or "")
        label_to_row[lab] = rnew

    # Apply alias additions
    for canonical, aliases in (alias_additions or {}).items():
        canon = str(canonical).strip()
        if not canon or canon not in label_to_row:
            continue
        r_i = label_to_row[canon]
        existing = str(ws.cell(row=r_i, column=col_alias).value or "").strip()
        parts = []
        if existing:
            parts = [p.strip() for p in existing.replace(",", "|").split("|") if p.strip()]
        merged = set([_canon_simple(p) for p in parts])
        out_parts = parts[:]
        for a in sorted({str(x).strip() for x in aliases if str(x).strip()}):
            ca = _canon_simple(a)
            if ca and ca not in merged and ca != _canon_simple(canon):
                merged.add(ca)
                out_parts.append(a)
        ws.cell(row=r_i, column=col_alias, value=" | ".join(out_parts))

# ------------------- Export helpers -------------------
def generate_template_workbook_bytes(
    original_file,
    updated_df: pd.DataFrame,
    processed_idx: Optional[Set[int]] = None,
    overwrite_processed_slots: bool = False,
    learned_knowledge: Optional[Dict[str, Any]] = None,
    learned_themes: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Return workbook bytes with K–T (dets), U–AD (dels), AE/AF/AG meta, plus AI Learned sheet."""
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

    # Persist learned knowledge
    try:
        write_learned_to_workbook(wb, learned_knowledge, learned_themes or {"Delighter": {}, "Detractor": {}})
    except Exception:
        pass

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

# ------------------- File Upload -------------------
uploaded_file = st.file_uploader("📂 Upload Excel (with 'Star Walk scrubbed verbatims' + 'Symptoms')", type=["xlsx"])
if not uploaded_file:
    st.stop()

uploaded_bytes = uploaded_file.read()
uploaded_file.seek(0)

# Load main sheet
try:
    df = pd.read_excel(uploaded_file, sheet_name="Star Walk scrubbed verbatims")
except ValueError:
    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file)

if "Verbatim" not in df.columns:
    st.error("Missing 'Verbatim' column.")
    st.stop()

df.columns = [str(c).strip() for c in df.columns]
df["Verbatim"] = df["Verbatim"].map(clean_text)

# Load Symptoms
DELIGHTERS, DETRACTORS, ALIASES = get_symptom_whitelists(uploaded_bytes)
if not DELIGHTERS and not DETRACTORS:
    st.warning("⚠️ No Symptoms found in 'Symptoms' tab.")
else:
    st.success(f"Loaded {len(DELIGHTERS)} Delighters, {len(DETRACTORS)} Detractors from Symptoms tab.")

# Load previously learned sheet (if present)
learned_kb0, learned_themes0 = load_learned_from_workbook(uploaded_bytes)
if learned_kb0 and not STATE["_learned"]["knowledge"]:
    STATE["_learned"]["knowledge"] = learned_kb0
if learned_themes0 and (not STATE["_learned"]["themes"]["Delighter"] and not STATE["_learned"]["themes"]["Detractor"]):
    STATE["_learned"]["themes"] = learned_themes0

# Canonical maps from APPROVED Symptoms (base)
DEL_MAP_BASE, DET_MAP_BASE, ALIAS_TO_LABEL_BASE = build_canonical_maps(DELIGHTERS, DETRACTORS, ALIASES)

# ------------------- LLM Settings -------------------
st.sidebar.header("🤖 LLM Settings")
MODEL_CHOICES = {
    "Fast – GPT-4o-mini": "gpt-4o-mini",
    "Balanced – GPT-4o": "gpt-4o",
    "Advanced – GPT-4.1": "gpt-4.1",
    "Most Advanced – GPT-5": "gpt-5",
}
model_label = st.sidebar.selectbox("Model", list(MODEL_CHOICES.keys()), index=1)
selected_model = MODEL_CHOICES[model_label]
temperature = st.sidebar.slider("Creativity (temperature)", 0.0, 1.0, 0.2, 0.1)

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
client = OpenAI(api_key=api_key) if (_HAS_OPENAI and api_key) else None
if client is None:
    st.sidebar.warning("OpenAI not configured — set OPENAI_API_KEY and install 'openai'.")

# ------------------- Recommended: Prelearn settings -------------------
st.sidebar.header("🧠 Prelearn (Recommended)")
prelearn_enabled = st.sidebar.checkbox(
    "Prelearn product knowledge + learned themes before symptomizing",
    value=True,
    help="Runs a batch miner to build product knowledge and a consistent learned symptom theme list. "
         "Helps cold start when Symptoms is blank or incomplete."
)
auto_prelearn_on_run = st.sidebar.checkbox(
    "Auto-run Prelearn once per upload (recommended)",
    value=True,
    help="If ON, clicking any Symptomize button will run Prelearn first (once per dataset fingerprint)."
)

with st.sidebar.expander("Prelearn advanced", expanded=False):
    prelearn_max_reviews = st.number_input("Max reviews to scan (sample)", min_value=200, max_value=5000, value=800, step=100)
    prelearn_batch_size = st.number_input("Batch size", min_value=20, max_value=120, value=60, step=10)
    prelearn_stratify = st.checkbox("Stratify sample by Star Rating", value=True)

# ------------------- Similarity / Canonical Merge settings -------------------
st.sidebar.header("🧬 Consistency / Merge Guard")
sim_threshold = st.sidebar.slider(
    "New-symptom similarity guard (string)",
    0.80, 0.99, 0.94, 0.01,
    help="Higher suppresses near-duplicates in the inbox."
)

canonical_merge_enabled = st.sidebar.checkbox(
    "Canonical-merge new symptom variants (recommended)",
    value=True,
    help="Maps new unlisted themes to existing approved/learned symptoms to prevent sprawl (e.g., Learning Curve → Initially Complicated)."
)
prefer_existing_canonical = st.sidebar.checkbox(
    "Prefer existing/approved label as canonical",
    value=True,
    help="If ON, when a variant matches an approved label, the approved label wins; variant becomes an alias suggestion."
)

use_semantic_merge = st.sidebar.checkbox(
    "Use semantic merge (embeddings) when available (recommended)",
    value=True,
    help="Helps catch meaning matches like 'learning curve' vs 'initially complicated'. Falls back safely if embeddings fail."
)

with st.sidebar.expander("Semantic merge advanced", expanded=False):
    embed_model = st.selectbox("Embedding model", ["text-embedding-3-small", "text-embedding-3-large"], index=0)
    semantic_merge_threshold = st.slider("Semantic merge threshold", 0.70, 0.95, 0.86, 0.01)

# ------------------- Themeizing toggle -------------------
themeize_toggle = st.sidebar.checkbox(
    "Theme-ize new symptom suggestions (recommended)",
    value=True,
    help="If ON, candidate labels are normalized to short, reusable themes."
)

# ------------------- Evidence settings -------------------
st.sidebar.header("🧾 Evidence Guard")
require_evidence = st.sidebar.checkbox(
    "Require evidence to write labels",
    value=True,
    help="If ON, a label must include ≥1 exact snippet from the review to be written."
)
verify_evidence_toggle = st.sidebar.checkbox(
    "Verify evidence snippets exist in verbatim (recommended)",
    value=True,
    help="If ON, rejects hallucinated evidence snippets before writing labels."
)
max_ev_per_label = st.sidebar.slider("Max evidence per label", 1, 3, 2)
max_ev_chars = st.sidebar.slider("Max evidence length (chars)", 40, 200, 120, 10)

# ------------------- Active symptom list augmentation -------------------
st.sidebar.header("📚 Active Symptom List")
augment_with_learned = st.sidebar.checkbox(
    "Augment tagging list with learned themes if Symptoms is missing/incomplete (recommended)",
    value=True,
    help="If ON, the labeler can use learned themes as allowed labels to stay consistent even if workbook Symptoms is blank or small."
)
min_labels_per_side = st.sidebar.number_input("Treat Symptoms as incomplete if < this many per side", min_value=0, max_value=100, value=8, step=1)

def compute_active_symptom_lists() -> Tuple[List[str], List[str], Dict[str, List[str]]]:
    """
    Returns (active_delighters, active_detractors, active_alias_map).
    If Symptoms are incomplete and augment is ON, include learned themes as allowed labels.
    """
    base_del = DELIGHTERS[:]
    base_det = DETRACTORS[:]
    base_alias = dict(ALIASES or {})

    learned = STATE["_learned"]["themes"] or {"Delighter": {}, "Detractor": {}}
    learned_del = list((learned.get("Delighter", {}) or {}).keys())
    learned_det = list((learned.get("Detractor", {}) or {}).keys())

    incomplete = (len(base_del) < int(min_labels_per_side)) or (len(base_det) < int(min_labels_per_side))

    if augment_with_learned and incomplete:
        # add learned labels
        for x in learned_del:
            if x and x not in base_del:
                base_del.append(x)
        for x in learned_det:
            if x and x not in base_det:
                base_det.append(x)

        # merge learned aliases into alias_map
        for side, labels in [("Delighter", base_del), ("Detractor", base_det)]:
            for lbl in (learned.get(side, {}) or {}).keys():
                if lbl in labels:
                    als = list((learned.get(side, {}).get(lbl, {}) or {}).get("aliases", set()) or set())
                    if als:
                        base_alias.setdefault(lbl, [])
                        for a in als:
                            if a and a not in base_alias[lbl]:
                                base_alias[lbl].append(a)

    return base_del, base_det, base_alias

ACTIVE_DELIGHTERS, ACTIVE_DETRACTORS, ACTIVE_ALIASES = compute_active_symptom_lists()
DEL_MAP, DET_MAP, ALIAS_TO_LABEL = build_canonical_maps(ACTIVE_DELIGHTERS, ACTIVE_DETRACTORS, ACTIVE_ALIASES)

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
  <div class="small" style="margin-top:10px;">
    Active tagging list: <b>{len(ACTIVE_DELIGHTERS)}</b> delighters, <b>{len(ACTIVE_DETRACTORS)}</b> detractors
    {("(augmented with learned themes)" if (augment_with_learned and ((len(DELIGHTERS)<min_labels_per_side) or (len(DETRACTORS)<min_labels_per_side))) else "")}
  </div>
</div>
""", unsafe_allow_html=True)

# ------------------- Prelearn UI -------------------
st.subheader("🧠 Prelearn: Product Knowledge + Learned Themes (Recommended)")

fp = _dataset_fingerprint(df)
prelearn_already = (STATE["_learned"].get("fingerprint") == fp) and (STATE["_learned"].get("knowledge") is not None or any(STATE["_learned"]["themes"][s] for s in ["Delighter","Detractor"]))

prelearn_btn = st.button("🧠 Run Prelearn now", use_container_width=True, disabled=(client is None))
prelearn_status = st.empty()

if prelearn_enabled and (prelearn_btn or (auto_prelearn_on_run and not prelearn_already)):
    if client is None:
        prelearn_status.warning("OpenAI not configured — cannot run Prelearn.")
    else:
        prelearn_status.info("Running Prelearn…")
        prelearn_from_df(
            df=df,
            client=client,
            model=selected_model,
            temperature=temperature,
            max_reviews=int(prelearn_max_reviews),
            batch_size=int(prelearn_batch_size),
            stratify_by_rating=bool(prelearn_stratify),
        )
        prelearn_status.success("Prelearn complete. Product knowledge + learned themes updated.")
        # Recompute active lists after prelearn (important!)
        ACTIVE_DELIGHTERS, ACTIVE_DETRACTORS, ACTIVE_ALIASES = compute_active_symptom_lists()
        DEL_MAP, DET_MAP, ALIAS_TO_LABEL = build_canonical_maps(ACTIVE_DELIGHTERS, ACTIVE_DETRACTORS, ACTIVE_ALIASES)

knowledge = STATE["_learned"].get("knowledge")
themes_learned = STATE["_learned"].get("themes", {"Delighter": {}, "Detractor": {}})

# Show knowledge card + learned themes snapshot
if knowledge or (themes_learned.get("Delighter") or themes_learned.get("Detractor")):
    col1, col2 = st.columns([1.2, 1.0])
    with col1:
        st.markdown("<div class='kcard'><h4>📌 Product Knowledge Card</h4>", unsafe_allow_html=True)
        if knowledge:
            def _list(v):
                if isinstance(v, list):
                    return ", ".join([str(x) for x in v[:12] if str(x).strip()])
                return str(v or "").strip()

            st.markdown(f"- **What it is:** {knowledge.get('what_it_is','')}")
            st.markdown(f"- **Key parts/terms:** {_list(knowledge.get('key_parts_and_terms', []))}")
            st.markdown(f"- **Usage patterns:** {_list(knowledge.get('usage_patterns', []))}")
            st.markdown(f"- **Common failures:** {_list(knowledge.get('common_failures', []))}")
            st.markdown(f"- **Common delights:** {_list(knowledge.get('common_delights', []))}")
            st.markdown(f"- **Care/maintenance:** {_list(knowledge.get('care_and_maintenance', []))}")
            st.markdown(f"- **Safety considerations:** {_list(knowledge.get('safety_considerations', []))}")
        else:
            st.write("(Run Prelearn to generate product knowledge.)")
        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        st.markdown("<div class='kcard'><h4>🧩 Learned Theme Library (Top)</h4>", unsafe_allow_html=True)
        for side, color in [("Detractor","red"),("Delighter","green")]:
            items = themes_learned.get(side, {}) or {}
            top = sorted(items.items(), key=lambda kv: (-_safe_int(kv[1].get("count",0),0), kv[0]))[:8]
            st.markdown(f"**{side}s**")
            if not top:
                st.write("(none yet)")
            else:
                chips = "<div class='chip-wrap'>" + "".join([
                    f"<span class='chip {color}'>{html.escape(lbl)} · {int(meta.get('count',0))}</span>"
                    for lbl, meta in top
                ]) + "</div>"
                st.markdown(chips, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("📚 View full learned themes (and promote to Symptoms)", expanded=False):
        def learned_table(side: str) -> pd.DataFrame:
            items = themes_learned.get(side, {}) or {}
            rows = []
            for lbl, meta in sorted(items.items(), key=lambda kv: (-_safe_int(kv[1].get("count",0),0), kv[0])):
                als = " | ".join(sorted({str(a).strip() for a in (meta.get("aliases") or set()) if str(a).strip()}))
                exs = " || ".join([truncate_text(e, 180) for e in (meta.get("examples") or [])][:3])
                rows.append({"Add": False, "Label": lbl, "Side": side, "Count": int(meta.get("count",0)), "Aliases": als, "Examples": exs})
            return pd.DataFrame(rows) if rows else pd.DataFrame({"Add": pd.Series(dtype=bool),"Label": pd.Series(dtype=str),"Side": pd.Series(dtype=str),"Count": pd.Series(dtype=int),"Aliases": pd.Series(dtype=str),"Examples": pd.Series(dtype=str)})

        tbl_ld = learned_table("Delighter")
        tbl_lt = learned_table("Detractor")
        with st.form("promote_learned_form", clear_on_submit=False):
            a,b = st.columns(2)
            with a:
                st.markdown("**Delighter learned themes**")
                ed_ld = st.data_editor(tbl_ld, num_rows="fixed", use_container_width=True, key="learned_del_editor")
            with b:
                st.markdown("**Detractor learned themes**")
                ed_lt = st.data_editor(tbl_lt, num_rows="fixed", use_container_width=True, key="learned_det_editor")
            promote_btn = st.form_submit_button("✅ Add selected learned themes to Symptoms & Download updated workbook")

        if promote_btn:
            selections = []
            if isinstance(ed_ld, pd.DataFrame) and not ed_ld.empty:
                for _, rr in ed_ld.iterrows():
                    if bool(rr.get("Add", False)) and str(rr.get("Label","")).strip():
                        selections.append((str(rr["Label"]).strip(), "Delighter"))
            if isinstance(ed_lt, pd.DataFrame) and not ed_lt.empty:
                for _, rr in ed_lt.iterrows():
                    if bool(rr.get("Add", False)) and str(rr.get("Label","")).strip():
                        selections.append((str(rr["Label"]).strip(), "Detractor"))

            if selections:
                uploaded_file.seek(0)
                wb = load_workbook(uploaded_file)
                upsert_symptoms_and_aliases(wb, selections, alias_additions={})
                # persist learned sheet too
                try:
                    write_learned_to_workbook(wb, STATE["_learned"]["knowledge"], STATE["_learned"]["themes"])
                except Exception:
                    pass
                out = io.BytesIO(); wb.save(out)
                st.download_button(
                    "⬇️ Download workbook (Symptoms updated)",
                    data=out.getvalue(),
                    file_name="Symptoms_Updated.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                st.success(f"Added {len(selections)} learned theme(s) to Symptoms.")
            else:
                st.info("No learned themes selected.")

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

# Row 1: actions
r1a, r1b, r1c, r1d, r1e = st.columns([1.4, 1.4, 1.8, 1.8, 1.2])
with r1a: run_n_btn = st.button("▶️ Symptomize N", use_container_width=True)
with r1b: run_all_btn = st.button("🚀 Symptomize All (current scope)", use_container_width=True)
with r1c: overwrite_btn = st.button("🧹 Overwrite & Symptomize ALL (start at row 1)", use_container_width=True)
with r1d: run_missing_both_btn = st.button("✨ Missing-Both One-Click", use_container_width=True)
with r1e: undo_btn = st.button("↩️ Undo last run", use_container_width=True)

# Row 2: batch size + presets
st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
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

# ------------------- Core runner -------------------
def _run_symptomize(rows_df: pd.DataFrame, overwrite_mode: bool = False):
    global df, ACTIVE_DELIGHTERS, ACTIVE_DETRACTORS, ACTIVE_ALIASES, DEL_MAP, DET_MAP, ALIAS_TO_LABEL

    # auto prelearn if enabled
    fp_now = _dataset_fingerprint(df)
    prelearn_ok = (STATE["_learned"].get("fingerprint") == fp_now) and (
        STATE["_learned"].get("knowledge") is not None or any(STATE["_learned"]["themes"][s] for s in ["Delighter","Detractor"])
    )
    if prelearn_enabled and auto_prelearn_on_run and (not prelearn_ok) and client is not None:
        st.info("Auto Prelearn is ON — running Prelearn first for consistency…")
        prelearn_from_df(
            df=df, client=client, model=selected_model, temperature=temperature,
            max_reviews=int(prelearn_max_reviews), batch_size=int(prelearn_batch_size),
            stratify_by_rating=bool(prelearn_stratify),
        )
        ACTIVE_DELIGHTERS, ACTIVE_DETRACTORS, ACTIVE_ALIASES = compute_active_symptom_lists()
        DEL_MAP, DET_MAP, ALIAS_TO_LABEL = build_canonical_maps(ACTIVE_DELIGHTERS, ACTIVE_DETRACTORS, ACTIVE_ALIASES)

    prog = st.progress(0.0)

    def _fmt_secs(sec: float) -> str:
        m = int(sec // 60); s = int(round(sec - m*60)); return f"{m}:{s:02d}"

    t0 = time.perf_counter(); eta_box = st.empty()
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

    # Known catalog for canonical merge (approved + learned)
    catalog = build_known_catalog(DELIGHTERS, DETRACTORS, ALIASES, STATE["_learned"])

    total_n = max(1, len(rows_df))
    knowledge_hint = format_knowledge_hint(STATE["_learned"].get("knowledge"))
    known_theme_hints = list(dict.fromkeys(
        (DELIGHTERS + DETRACTORS) +
        list((STATE["_learned"]["themes"].get("Delighter", {}) or {}).keys()) +
        list((STATE["_learned"]["themes"].get("Detractor", {}) or {}).keys())
    ))[:200]

    for k, (idx, row) in enumerate(rows_df.iterrows(), start=1):
        vb = row.get("Verbatim", "")
        needs_deli = bool(row.get("Needs_Delighters", False))
        needs_detr = bool(row.get("Needs_Detractors", False))

        # snapshot for undo
        old_vals = {f"AI Symptom Detractor {j}": df.loc[idx, f"AI Symptom Detractor {j}"] if f"AI Symptom Detractor {j}" in df.columns else None for j in range(1,11)}
        old_vals.update({f"AI Symptom Delighter {j}": df.loc[idx, f"AI Symptom Delighter {j}"] if f"AI Symptom Delighter {j}" in df.columns else None for j in range(1,11)})
        old_vals.update({"AI Safety": df.loc[idx, "AI Safety"] if "AI Safety" in df.columns else None,
                         "AI Reliability": df.loc[idx, "AI Reliability"] if "AI Reliability" in df.columns else None,
                         "AI # of Sessions": df.loc[idx, "AI # of Sessions"] if "AI # of Sessions" in df.columns else None})
        snapshot.append((int(idx), old_vals))

        # LLM
        if client is not None:
            dels, dets, unl_dels, unl_dets, ev_del_map, ev_det_map, safety, reliability, sessions = _openai_labeler_onecall(
                vb, client, selected_model, temperature,
                ACTIVE_DELIGHTERS, ACTIVE_DETRACTORS, ACTIVE_ALIASES,
                DEL_MAP, DET_MAP, ALIAS_TO_LABEL,
                known_theme_hints=known_theme_hints,
                knowledge_hint=knowledge_hint,
                require_evidence=require_evidence,
                verify_evidence=verify_evidence_toggle,
                max_ev_per_label=max_ev_per_label,
                max_ev_chars=max_ev_chars
            )
        else:
            dels, dets, unl_dels, unl_dets, ev_del_map, ev_det_map = [], [], [], [], {}, {}
            safety, reliability, sessions = "Not Mentioned", "Not Mentioned", "Unknown"

        df = ensure_ai_columns(df)
        wrote_dets, wrote_dels = [], []
        ev_written_det: Dict[str, List[str]] = {}
        ev_written_del: Dict[str, List[str]] = {}

        if needs_detr and dets:
            for j, lab in enumerate(dets[:10]):
                df.loc[idx, f"AI Symptom Detractor {j+1}"] = lab
                ev_written_det[lab] = ev_det_map.get(lab, [])
            wrote_dets = dets[:10]

        if needs_deli and dels:
            for j, lab in enumerate(dels[:10]):
                df.loc[idx, f"AI Symptom Delighter {j+1}"] = lab
                ev_written_del[lab] = ev_del_map.get(lab, [])
            wrote_dels = dels[:10]

        df.loc[idx, "AI Safety"] = safety
        df.loc[idx, "AI Reliability"] = reliability
        df.loc[idx, "AI # of Sessions"] = sessions

        # Themeize + canonical merge for unlisted candidates
        unl_dels_out = []
        unl_dets_out = []
        alias_hits = []

        def _handle_unlisted(items: List[str], side: str) -> List[str]:
            out_items = []
            for x in items or []:
                if not str(x).strip():
                    continue
                themed = thematize_label(x, side, use_llm_fallback=False) if themeize_toggle else str(x).strip()
                if canonical_merge_enabled:
                    res = resolve_candidate_to_canonical(
                        themed, side,
                        catalog=catalog,
                        prefer_existing=prefer_existing_canonical,
                        string_merge_threshold=sim_threshold,
                        semantic_merge_threshold=semantic_merge_threshold,
                        use_semantic=(use_semantic_merge and client is not None),
                        client=client,
                        embed_model=embed_model
                    )
                    if res["action"] == "merge" and res.get("alias"):
                        canon = res["canonical"]
                        # store alias suggestion
                        STATE["_learned"]["alias_suggestions"].setdefault(canon, set()).add(res["alias"])
                        alias_hits.append((res["alias"], canon, res["match_type"], float(res["score"])))
                        # do not add as new symptom candidate
                        continue
                    out_items.append(res["canonical"])
                else:
                    out_items.append(themed)
            # dedupe
            ded = []
            seen = set()
            for z in out_items:
                cz = _canon_simple(z)
                if cz and cz not in seen:
                    seen.add(cz); ded.append(z)
            return ded[:10]

        unl_dels_out = _handle_unlisted(unl_dels, "Delighter")
        unl_dets_out = _handle_unlisted(unl_dets, "Detractor")

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
            "Unlisted_Detractors": unl_dets_out,
            "Unlisted_Delighters": unl_dels_out,
            "Alias_Merges": alias_hits,
            ">10 Detractors Detected": len(dets) > 10,
            ">10 Delighters Detected": len(dels) > 10,
            "Safety": safety,
            "Reliability": reliability,
            "Sessions": sessions,
            "Evidence_Coverage": row_ev_cov,
        })
        processed_idx_set.add(int(idx))

        prog.progress(k/total_n)
        elapsed = time.perf_counter() - t0
        rate = (k / elapsed) if elapsed > 0 else 0.0
        rem = total_n - k
        eta_sec = (rem / rate) if rate > 0 else 0.0
        eta_box.markdown(f"**Progress:** {k}/{total_n} • **ETA:** ~ {_fmt_secs(eta_sec)} • **Speed:** {rate*60:.1f} rev/min")

    st.session_state["undo_stack"].append({"rows": snapshot})

# ------------------- Execute buttons -------------------
if client is not None and (run_n_btn or run_all_btn or overwrite_btn or run_missing_both_btn):
    if run_missing_both_btn:
        rows_iter = work[(work["Needs_Delighters"]) & (work["Needs_Detractors"])].sort_index()
        _run_symptomize(rows_iter, overwrite_mode=False)

    elif overwrite_btn:
        df = clear_all_ai_slots_in_df(df)
        colmap = detect_symptom_columns(df)
        work = detect_missing(df, colmap)
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
        st.info("Nothing to undo.")
        return
    snap = st.session_state["undo_stack"].pop()
    for idx, old_vals in snap.get("rows", []):
        for col, val in old_vals.items():
            if col not in df.columns:
                df[col] = None
            df.loc[idx, col] = val
    st.success("Reverted last run.")

if undo_btn:
    _undo_last_run()

# ------------------- Processed Reviews -------------------
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

            if rec.get("Alias_Merges"):
                with st.expander("🔁 Canonical merges detected (aliases)", expanded=False):
                    for alias, canon, mtype, score in rec["Alias_Merges"]:
                        st.write(f"- **{alias}** → **{canon}** ({mtype}, score={score:.2f})")

# ------------------- New Symptom Inbox (true new only) + Alias Suggestions -------------------
# Build new symptom candidates from processed rows (after canonical merge)
cand_del_raw: Dict[str, List[int]] = {}
cand_det_raw: Dict[str, List[int]] = {}
for rec in processed_rows:
    for u in rec.get("Unlisted_Delighters", []) or []:
        cand_del_raw.setdefault(u, []).append(rec["Index"])
    for u in rec.get("Unlisted_Detractors", []) or []:
        cand_det_raw.setdefault(u, []).append(rec["Index"])

# Filter near-dupes vs approved lists + aliases
whitelist_all = set(DELIGHTERS + DETRACTORS)
alias_all = set([a for lst in (ALIASES or {}).values() for a in lst]) if ALIASES else set()
wl_canon = {_canon_simple(x) for x in whitelist_all}
ali_canon = {_canon_simple(x) for x in alias_all}

def _filter_near_dupes(cmap: Dict[str, List[int]], cutoff: float = 0.94) -> Dict[str, List[int]]:
    filtered: Dict[str, List[int]] = {}
    seen_key: Dict[str, str] = {}
    for sym, refs in cmap.items():
        c = _canon_simple(sym)
        if c in wl_canon or c in ali_canon:
            continue
        try:
            m = difflib.get_close_matches(sym, list(whitelist_all), n=1, cutoff=cutoff)
            if m:
                continue
        except Exception:
            pass
        if c in seen_key:
            filtered[seen_key[c]].extend(refs)
        else:
            filtered[sym] = list(refs); seen_key[c] = sym
    return filtered

cand_del = _filter_near_dupes(cand_del_raw, cutoff=sim_threshold)
cand_det = _filter_near_dupes(cand_det_raw, cutoff=sim_threshold)

alias_suggestions = STATE["_learned"].get("alias_suggestions", {}) or {}

if cand_del or cand_det or alias_suggestions:
    st.subheader("🟡 Inbox: New Symptoms + Alias Suggestions")

    def _mk_table(cmap: Dict[str, List[int]], side_label: str) -> pd.DataFrame:
        if not cmap:
            return pd.DataFrame({
                "Add": pd.Series(dtype=bool),
                "Label": pd.Series(dtype=str),
                "Side": pd.Series(dtype=str),
                "Count": pd.Series(dtype=int),
                "Examples": pd.Series(dtype=str),
            })
        rows_tbl = []
        for sym, refs in sorted(cmap.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            examples = []
            for ridx in refs[:3]:
                try:
                    examples.append(str(df.loc[ridx, "Verbatim"]))
                except Exception:
                    pass
            rows_tbl.append({
                "Add": False,
                "Label": sym,
                "Side": side_label,
                "Count": int(len(refs)),
                "Examples": " | ".join(["— "+truncate_text(e,200) for e in examples])
            })
        return pd.DataFrame(rows_tbl).astype({"Add": bool, "Label": str, "Side": str, "Count": int, "Examples": str})

    def _mk_alias_table(alias_map: Dict[str, Set[str]]) -> pd.DataFrame:
        rows = []
        for canon, als in sorted(alias_map.items(), key=lambda kv: (kv[0].lower(), -len(kv[1]))):
            if not canon or not als:
                continue
            rows.append({
                "Apply": False,
                "Canonical Symptom": canon,
                "New Aliases": " | ".join(sorted({a.strip() for a in als if a.strip()})),
                "Alias Count": int(len({a for a in als if str(a).strip()}))
            })
        if not rows:
            return pd.DataFrame({"Apply": pd.Series(dtype=bool),"Canonical Symptom": pd.Series(dtype=str),
                                 "New Aliases": pd.Series(dtype=str),"Alias Count": pd.Series(dtype=int)})
        return pd.DataFrame(rows).astype({"Apply": bool, "Canonical Symptom": str, "New Aliases": str, "Alias Count": int})

    tbl_del = _mk_table(cand_del, "Delighter")
    tbl_det = _mk_table(cand_det, "Detractor")
    tbl_alias = _mk_alias_table(alias_suggestions)

    with st.form("inbox_form", clear_on_submit=False):
        cA, cB = st.columns(2)
        with cA:
            st.markdown("**New Delighter candidates**")
            editor_del = st.data_editor(tbl_del, num_rows="fixed", use_container_width=True, key="cand_del_editor_v74")
        with cB:
            st.markdown("**New Detractor candidates**")
            editor_det = st.data_editor(tbl_det, num_rows="fixed", use_container_width=True, key="cand_det_editor_v74")

        st.markdown("---")
        st.markdown("**Alias Suggestions (canonical merges)**")
        editor_alias = st.data_editor(tbl_alias, num_rows="fixed", use_container_width=True, key="alias_editor_v74")

        add_btn = st.form_submit_button("✅ Apply selected (new symptoms + aliases) and Download updated workbook")

    if add_btn:
        new_symptoms: List[Tuple[str, str]] = []
        alias_additions: Dict[str, Set[str]] = {}

        # New symptoms
        try:
            if isinstance(editor_del, pd.DataFrame) and not editor_del.empty:
                for _, r_ in editor_del.iterrows():
                    if bool(r_.get("Add", False)) and str(r_.get("Label", "")).strip():
                        side_val = str(r_.get("Side","Delighter")).strip() or "Delighter"
                        label_out = thematize_label(str(r_["Label"]).strip(), side_val, use_llm_fallback=False) if themeize_toggle else str(r_["Label"]).strip()
                        new_symptoms.append((label_out, side_val))
        except Exception:
            pass
        try:
            if isinstance(editor_det, pd.DataFrame) and not editor_det.empty:
                for _, r_ in editor_det.iterrows():
                    if bool(r_.get("Add", False)) and str(r_.get("Label", "")).strip():
                        side_val = str(r_.get("Side","Detractor")).strip() or "Detractor"
                        label_out = thematize_label(str(r_["Label"]).strip(), side_val, use_llm_fallback=False) if themeize_toggle else str(r_["Label"]).strip()
                        new_symptoms.append((label_out, side_val))
        except Exception:
            pass

        # Aliases
        try:
            if isinstance(editor_alias, pd.DataFrame) and not editor_alias.empty:
                for _, r_ in editor_alias.iterrows():
                    if bool(r_.get("Apply", False)) and str(r_.get("Canonical Symptom","")).strip():
                        canon = str(r_["Canonical Symptom"]).strip()
                        als = str(r_.get("New Aliases","") or "").strip()
                        als_list = [a.strip() for a in als.replace(",", "|").split("|") if a.strip()]
                        if als_list:
                            alias_additions.setdefault(canon, set()).update(set(als_list))
        except Exception:
            pass

        if new_symptoms or alias_additions:
            uploaded_file.seek(0)
            wb = load_workbook(uploaded_file)
            upsert_symptoms_and_aliases(wb, new_symptoms, alias_additions)
            # persist learned sheet too
            try:
                write_learned_to_workbook(wb, STATE["_learned"]["knowledge"], STATE["_learned"]["themes"])
            except Exception:
                pass
            out = io.BytesIO(); wb.save(out); out.seek(0)

            st.download_button(
                "⬇️ Download workbook (Symptoms + Aliases updated)",
                data=out.getvalue(),
                file_name="Symptoms_Updated.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            st.success(f"Applied {len(new_symptoms)} new symptom(s) and {len(alias_additions)} alias update(s).")
        else:
            st.info("Nothing selected.")

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
    learned_knowledge=STATE["_learned"].get("knowledge"),
    learned_themes=STATE["_learned"].get("themes"),
)

st.download_button(
    "⬇️ Download symptomized workbook (XLSX)",
    data=export_bytes,
    file_name=f"{file_base}_Symptomized.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# ------------------- Symptoms Catalog quick export (approved Symptoms only) -------------------
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
st.download_button("⬇️ Download Symptoms Catalog (XLSX)", sym_bytes.getvalue(),
                   file_name=f"{file_base}_Symptoms_Catalog.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ------------------- View Symptoms from Excel Workbook (expander) -------------------
st.subheader("📘 View Symptoms from Excel Workbook")
with st.expander("📘 View Symptoms from Excel Workbook", expanded=False):
    st.markdown("This reflects the **Symptoms** sheet as loaded; use the inbox to propose additions.")

    tabs = st.tabs(["Delighters", "Detractors", "Aliases", "Meta"])

    def _chips(items, color: str):
        items_sorted = sorted({str(x).strip() for x in (items or []) if str(x).strip()})
        if not items_sorted:
            st.write("(none)")
        else:
            htmlchips = "<div class='chip-wrap'>" + "".join([f"<span class='chip {color}'>{html.escape(x)}</span>" for x in items_sorted]) + "</div>"
            st.markdown(htmlchips, unsafe_allow_html=True)

    with tabs[0]:
        st.markdown("**Delighter labels from workbook**"); _chips(DELIGHTERS, "green")
    with tabs[1]:
        st.markdown("**Detractor labels from workbook**"); _chips(DETRACTORS, "red")
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

# Footer
st.divider()
st.caption(
    f"{APP_VERSION} — Evidence-locked labeling + Prelearn (product knowledge + learned themes), canonical merge + alias suggestions, "
    "one-call meta extraction, and persistence in AI Learned sheet. Exports: K–T/U–AD, meta: AE/AF/AG."
)



