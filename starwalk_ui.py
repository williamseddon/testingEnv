# starwalk_ui.py
# Streamlit 1.38+

from __future__ import annotations

import os, re, json, textwrap, warnings, unicodedata, difflib, io
from typing import List, Tuple, Dict, Iterable, Optional

import numpy as np
import pandas as pd
import streamlit as st
from email.message import EmailMessage
from streamlit.components.v1 import html as st_html

warnings.filterwarnings(
    "ignore",
    message="Data Validation extension is not supported and will be removed",
    module="openpyxl",
)

# Optional deps
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

# =========================
# Basic config + light mode
# =========================
st.set_page_config(layout="wide", page_title="Star Walk Analysis — Symptomize (beta)")

# Force light mode regardless of system preference
st_html("""
<script>
(function () {
  function setLight() {
    try {
      document.documentElement.setAttribute('data-theme','light');
      document.body && document.body.setAttribute('data-theme','light');
      window.localStorage.setItem('theme','light');
    } catch (e) {}
  }
  setLight();
  new MutationObserver(setLight).observe(
    document.documentElement,
    { attributes: true, attributeFilter: ['data-theme'] }
  );
})();
</script>
""", height=0)

# =========================
# Minimal global styles
# =========================
st.markdown("""
<style>
:root{
  --text:#0f172a; --muted:#475569; --muted-2:#64748b;
  --border-strong:#90a7c1; --border:#cbd5e1; --border-soft:#e2e8f0;
  --bg-app:#f6f8fc; --bg-card:#ffffff; --bg-tile:#f8fafc;
  --ok:#16a34a; --bad:#dc2626;
}
html, body, .stApp { background:var(--bg-app); color:var(--text);
  font-family: "Helvetica Neue", Helvetica, Arial, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
.block-container { padding-top:.75rem; padding-bottom:1rem; }

/* Cards */
.card{ background:var(--bg-card); border-radius:14px; padding:16px;
  box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); }

/* Badges */
.badge{ display:inline-block; padding:4px 8px; border-radius:999px; font-weight:600; font-size:.85rem; }
.badge.ok{ background:#e9fbf3; color:#065f46; border:1px solid #7ed9b3; }
.badge.warn{ background:#fff7ed; color:#7c2d12; border:1px solid #fdba74; }

/* Table tweaks */
[data-testid="stDataFrame"] { margin-top:8px; }
</style>
""", unsafe_allow_html=True)

# =========================
# Helpers & utilities
# =========================

NO_TEMP_MODELS = {"gpt-5", "gpt-5-chat-latest"}
def model_supports_temperature(model_id: str) -> bool:
    return model_id not in NO_TEMP_MODELS and not model_id.startswith("gpt-5")

SYMPTOM_COLS = [f"Symptom {i}" for i in range(1,21)]

def clean_text(x, keep_na=False) -> str:
    if pd.isna(x): return pd.NA if keep_na else ""
    s = str(x)
    # quick encoding cleanup
    for bad, good in {"â€™":"'", "â€˜":"‘", "â€œ":"“", "â€\x9d":"”", "â€“":"–", "â€”":"—", "Â":""}.items():
        s = s.replace(bad, good)
    s = s.strip()
    if s.upper() in {"<NA>","NA","N/A","NULL","NONE"}:
        return pd.NA if keep_na else ""
    return s

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return s

def uniq(seq: Iterable[str]) -> List[str]:
    out, seen = [], set()
    for x in seq:
        x = (x or "").strip()
        if not x: continue
        if x not in seen:
            seen.add(x); out.append(x)
    return out

def chunked(lst: List, n: int) -> Iterable[List]:
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def detect_symptom_sheet(xls: pd.ExcelFile) -> Optional[str]:
    for name in xls.sheet_names:
        if "symptom" in name.lower():
            return name
    return None

def pick_text_column(df: pd.DataFrame) -> Optional[str]:
    for c in ["Verbatim","Review","Review Text","Text","Body"]:
        if c in df.columns:
            return c
    return None

def symptoms_from_sheet(sym_df: pd.DataFrame) -> Tuple[List[str], List[str]]:
    # Find likely columns
    cols = {c.lower(): c for c in sym_df.columns}
    looks_del = [c for c in sym_df.columns if any(k in c.lower() for k in ["delight","positive","pro","like","good"])]
    looks_det = [c for c in sym_df.columns if any(k in c.lower() for k in ["detract","negative","con","dislike","bad"])]
    if not looks_del and not looks_det:
        # fallback: two-column sheet
        if len(sym_df.columns) >= 2:
            looks_del = [sym_df.columns[0]]
            looks_det = [sym_df.columns[1]]

    def col_to_list(cnames: List[str]) -> List[str]:
        vals = []
        for c in cnames:
            s = sym_df[c].astype("string").dropna().map(str).map(str.strip)
            vals.extend([v for v in s.tolist() if v])
        return uniq(vals)

    delighters = col_to_list(looks_del) if looks_del else []
    detractors = col_to_list(looks_det) if looks_det else []
    return delighters, detractors

def make_canonicalizer(choices: List[str]):
    base = {norm(x): x for x in choices}
    keys = list(base.keys())
    def canon(s: str) -> Tuple[str, bool]:
        n = norm(s)
        if not n: return s, False
        if n in base: return base[n], True
        # fuzzy
        best = difflib.get_close_matches(n, keys, n=1, cutoff=0.86)
        if best:
            return base[best[0]], True
        return s, False
    return canon

def all_symptoms_empty(row: pd.Series) -> bool:
    for c in SYMPTOM_COLS:
        if c in row.index:
            v = str(row[c]).strip()
            if v and v.upper() not in {"<NA>","NA","N/A","NONE","-"}:
                return False
    return True

# =========================
# Upload step
# =========================
st.sidebar.header("📁 Upload Star Walk File")
uploaded = st.sidebar.file_uploader("Choose Excel file (.xlsx)", type=["xlsx"])

if not uploaded:
    with st.sidebar:
        st.info("Upload an Excel (XLSX) with the main data and a Symptoms sheet.")
    st.stop()

# Read workbook once
try:
    xls = pd.ExcelFile(uploaded)
except Exception as e:
    st.error(f"Could not read Excel: {e}")
    st.stop()

# Load main sheet (named or fallback to first)
main_sheet_name = "Star Walk scrubbed verbatims" if "Star Walk scrubbed verbatims" in xls.sheet_names else xls.sheet_names[0]
df = pd.read_excel(xls, sheet_name=main_sheet_name)

# Pre-clean
for c in SYMPTOM_COLS:
    if c in df.columns:
        df[c] = df[c].apply(lambda v: clean_text(v, keep_na=True)).astype("string")
text_col = pick_text_column(df)
if not text_col:
    st.error("Could not find a review text column (looked for Verbatim / Review / Review Text / Text).")
    st.stop()
df[text_col] = df[text_col].astype("string").map(clean_text)

# Symptoms sheet
sym_sheet = detect_symptom_sheet(xls)
if sym_sheet:
    sym_df = pd.read_excel(xls, sheet_name=sym_sheet)
    base_delighters, base_detractors = symptoms_from_sheet(sym_df)
else:
    base_delighters, base_detractors = [], []

if not base_delighters and not base_detractors:
    st.warning("No delighters/detractors found on a Symptoms sheet. You can still run, but new items won't be snapped to a list.")

# Find rows without symptoms
empty_mask = df.apply(all_symptoms_empty, axis=1)
todo_ids = df.index[empty_mask].tolist()
total_reviews = len(df)
todo_count = len(todo_ids)

st.markdown(f"""
<div class="card">
  <h3>⭐ Symptomize (beta)</h3>
  <div style="display:flex;gap:10px;flex-wrap:wrap;">
    <span class="badge ok">{total_reviews:,} total reviews</span>
    <span class="badge warn">{todo_count:,} reviews without symptoms</span>
  </div>
  <p style="margin:.5rem 0 0 0;">Use OpenAI to propose up to 10 <b>detractors</b> (Symptom 1–10) and 10 <b>delighters</b> (Symptom 11–20) for the reviews that currently have empty Symptom 1–20 cells.</p>
</div>
""", unsafe_allow_html=True)

# =========================
# IQR-based default for max chars
# =========================
lengths = df[text_col].fillna("").map(len).to_numpy()
q1 = float(np.percentile(lengths, 25)) if len(lengths) else 400.0
median = float(np.percentile(lengths, 50)) if len(lengths) else 800.0
q3 = float(np.percentile(lengths, 75)) if len(lengths) else 1200.0
iqr = q3 - q1
suggest_max_chars = int(max(300, min(2500, q3)))  # Q3 is a good default cap

with st.expander("📏 Review length stats (for 'Max chars per review' tuning)", expanded=False):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Q1 (25%)", f"{int(q1):,} chars")
    c2.metric("Median", f"{int(median):,} chars")
    c3.metric("Q3 (75%)", f"{int(q3):,} chars")
    c4.metric("IQR", f"{int(iqr):,} chars")
    st.caption("Tip: Using Q3 as the default keeps most content while controlling token costs.")

# =========================
# Controls
# =========================
api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
MODEL_CHOICES = ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-5"]

colA, colB, colC, colD = st.columns([1.6, 1, 1, 1.2])
model = colA.selectbox("LLM Model", options=MODEL_CHOICES, index=0)
batch_size = colB.slider("Batch size", 5, 50, 20, 5, help="# reviews per API call")
fill_only_empties = colC.toggle("Fill only empties", value=True, help="Skip rows that already have any Symptom 1–20 filled.")
max_chars = colD.number_input("Max chars per review", min_value=200, max_value=4000, value=int(suggest_max_chars), step=50)

temp_supported = model_supports_temperature(model)
temperature = st.slider(
    "Creativity (temperature)", 0.0, 1.0, 0.2, 0.1,
    disabled=not temp_supported,
    help="Disabled for GPT-5 which requires default temperature."
)

# =========================
# Estimator (improved)
# =========================
def build_prompt(delighters: List[str], detractors: List[str]) -> str:
    dlist = "\n".join([f"- {x}" for x in delighters]) if delighters else "(none)"
    tlist = "\n".join([f"- {x}" for x in detractors]) if detractors else "(none)"
    return textwrap.dedent(f"""
    You are Shark Glossi Review Analyzer for Star Walk.
    For each review, select up to 10 detractors and up to 10 delighters from the lists below.
    If you detect a strong, new item that's not listed, include it under "new_delighters" or "new_detractors".
    Return strict JSON with shape:
      {{
        "<row_id>": {{
          "detractors": ["...", ...], 
          "delighters": ["...", ...],
          "new_detractors": ["...", ...],
          "new_delighters": ["...", ...]
        }},
        ...
      }}

    DELIGHTERS (pick ≤10):
    {dlist}

    DETRACTORS (pick ≤10):
    {tlist}

    Rules:
    - Keep ≤10 per group (fewer is fine).
    - Use short, canonical phrasings from the lists when possible.
    - If none apply, use empty array [] for that group.
    """)

# Throughput assumptions (rough; tuned to be conservative)
MODEL_TPS = {   # tokens/second
    "gpt-4o-mini": 5000.0,
    "gpt-4o":      2000.0,
    "gpt-4.1":     1200.0,
    "gpt-5":       1500.0,
}
MODEL_COST_PER_1K = {  # USD per 1K tokens (rough; adjust to your contract)
    "gpt-4o-mini": 0.15,
    "gpt-4o":      5.00,
    "gpt-4.1":     15.00,
    "gpt-5":       30.00,
}

def approx_tokens_from_chars(chars: int) -> int:
    # 1 token ≈ 4 chars (English), clamp >=1
    return max(1, int(chars / 4))

def estimate_for(ids: List[int], delighters: List[str], detractors: List[str]) -> Tuple[int, float, float]:
    """Return (tokens_total, cost_usd, seconds) using actual prompt size + per-batch texts."""
    if not ids: return (0, 0.0, 0.0)
    prompt = build_prompt(delighters, detractors)
    prompt_tokens = approx_tokens_from_chars(len(prompt))
    tps = MODEL_TPS.get(model, 1500.0)
    cost_per_1k = MODEL_COST_PER_1K.get(model, 0.15)
    total_tokens = 0
    n_calls = 0
    base_latency = 0.8  # s per request (network + overhead)
    json_overhead = 180  # tokens of structure overhead per call (conservative)

    for batch in chunked(ids, batch_size):
        # Sum batch text
        batch_chars = 0
        for rid in batch:
            s = str(df.loc[rid, text_col])[:int(max_chars)]
            batch_chars += len(s)
        batch_tokens = approx_tokens_from_chars(batch_chars) + prompt_tokens + json_overhead
        total_tokens += batch_tokens
        n_calls += 1

    est_seconds = total_tokens / max(1.0, tps) + n_calls * base_latency
    est_cost = (total_tokens / 1000.0) * cost_per_1k
    return int(total_tokens), float(est_cost), float(est_seconds)

if todo_ids:
    toks, cost, secs = estimate_for(todo_ids, base_delighters, base_detractors)
    st.caption(f"Estimate for {len(todo_ids):,} review(s): ~{toks:,} tokens • ~${cost:,.2f} • ~{secs:,.1f}s  (model={model}, batch={batch_size})")

# =========================
# LLM call
# =========================
def validate_llm_payload(raw: dict) -> Dict[int, dict]:
    out: Dict[int, dict] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        try:
            rid = int(k)
        except Exception:
            continue
        if not isinstance(v, dict): continue
        detr = v.get("detractors", []) or []
        deli = v.get("delighters", []) or []
        ndt  = v.get("new_detractors", []) or []
        ndl  = v.get("new_delighters", []) or []
        # keep ≤ 10 each
        detr = uniq(detr)[:10]
        deli = uniq(deli)[:10]
        ndt  = uniq(ndt)
        ndl  = uniq(ndl)
        out[rid] = {"detractors": detr, "delighters": deli,
                    "new_detractors": ndt, "new_delighters": ndl}
    return out

def call_llm(batch_rows: List[Tuple[int,str]], dls: List[str], dts: List[str]) -> Dict[int, dict]:
    if not _HAS_OPENAI:
        st.error("openai package not installed.")
        return {}
    if not api_key:
        st.error("Missing OPENAI_API_KEY.")
        return {}
    client = OpenAI(api_key=api_key)

    items = [{"row_id": rid, "text": (txt or "")[:int(max_chars)]} for rid, txt in batch_rows]
    messages = [
        {"role":"system","content": build_prompt(dls, dts)},
        {"role":"user","content": "REVIEWS:\n" + json.dumps({"items": items}, ensure_ascii=False)}
    ]

    params = {
        "model": model,
        "messages": messages,
        "response_format": {"type":"json_object"},
    }
    if model_supports_temperature(model):
        params["temperature"] = float(temperature)

    try:
        resp = client.chat.completions.create(**params)
        content = resp.choices[0].message.content
        raw = json.loads(content)
        return validate_llm_payload(raw)
    except Exception as e:
        st.error(f"LLM error: {e}")
        return {}

# Canonicalizers (snap to list names when close)
canon_dl = make_canonicalizer(base_delighters)
canon_dt = make_canonicalizer(base_detractors)

def snap_to_lists(rec: dict) -> dict:
    cds, cdt = [], []
    for x in rec.get("delighters", []):
        y, _ = canon_dl(x)
        cds.append(y)
    for x in rec.get("detractors", []):
        y, _ = canon_dt(x)
        cdt.append(y)
    rec["delighters"] = uniq(cds)[:10]
    rec["detractors"] = uniq(cdt)[:10]
    return rec

# =========================
# Run / Review / Apply
# =========================
if "sympto_suggestions" not in st.session_state:
    st.session_state["sympto_suggestions"] = {}  # {row_id: {...}}
if "applied_backup" not in st.session_state:
    st.session_state["applied_backup"] = None    # holds df backup for undo

run_cols = st.columns([1,1,2,1,1])
symptomize_click = run_cols[0].button(f"💡 Symptomize {todo_count} review(s) now", disabled=(todo_count==0 or not _HAS_OPENAI or not api_key))
clear_click = run_cols[1].button("Clear suggestions")
peek_len = run_cols[3].slider("Preview length", 120, 1200, 420, 20)
show_only_new = run_cols[4].toggle("Only rows w/ NEW items", value=False)

if clear_click:
    st.session_state["sympto_suggestions"] = {}

if symptomize_click and todo_ids:
    with st.spinner("Calling LLM and parsing suggestions…"):
        all_sug: Dict[int, dict] = {}
        for batch in chunked(todo_ids, batch_size):
            rows = [(rid, str(df.loc[rid, text_col])) for rid in batch]
            out = call_llm(rows, base_delighters, base_detractors)
            for rid, rec in out.items():
                all_sug[rid] = snap_to_lists(rec)
        st.session_state["sympto_suggestions"] = all_sug

sug = st.session_state["sympto_suggestions"]
if sug:
    st.markdown("### ✅ Review suggestions")
    # Gather new items across all rows
    all_new_dl, all_new_dt = [], []
    for v in sug.values():
        all_new_dl.extend(v.get("new_delighters", []))
        all_new_dt.extend(v.get("new_detractors", []))
    all_new_dl = uniq(all_new_dl)
    all_new_dt = uniq(all_new_dt)

    with st.expander("🔎 Proposed NEW symptoms (approve to allow on export)", expanded=bool(all_new_dl or all_new_dt)):
        c1, c2 = st.columns(2)
        approved_dl = c1.multiselect("Approve new Delighters", all_new_dl, default=[])
        approved_dt = c2.multiselect("Approve new Detractors", all_new_dt, default=[])
        st.caption("Approved items will be included on the 'Approved_New_Symptoms' sheet in the exported workbook.")

    # Build preview rows
    preview = []
    for rid, rec in sug.items():
        if show_only_new and not (rec.get("new_delighters") or rec.get("new_detractors")):
            continue
        full_txt = str(df.loc[rid, text_col])
        preview.append({
            "Row ID": rid,
            "Review (preview)": full_txt[:int(peek_len)],
            "Detractors (≤10)": "; ".join(rec.get("detractors", []))[:800],
            "Delighters (≤10)": "; ".join(rec.get("delighters", []))[:800],
            "NEW detractors": "; ".join(rec.get("new_detractors", []))[:300],
            "NEW delighters": "; ".join(rec.get("new_delighters", []))[:300],
        })

    if preview:
        st.dataframe(pd.DataFrame(preview).sort_values("Row ID"), use_container_width=True, hide_index=True)

    # Full-review peek
    with st.expander("📖 Peek full review", expanded=False):
        ids_sorted = sorted(sug.keys())
        if ids_sorted:
            rid = st.selectbox("Row ID", ids_sorted)
            colL, colR = st.columns([1.2, 1])
            colL.markdown("**Full review text**")
            colL.write(str(df.loc[rid, text_col]))
            rec = sug[rid]
            colR.markdown("**Suggested detractors**")
            colR.write(", ".join(rec.get("detractors", [])) or "—")
            colR.markdown("**Suggested delighters**")
            colR.write(", ".join(rec.get("delighters", [])) or "—")
            if rec.get("new_detractors") or rec.get("new_delighters"):
                colR.info(f"NEW → Detractors: {', '.join(rec.get('new_detractors', [])) or '—'} | Delighters: {', '.join(rec.get('new_delighters', [])) or '—'}")

    # Choose rows to apply
    ids_for_apply = st.multiselect("Select review row IDs to apply", sorted(sug.keys()), default=sorted(sug.keys())[: min(50, len(sug))])

    def apply_to_df(target_ids: List[int]):
        # backup
        st.session_state["applied_backup"] = df.copy(deep=True)
        applied = 0
        for rid in target_ids:
            if rid not in sug: continue
            row = df.loc[rid]
            if fill_only_empties and not all_symptoms_empty(row):
                continue
            rec = sug[rid]
            det = rec.get("detractors", [])[:10]
            dle = rec.get("delighters", [])[:10]
            # write
            for i in range(10):
                c = f"Symptom {i+1}"
                if c in df.columns: df.at[rid, c] = det[i] if i < len(det) else pd.NA
            for i in range(10):
                c = f"Symptom {i+11}"
                if c in df.columns: df.at[rid, c] = dle[i] if i < len(dle) else pd.NA
            applied += 1
        return applied

    colA1, colA2, colA3 = st.columns([1,1,2])
    do_apply = colA1.button("✍️ Apply to selected rows", disabled=(not ids_for_apply))
    do_undo  = colA2.button("↩️ Undo last apply", disabled=(st.session_state["applied_backup"] is None))

    if do_apply:
        n = apply_to_df(ids_for_apply)
        st.success(f"Applied to {n} row(s).")
    if do_undo and st.session_state["applied_backup"] is not None:
        df = st.session_state["applied_backup"]
        st.session_state["applied_backup"] = None
        st.success("Reverted the last apply.")

    # Export XLSX: updated main + approved new symptoms
    if st.button("⬇️ Download symptomized workbook (.xlsx)"):
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df.to_excel(writer, sheet_name=main_sheet_name, index=False)
            approved_df = pd.DataFrame({
                "New Delighters (approved)": approved_dl or [],
                "New Detractors (approved)": approved_dt or []
            })
            if not approved_df.empty:
                approved_df.to_excel(writer, sheet_name="Approved_New_Symptoms", index=False)
            # Also include current canonical lists for reference
            pd.DataFrame({"Delighters": base_delighters}).to_excel(writer, sheet_name="Delighters_List", index=False)
            pd.DataFrame({"Detractors": base_detractors}).to_excel(writer, sheet_name="Detractors_List", index=False)
        st.download_button(
            "Download .xlsx",
            data=output.getvalue(),
            file_name="starwalk_symptomized.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
else:
    st.info("No suggestions yet. Click the **Symptomize** button to generate suggestions for rows with empty Symptom 1–20.")


