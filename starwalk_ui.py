# starwalk_ui.py
# Streamlit 1.38+

from __future__ import annotations

import io
import os
import re
import json
import time
import textwrap
import string
import difflib
from datetime import datetime, timedelta
from typing import List, Dict, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit.components.v1 import html as st_html

# ---------- Optional OpenAI ----------
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

# =========================
# Page / Theme / Global CSS
# =========================
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# Force light mode regardless of system pref
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
  html, body, .stApp {
    background: var(--bg-app);
    font-family: "Helvetica Neue", Helvetica, Arial, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Liberation Sans", sans-serif;
    color: var(--text);
  }
  .block-container { padding-top:.9rem; padding-bottom:1.2rem; }
  mark{ background:#fff2a8; padding:0 .2em; border-radius:3px; }

  /* Hero */
  .hero-wrap{
    position:relative; overflow:hidden; border-radius:14px; min-height:150px; margin:.25rem 0 1rem 0;
    box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06);
    background:linear-gradient(90deg, var(--bg-card) 0% 55%, transparent 55% 100%);
  }
  #hero-canvas{ position:absolute; left:0; top:0; width:55%; height:100%; display:block; }
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:0 18px; color:var(--text); }
  .hero-title{ font-size:clamp(22px,3.3vw,42px); font-weight:800; margin:0; }
  .hero-sub{ margin:4px 0 0 0; color:var(--muted); font-size:clamp(12px,1.1vw,16px); }
  .hero-right{ display:flex; align-items:center; justify-content:flex-end; width:40%; }
  .sn-logo{ height:48px; width:auto; display:block; }

  /* Cards */
  .metrics-grid { display:grid; grid-template-columns:repeat(3,minmax(260px,1fr)); gap:17px; }
  @media (max-width:1100px){ .metrics-grid { grid-template-columns:1fr; } }
  .metric-card{ background:var(--bg-card); border-radius:14px; padding:16px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); color:var(--text); }
  .metric-card h4{ margin:.2rem 0 .7rem 0; font-size:1.05rem; color:var(--text); }
  .metric-row{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
  .metric-box{ background:var(--bg-tile); border:1.6px solid var(--border); border-radius:12px; padding:12px; text-align:center; color:var(--text); }
  .metric-label{ color:var(--muted); font-size:.85rem; }
  .metric-kpi{ font-weight:800; font-size:1.8rem; letter-spacing:-0.01em; margin-top:2px; color:var(--text); }

  /* Review cards */
  .review-card{ background:var(--bg-card); border-radius:12px; padding:16px; margin:16px 0 24px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); color:var(--text); }
  .review-card p{ margin:.25rem 0; line-height:1.5; }

  /* Plotly spacing */
  [data-testid="stPlotlyChart"]{ margin-top:18px !important; margin-bottom:30px !important; }
</style>
"""
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# Simple hero with transparent area behind logo
def render_hero():
    logo_html = (
        '<img class="sn-logo" '
        'src="https://upload.wikimedia.org/wikipedia/commons/e/ea/SharkNinja_logo.svg" '
        'alt="SharkNinja logo"/>'
    )
    HERO = f"""
    <div class="hero-wrap">
      <canvas id="hero-canvas"></canvas>
      <div class="hero-inner">
        <div>
          <h1 class="hero-title">Star Walk Analysis Dashboard</h1>
          <div class="hero-sub">Insights, trends, and ratings — fast.</div>
        </div>
        <div class="hero-right">{logo_html}</div>
      </div>
    </div>
    <script>
    (function(){{
      const c = document.getElementById('hero-canvas');
      if(!c) return;
      const ctx = c.getContext('2d', {{alpha:true}});
      const DPR = window.devicePixelRatio || 1;
      function resize(){{
        const r = c.getBoundingClientRect();
        c.width = Math.max(300, r.width|0) * DPR;
        c.height = Math.max(120, r.height|0) * DPR;
        ctx.setTransform(DPR,0,0,DPR,0,0);
      }}
      window.addEventListener('resize', resize, {{passive:true}});
      resize();
      let N = 140;
      let stars = Array.from({{length:N}}, () => ({{x: Math.random()*c.width, y: Math.random()*c.height, r: .6+Math.random()*1.4, s:.3+Math.random()*0.9}}));
      function tick(){{
        ctx.clearRect(0,0,c.width,c.height);
        for(const s of stars){{
          ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, Math.PI*2);
          ctx.fillStyle = 'rgba(255,200,50,.9)'; ctx.fill();
          s.x += 0.24*s.s; if(s.x > c.width) s.x = 0;
        }}
        requestAnimationFrame(tick);
      }}
      tick();
    }})();
    </script>
    """
    st_html(HERO, height=160)

render_hero()

# =========================
# Helpers
# =========================
SYMPTOM_COLS = [f"Symptom {i}" for i in range(1, 21)]
DET_COLS = [f"Symptom {i}" for i in range(1, 11)]
DEL_COLS = [f"Symptom {i}" for i in range(11, 21)]

def _strip(x) -> str:
    if pd.isna(x): return ""
    return str(x).strip()

def clean_text(x: str) -> str:
    if pd.isna(x): return ""
    s = str(x)
    # quick smart-quote fix
    s = (s.replace("â€™","'").replace("â€˜","‘").replace("â€œ","“").replace("â€\x9d","”")
           .replace("â€“","–").replace("â€”","—").replace("Â","")).strip()
    return s

def norm_key(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.translate(str.maketrans("", "", string.punctuation))
    return " ".join(s.split())

def show_metric_card(title: str, count: int, avg: float, pct12: float) -> str:
    return textwrap.dedent(f"""
    <div class="metric-card">
      <h4>{title}</h4>
      <div class="metric-row">
        <div class="metric-box"><div class="metric-label">Count</div><div class="metric-kpi">{count:,}</div></div>
        <div class="metric-box"><div class="metric-label">Avg ★</div><div class="metric-kpi">{avg:.1f}</div></div>
        <div class="metric-box"><div class="metric-label">% 1–2★</div><div class="metric-kpi">{pct12:.1f}%</div></div>
      </div>
    </div>
    """).strip()

# =========================
# Upload
# =========================
st.sidebar.header("Upload Star Walk File")
uploaded = st.sidebar.file_uploader("Choose Excel File", type=["xlsx"])

if not uploaded:
    st.info("Upload an Excel file to begin. Expect a **'Star Walk scrubbed verbatims'** sheet and a **'Symptoms'** sheet.")
    st.stop()

# =========================
# Load workbook
# =========================
try:
    xls = pd.ExcelFile(uploaded)
except Exception as e:
    st.error(f"Unable to read workbook: {e}")
    st.stop()

# Pick review sheet
REVIEWS_SHEET = "Star Walk scrubbed verbatims" if "Star Walk scrubbed verbatims" in xls.sheet_names else xls.sheet_names[0]
try:
    df = pd.read_excel(xls, sheet_name=REVIEWS_SHEET)
except Exception as e:
    st.error(f"Could not read reviews sheet: {e}")
    st.stop()

# Ensure columns
for col in SYMPTOM_COLS:
    if col not in df.columns:
        df[col] = ""

# Clean
if "Verbatim" in df.columns:
    df["Verbatim"] = df["Verbatim"].astype("string").map(clean_text)
if "Star Rating" in df.columns:
    df["Star Rating"] = pd.to_numeric(df["Star Rating"], errors="coerce")

# Symptoms sheet
SYMPTOMS_SHEET = "Symptoms" if "Symptoms" in xls.sheet_names else None
symptom_lists = {"delighters": [], "detractors": []}

def _load_symptoms():
    global symptom_lists
    if not SYMPTOMS_SHEET:
        return
    s = pd.read_excel(xls, sheet_name=SYMPTOMS_SHEET)
    cols = [c for c in s.columns if str(c).strip()]
    # heuristic: first two columns are Delighters / Detractors
    if not cols:
        return
    dls = s[cols[0]].dropna().astype(str).map(str.strip).tolist()
    dts = s[cols[1]].dropna().astype(str).map(str.strip).tolist() if len(cols) > 1 else []
    # unique, keep order
    def _uniq(lst): 
        seen=set(); out=[]
        for v in lst:
            if v and v not in seen: seen.add(v); out.append(v)
        return out
    symptom_lists = {"delighters": _uniq(dls), "detractors": _uniq(dts)}

_load_symptoms()

# =========================
# Detect reviews needing symptoms
# =========================
sym_empty_mask = df[SYMPTOM_COLS].applymap(_strip).eq("").all(axis=1)
need_count = int(sym_empty_mask.sum())

with st.container():
    st.success("File uploaded successfully. Ready to proceed with analysis.")
    st.markdown(
        f"### ✨ Symptomization\n"
        f"**{need_count:,}** review(s) have empty **Symptom 1–20**. "
        f"Use the controls below to generate **up to 10 detractors** and **up to 10 delighters** per review."
    )

# =========================
# Symptomize controls
# =========================
api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
MODEL_CHOICES = ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-5"]
col1, col2, col3, col4 = st.columns([1.6,1,1,1.2])
model = col1.selectbox("Model", options=MODEL_CHOICES, index=0)
batch_size = col2.slider("Batch size", 5, 50, 20, 5, help="# reviews per API call")
fill_only_empties = col3.toggle("Fill only empties", value=True, help="Don't overwrite non-empty Symptom cells.")
max_chars = col4.number_input("Max chars per review", min_value=200, max_value=4000, value=1500, step=100)

# Compute targets
text_col = next((c for c in ["Verbatim","Review","Review Text","Text"] if c in df.columns), None)
todo_idx: List[int] = df.index[sym_empty_mask].tolist() if text_col else []

# Cost/time estimate
def rough_tokens(s: str) -> int:
    return max(1, int(len(s)/4))

def estimate_cost_time(ids: List[int]) -> Tuple[int, float, float]:
    toks = 0
    for rid in ids:
        t = str(df.loc[rid, text_col])[:max_chars]
        toks += min(rough_tokens(t), 1200) + 400  # +overhead
    per_1k = {"gpt-4o-mini": 0.15, "gpt-4o": 5.0, "gpt-4.1": 15.0, "gpt-5": 30.0}.get(model, 0.15)
    est_cost = (toks/1000.0) * per_1k
    batches = max(1, (len(ids)+batch_size-1)//batch_size)
    est_seconds = batches * 1.5
    return toks, est_cost, est_seconds

if text_col and todo_idx:
    toks, cost, secs = estimate_cost_time(todo_idx)
    st.caption(f"Estimated ~{toks:,} tokens • ~${cost:,.2f} • ~{secs:,.0f}s for {len(todo_idx):,} review(s).")

# =========================
# LLM prompt & parsing
# =========================
def build_prompt(dls: List[str], dts: List[str]) -> str:
    return (
        "You are Shark Glossi Review Analyzer. Analyze each review text and pick **at most** 10 'detractors' "
        "and **at most** 10 'delighters' from the provided allowed lists. Choose only the most important items. "
        "If fewer than 10 apply, return fewer. If you notice a clearly relevant item that is **not** in the allowed list, "
        "include it in 'new_delighters' or 'new_detractors' — do not exceed 5 new items total. "
        "Return JSON with this schema:\n"
        "{\n"
        '  "items":[\n'
        '    {"row_id": <int>, "delighters":[<str>...], "detractors":[<str>...], "new_delighters":[<str>...], "new_detractors":[<str>...]},\n'
        "    ...\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Use only from allowed lists unless in the 'new_*' fields.\n"
        "- Keep names exactly as in the allowed lists (case/spacing included).\n"
        "- Never exceed 10 per type; duplicates forbidden.\n"
        "- Omit commentary.\n\n"
        f"ALLOWED_DELIGHTERS = {json.dumps(dls, ensure_ascii=False)}\n"
        f"ALLOWED_DETRACTORS = {json.dumps(dts, ensure_ascii=False)}\n"
    )

def _uniq_keep_order(lst: List[str]) -> List[str]:
    seen=set(); out=[]
    for v in lst:
        v=v.strip()
        if v and v not in seen:
            seen.add(v); out.append(v)
    return out[:10]

def validate_llm_payload(raw: dict) -> Dict[int, dict]:
    """Strict validation + trimming."""
    out: Dict[int, dict] = {}
    if not isinstance(raw, dict): return out
    items = raw.get("items")
    if not isinstance(items, list): return out
    for it in items:
        try:
            rid = int(it.get("row_id"))
        except Exception:
            continue
        dls = [x for x in it.get("delighters", []) if isinstance(x, str)]
        dts = [x for x in it.get("detractors", []) if isinstance(x, str)]
        ndl = [x for x in it.get("new_delighters", []) if isinstance(x, str)]
        ndt = [x for x in it.get("new_detractors", []) if isinstance(x, str)]
        out[rid] = {
            "delighters": _uniq_keep_order(dls),
            "detractors": _uniq_keep_order(dts),
            "new_delighters": _uniq_keep_order(ndl),
            "new_detractors": _uniq_keep_order(ndt),
        }
    return out

def canonicalize(item: str, allowed: List[str], cutoff: float = 0.9) -> Tuple[str, bool]:
    if item in allowed:
        return item, True
    m = difflib.get_close_matches(item, allowed, n=1, cutoff=cutoff)
    return (m[0], True) if m else (item, False)

def call_llm(batch_rows: List[Tuple[int,str]], dls: List[str], dts: List[str]) -> Dict[int, dict]:
    """Call OpenAI, parse/validate, snap to canonical names."""
    if not _HAS_OPENAI:
        st.error("openai package is not installed.")
        return {}
    if not api_key:
        st.error("Missing OPENAI_API_KEY.")
        return {}
    client = OpenAI(api_key=api_key)

    items = [{"row_id": rid, "text": (txt or "")[:max_chars]} for rid, txt in batch_rows]
    messages = [
        {"role":"system","content": build_prompt(dls, dts)},
        {"role":"user","content": "REVIEWS:\n" + json.dumps({"items": items}, ensure_ascii=False)}
    ]
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            response_format={"type":"json_object"},
        )
        content = resp.choices[0].message.content
        raw = json.loads(content)
        parsed = validate_llm_payload(raw)
        # snap to canonicals (keep unknowns in new_* buckets)
        snapped: Dict[int, dict] = {}
        for rid, rec in parsed.items():
            cds, cdt = [], []
            for x in rec["delighters"]:
                y, ok = canonicalize(x, dls)
                cds.append(y if ok else x)
            for x in rec["detractors"]:
                y, ok = canonicalize(x, dts)
                cdt.append(y if ok else x)
            snapped[rid] = {
                "delighters": _uniq_keep_order(cds),
                "detractors": _uniq_keep_order(cdt),
                "new_delighters": rec.get("new_delighters", []),
                "new_detractors": rec.get("new_detractors", []),
            }
        return snapped
    except Exception as e:
        st.error(f"LLM error: {e}")
        return {}

# =========================
# Run / progress / cancel
# =========================
if "cancel_symptomize" not in st.session_state:
    st.session_state["cancel_symptomize"] = False
if "sympto_suggestions" not in st.session_state:
    st.session_state["sympto_suggestions"] = {}  # row_id -> dict
if "df_backup" not in st.session_state:
    st.session_state["df_backup"] = None
if "change_log" not in st.session_state:
    st.session_state["change_log"] = []  # dicts

col_run, col_cancel = st.columns([3,1])
run_clicked = col_run.button(
    f"✨ Symptomize {len(todo_idx):,} review(s) with OpenAI",
    type="primary",
    disabled=not (text_col and todo_idx and symptom_lists["delighters"] or symptom_lists["detractors"])
)
if col_cancel.button("Cancel"):
    st.session_state["cancel_symptomize"] = True

if run_clicked:
    st.session_state["cancel_symptomize"] = False
    rows = [(int(i), str(df.loc[i, text_col])) for i in todo_idx]
    sugg_all: Dict[int, dict] = {}
    p = st.progress(0.0, text="Starting…")
    total = max(1, (len(rows)+batch_size-1)//batch_size)
    done = 0
    for s in range(0, len(rows), batch_size):
        if st.session_state["cancel_symptomize"]:
            st.warning("Cancelled. Partial suggestions retained below.")
            break
        batch = rows[s:s+batch_size]
        out = call_llm(batch, symptom_lists["delighters"], symptom_lists["detractors"])
        sugg_all.update(out)
        done += 1
        p.progress(min(1.0, done/total), text=f"Batch {done}/{total}")
        time.sleep(0.1)
    st.session_state["sympto_suggestions"] = sugg_all
    if sugg_all:
        st.success(f"Suggestions ready for {len(sugg_all):,} review(s). Review and apply below.")
    else:
        st.info("No suggestions were produced.")

# =========================
# Review Suggestions UI
# =========================
sug = st.session_state["sympto_suggestions"]
if sug:
    st.markdown("### ✅ Review Suggestions")
    # Aggregate new symptoms
    proposed_new_dl, proposed_new_dt = [], []
    for v in sug.values():
        proposed_new_dl.extend(v.get("new_delighters", []))
        proposed_new_dt.extend(v.get("new_detractors", []))
    # unique
    def _uniq(lst):
        seen=set(); out=[]
        for x in lst:
            x=x.strip()
            if x and x not in seen:
                seen.add(x); out.append(x)
        return out
    proposed_new_dl = _uniq(proposed_new_dl)
    proposed_new_dt = _uniq(proposed_new_dt)

    with st.expander("🔎 Proposed NEW symptoms (not in Symptoms sheet) — approve to add", expanded=bool(proposed_new_dl or proposed_new_dt)):
        col_a, col_b = st.columns(2)
        approved_dl = col_a.multiselect("Approve new **Delighters** to add", proposed_new_dl, default=[])
        approved_dt = col_b.multiselect("Approve new **Detractors** to add", proposed_new_dt, default=[])

    # Choose rows to apply
    ids = sorted(sug.keys())
    st.caption("Select the rows you want to apply. Only Symptom **1–10** (detractors) and **11–20** (delighters) will be written.")
    to_apply = st.multiselect("Select review row IDs to apply", ids, default=ids[: min(50, len(ids))])

    # Preview table
    prev_rows = []
    for rid in to_apply[:200]:  # prevent huge table
        v = sug[rid]
        prev_rows.append({
            "Row ID": rid,
            "Detractors (≤10)": "; ".join(v.get("detractors", []))[:500],
            "Delighters (≤10)": "; ".join(v.get("delighters", []))[:500],
        })
    if prev_rows:
        st.dataframe(pd.DataFrame(prev_rows), use_container_width=True, hide_index=True)

    # Apply / Undo
    def apply_suggestions_to_df(sel_ids: List[int]) -> int:
        st.session_state["df_backup"] = df.copy(deep=True)
        batch_id = f"batch-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        who = os.getenv("USER") or "analyst"
        changed = 0
        for rid in sel_ids:
            rec = sug.get(rid, {})
            det = rec.get("detractors", [])
            dlt = rec.get("delighters", [])

            before = {c: _strip(df.at[rid, c]) for c in SYMPTOM_COLS}

            # write detractors (1..10)
            for i in range(10):
                col = DET_COLS[i]
                newv = det[i] if i < len(det) else ""
                if fill_only_empties and _strip(df.at[rid, col]):
                    continue
                df.at[rid, col] = newv
            # write delighters (11..20)
            for i in range(10):
                col = DEL_COLS[i]
                newv = dlt[i] if i < len(dlt) else ""
                if fill_only_empties and _strip(df.at[rid, col]):
                    continue
                df.at[rid, col] = newv

            after = {c: _strip(df.at[rid, c]) for c in SYMPTOM_COLS}
            if before != after:
                changed += 1
                st.session_state["change_log"].append({
                    "Batch ID": batch_id,
                    "When (UTC)": datetime.utcnow().isoformat(timespec="seconds"),
                    "Who": who,
                    "Row ID": rid,
                    "Before": json.dumps(before, ensure_ascii=False),
                    "After": json.dumps(after, ensure_ascii=False),
                })
        # Add approved new symptoms to master lists (in-memory; also exported)
        if approved_dl:
            for x in approved_dl:
                if x not in symptom_lists["delighters"]:
                    symptom_lists["delighters"].append(x)
        if approved_dt:
            for x in approved_dt:
                if x not in symptom_lists["detractors"]:
                    symptom_lists["detractors"].append(x)
        return changed

    c1, c2 = st.columns([1,1])
    if c1.button("✅ Apply Selected Suggestions", type="primary", disabled=not to_apply):
        changed = apply_suggestions_to_df(to_apply)
        st.success(f"Applied to {changed} row(s). You can download the updated workbook below.")

    if c2.button("↩️ Undo last apply"):
        if st.session_state["df_backup"] is not None:
            df[:] = st.session_state["df_backup"]
            st.session_state["df_backup"] = None
            st.info("Restored to the previous state.")
        else:
            st.warning("Nothing to undo.")

# =========================
# Quick metrics & chart
# =========================
st.markdown("---")
st.markdown("## ⭐ Star Rating Metrics")

def pct_12(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float((s <= 2).mean() * 100) if not s.empty else 0.0

cnt = len(df)
avg = float(pd.to_numeric(df.get("Star Rating"), errors="coerce").mean()) if "Star Rating" in df.columns else 0.0
pct = pct_12(df.get("Star Rating")) if "Star Rating" in df.columns else 0.0
html_cards = (
    '<div class="metrics-grid">'
    f'{show_metric_card("All Reviews", cnt, avg, pct)}'
    '</div>'
)
st.markdown(html_cards, unsafe_allow_html=True)

if "Star Rating" in df.columns:
    vc = pd.to_numeric(df["Star Rating"], errors="coerce").dropna().value_counts().sort_index()
    labels = [f"{int(k)} stars" for k in vc.index]
    fig = go.Figure(go.Bar(x=vc.values, y=labels, orientation="h",
                           text=[f"{int(v)}" for v in vc.values], textposition="auto",
                           marker=dict(color=["#EF4444", "#F59E0B", "#EAB308", "#10B981", "#22C55E"])))
    fig.update_layout(title="<b>Star Rating Distribution</b>", template="plotly_white",
                      xaxis=dict(title="Count", showgrid=False), yaxis=dict(showgrid=False),
                      margin=dict(l=40,r=40,t=45,b=40))
    st.plotly_chart(fig, use_container_width=True)

# =========================
# Export (Reviews + Symptoms + Change Log)
# =========================
st.markdown("---")
st.markdown("## ⬇️ Export")

def make_download_bytes(df_reviews: pd.DataFrame, symptoms: dict, reviews_sheet: str, symptoms_sheet: str | None):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df_reviews.to_excel(w, index=False, sheet_name=reviews_sheet or "Reviews")
        # rebuild a normalized Symptoms sheet
        max_len = max(len(symptoms["delighters"]), len(symptoms["detractors"]))
        dl = symptoms["delighters"] + [""] * (max_len - len(symptoms["delighters"]))
        dt = symptoms["detractors"] + [""] * (max_len - len(symptoms["detractors"]))
        pd.DataFrame({"Delighters": dl, "Detractors": dt}).to_excel(
            w, index=False, sheet_name=(symptoms_sheet or "Symptoms")
        )
        if st.session_state["change_log"]:
            pd.DataFrame(st.session_state["change_log"]).to_excel(w, index=False, sheet_name="Change Log")
    return buf.getvalue()

bytes_xlsx = make_download_bytes(df, symptom_lists, REVIEWS_SHEET, SYMPTOMS_SHEET)
st.download_button(
    "Download updated workbook (XLSX)",
    bytes_xlsx,
    file_name="starwalk_updated.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# =========================
# End
# =========================

