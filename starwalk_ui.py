# ---------- Star Walk — Upload + Symptomize (Enhanced UX, Accuracy & Approvals) ----------
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

# Optional OpenAI
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

# ---------------- Page Config ----------------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# ---------------- Force Light Mode ----------------
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
GLOBAL_CSS = """
<style>
  :root { scroll-behavior: smooth; scroll-padding-top: 96px; }
  *, ::before, ::after { box-sizing: border-box; }
  @supports (scrollbar-color: transparent transparent){ * { scrollbar-width: thin; scrollbar-color: transparent transparent; } }
  :root{
    --text:#0f172a; --muted:#475569; --muted-2:#64748b;
    --border-strong:#90a7c1; --border:#cbd5e1; --border-soft:#e2e8f0;
    --bg-app:#f6f8fc; --bg-card:#ffffff; --bg-tile:#f8fafc;
    --ring:#3b82f6; --ok:#16a34a; --bad:#dc2626;
    --gap-sm:12px; --gap-md:20px; --gap-lg:32px;
  }
  html, body, .stApp { background: var(--bg-app); font-family: "Helvetica Neue", Helvetica, Arial, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Liberation Sans", sans-serif; color: var(--text); }
  .block-container { padding-top:.9rem; padding-bottom:1.2rem; }
  .hero-wrap{ position:relative; overflow:hidden; border-radius:14px; min-height:120px; margin:.25rem 0 1rem 0; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); background:linear-gradient(90deg, var(--bg-card) 0% 55%, transparent 55% 100%); }
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:10px 18px; color:var(--text); }
  .hero-title{ font-size:clamp(22px,3.1vw,40px); font-weight:800; margin:0; }
  .hero-sub{ margin:4px 0 0 0; color:var(--muted); font-size:clamp(12px,1.1vw,16px); }
  .hero-right{ display:flex; align-items:center; justify-content:flex-end; width:40%; }
  .sn-logo{ height:46px; width:auto; display:block; opacity:.92; }
  .card{ background:var(--bg-card); border-radius:14px; padding:16px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); }
  .muted{ color:var(--muted); }
  .kpi{ display:flex; gap:14px; flex-wrap:wrap }
  .pill{ padding:8px 12px; border-radius:999px; border:1.5px solid var(--border); background:var(--bg-tile); font-weight:700 }
  .review-quote { white-space:pre-wrap; background:var(--bg-tile); border:1.5px solid var(--border); border-radius:12px; padding:8px 10px; }
  mark { background:#fff2a8; padding:0 .15em; border-radius:3px; }
</style>
"""
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# ---------------- Header ----------------
st.markdown(
    """
    <div class="hero-wrap">
      <div class="hero-inner">
        <div>
          <div class="hero-title">Star Walk — Symptomize Reviews</div>
          <div class="hero-sub">Upload, detect missing symptoms, and let AI suggest precise delighters & detractors (with human approval).</div>
        </div>
        <div class="hero-right"><img class="sn-logo" src="https://upload.wikimedia.org/wikipedia/commons/e/ea/SharkNinja_logo.svg" alt="SharkNinja"/></div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------- Upload ----------------
st.sidebar.header("📁 Upload Star Walk File")
uploaded = st.sidebar.file_uploader("Choose Excel File", type=["xlsx"], accept_multiple_files=False)

# Persist raw bytes for formatting-preserving save
if uploaded and "uploaded_bytes" not in st.session_state:
    uploaded.seek(0)
    st.session_state["uploaded_bytes"] = uploaded.read()
    uploaded.seek(0)

if not uploaded:
    st.info("Upload a .xlsx workbook to begin.")
    st.stop()

# Load main sheet
try:
    try:
        df = pd.read_excel(uploaded, sheet_name="Star Walk scrubbed verbatims")
    except ValueError:
        df = pd.read_excel(uploaded)
except Exception as e:
    st.error(f"Could not read the Excel file: {e}")
    st.stop()

# ---------------- Identify Symptom Columns ----------------
explicit_cols = [f"Symptom {i}" for i in range(1,21)]
SYMPTOM_COLS = [c for c in explicit_cols if c in df.columns]
if not SYMPTOM_COLS and len(df.columns) >= 30:
    SYMPTOM_COLS = df.columns[10:30].tolist()  # K–AD fallback
if not SYMPTOM_COLS:
    st.error("Couldn't locate Symptom 1–20 columns (K–AD).")
    st.stop()

# Missing symptom rows
is_empty = df[SYMPTOM_COLS].isna() | (df[SYMPTOM_COLS].astype(str).applymap(lambda x: str(x).strip().upper() in {"", "NA", "N/A", "NONE", "NULL", "-"}))
mask_empty = is_empty.all(axis=1)
missing_idx = df.index[mask_empty].tolist()
missing_count = len(missing_idx)

# Review length IQR for ETA
verb_series = df.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str)
q1 = verb_series.str.len().quantile(0.25) if not verb_series.empty else 0
q3 = verb_series.str.len().quantile(0.75) if not verb_series.empty else 0
IQR = (q3 - q1) if (q3 or q1) else 0

# ---------------- Load symptom dictionary from "Symptoms" sheet if present ----------------

def load_symptom_lists(raw_bytes: bytes) -> Tuple[list, list]:
    try:
        xls = pd.ExcelFile(io.BytesIO(raw_bytes))
        target = None
        for n in xls.sheet_names:
            if n.strip().lower() in {"symptoms","symptom","symptom sheet","symptom tab"}:
                target = n; break
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

ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS = load_symptom_lists(st.session_state.get("uploaded_bytes", b""))
ALLOWED_DELIGHTERS_SET = set(ALLOWED_DELIGHTERS)
ALLOWED_DETRACTORS_SET = set(ALLOWED_DETRACTORS)

if not ALLOWED_DELIGHTERS and not ALLOWED_DETRACTORS:
    st.warning("Couldn't find a 'Symptoms' sheet with Delighters/Detractors. AI will only use conservative keyword fallback.")

# ---------------- Top KPIs & Actions ----------------
st.markdown("### Status")
colA, colB, colC, colD = st.columns([2,2,2,3])
with colA:
    st.markdown(f"<div class='pill'>🧾 Total reviews: <b>{len(df)}</b></div>", unsafe_allow_html=True)
with colB:
    st.markdown(f"<div class='pill'>❌ Missing symptoms: <b>{missing_count}</b></div>", unsafe_allow_html=True)
with colC:
    st.markdown(f"<div class='pill'>✂ IQR chars: <b>{int(IQR)}</b></div>", unsafe_allow_html=True)
with colD:
    st.caption("Estimates scale by model + text length; they are indicative only.")

left, mid, right = st.columns([2,2,3])
with left:
    batch_n = st.slider("How many to process this run", 1, 20, min(10, max(1, missing_count)) if missing_count else 10)
with mid:
    model_choice = st.selectbox("Model", ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-5"], index=0)
with right:
    strictness = st.slider("Strictness (higher = fewer, more precise)", 0.55, 0.90, 0.72, 0.01, help="Confidence threshold; also reduces near-duplicate choices.")

# ETA (heuristic) — scaled by interquartile text length
rows = min(batch_n, missing_count)
speed_base = 1.1 if model_choice in {"gpt-4o-mini","gpt-4o"} else (1.5 if model_choice=="gpt-4.1" else 2.0)
length_factor = max(0.75, min(1.35, ((q1+q3)/800.0) if (q1+q3)>0 else 1.0))
eta_secs = int(round(rows * speed_base * length_factor))
st.caption(f"Will attempt {rows} rows • Rough ETA: ~{eta_secs}s")

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if missing_count and not _HAS_OPENAI:
    st.warning("Install `openai` and set `OPENAI_API_KEY` to enable AI labeling.")
if missing_count and _HAS_OPENAI and not api_key:
    st.warning("Set `OPENAI_API_KEY` (env or secrets) to enable AI labeling.")

# ---------------- Session State ----------------
st.session_state.setdefault("symptom_suggestions", [])
st.session_state.setdefault("sug_selected", set())
st.session_state.setdefault("approved_new_delighters", set())
st.session_state.setdefault("approved_new_detractors", set())

# ---------------- Helpers ----------------

def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()

def _escape_md(s: str) -> str:
    """Escape a subset of Markdown so reviews render predictably in Streamlit."""
    if s is None:
        return ""
    return re.sub(r'([\`*_{}\[\]()#+\-.!>])', r'\\\1', str(s))

# Conservative dedupe + cut to N

def _dedupe_keep_top(items: List[Tuple[str, float]], top_n: int = 10, min_conf: float = 0.60) -> List[str]:
    items = [(n, c) for (n, c) in items if c >= min_conf]
    kept: List[Tuple[str, float]] = []
    for n, c in sorted(items, key=lambda x: -x[1]):
        n_norm = _normalize_name(n)
        # avoid near-duplicates (e.g., "initial difficulty" vs "learning curve")
        if not any(difflib.SequenceMatcher(None, n_norm, _normalize_name(k)).ratio() > 0.90 for k, _ in kept):
            kept.append((n, c))
        if len(kept) >= top_n: break
    return [n for n, _ in kept]

# Highlight allowed terms in review for quick verification

def _highlight_terms(text: str, allowed: List[str]) -> str:
    safe = _escape_md(text)
    # Simple HTML mark on raw string in a separate block below (not markdown)
    html = re.escape(text)
    out = text
    for t in sorted(set(allowed), key=len, reverse=True):
        if not t.strip():
            continue
        try:
            out = re.sub(rf"(\b{re.escape(t)}\b)", r"<mark>\1</mark>", out, flags=re.IGNORECASE)
        except re.error:
            pass
    return out

# Model call (JSON-only)

def _llm_pick(review: str, stars, allowed_del: List[str], allowed_det: List[str], min_conf: float):
    """Return (allowed_delighters, allowed_detractors, novel_delighters, novel_detractors)."""
    if not review or (not allowed_del and not allowed_det):
        return [], [], [], []

    sys_prompt = """You are labeling a single user review.
Choose up to 10 delighters and up to 10 detractors ONLY from the provided lists.
Return JSON exactly like: {"delighters":[{"name":"...","confidence":0.0}], "detractors":[{"name":"...","confidence":0.0}]}
Rules:
1) If not clearly present, OMIT it.
2) Prefer precision over recall; avoid stretch matches.
3) Avoid near-duplicates (use canonical terms, e.g., 'Learning curve' not 'Initial difficulty').
4) If stars are 1–2, bias to detractors; if 4–5, bias to delighters; otherwise neutral.
"""

    user =  {
        "review": review[:4000],
        "stars": float(stars) if (stars is not None and (not pd.isna(stars))) else None,
        "allowed_delighters": allowed_del[:80],
        "allowed_detractors": allowed_det[:80]
    }

    dels, dets, novel_dels, novel_dets = [], [], [], []

    if _HAS_OPENAI and api_key:
        try:
            client = OpenAI(api_key=api_key)
            req = {
                "model": model_choice,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": json.dumps(user)}
                ],
                "response_format": {"type": "json_object"}
            }
            # GPT-5 rejects non-default temperature; omit for that family
            if not str(model_choice).startswith("gpt-5"):
                req["temperature"] = 0.2
            out = client.chat.completions.create(**req)
            content = out.choices[0].message.content or "{}"
            data = json.loads(content)
            dels_raw = data.get("delighters", []) or []
            dets_raw = data.get("detractors", []) or []
            dels_pairs = [(d.get("name", ""), float(d.get("confidence", 0))) for d in dels_raw if d.get("name")]
            dets_pairs = [(d.get("name", ""), float(d.get("confidence", 0))) for d in dets_raw if d.get("name")]
            for n, c in dels_pairs:
                if n in ALLOWED_DELIGHTERS_SET:
                    dels.append((n, c))
                else:
                    novel_dels.append((n, c))
            for n, c in dets_pairs:
                if n in ALLOWED_DETRACTORS_SET:
                    dets.append((n, c))
                else:
                    novel_dets.append((n, c))
            return (
                _dedupe_keep_top(dels, 10, min_conf),
                _dedupe_keep_top(dets, 10, min_conf),
                _dedupe_keep_top(novel_dels, 5, max(0.70, min_conf)),
                _dedupe_keep_top(novel_dets, 5, max(0.70, min_conf))
            )
        except Exception:
            pass

    # Conservative keyword fallback (no-API)
    text = " " + review.lower() + " "
    def pick_from_allowed(allowed: List[str]) -> List[str]:
        scored = []
        for a in allowed:
            a_norm = _normalize_name(a)
            toks = [t for t in a_norm.split() if len(t) > 2]
            if not toks:
                continue
            score = sum(1 for t in toks if f" {t} " in text) / len(toks)
            if score >= min_conf:
                scored.append((a, 0.60 + 0.4 * score))
        return _dedupe_keep_top(scored, 10, min_conf)

    return pick_from_allowed(allowed_del), pick_from_allowed(allowed_det), [], []

# ---------------- Run Symptomize ----------------
can_run = missing_count > 0 and ((not _HAS_OPENAI) or (api_key is not None))
run = st.button(f"✨ Symptomize next {min(batch_n, missing_count)} review(s)", disabled=not can_run)

if run and missing_idx:
    todo = missing_idx[:batch_n]
    progress = st.progress(0)
    status = st.empty()
    for i, idx in enumerate(todo, start=1):
        row = df.loc[idx]
        review_txt = str(row.get("Verbatim", "") or "").strip()
        stars = row.get("Star Rating", None)
        dels, dets, novel_dels, novel_dets = _llm_pick(review_txt, stars, ALLOWED_DELIGHTERS, ALLOWED_DETRACTORS, strictness)
        st.session_state["symptom_suggestions"].append({
            "row_index": int(idx),
            "stars": float(stars) if pd.notna(stars) else None,
            "review": review_txt,
            "delighters": dels,
            "detractors": dets,
            "novel_delighters": novel_dels,
            "novel_detractors": novel_dets,
            "approve_novel_del": [],
            "approve_novel_det": [],
        })
        progress.progress(i/len(todo))
        status.info(f"Processed {i}/{len(todo)}")
    status.success("Done!")
    st.rerun()

# ---------------- Review & Approve ----------------
sugs = st.session_state.get("symptom_suggestions", [])
if sugs:
    st.markdown("## 🔍 Review & Approve Suggestions")

    # Bulk actions
    with st.expander("Bulk actions", expanded=True):
        c1,c2,c3,c4 = st.columns([1,1,2,3])
        with c1:
            if st.button("Select all"):
                st.session_state["sug_selected"] = set(range(len(sugs)))
        with c2:
            if st.button("Clear selection"):
                st.session_state["sug_selected"] = set()
        with c3:
            if st.button("Keep only non-empty"):
                st.session_state["sug_selected"] = {i for i,s in enumerate(sugs) if s["delighters"] or s["detractors"]}
        with c4:
            max_apply = st.slider("Max rows to apply now", 1, len(sugs), min(20, len(sugs)))

    for i, s in enumerate(sugs):
        label = f"Review #{i} • Stars: {s.get('stars','-')} • {len(s['delighters'])} delighters / {len(s['detractors'])} detractors"
        with st.expander(label, expanded=(i==0)):
            # Select
            checked = i in st.session_state["sug_selected"]
            if st.checkbox("Select for apply", value=checked, key=f"sel_{i}"):
                st.session_state["sug_selected"].add(i)
            else:
                st.session_state["sug_selected"].discard(i)

            # Full review with highlights of allowed terms
            if s["review"]:
                highlighted = _highlight_terms(s["review"], ALLOWED_DELIGHTERS + ALLOWED_DETRACTORS)
                st.markdown("**Full review:**")
                st.markdown(f"<div class='review-quote'>{highlighted}</div>", unsafe_allow_html=True)
            else:
                st.markdown("**Full review:** (empty)")

            c1,c2 = st.columns(2)
            with c1:
                st.write("**Detractors (≤10)**")
                st.code("; ".join(s["detractors"]) if s["detractors"] else "–")
            with c2:
                st.write("**Delighters (≤10)**")
                st.code("; ".join(s["delighters"]) if s["delighters"] else "–")

            # Novel candidates with approval toggles
            if s["novel_detractors"] or s["novel_delighters"]:
                st.info("Potential NEW symptoms (not in your list). Approve to add & allow.")
                c3,c4 = st.columns(2)
                with c3:
                    if s["novel_detractors"]:
                        st.write("**Novel Detractors (proposed)**")
                        picks = []
                        for j, name in enumerate(s["novel_detractors"]):
                            if st.checkbox(name, key=f"novdet_{i}_{j}"):
                                picks.append(name)
                        s["approve_novel_det"] = picks
                with c4:
                    if s["novel_delighters"]:
                        st.write("**Novel Delighters (proposed)**")
                        picks = []
                        for j, name in enumerate(s["novel_delighters"]):
                            if st.checkbox(name, key=f"novdel_{i}_{j}"):
                                picks.append(name)
                        s["approve_novel_del"] = picks

    if st.button("✅ Apply selected to DataFrame"):
        picked = [i for i in st.session_state["sug_selected"]]
        if not picked:
            st.warning("Nothing selected.")
        else:
            picked = picked[:max_apply]
            for i in picked:
                s = sugs[i]
                ri = s["row_index"]
                dets_final = (s["detractors"] + s.get("approve_novel_det", []))[:10]
                dels_final = (s["delighters"] + s.get("approve_novel_del", []))[:10]
                # write to df
                for j, name in enumerate(dets_final, start=1):
                    col = f"Symptom {j}"
                    if col in df.columns:
                        df.at[ri, col] = name
                for j, name in enumerate(dels_final, start=11):
                    col = f"Symptom {j}"
                    if col in df.columns:
                        df.at[ri, col] = name
                # accumulate approved-new for workbook append later
                for n in s.get("approve_novel_del", []):
                    st.session_state["approved_new_delighters"].add(n)
                for n in s.get("approve_novel_det", []):
                    st.session_state["approved_new_detractors"].add(n)
            st.success(f"Applied {len(picked)} row(s) to DataFrame.")

# ---------------- Download Updated Workbook ----------------

def offer_downloads():
    st.markdown("### ⬇️ Download Updated Workbook")
    if "uploaded_bytes" not in st.session_state:
        st.info("Upload a workbook first.")
        return
    raw = st.session_state["uploaded_bytes"]
    # Try formatting-preserving write of symptom columns, and append approved novel symptoms
    if _HAS_OPENPYXL:
        try:
            bio = io.BytesIO(raw)
            wb = load_workbook(bio)
            data_sheet = "Star Walk scrubbed verbatims"
            if data_sheet not in wb.sheetnames:
                data_sheet = wb.sheetnames[0]
            ws = wb[data_sheet]
            headers = {ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column+1)}
            def col_idx(name): return headers.get(name)
            # Write symptoms only (data begins row 2)
            for df_row_idx, row in df.reset_index(drop=True).iterrows():
                excel_row = 2 + df_row_idx
                for c in SYMPTOM_COLS:
                    ci = col_idx(c)
                    if ci:
                        ws.cell(row=excel_row, column=ci).value = row.get(c, None)
            # Append approved novel items into Symptoms sheet if present
            symptoms_sheet_name = None
            for n in wb.sheetnames:
                if n.strip().lower() in {"symptoms","symptom","symptom sheet","symptom tab"}:
                    symptoms_sheet_name = n; break
            if symptoms_sheet_name:
                ss = wb[symptoms_sheet_name]
                # map headers
                sh = {ss.cell(row=1, column=ci).value: ci for ci in range(1, ss.max_column+1)}
                del_col = sh.get("Delighters") or sh.get("delighters")
                det_col = sh.get("Detractors") or sh.get("detractors")
                if del_col:
                    existing = set()
                    for r in range(2, ss.max_row+1):
                        v = ss.cell(row=r, column=del_col).value
                        if v and str(v).strip(): existing.add(str(v).strip())
                    for item in sorted(st.session_state["approved_new_delighters"]):
                        if item not in existing:
                            ss.append([None]*(del_col-1) + [item])
                if det_col:
                    existing = set()
                    for r in range(2, ss.max_row+1):
                        v = ss.cell(row=r, column=det_col).value
                        if v and str(v).strip(): existing.add(str(v).strip())
                    for item in sorted(st.session_state["approved_new_detractors"]):
                        if item not in existing:
                            row = [None]*(det_col-1) + [item]
                            ss.append(row)
            out = io.BytesIO()
            wb.save(out)
            st.download_button("Download updated workbook (.xlsx)", data=out.getvalue(), file_name="StarWalk_updated.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            return
        except Exception:
            pass
    # Fallback
    out2 = io.BytesIO()
    with pd.ExcelWriter(out2, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="Star Walk scrubbed verbatims")
    st.download_button("Download updated workbook (.xlsx) — no formatting", data=out2.getvalue(), file_name="StarWalk_updated_basic.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

offer_downloads()

