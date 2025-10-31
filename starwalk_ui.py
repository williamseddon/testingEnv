# starwalk_ui_v7_1.py — ETA, presets, overwrite, undo, similarity guard, polished UI, evidence highlighting (no header relabeling)
# Requirements: streamlit>=1.28, pandas, openpyxl, openai (optional)

import streamlit as st
import pandas as pd
import numpy as np
import io, os, json, difflib, time, re, html
from typing import List, Dict, Tuple, Optional, Set
from datetime import datetime

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

# ------------------- Page Setup -------------------
st.set_page_config(layout="wide", page_title="Review Symptomizer — v7.1")
st.title("✨ Review Symptomizer — v7.1")
st.caption("Exact export (K–T dets, U–AD dels) • ETA + presets + overwrite • Undo • New‑symptom inbox • Tiles UI • Similarity guard • Highlighted evidence")

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
      div[data-testid="stProgress"] > div > div { background: linear-gradient(90deg, var(--brand), var(--brand2)); }
      /* Sticky toolbar for run controls */
      .toolbar { position: sticky; top: 8px; z-index: 5; background: rgba(255,255,255,.85); backdrop-filter: blur(6px); border: 1px solid #e6eaf0; border-radius: 12px; padding: 10px 12px; box-shadow: 0 4px 14px rgba(16,24,40,.06); }
      mark.hl { background: #fde68a; padding: 0 .15em; border-radius: .25em; }
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
    # Unique, longest-first to avoid nested matches
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
    if not columns:
        return False
    for c in columns:
        if c in row and is_filled(row[c]):
            return True
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


def _openai_labeler(
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
) -> Tuple[List[str], List[str], List[str], List[str]]:
    if not verbatim or not verbatim.strip():
        return [], [], [], []

    sys = "
    # --- inside _openai_meta_extractor(...) ---
    sys = (
        "Extract three fields from this consumer review. Use ONLY the allowed values.\n"
        "SAFETY one of: ['Not Mentioned','Concern','Positive']\n"
        "RELIABILITY one of: ['Not Mentioned','Negative','Neutral','Positive']\n"
        "SESSIONS one of: ['0','1','2–3','4–9','10+','Unknown']\n"
        'Return strict JSON {"safety":"…","reliability":"…","sessions":"…"}'
    )

    ])

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=float(temperature),
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": f'Review:
"""{verbatim.strip()}"""'},
            ],
            response_format={"type": "json_object"}
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)

        def _map_side(items: List[str], side: str) -> List[str]:
            mapped: List[str] = []
            for x in (items or []):
                key = _canon(x)
                if side == "del":
                    label = del_map.get(key) or alias_to_label.get(key)
                    if label and (label in delighters) and (label not in mapped):
                        mapped.append(label)
                else:
                    label = det_map.get(key) or alias_to_label.get(key)
                    if label and (label in detractors) and (label not in mapped):
                        mapped.append(label)
            return mapped[:10]

        dels = _map_side(data.get("delighters", []), side="del")
        dets = _map_side(data.get("detractors", []), side="det")
        unl_dels = [x for x in (data.get("unlisted_delighters", []) or [])][:10]
        unl_dets = [x for x in (data.get("unlisted_detractors", []) or [])][:10]
        return dels, dets, unl_dels, unl_dets
    except Exception:
        return [], [], [], []


def _openai_meta_extractor(verbatim: str, client, model: str, temperature: float) -> Tuple[str, str, str]:
    if not verbatim or not verbatim.strip():
        return "Not Mentioned", "Not Mentioned", "Unknown"
    sys = "
".join([
        "Extract three fields from this consumer review. Use ONLY the allowed values.",
        "SAFETY one of: ['Not Mentioned','Concern','Positive']",
        "RELIABILITY one of: ['Not Mentioned','Negative','Neutral','Positive']",
        "SESSIONS one of: ['0','1','2–3','4–9','10+','Unknown']",
        'Return strict JSON {"safety":"…","reliability":"…","sessions":"…"}',
    ])
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=float(temperature),
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": f'Review:
"""{verbatim.strip()}"""'},
            ],
            response_format={"type": "json_object"}
        )
        content = resp.choices[0].message.content or "{}"
        data = json.loads(content)
        s = str(data.get("safety", "Not Mentioned")).strip()
        r = str(data.get("reliability", "Not Mentioned")).strip()
        n = str(data.get("sessions", "Unknown")).strip()
        s = s if s in SAFETY_ENUM else "Not Mentioned"
        r = r if r in RELIABILITY_ENUM else "Not Mentioned"
        n = n if n in SESSIONS_ENUM else "Unknown"
        return s, r, n
    except Exception:
        return "Not Mentioned", "Not Mentioned", "Unknown"

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

    # Ensure meta headers if we wrote meta values
    for name, col in META_ORDER:
        col_idx = column_index_from_string(col)
        if not ws.cell(row=1, column=col_idx).value:
            ws.cell(row=1, column=col_idx, value=name)

    fill_green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")  # Delighters
    fill_red   = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")  # Detractors
    fill_yel   = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")  # Safety
    fill_blu   = PatternFill(start_color="CFE2F3", end_color="CFE2F3", fill_type="solid")  # Reliability
    fill_pur   = PatternFill(start_color="EAD1DC", end_color="EAD1DC", fill_type="solid")  # # of Sessions

    pset = set(processed_idx or [])

    def _clear_template_slots(row_i: int):
        for col_idx in DET_INDEXES + DEL_INDEXES + list(META_INDEXES.values()):
            ws.cell(row=row_i, column=col_idx, value=None)

    for i, (rid, r) in enumerate(df2.iterrows(), start=2):
        if overwrite_processed_slots and (int(rid) in pset):
            _clear_template_slots(i)
        # Detractors → K..T
        for j, col_idx in enumerate(DET_INDEXES, start=1):
            val = r.get(f"AI Symptom Detractor {j}")
            cv = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cv)
            if cv is not None:
                cell.fill = fill_red
        # Delighters → U..AD
        for j, col_idx in enumerate(DEL_INDEXES, start=1):
            val = r.get(f"AI Symptom Delighter {j}")
            cv = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cv)
            if cv is not None:
                cell.fill = fill_green
        # Meta → AE/AF/AG
        safety = r.get("AI Safety")
        reliab = r.get("AI Reliability")
        sess   = r.get("AI # of Sessions")
        if is_filled(safety):
            c = ws.cell(row=i, column=META_INDEXES["Safety"], value=str(safety))
            c.fill = fill_yel
        if is_filled(reliab):
            c = ws.cell(row=i, column=META_INDEXES["Reliability"], value=str(reliab))
            c.fill = fill_blu
        if is_filled(sess):
            c = ws.cell(row=i, column=META_INDEXES["# of Sessions"], value=str(sess))
            c.fill = fill_pur

    # column widths
    for c in DET_INDEXES + DEL_INDEXES + list(META_INDEXES.values()):
        try:
            ws.column_dimensions[get_column_letter(c)].width = 28
        except Exception:
            pass

    out = io.BytesIO(); wb.save(out)
    return out.getvalue()

# ------------------- Helpers: add new symptoms -------------------

def add_new_symptoms_to_workbook(original_file, selections: List[Tuple[str, str]]) -> bytes:
    """Safely add new symptoms to the 'Symptoms' sheet.
    Robust to missing/blank headers and never writes to negative/zero columns.
    """
    original_file.seek(0)
    wb = load_workbook(original_file)

    # Ensure the sheet exists
    if "Symptoms" not in wb.sheetnames:
        ws = wb.create_sheet("Symptoms")
    else:
        ws = wb["Symptoms"]

    # Read current header row (may be partially/fully blank)
    try:
        headers_row = [c.value for c in ws[1]]
    except Exception:
        headers_row = []
    headers = [str(h).strip() if h is not None else "" for h in headers_row]
    header_map = {str(h).strip().lower(): i + 1 for i, h in enumerate(headers) if str(h).strip()}

    used_cols: Set[int] = set()

    def _ensure_header(name: str, synonyms: List[str], preferred_index: Optional[int] = None) -> int:
        """Return a 1-based column index for a header. Create header if needed."""
        # Try to find an existing header by any synonym
        for syn in synonyms:
            idx = header_map.get(str(syn).lower())
            if idx:
                # If header cell text is blank, fill with canonical name
                if not ws.cell(row=1, column=idx).value:
                    ws.cell(row=1, column=idx, value=name)
                used_cols.add(idx)
                return idx
        # Not found: choose a safe new column index
        max_col = int(getattr(ws, "max_column", 0) or 0)
        idx = preferred_index if (preferred_index and preferred_index > 0) else (max_col + 1 if max_col > 0 else 1)
        while idx in used_cols:
            idx += 1
        ws.cell(row=1, column=idx, value=name)
        used_cols.add(idx)
        return idx

    # Ensure columns exist and get their indices (1-based)
    col_label = _ensure_header("Symptom", ["symptom", "label", "name", "item"], preferred_index=1)
    col_type  = _ensure_header("Type", ["type", "polarity", "category", "side"], preferred_index=2)
    col_alias = _ensure_header("Aliases", ["aliases", "alias"], preferred_index=3)

    # Build set of existing labels to avoid duplicates
    existing: Set[str] = set()
    try:
        last_row = int(getattr(ws, "max_row", 0) or 0)
    except Exception:
        last_row = 0
    for r_i in range(2, last_row + 1):
        v = ws.cell(row=r_i, column=col_label).value
        if v:
            existing.add(str(v).strip())

    # Append new selections
    for label, side in selections:
        lab = str(label).strip()
        if not lab or (lab in existing):
            continue
        rnew = (int(getattr(ws, "max_row", 1) or 1)) + 1
        ws.cell(row=rnew, column=col_label, value=lab)
        ws.cell(row=rnew, column=col_type, value=str(side).strip() or "")
        # Aliases left blank intentionally
        existing.add(lab)

    out = io.BytesIO(); wb.save(out); out.seek(0)
    return out.getvalue()

# ------------------- File Upload -------------------
uploaded_file = st.file_uploader("📂 Upload Excel (with 'Star Walk scrubbed verbatims' + 'Symptoms')", type=["xlsx"])
if not uploaded_file:
    st.stop()

uploaded_bytes = uploaded_file.read()
uploaded_file.seek(0)

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
df["Verbatim"] = df["Verbatim"].astype(str).map(clean_text)

# Load Symptoms
DELIGHTERS, DETRACTORS, ALIASES = get_symptom_whitelists(uploaded_bytes)
if not DELIGHTERS and not DETRACTORS:
    st.warning("⚠️ No Symptoms found in 'Symptoms' tab.")
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

# Similarity guard for new-symptom proposals
sim_threshold = st.sidebar.slider("New‑symptom similarity guard", 0.80, 0.99, 0.94, 0.01,
                                  help="Raise to suppress near‑duplicates; lower to see more proposals.")

# ------------------- Scope & Preview -------------------
st.subheader("🧪 Symptomize")
scope = st.selectbox(
    "Choose scope",
    ["Missing both", "Any missing", "Missing delighters only", "Missing detractors only"],
    index=0,
)

if scope == "Missing both":
    target = work[(work["Needs_Delighters"]) & (work["Needs_Detractors"]) ]
elif scope == "Missing delighters only":
    target = work[(work["Needs_Delighters"]) & (~work["Needs_Detractors"]) ]
elif scope == "Missing detractors only":
    target = work[(~work["Needs_Delighters"]) & (work["Needs_Detractors"]) ]
else:
    target = work[(work["Needs_Delighters"]) | (work["Needs_Detractors"]) ]

st.write(f"🔎 **{len(target):,} reviews** match the selected scope.")
with st.expander("Preview in-scope rows", expanded=False):
    preview_cols = ["Verbatim", "Has_Delighters", "Has_Detractors", "Needs_Delighters", "Needs_Detractors"]
    extras = [c for c in ["Star Rating", "Review Date", "Source"] if c in target.columns]
    st.dataframe(target[preview_cols + extras].head(200), use_container_width=True)

# Controls (presets + ETA + sticky toolbar)
with st.container():
    st.markdown("<div class='toolbar'>", unsafe_allow_html=True)
    c1, c2, c3, c4, c5, c6 = st.columns([1.8,1,1.2,1.6,1.3,1.3])
    with c1:
        n_default = 10 if len(target) >= 10 else max(1, len(target))
        if "n_to_process" not in st.session_state:
            st.session_state["n_to_process"] = n_default
        n_to_process = st.number_input("How many to symptomize (from top of scope)", min_value=1, max_value=max(1, len(target)), value=st.session_state["n_to_process"], step=1, key="n_to_process")
        # quick presets
        p1, p2, p3, p4 = st.columns(4)
        with p1:
            if st.button("10"):
                st.session_state["n_to_process"] = min(10, max(1, len(target)))
        with p2:
            if st.button("25"):
                st.session_state["n_to_process"] = min(25, max(1, len(target)))
        with p3:
            if st.button("50"):
                st.session_state["n_to_process"] = min(50, max(1, len(target)))
        with p4:
            if st.button("100"):
                st.session_state["n_to_process"] = min(100, max(1, len(target)))
    with c2:
        run_n_btn = st.button("Symptomize N", use_container_width=True)
    with c3:
        run_all_btn = st.button("Symptomize All", use_container_width=True)
    with c4:
        overwrite_btn = st.button("Overwrite & Re‑symptomize", use_container_width=True)
    with c5:
        run_missing_both_btn = st.button("✨ Missing‑Both One‑Click", use_container_width=True)
    with c6:
        undo_btn = st.button("↩️ Undo last run", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

processed_rows: List[Dict] = []
processed_idx_set: Set[int] = set()
if "undo_stack" not in st.session_state:
    st.session_state["undo_stack"] = []

# --- Core runner ---

def _run_symptomize(rows_df: pd.DataFrame, overwrite_mode: bool = False):
    global df
    max_per_side = 10
    prog = st.progress(0.0)

    def _fmt_secs(sec: float) -> str:
        m = int(sec // 60)
        s = int(round(sec - m*60))
        return f"{m}:{s:02d}"

    t0 = time.perf_counter()
    eta_box = st.empty()

    # Prepare undo snapshot for this run
    snapshot: List[Tuple[int, Dict[str, Optional[str]]]] = []

    # Overwrite mode: clear AI columns for these rows first
    if overwrite_mode:
        df = ensure_ai_columns(df)
        idxs = rows_df.index.tolist()
        for idx_clear in idxs:
            # record old values for undo
            old_vals = {f"AI Symptom Detractor {j}": df.loc[idx_clear, f"AI Symptom Detractor {j}"] if f"AI Symptom Detractor {j}" in df.columns else None for j in range(1,11)}
            old_vals.update({f"AI Symptom Delighter {j}": df.loc[idx_clear, f"AI Symptom Delighter {j}"] if f"AI Symptom Delighter {j}" in df.columns else None for j in range(1,11)})
            old_vals.update({"AI Safety": df.loc[idx_clear, "AI Safety"] if "AI Safety" in df.columns else None,
                             "AI Reliability": df.loc[idx_clear, "AI Reliability"] if "AI Reliability" in df.columns else None,
                             "AI # of Sessions": df.loc[idx_clear, "AI # of Sessions"] if "AI # of Sessions" in df.columns else None})
            snapshot.append((int(idx_clear), old_vals))
            for j in range(1, 10+1):
                df.loc[idx_clear, f"AI Symptom Detractor {j}"] = None
                df.loc[idx_clear, f"AI Symptom Delighter {j}"] = None
            # meta stays; re-write below

    total_n = max(1, len(rows_df))
    for k, (idx, row) in enumerate(rows_df.iterrows(), start=1):
        vb = row.get("Verbatim", "")
        needs_deli = bool(row.get("Needs_Delighters", False))
        needs_detr = bool(row.get("Needs_Detractors", False))

        # Record old values for undo if not already recorded
        if not overwrite_mode:
            old_vals = {f"AI Symptom Detractor {j}": df.loc[idx, f"AI Symptom Detractor {j}"] if f"AI Symptom Detractor {j}" in df.columns else None for j in range(1,11)}
            old_vals.update({f"AI Symptom Delighter {j}": df.loc[idx, f"AI Symptom Delighter {j}"] if f"AI Symptom Delighter {j}" in df.columns else None for j in range(1,11)})
            old_vals.update({"AI Safety": df.loc[idx, "AI Safety"] if "AI Safety" in df.columns else None,
                             "AI Reliability": df.loc[idx, "AI Reliability"] if "AI Reliability" in df.columns else None,
                             "AI # of Sessions": df.loc[idx, "AI # of Sessions"] if "AI # of Sessions" in df.columns else None})
            snapshot.append((int(idx), old_vals))

        try:
            dels, dets, unl_dels, unl_dets = _openai_labeler(
                vb, client, selected_model, temperature,
                DELIGHTERS, DETRACTORS, ALIASES,
                DEL_MAP, DET_MAP, ALIAS_TO_LABEL
            ) if client else ([], [], [], [])
        except Exception:
            dels, dets, unl_dels, unl_dets = [], [], [], []

        # Meta
        try:
            safety, reliability, sessions = _openai_meta_extractor(vb, client, selected_model, temperature) if client else ("Not Mentioned","Not Mentioned","Unknown")
        except Exception:
            safety, reliability, sessions = "Not Mentioned","Not Mentioned","Unknown"

        df = ensure_ai_columns(df)

        wrote_dets, wrote_dels = [], []
        more_than_10_dets = len(dets) > 10
        more_than_10_dels = len(dels) > 10

        if needs_detr and dets:
            for j, lab in enumerate(dets[:max_per_side]):
                col = f"AI Symptom Detractor {j+1}"
                if col not in df.columns: df[col] = None
                df.loc[idx, col] = lab
            wrote_dets = dets[:max_per_side]
        if needs_deli and dels:
            for j, lab in enumerate(dels[:max_per_side]):
                col = f"AI Symptom Delighter {j+1}"
                if col not in df.columns: df[col] = None
                df.loc[idx, col] = lab
            wrote_dels = dels[:max_per_side]

        # meta always saved
        df.loc[idx, "AI Safety"] = safety
        df.loc[idx, "AI Reliability"] = reliability
        df.loc[idx, "AI # of Sessions"] = sessions

        processed_rows.append({
            "Index": int(idx),
            "Verbatim": str(vb),
            "Added_Detractors": wrote_dets,
            "Added_Delighters": wrote_dels,
            "Unlisted_Detractors": unl_dets,
            "Unlisted_Delighters": unl_dels,
            ">10 Detractors Detected": more_than_10_dets,
            ">10 Delighters Detected": more_than_10_dels,
            "Safety": safety,
            "Reliability": reliability,
            "Sessions": sessions,
        })
        processed_idx_set.add(int(idx))

        # progress + ETA
        prog.progress(k/total_n)
        elapsed = time.perf_counter() - t0
        rate = (k / elapsed) if elapsed > 0 else 0.0
        rem = total_n - k
        eta_sec = (rem / rate) if rate > 0 else 0.0
        eta_box.markdown(f"**Progress:** {k}/{total_n} • **ETA:** ~ {_fmt_secs(eta_sec)} • **Speed:** {rate*60:.1f} rev/min")

    # Push snapshot to undo stack at end of run
    st.session_state["undo_stack"].append({"rows": snapshot})

# Execute by buttons
if client is not None and (run_n_btn or run_all_btn or overwrite_btn or run_missing_both_btn):
    if run_missing_both_btn:
        rows_iter = work[(work["Needs_Delighters"]) & (work["Needs_Detractors"])]
        _run_symptomize(rows_iter, overwrite_mode=False)
    elif overwrite_btn:
        rows_iter = target if run_all_btn else target.head(int(st.session_state.get("n_to_process", 10)))
        _run_symptomize(rows_iter, overwrite_mode=True)
    else:
        rows_iter = target if run_all_btn else target.head(int(st.session_state.get("n_to_process", 10)))
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

# ------------------- Processed Reviews (chips + highlighted evidence) -------------------
if processed_rows:
    st.subheader("🧾 Processed Reviews (this run)")

    def _esc(s: str) -> str:
        return (str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))

    for rec in processed_rows:
        head = f"Row {rec['Index']} — Dets: {len(rec['Added_Detractors'])} • Dels: {len(rec['Added_Delighters'])}"
        if rec[">10 Detractors Detected"] or rec[">10 Delighters Detected"]:
            head += " • ⚠︎ >10 detected (trimmed to 10)"
        with st.expander(head):
            # Build highlight term list = labels + known aliases for those labels
            terms: List[str] = []
            for lab in list(rec["Added_Detractors"]) + list(rec["Added_Delighters"]):
                terms.append(lab)
                if ALIASES and lab in ALIASES:
                    terms.extend(ALIASES[lab])
            st.markdown("**Verbatim (evidence highlighted)**")
            st.markdown(highlight_text(rec["Verbatim"], terms), unsafe_allow_html=True)

            # Meta chips (Safety / Reliability / Sessions)
            meta_html = (
                "<div class='chips-block chip-wrap'>"
                f"<span class='chip yellow'>Safety: {_esc(rec.get('Safety','Not Mentioned'))}</span>"
                f"<span class='chip blue'>Reliability: {_esc(rec.get('Reliability','Not Mentioned'))}</span>"
                f"<span class='chip purple'># Sessions: {_esc(rec.get('Sessions','Unknown'))}</span>"
                "</div>"
            )
            st.markdown(meta_html, unsafe_allow_html=True)

            st.markdown("**Detractors added**")
            st.markdown("<div class='chips-block chip-wrap'>" + "".join([f"<span class='chip red'>{_esc(x)}</span>" for x in rec["Added_Detractors"]]) + "</div>", unsafe_allow_html=True)
            st.markdown("**Delighters added**")
            st.markdown("<div class='chips-block chip-wrap'>" + "".join([f"<span class='chip green'>{_esc(x)}</span>" for x in rec["Added_Delighters"]]) + "</div>", unsafe_allow_html=True)
            if rec["Unlisted_Detractors"]:
                st.markdown("**Unlisted detractors (candidates)**")
                st.markdown("<div class='chips-block chip-wrap'>" + "".join([f"<span class='chip red'>{_esc(x)}</span>" for x in rec["Unlisted_Detractors"]]) + "</div>", unsafe_allow_html=True)
            if rec["Unlisted_Delighters"]:
                st.markdown("**Unlisted delighters (candidates)**")
                st.markdown("<div class='chips-block chip-wrap'>" + "".join([f"<span class='chip green'>{_esc(x)}</span>" for x in rec["Unlisted_Delighters"]]) + "</div>", unsafe_allow_html=True)

# ------------------- New Symptom Candidates (Approval form) -------------------
cand_del: Dict[str, List[int]] = {}
cand_det: Dict[str, List[int]] = {}
for rec in processed_rows:
    for u in rec.get("Unlisted_Delighters", []) or []:
        cand_del.setdefault(u, []).append(rec["Index"])
    for u in rec.get("Unlisted_Detractors", []) or []:
        cand_det.setdefault(u, []).append(rec["Index"])

# Suppress near-duplicates vs whitelist & aliases
whitelist_all = set(DELIGHTERS + DETRACTORS)
alias_all = set([a for lst in ALIASES.values() for a in lst]) if ALIASES else set()
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
            filtered[sym] = list(refs)
            seen_key[c] = sym
    return filtered

cand_del = _filter_near_dupes(cand_del, cutoff=sim_threshold)
cand_det = _filter_near_dupes(cand_det, cutoff=sim_threshold)

if cand_del or cand_det:
    st.subheader("🟡 New Symptom Inbox — Review & Approve")

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
                "Examples": " | ".join(["— "+e[:200] for e in examples])
            })
        tbl = pd.DataFrame(rows_tbl)
        return tbl.astype({"Add": bool, "Label": str, "Side": str, "Count": int, "Examples": str})

    tbl_del = _mk_table(cand_del, "Delighter")
    tbl_det = _mk_table(cand_det, "Detractor")

    with st.form("new_symptom_candidates_form", clear_on_submit=False):
        cA, cB = st.columns(2)
        with cA:
            st.markdown("**Delighter candidates**")
            editor_del = st.data_editor(
                tbl_del,
                num_rows="fixed",
                use_container_width=True,
                column_config={
                    "Add": st.column_config.CheckboxColumn(help="Check to add to the Symptoms sheet"),
                    "Label": st.column_config.TextColumn(),
                    "Side": st.column_config.SelectboxColumn(options=["Delighter","Detractor"]),
                    "Count": st.column_config.NumberColumn(format="%d"),
                    "Examples": st.column_config.TextColumn(width="large"),
                },
                key="cand_del_editor",
            )
        with cB:
            st.markdown("**Detractor candidates**")
            editor_det = st.data_editor(
                tbl_det,
                num_rows="fixed",
                use_container_width=True,
                column_config={
                    "Add": st.column_config.CheckboxColumn(help="Check to add to the Symptoms sheet"),
                    "Label": st.column_config.TextColumn(),
                    "Side": st.column_config.SelectboxColumn(options=["Delighter","Detractor"]),
                    "Count": st.column_config.NumberColumn(format="%d"),
                    "Examples": st.column_config.TextColumn(width="large"),
                },
                key="cand_det_editor",
            )
        add_btn = st.form_submit_button("✅ Add selected to Symptoms & Download updated workbook")

    if add_btn:
        selections: List[Tuple[str, str]] = []
        try:
            if isinstance(editor_del, pd.DataFrame) and not editor_del.empty:
                for _, r_ in editor_del.iterrows():
                    if bool(r_.get("Add", False)) and str(r_.get("Label", "")).strip():
                        side_val = str(r_.get("Side","Delighter")).strip() or "Delighter"
                        selections.append((str(r_["Label"]).strip(), side_val))
        except Exception:
            pass
        try:
            if isinstance(editor_det, pd.DataFrame) and not editor_det.empty:
                for _, r_ in editor_det.iterrows():
                    if bool(r_.get("Add", False)) and str(r_.get("Label", "")).strip():
                        side_val = str(r_.get("Side","Detractor")).strip() or "Detractor"
                        selections.append((str(r_["Label"]).strip(), side_val))
        except Exception:
            pass

        if selections:
            updated_bytes = add_new_symptoms_to_workbook(uploaded_file, selections)
            st.download_button(
                "⬇️ Download 'Symptoms' (updated)",
                data=updated_bytes,
                file_name="Symptoms_Updated.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            st.success(f"Queued {len(selections)} new label(s) into the Symptoms tab.")
        else:
            st.info("No candidates selected.")

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
st.download_button("⬇️ Download Symptoms Catalog (XLSX)", sym_bytes.getvalue(),
                   file_name=f"{file_base}_Symptoms_Catalog.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ------------------- View Symptoms from Workbook (expander) -------------------
st.subheader("📘 View Symptoms from Excel Workbook")
with st.expander("📘 View Symptoms from Excel Workbook", expanded=False):
    st.markdown("This reflects the **Symptoms** sheet as loaded; use the inbox below to propose additions.")

    tabs = st.tabs(["Delighters", "Detractors", "Aliases", "Meta"])  # includes Meta tab

    def _esc(s: str) -> str:
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _chips(items, color: str):
        items_sorted = sorted({str(x).strip() for x in (items or []) if str(x).strip()})
        if not items_sorted:
            st.write("(none)")
        else:
            htmlchips = "<div class='chip-wrap'>" + "".join([f"<span class='chip {color}'>{_esc(x)}</span>" for x in items_sorted]) + "</div>"
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
        # ensure meta columns exist
        df_meta = ensure_ai_columns(df.copy())

        def _count(col: str, order: List[str]) -> pd.DataFrame:
            if col not in df_meta.columns:
                return pd.DataFrame({"Value": order, "Count": [0] * len(order)})
            vc = (
                df_meta[col]
                .fillna("Not Mentioned")
                .astype(str)
                .value_counts()
                .reindex(order, fill_value=0)
            )
            return vc.rename_axis("Value").reset_index(name="Count")

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("**Safety**")
            df_s = _count("AI Safety", SAFETY_ENUM)
            st.bar_chart(df_s.set_index("Value")["Count"])
            chips = "<div class='chip-wrap'>" + "".join([f"<span class='chip yellow'>{_esc(v)} · {int(c)}</span>" for v, c in df_s.itertuples(index=False)]) + "</div>"
            st.markdown(chips, unsafe_allow_html=True)
        with c2:
            st.markdown("**Reliability**")
            df_r = _count("AI Reliability", RELIABILITY_ENUM)
            st.bar_chart(df_r.set_index("Value")["Count"])
            chips = "<div class='chip-wrap'>" + "".join([f"<span class='chip blue'>{_esc(v)} · {int(c)}</span>" for v, c in df_r.itertuples(index=False)]) + "</div>"
            st.markdown(chips, unsafe_allow_html=True)
        with c3:
            st.markdown("**# of Sessions**")
            df_n = _count("AI # of Sessions", SESSIONS_ENUM)
            st.bar_chart(df_n.set_index("Value")["Count"])
            chips = "<div class='chip-wrap'>" + "".join([f"<span class='chip purple'>{_esc(v)} · {int(c)}</span>" for v, c in df_n.itertuples(index=False)]) + "</div>"
            st.markdown(chips, unsafe_allow_html=True)

# ------------------- Browse Symptoms -------------------

st.subheader("🔎 Browse Symptoms")
view_side = st.selectbox("View", ["Detractors", "Delighters"], index=0)

col_det_all = colmap.get("manual_detractors", []) + colmap.get("ai_detractors", [])
col_del_all = colmap.get("manual_delighters", []) + colmap.get("ai_delighters", [])

def _label_counts(df_in: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    vals: List[str] = []
    for c in cols:
        if c in df_in.columns:
            series = df_in[c].dropna().astype(str).map(str.strip)
            vals.extend([v for v in series if is_filled(v)])
    vc = pd.Series(vals).value_counts().reset_index() if vals else pd.DataFrame(columns=["Label","Count"]).assign(Label=[], Count=[])
    if not vc.empty:
        vc.columns = ["Label", "Count"]
    return vc

counts_df = _label_counts(df, col_det_all if view_side=="Detractors" else col_del_all)

st.markdown("**Top labels**")
if counts_df.empty:
    st.write("(none found)")
else:
    color = "red" if view_side=="Detractors" else "green"
    chips_html = "<div class='chip-wrap'>" + "".join([f"<span class='chip {color}'>{l} · {c}</span>" for l, c in counts_df.head(60).itertuples(index=False)]) + "</div>"
    st.markdown(chips_html, unsafe_allow_html=True)

# ------------------- Quick Label Picker (dropdowns for all options) -------------------
st.subheader("🎯 Quick Label Picker")
qp_col1, qp_col2, qp_col3 = st.columns([1.2,2,2])
with qp_col1:
    qp_side = st.selectbox("Side", ["Delighters", "Detractors"], index=0, key="qp_side")

# Build option list from whitelist (not from data, so you can browse everything available)
qp_options = sorted(DELIGHTERS) if qp_side == "Delighters" else sorted(DETRACTORS)
if not qp_options:
    st.info("No options found in the Symptoms tab for this side.")
else:
    with qp_col2:
        qp_label = st.selectbox("Label", qp_options, key="qp_label")
    with qp_col3:
        st.markdown("**Picked**")
        color = "green" if qp_side=="Delighters" else "red"
        st.markdown(f"<div class='chip-wrap'><span class='chip {color}'>{qp_label}</span></div>", unsafe_allow_html=True)

    # Show where this label already appears in labeled columns (manual + AI)
    side_cols = (colmap.get("manual_delighters", []) + colmap.get("ai_delighters", [])) if qp_side=="Delighters" else (colmap.get("manual_detractors", []) + colmap.get("ai_detractors", []))
    mask_any = pd.Series([False]*len(df))
    for c in side_cols:
        if c in df.columns:
            try:
                mask_any = mask_any | (df[c].astype(str).str.strip() == qp_label)
            except Exception:
                pass
    labeled_hits = df.loc[mask_any]

    st.markdown("**Labeled occurrences**")
    if labeled_hits.empty:
        st.write("(no labeled occurrences found in the current sheet)")
    else:
        show_cols = ["Verbatim"] + [c for c in ["Star Rating", "Review Date", "Source"] if c in labeled_hits.columns]
        st.dataframe(labeled_hits[show_cols].head(200), use_container_width=True)

    # Also show plain-text mentions in verbatims as a fallback view
    try:
        verb_mask = df["Verbatim"].str.contains(qp_label, case=False, na=False, regex=False)
        mention_hits = df.loc[verb_mask & (~mask_any)]  # exclude already labeled rows
    except Exception:
        mention_hits = pd.DataFrame()

    st.markdown("**Mentions in verbatims (not yet labeled)**")
    if mention_hits.empty:
        st.write("(no additional verbatim mentions)")
    else:
        show_cols2 = ["Verbatim"] + [c for c in ["Star Rating", "Review Date", "Source"] if c in mention_hits.columns]
        st.dataframe(mention_hits[show_cols2].head(200), use_container_width=True)

# Footer
st.divider()
st.caption("Exports write EXACTLY to K–T (dets) and U–AD (dels); meta to AE/AF/AG. Undo enabled. Approvals use a real submit button. ETA & speed shown during runs. Similarity guard filters near‑dupe proposals. Evidence highlighting shows where terms appear.")






