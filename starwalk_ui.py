# starwalk_ui_v4.py — BEST-IN-CLASS UI UPDATE
# Streamlit App — Manual Symptomize (Run N/ALL) • Template‑Exact Export (K–T dets, U–AD dels)
# New Symptom Inbox (Approval + References) • Browse & Stats • Robust dtype & layout fixes
# Visual: green/red chips for labels; full verbatim (no truncation)
# Requirements: streamlit>=1.28, pandas, openpyxl, openai

import streamlit as st
import pandas as pd
import numpy as np
import io, os, re, json, difflib, html
from typing import List, Dict, Tuple
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
st.set_page_config(layout="wide", page_title="Star Walk Review Analyzer v4 — Premium UI")
st.title("🌟 Star Walk Review Analyzer v4")
st.caption("Manual Symptomize • Approval Inbox • Browse & Stats • Export to Template (K–T dets, U–AD dels)")

# Best-in-class visuals: cards + green/red chips
st.markdown(
    """
    <style>
    :root { --chip-radius:999px; --gray:#64748b; }
    .card{padding:16px;border-radius:14px;border:1px solid rgba(0,0,0,.08);background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.04);margin-bottom:12px}
    .chips{margin-top:6px;margin-bottom:2px}
    .chip{display:inline-block;padding:6px 10px;margin:2px 6px 2px 0;border-radius:999px;font-size:12.5px;font-weight:500;border:1px solid transparent}
    .chip-del{background:#E8F6EC;border-color:#8FD6A5;color:#166534}
    .chip-det{background:#FDECEC;border-color:#F5A5A5;color:#991B1B}
    .muted{color:#64748b}
    .hrow{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
    .pill{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;border:1px solid rgba(0,0,0,.06);background:#fafafa}
    </style>
    """,
    unsafe_allow_html=True,
)

# ------------------- Utilities -------------------
NON_VALUES = {"<NA>", "NA", "N/A", "NONE", "-", "", "NAN", "NULL"}

def clean_text(x: object) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    return str(x).strip()

def is_filled(val: object) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    s = str(val).strip()
    return (s != "") and (s.upper() not in NON_VALUES)

@st.cache_data(show_spinner=False)
def get_symptom_whitelists(file_bytes: bytes) -> Tuple[List[str], List[str], Dict[str, List[str]]]:
    """Load delighters/detractors/aliases from 'Symptoms' sheet. Cached by file bytes."""
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
        # Wide format fallback
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

# ---------- Column detection & missing flags ----------

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

# ------------------- Fixed template mapping -------------------
DET_LETTERS = ["K","L","M","N","O","P","Q","R","S","T"]  # 10
DEL_LETTERS = ["U","V","W","X","Y","Z","AA","AB","AC","AD"]  # 10
DET_INDEXES = [column_index_from_string(c) for c in DET_LETTERS]
DEL_INDEXES = [column_index_from_string(c) for c in DEL_LETTERS]
AI_DET_HEADERS = [f"AI Symptom Detractor {i}" for i in range(1, 11)]
AI_DEL_HEADERS = [f"AI Symptom Delighter {i}" for i in range(1, 11)]

def ensure_ai_columns(df_in: pd.DataFrame) -> pd.DataFrame:
    for h in AI_DET_HEADERS + AI_DEL_HEADERS:
        if h not in df_in.columns:
            df_in[h] = None
    return df_in

# ---------- Canonicalization helpers ----------

def _canon(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def build_canonical_maps(delighters: List[str], detractors: List[str], alias_map: Dict[str, List[str]]):
    del_map = {_canon(x): x for x in delighters}
    det_map = {_canon(x): x for x in detractors}
    alias_to_label: Dict[str, str] = {}
    for label, aliases in (alias_map or {}).items():
        for a in aliases:
            alias_to_label[_canon(a)] = label
    return del_map, det_map, alias_to_label

# --- Tile renderer (green for delighters, red for detractors) ---

def render_tiles(items: List[str], side: str) -> str:
    try:
        if not items:
            return "<span class='muted'>—</span>"
        cls = "chip chip-del" if side == "del" else "chip chip-det"
        return "".join(f"<span class='{cls}'>" + html.escape(str(x)) + "</span>" for x in items)
    except Exception:
        return "<span class='muted'>—</span>"

# ---------- LLM labeler ----------

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

def generate_updated_workbook_bytes(original_file, updated_df: pd.DataFrame) -> bytes:
    """Return bytes for a workbook matching the original, with values written to
    Detractors K–T and Delighters U–AD (10 each). Header row is preserved."""
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
        # Detractors K–T
        for j, col_idx in enumerate(DET_INDEXES, start=1):
            val = r.get(f"AI Symptom Detractor {j}")
            cell_value = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cell_value)
            if cell_value is not None:
                cell.fill = fill_red
        # Delighters U–AD
        for j, col_idx in enumerate(DEL_INDEXES, start=1):
            val = r.get(f"AI Symptom Delighter {j}")
            cell_value = None if (pd.isna(val) or str(val).strip() == "") else val
            cell = ws.cell(row=i, column=col_idx, value=cell_value)
            if cell_value is not None:
                cell.fill = fill_green

    # Optional widths
    for c in DET_INDEXES + DEL_INDEXES:
        try:
            ws.column_dimensions[get_column_letter(c)].width = 28
        except Exception:
            pass

    out = io.BytesIO(); wb.save(out)
    return out.getvalue()

# --- Add new symptoms to Symptoms sheet ---

def add_new_symptoms_to_workbook(original_file, selections: List[Tuple[str, str]]) -> bytes:
    """Add (label, side) pairs to the 'Symptoms' sheet and return workbook bytes.
    selections: list of (label, side) where side is 'Delighter' or 'Detractor'.
    Creates the sheet/headers if missing. Skips duplicates (by label).
    """
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

    if len(headers) < col_label or not headers[col_label-1]:
        ws.cell(row=1, column=col_label, value="Symptom")
    if len(headers) < col_type or not headers[col_type-1]:
        ws.cell(row=1, column=col_type, value="Type")

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
        existing.add(lab)

    out = io.BytesIO(); wb.save(out); out.seek(0)
    return out.getvalue()

# --- Editor schema helper (prevents Streamlit dtype errors in st.data_editor) ---

def _ensure_editor_schema(df_edit: pd.DataFrame) -> pd.DataFrame:
    try:
        if df_edit is None or not isinstance(df_edit, pd.DataFrame) or df_edit.empty:
            return pd.DataFrame({
                "Add": pd.Series(dtype="bool"),
                "Label": pd.Series(dtype="string"),
                "Side": pd.Series(dtype="string"),
                "Count": pd.Series(dtype="int64"),
                "Examples": pd.Series(dtype="string"),
            })
        if "Add" in df_edit.columns:
            df_edit["Add"] = df_edit["Add"].fillna(False).astype("bool")
        for col in ["Label", "Side", "Examples"]:
            if col in df_edit.columns:
                df_edit[col] = df_edit[col].astype("string")
        if "Count" in df_edit.columns:
            df_edit["Count"] = pd.to_numeric(df_edit["Count"], errors="coerce").fillna(0).astype("int64")
        return df_edit
    except Exception:
        return pd.DataFrame({
            "Add": pd.Series(dtype="bool"),
            "Label": pd.Series(dtype="string"),
            "Side": pd.Series(dtype="string"),
            "Count": pd.Series(dtype="int64"),
            "Examples": pd.Series(dtype="string"),
        })

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

# Normalize column names (trim whitespace)
df.columns = [str(c).strip() for c in df.columns]
df["Verbatim"] = df["Verbatim"].astype(str).map(clean_text)

# Load Symptoms from sheet (cached)
DELIGHTERS, DETRACTORS, ALIASES = get_symptom_whitelists(uploaded_bytes)
if not DELIGHTERS and not DETRACTORS:
    st.warning("⚠️ No Symptoms found in 'Symptoms' tab.")
else:
    st.success(f"Loaded {len(DELIGHTERS)} Delighters, {len(DETRACTORS)} Detractors from Symptoms tab.")

# Sidebar LLM settings + client (defined once)
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

# Build canonical maps for robust matching
DEL_MAP, DET_MAP, ALIAS_TO_LABEL = build_canonical_maps(DELIGHTERS, DETRACTORS, ALIASES)

# ------------------- Detection & Preview -------------------
colmap = detect_symptom_columns(df)
work = detect_missing(df, colmap)

# Summary row
st.markdown(
    f"<div class='hrow'><span class='pill'>Dataset: <b>{len(work):,}</b></span>"
    f"<span class='pill'>Need Delighters: <b>{int(work['Needs_Delighters'].sum()):,}</b></span>"
    f"<span class='pill'>Need Detractors: <b>{int(work['Needs_Detractors'].sum()):,}</b></span>"
    f"<span class='pill'>Missing Both: <b>{int(work['Needs_Symptomization'].sum()):,}</b></span></div>",
    unsafe_allow_html=True,
)

# Scope filter
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
with st.expander("Preview rows that need symptomization", expanded=False):
    preview_cols = ["Verbatim", "Has_Delighters", "Has_Detractors", "Needs_Delighters", "Needs_Detractors"]
    extras = [c for c in ["Star Rating", "Review Date", "Source"] if c in target.columns]
    st.dataframe(target[preview_cols + extras].head(200), use_container_width=True)

# ------------------- Symptomize Center (main) -------------------
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

            processed_rows: List[Dict] = []
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

                wrote_dets: List[str] = []
                wrote_dels: List[str] = []

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
                    "Verbatim": str(vb),  # full text, no truncation
                    "Added_Delighters": wrote_dels,
                    "Added_Detractors": wrote_dets,
                    "Unlisted_Delighters": unl_dels,
                    "Unlisted_Detractors": unl_dets,
                })

                prog.progress(k/total_n)

            st.session_state["processed_rows"] = processed_rows
            st.session_state["cand_del_map"] = cand_del
            st.session_state["cand_det_map"] = cand_det

            st.success(f"Symptomized {len(processed_rows)} review(s) in the selected scope.")

            # Processed log
            with st.expander("🧾 Processed reviews (this run)", expanded=False):
                for rec in processed_rows:
                    head = f"Row {rec['Index']} — Dets added: {len(rec['Added_Detractors'])}, Dels added: {len(rec['Added_Delighters'])}"
                    with st.expander(head):
                        html_block = (
                            "<div class='card'>"
                            "<div class='muted'>Verbatim</div>"
                            f"<div>{html.escape(rec['Verbatim'])}</div>"
                            "<div class='muted' style='margin-top:10px'>Detractors</div>"
                            f"<div class='chips'>{render_tiles(rec.get('Added_Detractors', []), 'det')}</div>"
                            "<div class='muted' style='margin-top:6px'>Delighters</div>"
                            f"<div class='chips'>{render_tiles(rec.get('Added_Delighters', []), 'del')}</div>"
                            "<div class='muted' style='margin-top:6px'>Unlisted candidates</div>"
                            f"<div class='chips'>{render_tiles(rec.get('Unlisted_Detractors', []), 'det')}{render_tiles(rec.get('Unlisted_Delighters', []), 'del')}</div>"
                            "</div>"
                        )
                        st.markdown(html_block, unsafe_allow_html=True)

# ------------------- New Symptom Inbox -------------------
st.subheader("🟡 New Symptom Inbox — Review & Approve")
cand_del = st.session_state.get("cand_del_map", {})
cand_det = st.session_state.get("cand_det_map", {})

if not cand_del and not cand_det:
    st.info("No new candidate symptoms from this session yet. Run Symptomize first.")
else:
    st.success(f"Found {len(cand_del) + len(cand_det)} candidate(s). Review & approve below.")

    def _mk_table(cmap: Dict[str, List[int]], side_label: str) -> pd.DataFrame:
        rows_tbl = []
        for sym, refs in sorted(cmap.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            examples = []
            for ridx in refs[:2]:
                try:
                    ex = df.loc[ridx, "Verbatim"]
                    examples.append((str(ex) or ""))  # full text
                except Exception:
                    pass
            rows_tbl.append({
                "Add": False,
                "Label": sym,
                "Side": side_label,
                "Count": len(refs),
                "Examples": " | ".join(["— "+e for e in examples])
            })
        return pd.DataFrame(rows_tbl) if rows_tbl else pd.DataFrame({"Add": [], "Label": [], "Side": [], "Count": [], "Examples": []})

    tbl_del = _ensure_editor_schema(_mk_table(cand_del, "Delighter"))
    tbl_det = _ensure_editor_schema(_mk_table(cand_det, "Detractor"))

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
                "Side": st.column_config.TextColumn(disabled=True),
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
                "Side": st.column_config.TextColumn(disabled=True),
                "Count": st.column_config.NumberColumn(format="%d"),
                "Examples": st.column_config.TextColumn(width="large"),
            },
            key="cand_det_editor",
        )

    # References per candidate
    st.markdown("**References per candidate**")
    for sym, refs in sorted(cand_det.items(), key=lambda kv: -len(kv[1])):
        with st.expander(f"Detractor — {sym} • {len(refs)} reference(s)"):
            ref_rows = []
            for ridx in refs:
                row_dict = {"Index": int(ridx)}
                if "Star Rating" in df.columns:
                    row_dict["Star Rating"] = df.loc[ridx, "Star Rating"]
                row_dict["Verbatim"] = str(df.loc[ridx, "Verbatim"])  # full text
                ref_rows.append(row_dict)
            st.dataframe(pd.DataFrame(ref_rows), use_container_width=True, hide_index=True)
    for sym, refs in sorted(cand_del.items(), key=lambda kv: -len(kv[1])):
        with st.expander(f"Delighter — {sym} • {len(refs)} reference(s)"):
            ref_rows = []
            for ridx in refs:
                row_dict = {"Index": int(ridx)}
                if "Star Rating" in df.columns:
                    row_dict["Star Rating"] = df.loc[ridx, "Star Rating"]
                row_dict["Verbatim"] = str(df.loc[ridx, "Verbatim"])  # full text
                ref_rows.append(row_dict)
            st.dataframe(pd.DataFrame(ref_rows), use_container_width=True, hide_index=True)

    st.divider()
    if st.button("✅ Add selected to Symptoms & Download updated workbook", type="primary"):
        selections: List[Tuple[str, str]] = []
        try:
            if isinstance(editor_del, pd.DataFrame) and not editor_del.empty:
                for _, r_ in editor_del.iterrows():
                    if bool(r_.get("Add", False)) and str(r_.get("Label", "")).strip():
                        selections.append((str(r_["Label"]).strip(), "Delighter"))
        except Exception:
            pass
        try:
            if isinstance(editor_det, pd.DataFrame) and not editor_det.empty:
                for _, r_ in editor_det.iterrows():
                    if bool(r_.get("Add", False)) and str(r_.get("Label", "")).strip():
                        selections.append((str(r_["Label"]).strip(), "Detractor"))
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
export_bytes = generate_updated_workbook_bytes(uploaded_file, df)
st.download_button(
    "⬇️ Download symptomized workbook (XLSX)",
    data=export_bytes,
    file_name=f"{file_base}_Symptomized.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# ------------------- Browse & Stats -------------------
st.subheader("🔎 Browse & Stats")
view_side = st.selectbox("View", ["Detractors", "Delighters"], index=0)

col_det_all = colmap.get("manual_detractors", []) + colmap.get("ai_detractors", [])
col_del_all = colmap.get("manual_delighters", []) + colmap.get("ai_delighters", [])

def _label_counts(df_in: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    vals: List[str] = []
    for c in cols:
        if c in df_in.columns:
            series = df_in[c].dropna().astype(str).map(str.strip)
            vals.extend([v for v in series if is_filled(v)])
    if not vals:
        return pd.DataFrame({"Label": [], "Count": []})
    vc = pd.Series(vals).value_counts().reset_index()
    vc.columns = ["Label", "Count"]
    return vc

if view_side == "Detractors":
    counts_df = _label_counts(df, col_det_all)
    whitelist = pd.DataFrame({"Label": DETRACTORS}) if DETRACTORS else pd.DataFrame({"Label": []})
else:
    counts_df = _label_counts(df, col_del_all)
    whitelist = pd.DataFrame({"Label": DELIGHTERS}) if DELIGHTERS else pd.DataFrame({"Label": []})

m1, m2, m3 = st.columns(3)
with m1:
    st.metric("Total reviews", f"{len(df):,}")
with m2:
    st.metric("In-scope now", f"{len(target):,}")
with m3:
    st.metric("Unique labels (found)", f"{counts_df['Label'].nunique() if not counts_df.empty else 0}")

c_left, c_right = st.columns(2)
with c_left:
    st.markdown("**Top labels in data**")
    st.dataframe(counts_df.head(50), use_container_width=True, hide_index=True)
with c_right:
    st.markdown("**Whitelist**")
    st.dataframe(whitelist, use_container_width=True, hide_index=True)

# ------------------- Footer -------------------
st.divider()
st.caption("Tip: Choose a scope, preview, then Run N/ALL. Export writes exactly to K–T (dets) and U–AD (dels). No extra headers added.")
