# starwalk_ui_v4.py (fixed + smarter de‑dup)
# Streamlit App — Dynamic Symptoms • Model Selector • Smart Auto‑Symptomization • Approval Queue
# Exact export (Detractors → K–T, Delighters → U–AD), Color‑Coded • Stronger duplicate suppression
# Requirements: streamlit>=1.28, pandas, openpyxl, openai (optional), rapidfuzz (optional)

import streamlit as st
import pandas as pd
import numpy as np
import io, os, re, json, difflib, math
from typing import List, Dict, Tuple
from datetime import datetime

# Optional: OpenAI
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    OpenAI = None  # type: ignore
    _HAS_OPENAI = False

# Optional: RapidFuzz for stronger fuzzy matching
try:
    from rapidfuzz import fuzz as rf_fuzz
    _HAS_RAPIDFUZZ = True
except Exception:
    _HAS_RAPIDFUZZ = False

# Excel handling
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from openpyxl.styles import PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter

# ------------------- Page Setup -------------------
st.set_page_config(layout="wide", page_title="Star Walk Review Analyzer v4")
st.title("🌟 Star Walk Review Analyzer v4")
st.caption("Dynamic Symptoms • Model Selector • Smart Auto‑Symptomize • Approval Queue • Color‑Coded Excel Export")

# Compatibility shim: some legacy blocks referenced this flag.
build_clicked = False

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
                    alias_map[lbl] = [p.strip() for p in re.split(r"[|,]", als) if p.strip()]
    else:
        for lc, orig in lowcols.items():
            if ("delight" in lc) or ("positive" in lc) or lc in {"pros"}:
                delighters.extend(_clean(df_sym[orig]))
            if ("detract" in lc) or ("negative" in lc) or lc in {"cons"}:
                detractors.extend(_clean(df_sym[orig]))
        delighters = list(dict.fromkeys(delighters))
        detractors = list(dict.fromkeys(detractors))

    return delighters, detractors, alias_map

@st.cache_data(show_spinner=False)
def read_symptoms_sheet(file_bytes: bytes) -> pd.DataFrame:
    bio = io.BytesIO(file_bytes)
    try:
        df_sym = pd.read_excel(bio, sheet_name="Symptoms")
        if df_sym is None:
            return pd.DataFrame()
        df_sym.columns = [str(c).strip() for c in df_sym.columns]
        return df_sym
    except Exception:
        return pd.DataFrame()

# ------------------- Column detection & missing flags -------------------
def detect_symptom_columns(df: pd.DataFrame) -> Dict[str, List[str]]:
    cols = [str(c).strip() for c in df.columns]
    man_det = [f"Symptom {i}" for i in range(1, 11) if f"Symptom {i}" in cols]
    man_del = [f"Symptom {i}" for i in range(11, 21) if f"Symptom {i}" in cols]
    ai_det  = [c for c in cols if re.fullmatch(r"AI Symptom Detractor \d+", c)]
    ai_del  = [c for c in cols if re.fullmatch(r"AI Symptom Delighter \d+", c)]
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

# ------------------- Fixed template column mapping -------------------
DET_LETTERS = ["K","L","M","N","O","P","Q","R","S","T"]
DEL_LETTERS = ["U","V","W","X","Y","Z","AA","AB","AC","AD"]
DET_INDEXES = [column_index_from_string(c) for c in DET_LETTERS]
DEL_INDEXES = [column_index_from_string(c) for c in DEL_LETTERS]
AI_DET_HEADERS = [f"AI Symptom Detractor {i}" for i in range(1, 11)]
AI_DEL_HEADERS = [f"AI Symptom Delighter {i}" for i in range(1, 11)]

def ensure_ai_columns(df_in: pd.DataFrame) -> pd.DataFrame:
    for h in AI_DET_HEADERS + AI_DEL_HEADERS:
        if h not in df_in.columns:
            df_in[h] = None
    return df_in

# ------------------- Canonicalization & similarity -------------------
def _canon(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()

def _norm_tokens(s: str) -> List[str]:
    s = _canon(re.sub(r"[^a-zA-Z0-9\s]", " ", s))
    return [t for t in s.split() if t]

def _token_set_key(s: str) -> str:
    return " ".join(sorted(set(_norm_tokens(s))))

def _jaccard(a: str, b: str) -> float:
    A, B = set(_norm_tokens(a)), set(_norm_tokens(b))
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)

def string_similarity(a: str, b: str) -> float:
    a_s, b_s = a or "", b or ""
    r1 = difflib.SequenceMatcher(None, a_s.lower(), b_s.lower()).ratio()
    r2 = _jaccard(a_s, b_s)
    r3 = 0.0
    if _HAS_RAPIDFUZZ:
        try:
            r3 = max(rf_fuzz.QRatio(a_s, b_s), rf_fuzz.token_set_ratio(a_s, b_s), rf_fuzz.token_sort_ratio(a_s, b_s)) / 100.0
        except Exception:
            r3 = 0.0
    return max(r1, r2, r3)

# Optional: Embeddings for semantic dedup (cached)
EMBED_MODEL = "text-embedding-3-small"

def _get_client():
    api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
    return OpenAI(api_key=api_key) if (_HAS_OPENAI and api_key) else None

client = _get_client()

def _embed_cached(text: str):
    if client is None:
        return None
    key = f"emb::{EMBED_MODEL}::{text}"
    cache = st.session_state.setdefault("_emb_cache", {})
    if key in cache:
        return cache[key]
    try:
        vec = client.embeddings.create(model=EMBED_MODEL, input=text).data[0].embedding
        cache[key] = vec
        return vec
    except Exception:
        return None

def _cosine(u: List[float], v: List[float]) -> float:
    if u is None or v is None:
        return 0.0
    s = sum(uu*vv for uu, vv in zip(u, v))
    nu = math.sqrt(sum(uu*uu for uu in u))
    nv = math.sqrt(sum(vv*vv for vv in v))
    if nu == 0 or nv == 0:
        return 0.0
    return s / (nu * nv)

# Build corpus (labels + aliases → canonical label)
def build_corpus_maps(delighters: List[str], detractors: List[str], alias_map: Dict[str, List[str]]):
    del_corpus: Dict[str, str] = {}
    det_corpus: Dict[str, str] = {}
    for lbl in delighters:
        del_corpus[lbl] = lbl
    for lbl in detractors:
        det_corpus[lbl] = lbl
    for lbl, aliases in (alias_map or {}).items():
        for a in aliases or []:
            if lbl in delighters:
                del_corpus[a] = lbl
            if lbl in detractors:
                det_corpus[a] = lbl
    return del_corpus, det_corpus

# Best match finder (string + optional embeddings)
def best_match(candidate: str, side: str, del_corpus: Dict[str, str], det_corpus: Dict[str, str], use_embeddings: bool, str_thresh: float, emb_thresh: float) -> Tuple[str, float, str]:
    corpus = del_corpus if side == "Delighter" else det_corpus
    best_label, best_txt, best_score, best_method = "", "", 0.0, "string"

    # 1) String-based
    for txt, label in corpus.items():
        s = string_similarity(candidate, txt)
        if s > best_score:
            best_score, best_label, best_txt, best_method = s, label, txt, "string"

    # 2) Embeddings vs canonical labels only (fewer calls)
    if use_embeddings and client is not None:
        try:
            c_vec = _embed_cached(candidate)
            for label in set(corpus.values()):
                l_vec = _embed_cached(label)
                s = _cosine(c_vec, l_vec)
                if s > best_score:
                    best_score, best_label, best_txt, best_method = s, label, label, "embed"
        except Exception:
            pass

    return best_label, float(best_score), best_method

# ---------- LLM labeler ----------

def build_canonical_maps(delighters: List[str], detractors: List[str], alias_map: Dict[str, List[str]]):
    del_map = {_canon(x): x for x in delighters}
    det_map = {_canon(x): x for x in detractors}
    alias_to_label: Dict[str, str] = {}
    for label, aliases in (alias_map or {}).items():
        for a in aliases:
            alias_to_label[_canon(a)] = label
    return del_map, det_map, alias_to_label


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

    sys = (
        "Classify this Shark Glossi review into delighters and detractors. "
        "Use ONLY the provided whitelists; do NOT invent labels. "
        "If a synonym is close but not listed, output it under the correct 'unlisted' bucket.\n"
        f"DELIGHTERS = {json.dumps(delighters, ensure_ascii=False)}\n"
        f"DETRACTORS = {json.dumps(detractors, ensure_ascii=False)}\n"
        f"ALIASES = {json.dumps(alias_map, ensure_ascii=False)}\n"
        'Return strict JSON: {"delighters":[],"detractors":[],"unlisted_delighters":[],"unlisted_detractors":[]}'
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=float(temperature),
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": f"Review:\n\"\"\"{verbatim.strip()}\"\"\""},
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
                    label = del_map.get(key)
                    if not label:
                        alias_label = alias_to_label.get(key)
                        if alias_label and alias_label in delighters:
                            label = alias_label
                else:
                    label = det_map.get(key)
                    if not label:
                        alias_label = alias_to_label.get(key)
                        if alias_label and alias_label in detractors:
                            label = alias_label
                if label and label not in mapped:
                    mapped.append(label)
            return mapped[:10]

        dels = _map_side(data.get("delighters", []), side="del")
        dets = _map_side(data.get("detractors", []), side="det")
        unl_dels = [x for x in (data.get("unlisted_delighters", []) or [])][:10]
        unl_dets = [x for x in (data.get("unlisted_detractors", []) or [])][:10]
        return dels, dets, unl_dels, unl_dets
    except Exception:
        return [], [], [], []

# ------------------- Export helpers -------------------

def write_updated_excel(original_file, updated_df: pd.DataFrame, output_name="AI_Symptomized_Reviews.xlsx"):
    """Write AI columns into the exact template positions:
    Detractors → K..T (10), Delighters → U..AD (10). Preserve headers and color only filled cells."""
    original_file.seek(0)
    wb = load_workbook(original_file)
    sheet_name = "Star Walk scrubbed verbatims"
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.sheetnames[0]
    ws: Worksheet = wb[sheet_name]

    updated_df = ensure_ai_columns(updated_df)

    fill_green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    fill_red   = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

    for i, (_, r) in enumerate(updated_df.iterrows(), start=2):
        for j, col_idx in enumerate(DET_INDEXES, start=1):
            val = r.get(f"AI Symptom Detractor {j}")
            cell_value = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cell_value)
            if cell_value is not None:
                cell.fill = fill_red
        for j, col_idx in enumerate(DEL_INDEXES, start=1):
            val = r.get(f"AI Symptom Delighter {j}")
            cell_value = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cell_value)
            if cell_value is not None:
                cell.fill = fill_green

    for c in DET_INDEXES + DEL_INDEXES:
        try:
            ws.column_dimensions[get_column_letter(c)].width = 28
        except Exception:
            pass

    out = io.BytesIO(); wb.save(out); out.seek(0)
    st.download_button(
        "⬇️ Download Updated Excel (Color‑Coded)", out,
        file_name=output_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def generate_updated_workbook_bytes(original_file, updated_df: pd.DataFrame) -> bytes:
    """Return bytes for a workbook matching the original, with AI columns placed in template slots:
    Detractors K–T, Delighters U–AD (10 each)."""
    original_file.seek(0)
    wb = load_workbook(original_file)
    sheet_name = "Star Walk scrubbed verbatims"
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.sheetnames[0]
    ws: Worksheet = wb[sheet_name]

    updated_df = ensure_ai_columns(updated_df.copy())

    fill_green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    fill_red   = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")

    for i, (_, r) in enumerate(updated_df.iterrows(), start=2):
        for j, col_idx in enumerate(DET_INDEXES, start=1):
            val = r.get(f"AI Symptom Detractor {j}")
            cell_value = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cell_value)
            if cell_value is not None:
                cell.fill = fill_red
        for j, col_idx in enumerate(DEL_INDEXES, start=1):
            val = r.get(f"AI Symptom Delighter {j}")
            cell_value = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cell_value)
            if cell_value is not None:
                cell.fill = fill_green

    out = io.BytesIO(); wb.save(out)
    return out.getvalue()

# ------------------- Helpers: add new symptoms to Symptoms sheet -------------------

def add_new_symptoms_to_workbook(original_file, selections: List[Tuple[str, str]]) -> bytes:
    original_file.seek(0)
    wb = load_workbook(original_file)
    if "Symptoms" not in wb.sheetnames:
        ws = wb.create_sheet("Symptoms")
        ws.cell(row=1, column=1, value="Symptom")
        ws.cell(row=1, column=2, value="Type")
        ws.cell(row=1, column=3, value="Aliases")
    else:
        ws = wb["Symptoms"]

    headers = [str(c.value).strip() if c.value else "" for c in ws[1]]
    hlow = [h.lower() for h in headers]

    def _col_idx(names: List[str]) -> int:
        for nm in names:
            if nm.lower() in hlow:
                return hlow.index(nm.lower()) + 1
        return -1

    col_label = _col_idx(["symptom", "label", "name", "item"]) or 1
    col_type  = _col_idx(["type", "polarity", "category", "side"]) or 2
    col_alias = _col_idx(["aliases", "alias"])  # may be -1

    if len(headers) < col_label or not headers[col_label-1]:
        ws.cell(row=1, column=col_label, value="Symptom")
    if len(headers) < col_type or not headers[col_type-1]:
        ws.cell(row=1, column=col_type, value="Type")
    if col_alias == -1:
        col_alias = len(headers) + 1
        ws.cell(row=1, column=col_alias, value="Aliases")

    existing = set()
    for r_i in range(2, ws.max_row + 1):
        v = ws.cell(row=r_i, column=col_label).value
        if v:
            existing.add(str(v).strip())

    for label, side in selections:
        lab = str(label).strip()
        if not lab:
            continue
        if lab in existing:
            continue
        row_new = ws.max_row + 1
        ws.cell(row=row_new, column=col_label, value=lab)
        ws.cell(row=row_new, column=col_type, value=str(side).strip() or "")
        ws.cell(row=row_new, column=col_alias, value="")
        existing.add(lab)

    out = io.BytesIO(); wb.save(out); out.seek(0)
    return out.getvalue()

# ------------------- File Upload -------------------
uploaded_file = st.file_uploader("📂 Upload Excel (with 'Star Walk scrubbed verbatims' + 'Symptoms')", type=["xlsx"])
if not uploaded_file:
    st.stop()

# Read once into bytes for caching + multiple passes
uploaded_bytes = uploaded_file.read()
uploaded_file.seek(0)

# Reviews sheet
try:
    df = pd.read_excel(uploaded_file, sheet_name="Star Walk scrubbed verbatims")
except ValueError:
    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file)

if "Verbatim" not in df.columns:
    st.error("Missing 'Verbatim' column.")
    st.stop()

# Normalize columns
df.columns = [str(c).strip() for c in df.columns]
df["Verbatim"] = df["Verbatim"].astype(str).map(clean_text)

# Load Symptoms
DELIGHTERS, DETRACTORS, ALIASES = get_symptom_whitelists(uploaded_bytes)
DEL_MAP, DET_MAP, ALIAS_TO_LABEL = build_canonical_maps(DELIGHTERS, DETRACTORS, ALIASES)
DEL_CORPUS, DET_CORPUS = build_corpus_maps(DELIGHTERS, DETRACTORS, ALIASES)

if not DELIGHTERS and not DETRACTORS:
    st.warning("⚠️ No Symptoms found in 'Symptoms' tab.")
else:
    st.success(f"Loaded {len(DELIGHTERS)} Delighters, {len(DETRACTORS)} Detractors from Symptoms tab.")

# ------------------- LLM Settings -------------------
st.sidebar.header("🤖 LLM Settings")
MODEL_CHOICES = {
    "Fast – GPT‑4o‑mini": "gpt-4o-mini",
    "Balanced – GPT‑4o": "gpt-4o",
    "Advanced – GPT‑4.1": "gpt-4.1",
    "Most Advanced – GPT‑5": "gpt-5",
}
model_label = st.sidebar.selectbox("Model", list(MODEL_CHOICES.keys()), index=1)
selected_model = MODEL_CHOICES[model_label]
temperature = st.sidebar.slider("Creativity (temperature)", 0.0, 1.0, 0.2, 0.1)
if client is None:
    st.sidebar.warning("OpenAI not configured — set OPENAI_API_KEY and install 'openai'.")

# ------------------- Detection & KPIs -------------------
colmap = detect_symptom_columns(df)
work = detect_missing(df, colmap)

st.info(f"Total reviews: {len(work):,} • Need BOTH sides: {int(work['Needs_Symptomization'].sum()):,}")

# Scope picker
scope = st.radio(
    "Process scope",
    ["Any missing", "Missing both", "Missing delighters only", "Missing detractors only"],
    horizontal=True,
)

if scope == "Missing both":
    target = work[(work["Needs_Delighters"]) & (work["Needs_Detractors"])]
elif scope == "Missing delighters only":
    target = work[(work["Needs_Delighters"]) & (~work["Needs_Detractors"]) ]
elif scope == "Missing detractors only":
    target = work[(~work["Needs_Delighters"]) & (work["Needs_Detractors"]) ]
else:
    target = work[(work["Needs_Delighters"]) | (work["Needs_Detractors"]) ]

st.write(f"🔎 **{len(target):,} reviews** match the **selected scope**.")

# ------------------- Symptomize Center -------------------
st.subheader("🧪 Symptomize")
count_max = int(len(target))
if count_max == 0:
    st.info("Nothing in scope to symptomize.")
else:
    st.caption("Choose how many to process — nothing runs automatically.")
    c1, c2, c3, _ = st.columns([2,1,1,2])
    with c1:
        n_default = int(min(50, max(1, count_max)))
        n_to_sym_main = st.number_input("Count to symptomize (from top of scope)", min_value=1, max_value=count_max, value=n_default, step=1)
    with c2:
        run_n_main = st.button("Run N", use_container_width=True)
    with c3:
        run_all_main = st.button("Run ALL", use_container_width=True)

    if run_n_main or run_all_main:
        if client is None:
            st.error("OpenAI not configured — cannot symptomize.")
        else:
            max_per_side = 10
            rows_iter = target if run_all_main else target.head(int(n_to_sym_main))
            prog = st.progress(0.0)
            total_n = max(1, len(rows_iter))

            processed_rows = []
            cand_del: Dict[str, List[int]] = {}
            cand_det: Dict[str, List[int]] = {}

            for k, (idx, row) in enumerate(rows_iter.iterrows(), start=1):
                vb = row.get("Verbatim", "")
                needs_deli = bool(row.get("Needs_Delighters", False))
                needs_detr = bool(row.get("Needs_Detractors", False))
                try:
                    dels, dets, unl_dels, unl_dets = _openai_labeler(
                        vb, client, selected_model, temperature,
                        DELIGHTERS, DETRACTORS, ALIASES,
                        DEL_MAP, DET_MAP, ALIAS_TO_LABEL
                    )
                except Exception:
                    dels, dets, unl_dels, unl_dets = [], [], [], []

                wrote_dets = []
                wrote_dels = []
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

                for u in unl_dels:
                    cand_del.setdefault(u, []).append(idx)
                for u in unl_dets:
                    cand_det.setdefault(u, []).append(idx)

                processed_rows.append({
                    "Index": int(idx),
                    "Verbatim": str(vb),  # full verbatim
                    "Added_Delighters": wrote_dels,
                    "Added_Detractors": wrote_dets,
                    "Unlisted_Delighters": unl_dels,
                    "Unlisted_Detractors": unl_dets,
                })

                prog.progress(k/total_n)

            st.session_state["processed_rows"] = processed_rows
            st.success(f"Symptomized {len(processed_rows)} review(s) in the selected scope.")

            # ------------------- New Symptom Candidates (de‑dup aware) -------------------
            cand_total = len(cand_del) + len(cand_det)
            if cand_total:
                st.subheader("🟡 New Symptom Inbox — Smarter de‑dup")

                # Settings for de‑dup
                with st.expander("De‑dup settings", expanded=True):
                    cdd1, cdd2, cdd3, cdd4 = st.columns([1,1,1,1])
                    with cdd1:
                        str_thresh = st.slider("String threshold", 0.70, 0.98, 0.88, 0.01, help="Above this, we treat as likely alias")
                    with cdd2:
                        use_emb = st.checkbox("Use embeddings (if configured)", value=False)
                    with cdd3:
                        emb_thresh = st.slider("Embed threshold", 0.70, 0.98, 0.86, 0.01, disabled=not use_emb)
                    with cdd4:
                        hide_near_dups = st.checkbox("Hide near‑duplicates by default", value=True)

                def _mk_table(cmap: Dict[str, List[int]], side_label: str) -> pd.DataFrame:
                    # First, merge near‑identical candidates by token set key
                    grouped: Dict[str, Dict] = {}
                    for sym, refs in cmap.items():
                        key = _token_set_key(sym)
                        bucket = grouped.setdefault(key, {"rep": sym, "refs": []})
                        # pick longer rep (more informative)
                        if len(sym) > len(bucket["rep"]):
                            bucket["rep"] = sym
                        bucket["refs"].extend(refs)

                    rows_tbl = []
                    for _, info in grouped.items():
                        sym = info["rep"]
                        refs = info["refs"]
                        # Examples
                        examples = []
                        for ridx in refs[:3]:
                            try:
                                examples.append((str(df.loc[ridx, "Verbatim"]) or ""))
                            except Exception:
                                pass
                        examples_text = " | ".join(["— "+e[:180] for e in examples])

                        # Best match against whitelist + aliases
                        best_lbl, score, method = best_match(sym, side_label, DEL_CORPUS, DET_CORPUS, use_emb, str_thresh, emb_thresh)
                        near_dup = (score >= (emb_thresh if (method == "embed" and use_emb) else str_thresh))

                        rows_tbl.append({
                            "Add": False,
                            "Label": sym,
                            "Side": side_label,
                            "Count": len(refs),
                            "Examples": examples_text,
                            "Suggested Mapping": best_lbl,
                            "Similarity": round(score*100, 1),
                            "Method": method,
                            "NearDuplicate": near_dup,
                        })
                    if not rows_tbl:
                        return pd.DataFrame({"Add": [], "Label": [], "Side": [], "Count": [], "Examples": [], "Suggested Mapping": [], "Similarity": [], "Method": [], "NearDuplicate": []})
                    return pd.DataFrame(rows_tbl).sort_values(["NearDuplicate","Count"], ascending=[True, False]).reset_index(drop=True)

                tbl_del = _mk_table(cand_del, "Delighter")
                tbl_det = _mk_table(cand_det, "Detractor")

                # Combine and optionally hide near‑dups
                show_df = pd.concat([tbl_det, tbl_del], ignore_index=True)
                if hide_near_dups:
                    show_df = show_df[~show_df["NearDuplicate"]].reset_index(drop=True)

                if show_df.empty:
                    st.info("No novel candidates after de‑dup. (Near‑duplicates were hidden.)")
                else:
                    st.markdown("**Decide for each candidate:** set *Action* to `Add as new` or `Alias of`, and choose a *Target Label* if aliasing. Near‑duplicates are pre‑flagged.")
                    # Default action policy: alias when similarity exceeds threshold
                    def _default_action(row):
                        return "Alias of" if bool(row.get("NearDuplicate", False)) or str(row.get("Suggested Mapping", "")).strip() else "Add as new"
                    show_df["Action"] = show_df.apply(_default_action, axis=1)
                    show_df["Target Label"] = show_df["Suggested Mapping"].fillna("")

                    edited = st.data_editor(
                        show_df,
                        num_rows="fixed",
                        use_container_width=True,
                        column_config={
                            "Action": st.column_config.SelectboxColumn(options=["Add as new", "Alias of"], required=True),
                            "Target Label": st.column_config.TextColumn(help="Required if aliasing"),
                            "Examples": st.column_config.TextColumn(width="large"),
                            "Similarity": st.column_config.NumberColumn(format="%.1f"),
                            "Method": st.column_config.TextColumn(),
                            "NearDuplicate": st.column_config.CheckboxColumn(disabled=True),
                        },
                        key="new_symptom_inbox",
                    )

                    confirm_changes = st.checkbox("I confirm the actions above are correct.")
                    if st.button("✅ Apply actions & Download updated 'Symptoms' workbook"):
                        if not confirm_changes:
                            st.error("Please confirm the actions before applying.")
                        else:
                            uploaded_file.seek(0)
                            wb = load_workbook(uploaded_file)
                            if "Symptoms" not in wb.sheetnames:
                                st.error("No 'Symptoms' sheet found; cannot apply approvals.")
                            else:
                                ws = wb["Symptoms"]
                                headers = [str(c.value).strip() if c.value else "" for c in ws[1]]
                                hlow = [h.lower() for h in headers]

                                def _col_idx(names: List[str]) -> int:
                                    for nm in names:
                                        if nm.lower() in hlow:
                                            return hlow.index(nm.lower()) + 1
                                    return -1

                                col_label = _col_idx(["symptom", "label", "name", "item"]) or 1
                                col_type  = _col_idx(["type", "polarity", "category", "side"]) or 2
                                col_alias = _col_idx(["aliases", "alias"])
                                if col_alias == -1:
                                    col_alias = len(headers) + 1
                                    ws.cell(row=1, column=col_alias, value="Aliases")

                                label_to_row: Dict[str, int] = {}
                                existing_aliases: Dict[str, str] = {}
                                for r_i in range(2, ws.max_row + 1):
                                    lbl = ws.cell(row=r_i, column=col_label).value
                                    if lbl:
                                        label_to_row[str(lbl).strip()] = r_i
                                        existing_aliases[str(lbl).strip()] = str(ws.cell(row=r_i, column=col_alias).value or "").strip()

                                audit_name = "Symptoms_Audit"
                                if audit_name not in wb.sheetnames:
                                    ws_a = wb.create_sheet(audit_name)
                                    ws_a.append(["Timestamp", "Approver", "Action", "Side", "Label", "Alias", "Target", "Count", "Source", "Similarity", "Method"])    
                                else:
                                    ws_a = wb[audit_name]

                                added_new = 0
                                added_aliases = 0
                                now_iso = datetime.utcnow().isoformat()
                                approver = ""
                                src_tag = "starwalk_ui_v4"

                                # Enforce alias if NearDuplicate is True even if user picked "Add as new"
                                def _append_alias(target_label: str, alias_text: str, side: str, cnt: int, sim: float, method: str):
                                    # ensure target exists
                                    if target_label not in label_to_row:
                                        new_row = ws.max_row + 1
                                        ws.cell(row=new_row, column=col_label, value=target_label)
                                        ws.cell(row=new_row, column=col_type, value=side)
                                        ws.cell(row=new_row, column=col_alias, value="")
                                        label_to_row[target_label] = new_row
                                    row_i = label_to_row[target_label]
                                    current = existing_aliases.get(target_label, "")
                                    parts = [p.strip() for p in re.split(r"[|,]", current) if p.strip()]
                                    if alias_text not in parts and alias_text != target_label:
                                        parts.append(alias_text)
                                        new_val = " | ".join(parts)
                                        ws.cell(row=row_i, column=col_alias, value=new_val)
                                        existing_aliases[target_label] = new_val
                                    ws_a.append([now_iso, approver, "Add Alias", side, target_label, alias_text, target_label, cnt, src_tag, round(sim,3), method])

                                for _, rec in edited.iterrows():
                                    sym = str(rec.get("Label", "")).strip()
                                    side = str(rec.get("Side", "")).strip()
                                    action = str(rec.get("Action", "")).strip()
                                    target = str(rec.get("Target Label", "")).strip() or str(rec.get("Suggested Mapping", "")).strip()
                                    cnt = int(rec.get("Count", 0))
                                    near_dup = bool(rec.get("NearDuplicate", False))
                                    sim = float(rec.get("Similarity", 0.0))
                                    method = str(rec.get("Method", ""))
                                    if not sym:
                                        continue

                                    if near_dup:
                                        # Force aliasing even if user selected "Add as new"
                                        if not target:
                                            # as a fallback, pick the best current label by side
                                            target, _, _ = best_match(sym, side, DEL_CORPUS, DET_CORPUS, use_emb, str_thresh, emb_thresh)
                                        if target:
                                            _append_alias(target, sym, side, cnt, sim/100.0, method)
                                            added_aliases += 1
                                        continue

                                    # Non-near-dup: respect user action
                                    if action == "Add as new":
                                        if sym not in label_to_row:
                                            new_row = ws.max_row + 1
                                            ws.cell(row=new_row, column=col_label, value=sym)
                                            ws.cell(row=new_row, column=col_type, value=side)
                                            ws.cell(row=new_row, column=col_alias, value="")
                                            label_to_row[sym] = new_row
                                            ws_a.append([now_iso, approver, "Add Label", side, sym, "", "", cnt, src_tag, "", ""]) 
                                            added_new += 1
                                    else:
                                        if target:
                                            _append_alias(target, sym, side, cnt, sim/100.0, method)
                                            added_aliases += 1

                                out = io.BytesIO(); wb.save(out); out.seek(0)
                                st.download_button(
                                    "⬇️ Download Workbook (Symptoms + Aliases + Audit Updated)", out,
                                    file_name="Symptoms_Updated.xlsx",
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                                )
                                st.success(f"Added {added_new} new label(s) and {added_aliases} alias(es). Near‑duplicates are auto‑aliased.")

            # ---- Processed reviews log ----
            if processed_rows:
                st.subheader("🧾 Processed reviews (this run)")
                for rec in processed_rows:
                    head = f"Row {rec['Index']} — Dets added: {len(rec['Added_Detractors'])}, Dels added: {len(rec['Added_Delighters'])}"
                    with st.expander(head):
                        st.markdown(f"**Verbatim**: {rec['Verbatim']}")
                        st.markdown("**Detractors added:** " + (", ".join(rec["Added_Detractors"]) if rec["Added_Detractors"] else "—"))
                        st.markdown("**Delighters added:** " + (", ".join(rec["Added_Delighters"]) if rec["Added_Delighters"] else "—"))
                        st.markdown("**Unlisted detractors (candidates):** " + (", ".join(rec["Unlisted_Detractors"]) if rec["Unlisted_Detractors"] else "—"))
                        st.markdown("**Unlisted delighters (candidates):** " + (", ".join(rec["Unlisted_Delighters"]) if rec["Unlisted_Delighters"] else "—"))

# ------------------- Download Symptomized Workbook -------------------
st.subheader("📦 Download Symptomized Workbook")
try:
    file_base = os.path.splitext(getattr(uploaded_file, 'name', 'Reviews'))[0]
except Exception:
    file_base = 'Reviews'
export_bytes = generate_updated_workbook_bytes(uploaded_file, df)
st.download_button(
    "⬇️ Download symptomized workbook (XLSX)",
    data=export_bytes,
    file_name=f"{file_base}_Symptomized.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# ------------------- Diagnostics -------------------
with st.expander("Detection diagnostics (advanced)", expanded=False):
    det_cols = colmap["manual_detractors"] + colmap["ai_detractors"]
    del_cols = colmap["manual_delighters"] + colmap["ai_delighters"]

    def _filled_counts(df_in: pd.DataFrame, cols: List[str], label: str):
        if not cols:
            st.info(f"No {label} columns detected.")
            return
        sample = df_in[[*cols]].head(100).copy()
        counts = sample.applymap(is_filled).sum(axis=1)
        st.write(f"{label}: first 100 rows — filled cell count per row (higher means already symptomized)")
        st.dataframe(pd.DataFrame({"filled_count": counts}).join(sample), use_container_width=True)

    _filled_counts(work, det_cols, "Detractors")
    _filled_counts(work, del_cols, "Delighters")

st.divider()
st.caption("Near‑duplicate candidates are auto‑flagged using string + optional embedding similarity. Defaults: 0.88 string / 0.86 embedding thresholds.")


