# ---------- Star Walk — Upload + Symptomize UI (complete) ----------
# Streamlit 1.38+

import io
import os
import re
import json
import difflib
from typing import List, Tuple

import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

# Optional: use OpenAI if available
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

# Optional: preserve workbook formatting
try:
    from openpyxl import load_workbook
    _HAS_OPENPYXL = True
except Exception:
    _HAS_OPENPYXL = False

# ---------------- Page + Theme ----------------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# Force light mode
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

# ---------------- Global CSS ----------------
st.markdown("""
<style>
  :root { scroll-behavior: smooth; scroll-padding-top: 96px; }
  *, ::before, ::after { box-sizing: border-box; }
  @supports (scrollbar-color: transparent transparent){ * { scrollbar-width: thin; scrollbar-color: transparent transparent; } }

  :root{
    --text:#0f172a; --muted:#475569; --muted-2:#64748b;
    --border-strong:#90a7c1; --border:#cbd5e1; --border-soft:#e2e8f0;
    --bg-app:#f6f8fc; --bg-card:#ffffff; --bg-tile:#f8fafc;
    --ring:#3b82f6; --ok:#16a34a; --bad:#dc2626;
  }
  html, body, .stApp {
    background: var(--bg-app);
    font-family: "Helvetica Neue", Helvetica, Arial, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Liberation Sans", sans-serif;
    color: var(--text);
  }
  .block-container { padding-top:.9rem; padding-bottom:1.2rem; }
  .card{
    background:var(--bg-card); border-radius:14px; padding:16px;
    box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06);
  }
  .muted{ color:var(--muted); }
  .pill{ display:inline-block; padding:4px 10px; border-radius:999px; background:var(--bg-tile); border:1px solid var(--border); font-weight:600; }
  .ok{ color:#065F46; }
  .warn{ color:#7C2D12; }
  .hero-wrap{
    position:relative; overflow:hidden; border-radius:14px; min-height:116px; margin:.25rem 0 1rem 0;
    box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06);
    background:linear-gradient(90deg, var(--bg-card) 0% 55%, transparent 55% 100%);
  }
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:10px 18px; }
  .hero-title{ font-size:clamp(22px,3.2vw,38px); font-weight:800; margin:0; }
  .hero-sub{ margin:4px 0 0 0; color:var(--muted); font-size:clamp(12px,1.1vw,16px); }
  .sn-logo{ height:46px; width:auto; display:block; opacity:.9; }
</style>
""", unsafe_allow_html=True)


# ---------------- Small helpers ----------------
def _is_empty_cell(v) -> bool:
    if pd.isna(v): return True
    s = str(v).strip()
    return s == "" or s.upper() in {"NA", "N/A", "NULL", "NONE", "-", "--"}

def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()

def _escape_md(s: str) -> str:
    # minimal safe markdown escape for inline text
    return re.sub(r'([_*`>])', r'\\\1', s)


# ---------------- Header ----------------
st.markdown("""
<div class="hero-wrap">
  <div class="hero-inner">
    <div>
      <div class="hero-title">Star Walk — Symptomize Reviews</div>
      <div class="hero-sub">Upload your workbook, detect reviews without symptoms, and let AI suggest precise delighters & detractors.</div>
    </div>
    <div><img class="sn-logo" src="https://upload.wikimedia.org/wikipedia/commons/e/ea/SharkNinja_logo.svg" alt="SharkNinja"></div>
  </div>
</div>
""", unsafe_allow_html=True)


# ---------------- Sidebar: Upload ----------------
st.sidebar.header("📁 Upload Star Walk File")
uploaded = st.sidebar.file_uploader("Choose Excel File", type=["xlsx"], accept_multiple_files=False)

# Keep original bytes (so we can write back with formatting)
if uploaded and "uploaded_bytes" not in st.session_state:
    uploaded.seek(0)
    st.session_state["uploaded_bytes"] = uploaded.read()
    uploaded.seek(0)

if not uploaded:
    st.info("Upload a `.xlsx` workbook to begin.")
    st.stop()

# ---------------- Load DataFrame ----------------
# Prefer sheet "Star Walk scrubbed verbatims", fallback to first
try:
    try:
        df = pd.read_excel(uploaded, sheet_name="Star Walk scrubbed verbatims")
    except ValueError:
        df = pd.read_excel(uploaded)
except Exception as e:
    st.error(f"Could not read the Excel file: {e}")
    st.stop()

# Robust Symptom column detection
explicit_cols = [f"Symptom {i}" for i in range(1, 21)]
SYMPTOM_COLS = [c for c in explicit_cols if c in df.columns]
if not SYMPTOM_COLS and len(df.columns) >= 30:
    # Fallback to K–AD (0-based col index 10..29 inclusive)
    SYMPTOM_COLS = df.columns[10:30].tolist()

if not SYMPTOM_COLS:
    st.error("Couldn't locate Symptom 1–20 columns. Please ensure the sheet has those columns or that columns K–AD are the symptoms.")
    st.stop()

# Detect rows with ALL symptom cells empty
mask_empty = df[SYMPTOM_COLS].applymap(_is_empty_cell).all(axis=1)
missing_idx = df.index[mask_empty].tolist()
missing_count = len(missing_idx)

# Show upload success + quick metrics
with st.container():
    st.success("File uploaded successfully.")
    # IQR of review lengths (for QA visibility)
    text_series = df.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str)
    lengths = text_series.str.len()
    if not lengths.empty:
        q1 = lengths.quantile(0.25)
        q3 = lengths.quantile(0.75)
        iqr = q3 - q1
        st.caption(f"Review length IQR (chars): Q1={int(q1)}, Q3={int(q3)}, IQR={int(iqr)}.")


# ---------------- Load Allowed Symptoms from "Symptoms" sheet ----------------
def load_symptom_lists_from_workbook(raw_bytes: bytes) -> Tuple[List[str], List[str]]:
    try:
        xls = pd.ExcelFile(io.BytesIO(raw_bytes))
        target = None
        for name in xls.sheet_names:
            if name.strip().lower() in {"symptoms", "symptom", "symptom sheet", "symptom tab"}:
                target = name
                break
        if not target:
            return [], []
        s = pd.read_excel(xls, target)
        cols = {c.lower().strip(): c for c in s.columns}
        dels = s[cols.get("delighters")] if "delighters" in cols else s.get("Delighters")
        dets = s[cols.get("detractors")] if "detractors" in cols else s.get("Detractors")
        del_list = [str(x).strip() for x in (dels or pd.Series(dtype=str)).dropna().tolist() if str(x).strip()]
        det_list = [str(x).strip() for x in (dets or pd.Series(dtype=str)).dropna().tolist() if str(x).strip()]
        return del_list, det_list
    except Exception:
        return [], []

ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS = load_symptom_lists_from_workbook(
    st.session_state.get("uploaded_bytes", b"")
)
ALLOWED_DELIGHTERS_SET = set(ALLOWED_DELIGHTERS)
ALLOWED_DETRACTORS_SET = set(ALLOWED_DETRACTORS)

# ---------------- Controls (top bar) ----------------
top = st.container()
with top:
    c1, c2, c3, c4 = st.columns([2, 2, 2, 3])

    with c1:
        st.markdown(f"**{missing_count}** reviews have all **Symptom 1–20** empty.")
    with c2:
        batch_n = st.slider("How many to process this run", 1, 20, min(10, max(1, missing_count)) if missing_count else 10)
    with c3:
        model_choice = st.selectbox("Model", ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-5"], index=0)
    with c4:
        # rough request/time estimate
        reqs = min(batch_n, missing_count)
        # very conservative throughput estimate (per-review seconds) by model
        spd = 1.2 if model_choice in {"gpt-4o-mini", "gpt-4o"} else (1.6 if model_choice == "gpt-4.1" else 2.2)
        secs = int(round(reqs * spd))
        st.caption(f"Est. requests: {reqs} • Rough time: ~{secs}s (single batch).")

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if missing_count and not _HAS_OPENAI:
    st.warning("Install `openai` and set `OPENAI_API_KEY` to enable AI labeling.")
if missing_count and _HAS_OPENAI and not api_key:
    st.warning("Set `OPENAI_API_KEY` (env or secrets) to enable AI labeling.")

# ---------------- Session state for suggestions/selection ----------------
st.session_state.setdefault("symptom_suggestions", [])    # list of dicts
st.session_state.setdefault("sug_selected", set())        # set of indices in suggestions list

# ---------------- LLM picker ----------------
def _dedupe_keep_top(items: List[Tuple[str, float]], top_n: int = 10, min_conf: float = 0.58) -> List[str]:
    """Drop near-duplicates (SequenceMatcher>0.9) and low-confidence."""
    items = [(n, c) for (n, c) in items if c >= min_conf]
    kept: List[Tuple[str, float]] = []
    for n, c in sorted(items, key=lambda x: -x[1]):
        n_norm = _normalize_name(n)
        if not any(difflib.SequenceMatcher(None, n_norm, _normalize_name(k)).ratio() > 0.90 for k, _ in kept):
            kept.append((n, c))
        if len(kept) >= top_n:
            break
    return [n for n, _ in kept]

def _llm_pick(review: str, stars, allowed_del: List[str], allowed_det: List[str]) -> Tuple[List[str], List[str]]:
    if not review or (not allowed_del and not allowed_det):
        return [], []

    sys = (
        "You label one review. Choose up to 10 delighters and up to 10 detractors ONLY from the provided lists.\n"
        "Return JSON: {\"delighters\":[{\"name\":\"...\",\"confidence\":0-1},...],"
        "\"detractors\":[{\"name\":\"...\",\"confidence\":0-1},...]}\n"
        "Rules: (1) If not clearly present, omit. (2) Prefer precision over recall. "
        "(3) Avoid near-duplicates (e.g., choose 'Learning curve' OR 'Initial difficulty', not both). "
        "(4) If none, return empty arrays."
    )
    user = {
        "review": review[:4000],
        "stars": float(stars) if pd.notna(stars) else None,
        "allowed_delighters": allowed_del[:60],
        "allowed_detractors": allowed_det[:60]
    }

    # Try OpenAI; if it fails, fall back to simple keyword match
    if _HAS_OPENAI and api_key:
        try:
            client = OpenAI(api_key=api_key)
            req = dict(
                model=model_choice,
                messages=[{"role":"system","content":sys},
                          {"role":"user","content":json.dumps(user)}],
                response_format={"type":"json_object"}
            )
            # Skip temperature for GPT-5 to avoid 400 unsupported_value
            if not str(model_choice).startswith("gpt-5"):
                req["temperature"] = 0.2

            out = client.chat.completions.create(**req)
            content = out.choices[0].message.content or "{}"
            data = json.loads(content)
            dels_raw = data.get("delighters", []) or []
            dets_raw = data.get("detractors", []) or []
            dels = [(d.get("name","").strip(), float(d.get("confidence", 0))) for d in dels_raw if d.get("name")]
            dets = [(d.get("name","").strip(), float(d.get("confidence", 0))) for d in dets_raw if d.get("name")]

            # Keep only items that exist in the allowed lists
            dels = [(n, c) if n in ALLOWED_DELIGHTERS_SET else (n, 0.0) for n, c in dels]
            dets = [(n, c) if n in ALLOWED_DETRACTORS_SET else (n, 0.0) for n, c in dets]

            return _dedupe_keep_top(dels, 10), _dedupe_keep_top(dets, 10)
        except Exception:
            pass

    # Fallback: conservative keyword overlap
    text = " " + review.lower() + " "
    def pick_from_allowed(allowed: List[str]) -> List[str]:
        scored = []
        for a in allowed:
            a_norm = _normalize_name(a)
            if not a_norm:
                continue
            toks = [t for t in a_norm.split() if len(t) > 2]
            if not toks:
                continue
            score = sum(1 for t in toks if f" {t} " in text) / len(toks)
            if score >= 0.75:  # conservative
                scored.append((a, 0.60 + 0.4*score))
        return _dedupe_keep_top(scored, 10)

    return pick_from_allowed(allowed_del), pick_from_allowed(allowed_det)

# ---------------- Symptomize action ----------------
run_col = st.container()
with run_col:
    left, right = st.columns([1,4])
    with left:
        can_run = missing_count > 0 and (not _HAS_OPENAI or api_key is not None)
        run = st.button(f"✨ Symptomize next {min(batch_n, missing_count)} review(s)", disabled=not can_run)
    with right:
        if missing_count == 0:
            st.info("No empty-symptom reviews detected.")
        elif not _HAS_OPENAI or not api_key:
            st.info("Add OpenAI to your environment and set `OPENAI_API_KEY` to enable AI labeling.")

# Process batch and store suggestions
if run and missing_idx:
    todo = missing_idx[:batch_n]
    progress = st.progress(0)
    status = st.empty()
    for i, idx in enumerate(todo, start=1):
        row = df.loc[idx]
        review_txt = str(row.get("Verbatim", "") or "").strip()
        stars = row.get("Star Rating", None)

        dels, dets = _llm_pick(review_txt, stars, ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS)
        st.session_state["symptom_suggestions"].append({
            "row_index": int(idx),
            "stars": float(stars) if pd.notna(stars) else None,
            "review": review_txt,
            "delighters": dels,
            "detractors": dets
        })
        progress.progress(i / len(todo))
        status.info(f"Processed {i}/{len(todo)}")
    status.success("Done!")
    st.rerun()


# ---------------- Review & Approve UI ----------------
sugs = st.session_state.get("symptom_suggestions", [])
if sugs:
    st.markdown("## 🔍 Review & Approve Suggestions")

    with st.expander("Bulk actions", expanded=True):
        cba1, cba2, cba3, cba4 = st.columns([1,1,2,3])
        with cba1:
            if st.button("Select all"):
                st.session_state["sug_selected"] = set(range(len(sugs)))
        with cba2:
            if st.button("Clear selection"):
                st.session_state["sug_selected"] = set()
        with cba3:
            if st.button("Keep only non-empty"):
                st.session_state["sug_selected"] = {i for i, s in enumerate(sugs) if s["delighters"] or s["detractors"]}
        with cba4:
            max_apply = st.slider("Max rows to apply this click", 1, len(sugs), min(20, len(sugs)))

    for i, s in enumerate(sugs):
        lab = f"Review #{i} • Stars: {s.get('stars','-')} • {len(s['delighters'])} delighters / {len(s['detractors'])} detractors"
        with st.expander(lab, expanded=(i == 0)):
            # Selection checkbox
            checked = i in st.session_state["sug_selected"]
            if st.checkbox("Select for apply", value=checked, key=f"sel_{i}"):
                st.session_state["sug_selected"].add(i)
            else:
                st.session_state["sug_selected"].discard(i)

            # Full review (escaped)
            review_block = _escape_md(s["review"]) if s["review"] else "*(empty)*"
            st.markdown(f"**Full review:**\n\n> {review_block}")

            c1, c2 = st.columns(2)
            with c1:
                st.write("**Detractors (≤10)**")
                st.code("; ".join(s["detractors"]) if s["detractors"] else "–")
            with c2:
                st.write("**Delighters (≤10)**")
                st.code("; ".join(s["delighters"]) if s["delighters"] else "–")

    if st.button("✅ Apply selected to DataFrame"):
        picked = [i for i in st.session_state["sug_selected"]]
        if not picked:
            st.warning("Nothing selected.")
        else:
            picked = picked[:max_apply]
            for i in picked:
                s = sugs[i]
                ri = s["row_index"]
                # Write detractors to Symptom 1..10; delighters to 11..20
                for j, name in enumerate(s["detractors"][:10], start=1):
                    col = f"Symptom {j}"
                    if col in df.columns:
                        df.at[ri, col] = name
                for j, name in enumerate(s["delighters"][:10], start=11):
                    col = f"Symptom {j}"
                    if col in df.columns:
                        df.at[ri, col] = name
            st.success(f"Applied {len(picked)} rows.")
            # Clear selection for applied rows
            for i in picked:
                st.session_state["sug_selected"].discard(i)

# ---------------- Download updated workbook (preserve formatting if possible) ----------------
def offer_downloads():
    st.markdown("### ⬇️ Download Updated Workbook")
    base_btn_label = "Download updated workbook (.xlsx)"
    if "uploaded_bytes" not in st.session_state:
        st.info("Upload a workbook first.")
        return

    raw = st.session_state["uploaded_bytes"]

    # Try to write back only Symptom columns using openpyxl (preserve styles)
    if _HAS_OPENPYXL:
        try:
            bio = io.BytesIO(raw)
            wb = load_workbook(bio)
            data_sheet = "Star Walk scrubbed verbatims"
            if data_sheet not in wb.sheetnames:
                data_sheet = wb.sheetnames[0]
            ws = wb[data_sheet]

            # Header map
            headers = {ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column + 1)}
            def col_idx(name): return headers.get(name)

            # Write back only the Symptom columns; assume data starts row 2
            for df_row_idx, row in df.reset_index(drop=True).iterrows():
                excel_row = 2 + df_row_idx
                for c in SYMPTOM_COLS:
                    ci = col_idx(c)
                    if ci:
                        ws.cell(row=excel_row, column=ci).value = row.get(c, None)

            out = io.BytesIO()
            wb.save(out)
            st.download_button(base_btn_label, data=out.getvalue(),
                               file_name="StarWalk_updated.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            return
        except Exception:
            pass

    # Fallback (plain export; formatting not preserved)
    out2 = io.BytesIO()
    with pd.ExcelWriter(out2, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="Star Walk scrubbed verbatims")
    st.download_button(base_btn_label + " (no formatting)", data=out2.getvalue(),
                       file_name="StarWalk_updated_basic.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

offer_downloads()
