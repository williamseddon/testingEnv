# Star Walk Analysis Dashboard — full updated app with AI Symptomization
# Streamlit 1.38+

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import time
import json
import re
import html as _html
import textwrap
import os
from io import BytesIO
from datetime import datetime, timedelta
from streamlit.components.v1 import html as st_html

# optional deps
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    OpenAI = None
    _HAS_OPENAI = False

try:
    import faiss  # optional acceleration for retrieval
    _HAS_FAISS = True
except Exception:
    _HAS_FAISS = False

try:
    import openpyxl
    _HAS_OPENPYXL = True
except Exception:
    _HAS_OPENPYXL = False

# ----------------------------
# Config & light-mode forcing
# ----------------------------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# Force light theme regardless of system prefs
st_html(
    """
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
  new MutationObserver(setLight).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
})();
</script>
""",
    height=0,
)

# ----------------------------
# Global CSS (light-first)
# ----------------------------
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
    color: var(--text);
    font-family: "Helvetica Neue", Helvetica, Arial, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Liberation Sans", sans-serif;
  }
  .block-container { padding-top:.9rem; padding-bottom:1.2rem; }
  mark{ background:#fff2a8; padding:0 .2em; border-radius:3px; }

  .hero-wrap{position:relative;overflow:hidden;border-radius:14px;min-height:150px;margin:.25rem 0 1rem 0;
    box-shadow:0 0 0 1.5px var(--border-strong),0 8px 14px rgba(15,23,42,.06);
    background:linear-gradient(90deg, var(--bg-card) 0% 55%, transparent 55% 100%);} 
  #hero-canvas{position:absolute;left:0;top:0;width:55%;height:100%;display:block}
  .hero-inner{position:absolute;inset:0;display:flex;align-items:center;justify-content:space-between;padding:0 18px;color:var(--text)}
  .hero-title{font-size:clamp(22px,3.3vw,42px);font-weight:800;margin:0}
  .hero-sub{margin:4px 0 0 0;color:var(--muted);font-size:clamp(12px,1.1vw,16px)}
  .hero-right{display:flex;align-items:center;justify-content:flex-end;width:40%}
  .sn-logo{height:48px;width:auto;display:block}

  .metrics-grid { display:grid; grid-template-columns:repeat(3,minmax(260px,1fr)); gap:17px; }
  @media (max-width:1100px){ .metrics-grid { grid-template-columns:1fr; } }
  .metric-card{ background:var(--bg-card); border-radius:14px; padding:16px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); color:var(--text); }
  .metric-card h4{ margin:.2rem 0 .7rem 0; font-size:1.05rem; color:var(--text); }
  .metric-row{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
  .metric-box{ background:var(--bg-tile); border:1.6px solid var(--border); border-radius:12px; padding:12px; text-align:center; color:var(--text); }
  .metric-label{ color:var(--muted); font-size:.85rem; }
  .metric-kpi{ font-weight:800; font-size:1.8rem; letter-spacing:-0.01em; margin-top:2px; color:var(--text); }

  .review-card{ background:var(--bg-card); border-radius:12px; padding:16px; margin:16px 0 24px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); color:var(--text); }
  .review-card p{ margin:.25rem 0; line-height:1.5; }
  .review-box{ background:var(--bg-tile); border:1px solid var(--border); border-radius:8px; padding:10px; font-size:.95rem; }

  [data-testid="stPlotlyChart"]{ margin-top:18px !important; margin-bottom:30px !important; }
</style>
"""

st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# ----------------------------
# Helpers
# ----------------------------
NO_TEMP_MODELS = {"gpt-5", "gpt-5-chat-latest"}

def model_supports_temperature(model_id: str) -> bool:
    return model_id not in NO_TEMP_MODELS and not str(model_id).startswith("gpt-5")

def esc(x) -> str:
    return _html.escape("" if pd.isna(x) else str(x))

def clean_text(x, keep_na: bool=False):
    if pd.isna(x):
        return pd.NA if keep_na else ""
    s = str(x).strip()
    # normalize a few common mojibake bits
    for bad, good in {"â€™":"'", "â€˜":"‘", "â€œ":"“", "â€\x9d":"”", "â€“":"–", "â€”":"—", "Â":""}.items():
        s = s.replace(bad, good)
    if not s or s.upper() in {"<NA>","NA","N/A","NULL","NONE"}:
        return pd.NA if keep_na else ""
    return s

SYM_COLUMNS = [f"Symptom {i}" for i in range(1,21)]

SYSTEM_INSTR = (
    "You are SharkNinja's Star Walk Review Symptomizer. "
    "For a given review text, select up to 10 delighters and up to 10 detractors, strictly from the provided lists. "
    "If none apply, return an empty list. If you see a very clear new symptom not present in the lists, propose it under new_candidates. "
    "Output strict JSON with keys: delighters[], detractors[], notes (string), new_candidates:{delighters[], detractors[]}. "
    "Do not exceed 10 items per list. Use the review’s wording to pick only the most relevant items."
)

_json_block = re.compile(r"\{[\s\S]*\}")

def build_prompt(review: str, delighters: list[str], detractors: list[str]) -> str:
    return (
        "Review (verbatim):\n\n" + review.strip() + "\n\n" +
        "Delighters catalog (choose from):\n- " + "\n- ".join(delighters) + "\n\n" +
        "Detractors catalog (choose from):\n- " + "\n- ".join(detractors) + "\n\n" +
        "Return JSON only with keys: delighters, detractors, notes, new_candidates (which itself has delighters and detractors). Max 10 each."
    )


def parse_json_safe(text: str) -> dict:
    if not text:
        return {}
    # Try straight JSON first
    try:
        return json.loads(text)
    except Exception:
        pass
    # Try extract the first {...} block
    m = _json_block.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}
    return {}

# ----------------------------
# Hero (with transparent right side + SN logo)
# ----------------------------
SN_LOGO = "https://upload.wikimedia.org/wikipedia/commons/e/ea/SharkNinja_logo.svg"

def render_hero():
    st_html(
        f"""
        <div class=\"hero-wrap\"> 
          <canvas id=\"hero-canvas\"></canvas>
          <div class=\"hero-inner\">
            <div>
              <h1 class=\"hero-title\">Star Walk Analysis Dashboard</h1>
              <div class=\"hero-sub\">Insights, trends, and ratings — fast.</div>
            </div>
            <div class=\"hero-right\"><img class=\"sn-logo\" src=\"{SN_LOGO}\" alt=\"SharkNinja logo\"></div>
          </div>
        </div>
        <script>
        (function(){
          const c = document.getElementById('hero-canvas');
          if(!c) return;
          const ctx = c.getContext('2d',{alpha:true});
          const DPR = window.devicePixelRatio||1; let w=0,h=0;
          function resize(){ const r=c.getBoundingClientRect(); w=Math.max(300,r.width|0); h=Math.max(120,r.height|0); c.width=w*DPR; c.height=h*DPR; ctx.setTransform(DPR,0,0,DPR,0,0);} 
          window.addEventListener('resize',resize,{passive:true}); resize();
          const N=140; let stars=Array.from({length:N},()=>({x:Math.random()*w,y:Math.random()*h,r:0.6+Math.random()*1.4,s:0.3+Math.random()*0.9}));
          function tick(){ ctx.clearRect(0,0,w,h); for(const s of stars){ ctx.beginPath(); ctx.arc(s.x,s.y,s.r,0,Math.PI*2); ctx.fillStyle='rgba(255,200,50,.9)'; ctx.fill(); s.x+=0.12*s.s; if(s.x>w) s.x=0; } requestAnimationFrame(tick);} tick();
        })();
        </script>
        """,
        height=160,
    )

render_hero()

# ----------------------------
# File upload & loading
# ----------------------------
st.markdown("### 📁 Upload Star Walk Excel (.xlsx)")
uploaded = st.file_uploader("Upload your Excel file", type=["xlsx"], help="Must include sheet 'Star Walk scrubbed verbatims'. Optional sheet 'Symptoms' with delighters/detractors.")
if not uploaded:
    st.info("Please upload an Excel file to continue.")
    st.stop()

# keep original bytes for formatting-preserving export later
orig_bytes = uploaded.getvalue()

# main data sheet
try:
    df = pd.read_excel(uploaded, sheet_name="Star Walk scrubbed verbatims")
except Exception:
    df = pd.read_excel(uploaded)  # fallback first sheet

# normalize columns used by UI
if "Star Rating" in df.columns:
    df["Star Rating"] = pd.to_numeric(df["Star Rating"], errors="coerce")
if "Review Date" in df.columns:
    df["Review Date"] = pd.to_datetime(df["Review Date"], errors="coerce")
if "Verbatim" in df.columns:
    df["Verbatim"] = df["Verbatim"].map(clean_text)

# ensure Symptom cols exist (don't create new, just detect existing 1..20)
sym_cols_present = [c for c in SYM_COLUMNS if c in df.columns]

# ----------------------------
# Load Symptoms catalog from sheet (Delighters/Detractors)
# ----------------------------
_delighters, _detractors = [], []
try:
    sym = pd.read_excel(BytesIO(orig_bytes), sheet_name="Symptoms")
    # try detect columns that contain 'delight' and 'detract'
    cand_dels = [c for c in sym.columns if re.search(r"delight", str(c), re.I)]
    cand_dets = [c for c in sym.columns if re.search(r"detract", str(c), re.I)]
    if cand_dels:
        _delighters = [clean_text(x) for x in sym[cand_dels[0]].dropna().astype(str).map(str.strip) if clean_text(x)]
    if cand_dets:
        _detractors = [clean_text(x) for x in sym[cand_dets[0]].dropna().astype(str).map(str.strip) if clean_text(x)]
    # fallback: first two columns as lists
    if not _delighters and sym.shape[1] >= 1:
        _delighters = [clean_text(x) for x in sym.iloc[:,0].dropna().astype(str).map(str.strip) if clean_text(x)]
    if not _detractors and sym.shape[1] >= 2:
        _detractors = [clean_text(x) for x in sym.iloc[:,1].dropna().astype(str).map(str.strip) if clean_text(x)]
except Exception:
    pass

# de-dup & cap whitespace
_delighters = sorted(list(dict.fromkeys([x for x in _delighters if x])))
_detractors = sorted(list(dict.fromkeys([x for x in _detractors if x])))

if not _delighters and not _detractors:
    st.warning("Couldn't find a 'Symptoms' sheet. AI will still work but cannot validate against a catalog.")

# ----------------------------
# Unsymptomized detection & length stats
# ----------------------------
if sym_cols_present:
    unsym_mask = df[sym_cols_present].apply(lambda row: all((str(v).strip()=="" or pd.isna(v)) for v in row), axis=1)
else:
    unsym_mask = pd.Series([True]*len(df), index=df.index)  # no columns present → treat all as unsymptomized for AI

unsym_count = int(unsym_mask.sum())

# review length statistics
verblens = df.get("Verbatim", pd.Series(dtype=str)).fillna("").astype(str).map(len)
if not verblens.empty:
    q1, q3 = np.percentile(verblens, [25, 75])
    iqr = float(q3-q1)
else:
    q1=q3=iqr=0.0

# ----------------------------
# Symptomization control panel
# ----------------------------
st.markdown("---")
left, mid, right = st.columns([1.5,1,1.6])
with left:
    st.subheader("🤖 AI Symptomization (beta)")
    st.write(f"**{unsym_count}** of **{len(df)}** reviews have empty Symptom 1–20.")
    st.caption(f"Review length IQR: Q1 = {q1:.0f} chars • Q3 = {q3:.0f} • IQR = {iqr:.0f}")
with mid:
    model_choices = [
        ("Fast & economical – 4o-mini", "gpt-4o-mini"),
        ("Balanced – 4o", "gpt-4o"),
        ("Advanced – 4.1", "gpt-4.1"),
        ("Most advanced – GPT-5", "gpt-5"),
        ("GPT-5 (Chat latest)", "gpt-5-chat-latest"),
    ]
    labels = [l for l,_ in model_choices]
    default_model = st.session_state.get("llm_model", "gpt-4o-mini")
    idx = next((i for i,(_,m) in enumerate(model_choices) if m==default_model), 0)
    sel_label = st.selectbox("Model", labels, index=idx, help="Temperature is auto-disabled for GPT‑5 family.")
    st.session_state["llm_model"] = dict(model_choices)[sel_label]
with right:
    batch_size = st.slider(
        "Batch size (1–20)", 1, min(20, unsym_count if unsym_count>0 else 1),
        value=min(10, unsym_count if unsym_count>0 else 1),
        help="How many unsymptomized reviews to process this run.")
    pick_mode = st.selectbox("Pick reviews by", ["Oldest first","Random sample","Longest reviews first"]) 
    exclude_short = st.checkbox("Exclude very short reviews", value=True, help="Skip reviews under N characters")
    min_len = st.number_input("Min chars (if excluding)", min_value=0, max_value=2000, value=30, step=5)

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
can_run_ai = _HAS_OPENAI and bool(api_key)
if not _HAS_OPENAI:
    st.info("Install the `openai` package and set OPENAI_API_KEY to enable AI features.")
elif not api_key:
    st.info("Set OPENAI_API_KEY in environment or .streamlit/secrets.toml to enable AI features.")

# ----------------------------
# Build candidate list for this run
# ----------------------------
unsym_idx = df.index[unsym_mask].tolist()
if exclude_short and "Verbatim" in df.columns:
    lens = df.loc[unsym_idx, "Verbatim"].fillna("").astype(str).map(len)
    unsym_idx = lens[lens >= int(min_len)].index.tolist()

if pick_mode == "Random sample" and unsym_idx:
    choose = list(np.random.choice(unsym_idx, size=min(batch_size, len(unsym_idx)), replace=False))
elif pick_mode == "Longest reviews first" and unsym_idx and "Verbatim" in df.columns:
    tmp = df.loc[unsym_idx, "Verbatim"].fillna("").astype(str).map(len)
    choose = tmp.sort_values(ascending=False).head(batch_size).index.tolist()
else:
    choose = unsym_idx[:batch_size]

st.markdown("---")
btn = st.button(
    f"🚀 Symptomize {len(choose)} review{'s' if len(choose)!=1 else ''}",
    disabled=(len(choose)==0 or not can_run_ai)
)

# container for results state
if "symptom_suggestions" not in st.session_state:
    st.session_state["symptom_suggestions"] = []
if "approved_new_symptoms" not in st.session_state:
    st.session_state["approved_new_symptoms"] = {"delighters": set(), "detractors": set()}

# ----------------------------
# Symptomization run
# ----------------------------

def run_symptomize(indices: list[int], df_in: pd.DataFrame, delighters: list[str], detractors: list[str], model: str, api_key: str):
    client = OpenAI(api_key=api_key)
    results = []

    # progress UI + throughput-based ETA
    prog = st.progress(0.0)
    eta_box = st.empty()

    total_chars = int(df_in.loc[indices, "Verbatim"].fillna("").astype(str).map(len).sum()) if "Verbatim" in df_in.columns else 0
    processed_chars = 0
    start = time.time()

    for i, ridx in enumerate(indices, start=1):
        text = str(df_in.at[ridx, "Verbatim"]) if "Verbatim" in df_in.columns else ""
        prompt = build_prompt(text, delighters, detractors)
        req = {"model": model, "messages": [{"role":"system","content": SYSTEM_INSTR}, {"role":"user","content": prompt}]}
        if model_supports_temperature(model):
            req["temperature"] = 0.2

        out = {"delighters":[],"detractors":[],"notes":"","new_candidates":{"delighters":[],"detractors":[]}}
        err = None
        try:
            resp = client.chat.completions.create(**req)
            content = (resp.choices[0].message.content or "").strip()
            parsed = parse_json_safe(content)
            if parsed:
                out = {
                    "delighters": [clean_text(x) for x in (parsed.get("delighters") or []) if clean_text(x)][:10],
                    "detractors": [clean_text(x) for x in (parsed.get("detractors") or []) if clean_text(x)][:10],
                    "notes": (parsed.get("notes") or "")[:600],
                    "new_candidates": {
                        "delighters": [clean_text(x) for x in (parsed.get("new_candidates",{}).get("delighters") or []) if clean_text(x)],
                        "detractors": [clean_text(x) for x in (parsed.get("new_candidates",{}).get("detractors") or []) if clean_text(x)],
                    },
                }
        except Exception as e:
            err = str(e)

        results.append({
            "row_index": int(ridx),
            "excel_row": int(ridx)+2,  # +2 assuming 1-based header row
            "review": text,
            "delighters": out["delighters"],
            "detractors": out["detractors"],
            "notes": out.get("notes",""),
            "new_delighters": out["new_candidates"].get("delighters",[]),
            "new_detractors": out["new_candidates"].get("detractors",[]),
            "error": err,
        })

        # progress + ETA
        processed_chars += len(text)
        frac = i/len(indices)
        prog.progress(frac)
        elapsed = max(0.001, time.time()-start)
        cps = processed_chars/elapsed  # chars per sec
        remaining_chars = max(0, total_chars-processed_chars)
        eta_s = (remaining_chars/cps) if cps>0 else 0
        eta_box.info(f"Processed {i}/{len(indices)} • ~{cps:,.0f} chars/s • ETA ~{eta_s:,.0f}s")

    eta_box.success(f"Completed {len(indices)} reviews in {time.time()-start:.1f}s")
    return results

if btn and choose and can_run_ai:
    st.session_state["symptom_suggestions"] = run_symptomize(choose, df, _delighters or [], _detractors or [], st.session_state["llm_model"], api_key)

# ----------------------------
# Suggestions review UI (with full review text)
# ----------------------------

def chips(items, css):
    if not items:
        return "<i>None</i>"
    return '<div style="display:flex;flex-wrap:wrap;gap:8px">' + ''.join([f"<span class='badge {css}'>{_html.escape(i)}</span>" for i in items]) + "</div>"

sugs = st.session_state.get("symptom_suggestions", [])
if sugs:
    st.markdown("---")
    st.subheader("Review & approve suggestions")

    approved_rows = []
    new_del_cands, new_det_cands = set(), set()

    for j, item in enumerate(sugs, start=1):
        with st.expander(f"Row {item['excel_row']} • {len(item['delighters'])} delighters / {len(item['detractors'])} detractors", expanded=False):
            st.markdown(f"<div class='review-box'><b>Full review:</b><br>{esc(item['review'])}</div>", unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            with c1:
                sel_del = st.multiselect(
                    f"Delighters (pick up to 10) — row {item['excel_row']}",
                    options=sorted((_delighters or []) + item.get("new_delighters", [])),
                    default=item["delighters"],
                    key=f"sel_del_{j}"
                )[:10]
                st.markdown(chips(sel_del, "pos"), unsafe_allow_html=True)
            with c2:
                sel_det = st.multiselect(
                    f"Detractors (pick up to 10) — row {item['excel_row']}",
                    options=sorted((_detractors or []) + item.get("new_detractors", [])),
                    default=item["detractors"],
                    key=f"sel_det_{j}"
                )[:10]
                st.markdown(chips(sel_det, "neg"), unsafe_allow_html=True)

            if item.get("new_delighters"): new_del_cands.update(item["new_delighters"]) 
            if item.get("new_detractors"): new_det_cands.update(item["new_detractors"]) 

            approved_rows.append({
                "row_index": item["row_index"],
                "excel_row": item["excel_row"],
                "delighters": sel_del,
                "detractors": sel_det,
            })

    st.markdown("---")
    st.subheader("New symptom candidates (approve to add to catalog)")
    cA, cB = st.columns(2)
    with cA:
        apr_del = st.multiselect("Approve new **Delighters**", sorted(list(new_del_cands)))
    with cB:
        apr_det = st.multiselect("Approve new **Detractors**", sorted(list(new_det_cands)))

    # buttons
    cbtn1, cbtn2, cbtn3 = st.columns([1,1,1])
    with cbtn1:
        apply_btn = st.button("✅ Apply approved to DataFrame (preview)")
    with cbtn2:
        clear_btn = st.button("🗑 Reset suggestions")
    with cbtn3:
        dl_btn = st.button("⬇️ Download updated Excel (preserve formatting)")

    if clear_btn:
        st.session_state["symptom_suggestions"] = []
        st.experimental_rerun()

    # Apply to in-memory DataFrame only (preview)
    if apply_btn:
        # apply selected items into Symptom 1..20 (up to 10 and 10)
        for row in approved_rows:
            vals = (row["delighters"] + row["detractors"])[:20]
            for idx, col in enumerate(SYM_COLUMNS[:len(vals)], start=0):
                if col in df.columns:
                    df.at[row["row_index"], col] = vals[idx]
        st.success(f"Applied approved symptoms to {len(approved_rows)} rows in memory. Use download to export.")

        # update approved new catalog (in session only until exported)
        st.session_state["approved_new_symptoms"] = {
            "delighters": set(apr_del),
            "detractors": set(apr_det),
        }

    # Export with formatting preserved
    if dl_btn:
        if not _HAS_OPENPYXL:
            st.error("openpyxl not installed; cannot export while preserving formatting.")
        else:
            try:
                bio = BytesIO(orig_bytes)
                wb = openpyxl.load_workbook(bio)
                # 1) write symptoms back to main sheet
                # locate the data sheet
                sheetnames = wb.sheetnames
                if "Star Walk scrubbed verbatims" in sheetnames:
                    ws = wb["Star Walk scrubbed verbatims"]
                else:
                    ws = wb[sheetnames[0]]

                # find header row mapping
                header_map = {}
                for cell in next(ws.iter_rows(min_row=1, max_row=1)):
                    key = str(cell.value).strip() if cell.value is not None else ""
                    header_map[key.lower()] = cell.column  # 1-based index
                # map Symptom 1..20 to columns that exist in sheet
                sym_col_idx = []
                for colname in SYM_COLUMNS:
                    found = header_map.get(colname.lower())
                    if found:
                        sym_col_idx.append((colname, found))

                # apply from current df values
                for row in approved_rows:
                    excel_r = int(row["row_index"]) + 2  # header at row 1
                    for k, (colname, col_idx) in enumerate(sym_col_idx):
                        val = df.at[row["row_index"], colname] if colname in df.columns else None
                        ws.cell(row=excel_r, column=col_idx, value=(None if (pd.isna(val) or val=="") else str(val)))

                # 2) append approved new symptoms into Symptoms sheet if present
                if "Symptoms" in wb.sheetnames:
                    ws2 = wb["Symptoms"]
                    # detect columns for delighters/detractors
                    hdr2 = {str(c.value).strip().lower(): c.column for c in next(ws2.iter_rows(min_row=1, max_row=1)) if c.value is not None}
                    dels_col = next((hdr2[k] for k in hdr2.keys() if "delight" in k), 1)
                    dets_col = next((hdr2[k] for k in hdr2.keys() if "detract" in k), 2)

                    # current sets in sheet to avoid duplicates
                    cur_dels = set()
                    cur_dets = set()
                    for r in ws2.iter_rows(min_row=2, values_only=True):
                        if r and r[0]:
                            try:
                                v = str(r[dels_col-1]).strip() if dels_col-1 < len(r) else None
                            except Exception:
                                v = None
                            if v:
                                cur_dels.add(v)
                        if r and len(r)>1:
                            try:
                                v = str(r[dets_col-1]).strip()
                            except Exception:
                                v = None
                            if v:
                                cur_dets.add(v)

                    add_dels = [x for x in st.session_state["approved_new_symptoms"]["delighters"] if x not in cur_dels]
                    add_dets = [x for x in st.session_state["approved_new_symptoms"]["detractors"] if x not in cur_dets]

                    # append each list to the bottom of its column
                    def append_to_col(ws, col_idx, items):
                        if not items: return
                        # find first empty row at the bottom
                        r = ws.max_row
                        while r >= 2 and (ws.cell(row=r, column=col_idx).value in (None, "")):
                            r -= 1
                        start = r+1
                        for i, val in enumerate(items):
                            ws.cell(row=start+i, column=col_idx, value=val)

                    append_to_col(ws2, dels_col, add_dels)
                    append_to_col(ws2, dets_col, add_dets)

                out = BytesIO()
                wb.save(out)
                out.seek(0)
                st.download_button(
                    "💾 Download updated workbook (.xlsx)",
                    data=out.getvalue(),
                    file_name="starwalk_symptomized.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                st.success("Workbook prepared. Download above.")
            except Exception as e:
                st.error(f"Export failed: {e}")

# ----------------------------
# Metrics & charts (unchanged core)
# ----------------------------
st.markdown("---")
st.markdown("## ⭐ Star Rating Metrics")
st.caption("All metrics reflect the current in-memory data (after any applied approvals).")

# simple split by Seeded if present
if "Seeded" in df.columns:
    seed_mask = df["Seeded"].astype(str).str.upper().eq("YES")
else:
    seed_mask = pd.Series(False, index=df.index)


def pct_12(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float((s <= 2).mean()*100) if not s.empty else 0.0

def section_stats(sub: pd.DataFrame) -> tuple[int,float,float]:
    cnt = len(sub)
    if cnt==0 or "Star Rating" not in sub.columns:
        return 0,0.0,0.0
    avg = float(pd.to_numeric(sub["Star Rating"], errors="coerce").mean())
    pct = pct_12(sub["Star Rating"])
    return cnt, avg, pct

all_cnt, all_avg, all_low = section_stats(df)
org = df[~seed_mask]
seed = df[seed_mask]
org_cnt, org_avg, org_low = section_stats(org)
seed_cnt, seed_avg, seed_low = section_stats(seed)


def card_html(title, count, avg, pct):
    return textwrap.dedent(f"""
    <div class=\"metric-card\"> 
      <h4>{_html.escape(title)}</h4>
      <div class=\"metric-row\"> 
        <div class=\"metric-box\"> 
          <div class=\"metric-label\">Count</div>
          <div class=\"metric-kpi\">{count:,}</div>
        </div>
        <div class=\"metric-box\"> 
          <div class=\"metric-label\">Avg ★</div>
          <div class=\"metric-kpi\">{avg:.1f}</div>
        </div>
        <div class=\"metric-box\"> 
          <div class=\"metric-label\">% 1–2★</div>
          <div class=\"metric-kpi\">{pct:.1f}%</div>
        </div>
      </div>
    </div>
    """).strip()

st.markdown(
    (
        '<div class=\"metrics-grid\">' +
        f'{card_html("All Reviews", all_cnt, all_avg, all_low)}' +
        f'{card_html("Organic (non-Seeded)", org_cnt, org_avg, org_low)}' +
        f'{card_html("Seeded", seed_cnt, seed_avg, seed_low)}' +
        '</div>'
    ),
    unsafe_allow_html=True,
)

# distribution
if "Star Rating" in df.columns:
    star_counts = pd.to_numeric(df["Star Rating"], errors="coerce").dropna().value_counts().sort_index()
else:
    star_counts = pd.Series([], dtype="int")

labels = [f"{int(k)} stars" for k in star_counts.index]
values = star_counts.values

fig = go.Figure(go.Bar(
    x=values, y=labels, orientation="h",
    text=[f"{int(v)} reviews" for v in values], textposition="auto",
    marker=dict(color=["#EF4444", "#F59E0B", "#EAB308", "#10B981", "#22C55E"])
))
fig.update_layout(title="<b>Star Rating Distribution</b>", xaxis_title="Number of Reviews", yaxis_title="Star Ratings", template="plotly_white", plot_bgcolor="white")
st.plotly_chart(fig, use_container_width=True)

st.markdown("---")

# ----------------------------
# All Reviews (paged)
# ----------------------------
st.markdown("### 📝 All Reviews")

if "review_page" not in st.session_state: st.session_state["review_page"] = 0
rpp = st.session_state.get("reviews_per_page", 10)
rpp = st.selectbox("Reviews per page", [10,20,50,100], index=[10,20,50,100].index(rpp))
st.session_state["reviews_per_page"] = rpp

N = len(df)
P = max(1, (N + rpp - 1)//rpp)
page = min(max(st.session_state["review_page"], 0), P-1)
start, end = page*rpp, min(N, (page+1)*rpp)
sub = df.iloc[start:end]

if sub.empty:
    st.warning("No reviews to show.")
else:
    for _, row in sub.iterrows():
        date_val = row.get("Review Date", pd.NaT)
        date_str = "-" if pd.isna(date_val) else pd.to_datetime(date_val).strftime("%Y-%m-%d")
        star_val = row.get("Star Rating", "")
        try: star_int = int(star_val) if pd.notna(star_val) else 0
        except: star_int = 0
        delis = [row.get(c) for c in SYM_COLUMNS[10:] if c in df.columns and pd.notna(row.get(c)) and str(row.get(c)).strip()]
        detrs = [row.get(c) for c in SYM_COLUMNS[:10] if c in df.columns and pd.notna(row.get(c)) and str(row.get(c)).strip()]

        st.markdown(
            f"""
            <div class='review-card'>
              <p><strong>Source:</strong> {esc(row.get('Source'))} | <strong>Model:</strong> {esc(row.get('Model (SKU)'))}</p>
              <p><strong>Country:</strong> {esc(row.get('Country'))} | <strong>Date:</strong> {esc(date_str)}</p>
              <p><strong>Rating:</strong> {'⭐'*star_int} ({esc(star_val)}/5)</p>
              <p><strong>Review:</strong> {esc(str(row.get('Verbatim') or ''))}</p>
              <div><strong>Delighter Symptoms:</strong> {chips(delis, 'pos')}</div>
              <div><strong>Detractor Symptoms:</strong> {chips(detrs, 'neg')}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

p1, p2, p3, p4, p5 = st.columns([1,1,2,1,1])
with p1:
    if st.button("⏮ First", disabled=(page==0)): st.session_state["review_page"]=0; st.experimental_rerun()
with p2:
    if st.button("⬅ Prev", disabled=(page==0)): st.session_state["review_page"]=max(0,page-1); st.experimental_rerun()
with p3:
    st.markdown(f"<div style='text-align:center;font-weight:700;'>Page {page+1} of {P} • Showing {start+1 if N else 0}–{end} of {N}</div>", unsafe_allow_html=True)
with p4:
    if st.button("Next ➡", disabled=(page>=P-1)): st.session_state["review_page"]=min(P-1,page+1); st.experimental_rerun()
with p5:
    if st.button("Last ⏭", disabled=(page>=P-1)): st.session_state["review_page"]=P-1; st.experimental_rerun()
