# starwalk_ui_v4.py — Minimalist Dashboard + One‑Click Symptomize (Missing BOTH)
# Streamlit App — Exact Template Export (K–T dets, U–AD dels) • New Symptom Inbox • Tiles UI
# Requirements: streamlit>=1.28, pandas, openpyxl, openai

import streamlit as st
import pandas as pd
import numpy as np
import io, os, re, json, html
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
st.set_page_config(layout="wide", page_title="Star Walk Review Analyzer v4 — Minimal UI")
st.title("🌟 Star Walk Review Analyzer v4")
st.caption("Two clear metrics • One‑click 'Missing BOTH' symptomize • Tiles for fast browsing • Exact K–T / U–AD export")

# Lightweight tile styles
st.markdown(
    """
    <style>
      .chip{display:inline-block;padding:6px 10px;margin:3px 6px 3px 0;border-radius:999px;font-size:12.5px;font-weight:500;border:1px solid transparent}
      .chip-del{background:#E8F6EC;border-color:#8FD6A5;color:#166534}
      .chip-det{background:#FDECEC;border-color:#F5A5A5;color:#991B1B}
      .muted{color:#64748b}
      .card{padding:16px;border-radius:14px;border:1px solid rgba(0,0,0,.08);background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.04);margin-bottom:12px}
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

# --- Tile renderer ---

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
    Detractors K–T and Delighters U–AD (10 each). Header row is preserved; no header renames."""
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
if not DELIGHTERS and not DETRACTORS:
    st.warning("⚠️ No Symptoms found in 'Symptoms' tab.")
else:
    st.success(f"Loaded {len(DELIGHTERS)} Delighters, {len(DETRACTORS)} Detractors from Symptoms tab.")

# Build canonical maps
DEL_MAP, DET_MAP, ALIAS_TO_LABEL = build_canonical_maps(DELIGHTERS, DETRACTORS, ALIASES)

# LLM settings
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
api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
client = OpenAI(api_key=api_key) if (_HAS_OPENAI and api_key) else None
if client is None:
    st.sidebar.warning("OpenAI not configured — set OPENAI_API_KEY and install 'openai'.")

# ------------------- Minimal Metrics + One‑Click -------------------
colmap = detect_symptom_columns(df)
work = detect_missing(df, colmap)

total = len(work)
need_both = int(work["Needs_Symptomization"].sum())
left, right = st.columns(2)
with left:
    st.metric("Total review count", f"{total:,}")
with right:
    st.metric("Number of reviews that need symptomization", f"{need_both:,}")

# Target = missing BOTH
target = work[work["Needs_Symptomization"]]

process_missing_both = st.button(
    "🚀 Process reviews missing BOTH (detractors & delighters)",
    type="primary",
    disabled=(client is None or need_both == 0)
)

# ------------------- Run (Missing BOTH) -------------------
processed_rows: List[Dict] = st.session_state.get("processed_rows", [])
cand_del_map: Dict[str, List[int]] = st.session_state.get("cand_del_map", {})
cand_det_map: Dict[str, List[int]] = st.session_state.get("cand_det_map", {})

if process_missing_both:
    if client is None:
        st.error("OpenAI not configured — cannot symptomize.")
    else:
        max_per_side = 10
        rows_iter = target
        prog = st.progress(0.0)
        total_n = max(1, len(rows_iter))

        processed_rows = []
        cand_del_map, cand_det_map = {}, {}

        for k, (idx, row) in enumerate(rows_iter.iterrows(), start=1):
            vb = row.get("Verbatim", "")
            try:
                dels, dets, unl_dels, unl_dets = _openai_labeler(
                    vb, client, selected_model, temperature,
                    DELIGHTERS, DETRACTORS, ALIASES,
                    DEL_MAP, DET_MAP, ALIAS_TO_LABEL
                )
            except Exception:
                dels, dets, unl_dels, unl_dets = [], [], [], []

            # Ensure 10/10 max written to AI columns
            ensure_ai_columns(df)
            if dets:
                for j, lab in enumerate(dets[:max_per_side]):
                    col = f"AI Symptom Detractor {j+1}"
                    df.loc[idx, col] = lab
            if dels:
                for j, lab in enumerate(dels[:max_per_side]):
                    col = f"AI Symptom Delighter {j+1}"
                    df.loc[idx, col] = lab

            for u in unl_dels:
                cand_del_map.setdefault(u, []).append(idx)
            for u in unl_dets:
                cand_det_map.setdefault(u, []).append(idx)

            processed_rows.append({
                "Index": int(idx),
                "Verbatim": str(vb),  # full verbatim (no truncation)
                "Added_Delighters": dels[:max_per_side],
                "Added_Detractors": dets[:max_per_side],
                "Unlisted_Delighters": unl_dels,
                "Unlisted_Detractors": unl_dets,
            })

            prog.progress(k/total_n)

        st.session_state["processed_rows"] = processed_rows
        st.session_state["cand_del_map"] = cand_del_map
        st.session_state["cand_det_map"] = cand_det_map
        st.success(f"Processed {len(processed_rows)} review(s) missing both.")

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

# ------------------- Browse all symptoms (chips) -------------------
with st.expander("🧩 Browse all symptoms (chips)", expanded=False):
    col_det_all = colmap.get("manual_detractors", []) + colmap.get("ai_detractors", [])
    col_del_all = colmap.get("manual_delighters", []) + colmap.get("ai_delighters", [])

    def _collect_unique(cols: List[str]) -> List[str]:
        items: List[str] = []
        for c in cols:
            if c in df.columns:
                vals = df[c].dropna().astype(str).map(str.strip)
                items.extend([v for v in vals if is_filled(v)])
        return sorted(list(dict.fromkeys(items)))

    uniq_det = _collect_unique(col_det_all)
    uniq_del = _collect_unique(col_del_all)

    st.markdown("**Detractors**", unsafe_allow_html=True)
    st.markdown(render_tiles(uniq_det, side="det"), unsafe_allow_html=True)
    st.markdown("**Delighters**", unsafe_allow_html=True)
    st.markdown(render_tiles(uniq_del, side="del"), unsafe_allow_html=True)

# ------------------- New Symptom Inbox (Approval + References) -------------------
st.subheader("🟡 New Symptom Inbox — Review & Approve")
if not cand_del_map and not cand_det_map:
    st.info("No new candidate symptoms from this session yet. Click the button above to process reviews.")
else:
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

    tbl_del = _mk_table(cand_del_map, "Delighter")
    tbl_det = _mk_table(cand_det_map, "Detractor")

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

    if st.button("✅ Add selected to Symptoms & Download updated workbook"):
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

# ------------------- Processed reviews log -------------------
if processed_rows:
    st.subheader("🧾 Processed reviews (this session)")
    for rec in processed_rows:
        head = f"Row {rec['Index']} — Dets added: {len(rec['Added_Detractors'])}, Dels added: {len(rec['Added_Delighters'])}"
        with st.expander(head):
            st.markdown("<div class='card'>" \
                        + "<div class='muted'>Verbatim</div>" \
                        + f"<div>{html.escape(rec['Verbatim'])}</div>" \
                        + "<div class='muted' style='margin-top:10px'>Detractors</div>" \
                        + f"<div>{render_tiles(rec.get('Added_Detractors', []), 'det')}</div>" \
                        + "<div class='muted' style='margin-top:6px'>Delighters</div>" \
                        + f"<div>{render_tiles(rec.get('Added_Delighters', []), 'del')}</div>" \
                        + "<div class='muted' style='margin-top:6px'>Unlisted candidates</div>" \
                        + f"<div>{render_tiles(rec.get('Unlisted_Detractors', []), 'det')}{render_tiles(rec.get('Unlisted_Delighters', []), 'del')}</div>" \
                        + "</div>",
                        unsafe_allow_html=True)

# ------------------- Footer -------------------
st.divider()
st.caption("Exports write exactly to K–T (detractors) and U–AD (delighters). No header renames. Use the button above to process only rows missing BOTH sides.")
