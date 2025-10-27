# starwalk_ui.py
# Streamlit 1.38+

import io
import os
import re
import json
import time
import math
import textwrap
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit.components.v1 import html as st_html
from openpyxl import load_workbook

warnings.filterwarnings(
    "ignore",
    message="Data Validation extension is not supported and will be removed",
    module="openpyxl",
)

# ---------- Optional text fixer ----------
try:
    from ftfy import fix_text as _ftfy_fix
    _HAS_FTFY = True
except Exception:
    _HAS_FTFY = False
    _ftfy_fix = None

# ---------- OpenAI SDK ----------
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

# ---------- Temp support guard ----------
NO_TEMP_MODELS = {"gpt-5", "gpt-5-chat-latest"}
def model_supports_temperature(model_id: str) -> bool:
    return (model_id not in NO_TEMP_MODELS) and (not model_id.lower().startswith("gpt-5"))

# ---------- Page config ----------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# ---------- Force Light Mode ----------
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

# ---------- Global CSS ----------
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
    color: var(--text);
    font-family: "Helvetica Neue", Helvetica, Arial, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Liberation Sans", sans-serif;
  }
  .block-container { padding-top:.9rem; padding-bottom:1.2rem; }
  mark{ background:#fff2a8; padding:0 .2em; border-radius:3px; }

  .hero-wrap{
    position:relative; overflow:hidden; border-radius:14px; min-height:140px; margin:.25rem 0 .8rem 0;
    box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06);
    background:linear-gradient(90deg, var(--bg-card) 0% 55%, transparent 55% 100%);
  }
  #hero-canvas{ position:absolute; left:0; top:0; width:55%; height:100%; display:block; }
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:0 18px; color:var(--text); }
  .hero-title{ font-size:clamp(22px,3.3vw,42px); font-weight:800; margin:0; }
  .hero-sub{ margin:4px 0 0 0; color:var(--muted); font-size:clamp(12px,1.1vw,16px); }
  .hero-right{ display:flex; align-items:center; justify-content:flex-end; width:40%; }
  .sn-logo{ height:46px; width:auto; display:block; }

  .metric-card{ background:var(--bg-card); border-radius:14px; padding:16px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); }
  .metric-row{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
  .metric-box{ background:var(--bg-tile); border:1.4px solid var(--border); border-radius:12px; padding:10px; text-align:center; }
  .metric-label{ color:var(--muted); font-size:.85rem; }
  .metric-kpi{ font-weight:800; font-size:1.6rem; letter-spacing:-0.01em; margin-top:2px; }
  .metrics-grid{ display:grid; grid-template-columns:repeat(3,minmax(260px,1fr)); gap:16px; }
  @media (max-width:1100px){ .metrics-grid { grid-template-columns:1fr; } }

  .review-card{ background:var(--bg-card); border-radius:12px; padding:14px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); margin:10px 0; }
  .badges{ display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; }
  .badge{ display:inline-flex; align-items:center; gap:.4ch; padding:6px 10px; border-radius:8px; font-weight:600; border:1.4px solid var(--border); background:var(--bg-tile); }
  .badge.pos{ border-color:#7ed9b3; background:#e9fbf3; color:#0b4f3e; }
  .badge.neg{ border-color:#f6b4b4; background:#fff1f2; color:#7f1d1d; }

  [data-testid="stProgress"] .stProgressBar{ height:12px; }
  [data-testid="stAlert"]{ border-radius:10px; }
</style>
""", unsafe_allow_html=True)

# ---------- Hero ----------
def render_hero():
    logo_html = ('<img class="sn-logo" '
                 'src="https://upload.wikimedia.org/wikipedia/commons/e/ea/SharkNinja_logo.svg" '
                 'alt="SharkNinja logo" />')
    st_html(f"""
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
        const ctx = c.getContext('2d', {{alpha:true}});
        const DPR = window.devicePixelRatio || 1;
        let w=0,h=0;
        function resize(){{
          const r = c.getBoundingClientRect();
          w = Math.max(300, r.width|0); h = Math.max(120, r.height|0);
          c.width = w*DPR; c.height = h*DPR; ctx.setTransform(DPR,0,0,DPR,0,0);
        }}
        window.addEventListener('resize', resize, {{passive:true}}); resize();
        const N = 120;
        const stars = Array.from({{length:N}}, ()=>({{x:Math.random()*w, y:Math.random()*h, r:.6+Math.random()*1.4, s:.3+Math.random()*1}}));
        function tick(){{
          ctx.clearRect(0,0,w,h);
          for(const s of stars){{ ctx.beginPath(); ctx.arc(s.x,s.y,s.r,0,Math.PI*2); ctx.fillStyle='rgba(255,200,50,.9)'; ctx.fill(); s.x+=0.12*s.s; if(s.x>w) s.x=0; }}
          requestAnimationFrame(tick);
        }} tick();
      }})();
      </script>
    """, height=160)

render_hero()

# ---------- Small helpers ----------
def clean_text(x: str, keep_na: bool = False) -> str:
    if pd.isna(x): return pd.NA if keep_na else ""
    s = str(x)
    if _HAS_FTFY:
        try: s = _ftfy_fix(s)
        except Exception: pass
    if any(ch in s for ch in ("Ã","Â","â","ï","€","™")):
        try:
            repaired = s.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            if repaired.strip(): s = repaired
        except Exception: pass
    for bad, good in {"â€™":"'", "â€˜":"‘", "â€œ":"“", "â€\x9d":"”", "â€“":"–", "â€”":"—", "Â":""}.items():
        s = s.replace(bad, good)
    s = s.strip()
    if s.upper() in {"<NA>","NA","N/A","NULL","NONE"}:
        return pd.NA if keep_na else ""
    return s

def esc(x) -> str:
    return ("" if pd.isna(x) else str(x)).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def first_existing_sheet(xl, names:list[str]) -> str | None:
    for n in names:
        if n in xl.sheet_names: return n
    return xl.sheet_names[0] if xl.sheet_names else None

SYMPTOM_COLS_DET = [f"Symptom {i}" for i in range(1, 11)]
SYMPTOM_COLS_DEL = [f"Symptom {i}" for i in range(11, 21)]
SYMPTOM_COLS_ALL = SYMPTOM_COLS_DET + SYMPTOM_COLS_DEL

# ---------- Sidebar: upload ----------
st.sidebar.header("Upload Star Walk File")
uploaded = st.sidebar.file_uploader("Choose Excel File", type=["xlsx"], accept_multiple_files=False)
if uploaded:
    st.sidebar.success("File uploaded successfully. Ready to proceed with analysis.")
else:
    st.sidebar.info("Drag & drop a .xlsx file.")
    st.stop()

# ---------- Load workbook ----------
try:
    xl = pd.ExcelFile(uploaded)
    main_sheet_name = first_existing_sheet(xl, ["Star Walk scrubbed verbatims"])
    df = xl.parse(main_sheet_name)
except Exception as e:
    st.error(f"Could not read workbook: {e}")
    st.stop()

# Clean key columns
for c in df.columns:
    if str(c).startswith("Symptom"):
        df[c] = df[c].apply(lambda v: clean_text(v, keep_na=True)).astype("string")

if "Verbatim" in df.columns:
    df["Verbatim"] = df["Verbatim"].astype("string").map(clean_text)
if "Review Date" in df.columns:
    df["Review Date"] = pd.to_datetime(df["Review Date"], errors="coerce")
if "Star Rating" in df.columns:
    df["Star Rating"] = pd.to_numeric(df["Star Rating"], errors="coerce")

# ---------- Read Symptoms sheet (delighters/detractors list) ----------
def read_symptom_lists(xl: pd.ExcelFile):
    cand_sheet = None
    for nm in xl.sheet_names:
        if nm.strip().lower() in {"symptoms","symptom","delighters & detractors","delighters_detractors"}:
            cand_sheet = nm; break
    if cand_sheet is None:
        # fallback: try a sheet literally named "Symptoms" else none
        cand_sheet = "Symptoms" if "Symptoms" in xl.sheet_names else None
    delighters, detractors = [], []
    if cand_sheet:
        s = xl.parse(cand_sheet)
        cols = {str(c).strip().lower(): c for c in s.columns}
        if "delighters" in cols: delighters = [clean_text(x) for x in s[cols["delighters"]].dropna().astype(str).tolist() if clean_text(x)]
        if "detractors" in cols: detractors = [clean_text(x) for x in s[cols["detractors"]].dropna().astype(str).tolist() if clean_text(x)]
        # alt layout support: columns "Type" (Delighter/Detractor) + "Item"
        if not delighters or not detractors:
            if "type" in cols and "item" in cols:
                tcol, icol = cols["type"], cols["item"]
                for _, r in s[[tcol,icol]].dropna().iterrows():
                    item = clean_text(r[icol])
                    if not item: continue
                    t = str(r[tcol]).strip().lower()
                    if "delight" in t: delighters.append(item)
                    elif "detract" in t: detractors.append(item)
    return sorted(set(delighters)), sorted(set(detractors))

delighters_list, detractors_list = read_symptom_lists(xl)
if not delighters_list or not detractors_list:
    st.warning("Symptoms sheet not found or empty. The AI step will still run but new items may be flagged frequently.")

# ---------- Unsymptomized detection ----------
def row_has_no_symptoms(r) -> bool:
    for c in SYMPTOM_COLS_ALL:
        if c in df.columns and (not pd.isna(r.get(c))) and str(r.get(c)).strip():
            return False
    return True

unsym_mask = df.apply(row_has_no_symptoms, axis=1)
unsym_count = int(unsym_mask.sum())
total_reviews = len(df)

# ---------- IQR of review character counts ----------
if "Verbatim" in df.columns:
    lengths = df["Verbatim"].fillna("").astype(str).str.len()
    if not lengths.empty:
        q1 = float(np.percentile(lengths, 25))
        q3 = float(np.percentile(lengths, 75))
        iqr = q3 - q1
        med = float(np.median(lengths))
        fig_box = go.Figure()
        fig_box.add_trace(go.Box(y=lengths, name="Review length (chars)", boxmean=True))
        fig_box.update_layout(title="Review Lengths — IQR", template="plotly_white", height=260, margin=dict(l=40,r=20,t=40,b=30))
        st.plotly_chart(fig_box, use_container_width=True)
        st.caption(f"Median: **{med:.0f}** • Q1: **{q1:.0f}** • Q3: **{q3:.0f}** • IQR: **{iqr:.0f}**")
    else:
        st.info("No review text found to compute IQR.")

# ---------- Bulk symptomize banner ----------
st.markdown("---")
colA, colB, colC = st.columns([1.6,1,1.2])
with colA:
    st.subheader("AI Symptomization (beta)")
    st.write(f"**{unsym_count}** of **{total_reviews}** reviews have empty Symptom 1–20.")
with colB:
    _model_choices = [
        ("Fast & economical – 4o-mini", "gpt-4o-mini"),
        ("Balanced – 4o", "gpt-4o"),
        ("Advanced – 4.1", "gpt-4.1"),
        ("Most advanced – GPT-5", "gpt-5"),
        ("GPT-5 (Chat latest)", "gpt-5-chat-latest"),
    ]
    _labels = [l for l,_ in _model_choices]
    _default = st.session_state.get("llm_model", "gpt-4o-mini")
    _idx = next((i for i,(_,m) in enumerate(_model_choices) if m==_default), 0)
    sel_label = st.selectbox("Model", _labels, index=_idx, help="Temperature is auto-disabled for GPT-5 family.")
    st.session_state["llm_model"] = dict(_model_choices)[sel_label]
with colC:
    # allow user to cap how many to process
    max_to_process = st.number_input("Max to process now", min_value=1, max_value=max(1, unsym_count), value=min(unsym_count, 200), step=1)

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if not _HAS_OPENAI or not api_key:
    st.warning("OpenAI key not configured. Set `OPENAI_API_KEY` in env or `.streamlit/secrets.toml`.")
    can_run_ai = False
else:
    can_run_ai = True

# ---------- Prompt builder ----------
SYSTEM_INSTR = "You are Shark Glossi Review Analyzer. Return ONLY valid JSON that matches the schema."

def build_prompt(review_text: str, delighters: list[str], detractors: list[str]) -> str:
    return textwrap.dedent(f"""
    Analyze the customer review and map **up to 10 delighters** and **up to 10 detractors** STRICTLY from the lists given.
    If fewer apply, return fewer. Do not invent categories.

    Return JSON with:
    {{
      "delighters": ["..."],
      "detractors": ["..."],
      "notes": "short notes",
      "new_candidates": {{
        "delighters": ["..."],   // items that seem right but are NOT in the provided list
        "detractors": ["..."]
      }}
    }}

    ### Allowed Delighters (choose from these only)
    - {chr(10)}- ".join(sorted(set(delighters))[:200])

    ### Allowed Detractors (choose from these only)
    - {chr(10)}- ".join(sorted(set(detractors))[:200])

    ### Review
    {review_text}
    """)

JSON_RE = re.compile(r"\{[\s\S]*\}", re.M)

def parse_json_safe(text: str) -> dict:
    if not text: return {}
    m = JSON_RE.search(text)
    try:
        return json.loads(m.group(0) if m else text)
    except Exception:
        return {}

# ---------- Progressing AI pass ----------
def estimate_seconds(chars:int, model:str) -> float:
    # crudely: tokens ≈ chars/4; assume ~70 tok/s for 4o-mini, 60 for 4o, 45 for 4.1, 35 for GPT-5
    rate = 70
    if model.endswith("4o"): rate = 60
    if model.endswith("4.1"): rate = 45
    if model.startswith("gpt-5"): rate = 35
    tokens = max(60, chars/4 + 256)  # prompt+response
    return tokens / rate + 0.4  # overhead

def run_symptomize(df_in: pd.DataFrame, mask: pd.Series, n_limit:int,
                   delighters: list[str], detractors: list[str],
                   model: str, api_key: str):
    client = OpenAI(api_key=api_key)
    idxs = df_in[mask].index.tolist()[:n_limit]
    results = []

    # ETA setup
    lengths = df_in.loc[idxs, "Verbatim"].fillna("").astype(str).str.len().tolist() if "Verbatim" in df_in.columns else [120]*len(idxs)
    naive_total = sum(estimate_seconds(c, model) for c in lengths)
    ph_eta = st.empty()
    prog = st.progress(0)
    started = time.time()
    smoothed = 0.0

    for i, ridx in enumerate(idxs, start=1):
        txt = str(df_in.at[ridx, "Verbatim"]) if "Verbatim" in df_in.columns else ""
        prompt = build_prompt(txt, delighters, detractors)
        req = {
            "model": model,
            "messages": [
                {"role":"system","content": SYSTEM_INSTR},
                {"role":"user","content": prompt}
            ],
        }
        if model_supports_temperature(model):
            req["temperature"] = 0.2

        t0 = time.time()
        try:
            resp = client.chat.completions.create(**req)
            content = (resp.choices[0].message.content or "").strip()
            data = parse_json_safe(content)
        except Exception as e:
            data = {"error": str(e)}

        # normalize
        dels = [clean_text(x) for x in (data.get("delighters") or []) if clean_text(x)]
        dets = [clean_text(x) for x in (data.get("detractors") or []) if clean_text(x)]
        dels = dels[:10]; dets = dets[:10]
        newc = data.get("new_candidates") or {}
        new_dels = [clean_text(x) for x in (newc.get("delighters") or []) if clean_text(x)]
        new_dets = [clean_text(x) for x in (newc.get("detractors") or []) if clean_text(x)]
        results.append({
            "row_index": int(ridx),
            "row_excel": int(ridx)+2,  # naive 1-based + header row
            "review": txt,
            "delighters": dels,
            "detractors": dets,
            "notes": data.get("notes") or "",
            "new_delighters": new_dels,
            "new_detractors": new_dets,
            "error": data.get("error")
        })

        # progress/ETA
        dt = time.time() - t0
        smoothed = dt if smoothed == 0 else (0.4*dt + 0.6*smoothed)
        remaining = len(idxs) - i
        est_each = max(0.2, smoothed)
        eta = remaining * est_each
        done_frac = i/len(idxs)
        prog.progress(min(1.0, done_frac))
        ph_eta.info(f"Processing {i}/{len(idxs)} … avg {est_each:.1f}s/review • ETA ~ {eta:.0f}s")

    ph_eta.success(f"Completed {len(idxs)} reviews in {time.time()-started:.1f}s")
    return results

# ---------- Export preserving formatting ----------
def export_preserving_formatting(
    uploaded_file, df_current: pd.DataFrame, main_sheet_name: str, symptom_cols: list[str],
    approved_new_delighters: list[str] | None = None,
    approved_new_detractors: list[str] | None = None,
) -> bytes:
    original_bytes = uploaded_file.getvalue()
    wb = load_workbook(filename=io.BytesIO(original_bytes))
    ws = wb[main_sheet_name] if main_sheet_name in wb.sheetnames else wb[wb.sheetnames[0]]

    # find header row (look for "Symptom 1" within first 20 rows)
    header_row_idx = None
    max_scan = min(ws.max_row, 25)
    for r in range(1, max_scan + 1):
        row_vals = [str(c.value).strip() if c.value is not None else "" for c in ws[r]]
        if "Symptom 1" in row_vals:
            header_row_idx = r
            break
    if header_row_idx is None:
        header_row_idx = 1

    col_map: dict[str, int] = {}
    for col in range(1, ws.max_column + 1):
        v = ws.cell(row=header_row_idx, column=col).value
        if isinstance(v, str) and v.strip() in symptom_cols:
            col_map[v.strip()] = col

    # write values
    for i in range(len(df_current)):
        excel_row = header_row_idx + 1 + i
        if excel_row > ws.max_row:
            ws.append([])
        row = df_current.iloc[i]
        for sc in symptom_cols:
            if sc in df_current.columns and sc in col_map:
                val = row.get(sc, None)
                ws.cell(row=excel_row, column=col_map[sc]).value = None if (pd.isna(val) or str(val).strip()=="") else str(val)

    # optional: approved new items summary
    if (approved_new_delighters or approved_new_detractors):
        sheet_name = "Approved_New_Symptoms"
        if sheet_name in wb.sheetnames:
            del wb[sheet_name]
        ws_new = wb.create_sheet(title=sheet_name)
        ws_new.cell(row=1, column=1, value="New Delighters (approved)")
        ws_new.cell(row=1, column=2, value="New Detractors (approved)")
        max_len = max(len(approved_new_delighters or []), len(approved_new_detractors or []))
        for i in range(max_len):
            if approved_new_delighters and i < len(approved_new_delighters):
                ws_new.cell(row=2+i, column=1, value=approved_new_delighters[i])
            if approved_new_detractors and i < len(approved_new_detractors):
                ws_new.cell(row=2+i, column=2, value=approved_new_detractors[i])

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out.getvalue()

# ---------- ACTION: run AI ----------
symptom_button = st.button(f"🚀 Symptomize {min(max_to_process, unsym_count)} reviews", disabled=(unsym_count==0 or not can_run_ai))
if symptom_button and can_run_ai:
    with st.spinner("Contacting OpenAI…"):
        suggestions = run_symptomize(
            df, unsym_mask, int(max_to_process),
            delighters_list, detractors_list,
            model=st.session_state["llm_model"], api_key=api_key
        )
    st.session_state["symptom_suggestions"] = suggestions

# ---------- Review & approve suggestions ----------
suggestions = st.session_state.get("symptom_suggestions") or []
if suggestions:
    st.markdown("### ✅ Review AI suggestions")
    # Build approval table with full review text
    rows = []
    new_del_cand, new_det_cand = set(), set()
    for s in suggestions:
        rows.append({
            "Approve?": True if not s.get("error") else False,
            "Row ID": s["row_index"],
            "Excel Row": s["row_excel"],
            "Detractors (≤10)": "; ".join(s["detractors"]),
            "Delighters (≤10)": "; ".join(s["delighters"]),
            "Review": s["review"],
            "Notes": s.get("notes",""),
            "Error": s.get("error","")
        })
        for x in s.get("new_delighters", []):
            if x and x not in delighters_list: new_del_cand.add(x)
        for x in s.get("new_detractors", []):
            if x and x not in detractors_list: new_det_cand.add(x)

    df_sug = pd.DataFrame(rows)
    edited = st.data_editor(
        df_sug,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Approve?": st.column_config.CheckboxColumn(),
            "Review": st.column_config.TextColumn(width="large"),
        },
        height=min(560, 120 + 32*len(df_sug))
    )

    # Apply approved rows to dataframe (fill into Symptom 1–20)
    if st.button("💾 Apply approved to table"):
        applied = 0
        for _, r in edited.iterrows():
            if not bool(r["Approve?"]): continue
            ridx = int(r["Row ID"])
            dets = [s.strip() for s in str(r["Detractors (≤10)"]).split(";") if s.strip()]
            dels = [s.strip() for s in str(r["Delighters (≤10)"]).split(";") if s.strip()]
            # write detractors into Symptom 1..10; delighters into 11..20
            for i, val in enumerate(dets[:10], start=1):
                col = f"Symptom {i}"
                if col in df.columns: df.at[ridx, col] = val
            for j, val in enumerate(dels[:10], start=11):
                col = f"Symptom {j}"
                if col in df.columns: df.at[ridx, col] = val
            applied += 1

        st.success(f"Applied {applied} rows.")
        # Recompute mask/count
        unsym_mask[:] = df.apply(row_has_no_symptoms, axis=1)
        st.session_state["symptom_suggestions"] = []  # clear after applying
        st.rerun()

    # New symptom candidates (user approval)
    with st.expander("🆕 New symptom candidates detected"):
        c1, c2 = st.columns(2)
        with c1:
            approve_dels = st.multiselect("Approve new **Delighters** to the master list", sorted(new_del_cand))
        with c2:
            approve_dets = st.multiselect("Approve new **Detractors** to the master list", sorted(new_det_cand))
        if st.button("➕ Add approved to list"):
            added_d = 0
            for x in approve_dels:
                if x not in delighters_list: delighters_list.append(x); added_d += 1
            added_t = 0
            for x in approve_dets:
                if x not in detractors_list: detractors_list.append(x); added_t += 1
            st.success(f"Added {added_d} delighters and {added_t} detractors for future runs.")

# ---------- Metrics snapshot ----------
st.markdown("---")
st.subheader("⭐ Star Rating Metrics")
def pct_12(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float((s <= 2).mean() * 100) if not s.empty else 0.0
def section_stats(sub: pd.DataFrame) -> tuple[int, float, float]:
    cnt = len(sub)
    if cnt == 0 or "Star Rating" not in sub.columns:
        return 0, 0.0, 0.0
    avg = float(pd.to_numeric(sub["Star Rating"], errors="coerce").mean())
    pct = pct_12(sub["Star Rating"])
    return cnt, avg, pct
cnt, avg, low = section_stats(df)
def card_html(title, count, avg, pct):
    return f"""
    <div class="metric-card">
      <h4>{esc(title)}</h4>
      <div class="metric-row">
        <div class="metric-box"><div class="metric-label">Count</div><div class="metric-kpi">{count:,}</div></div>
        <div class="metric-box"><div class="metric-label">Avg ★</div><div class="metric-kpi">{avg:.1f}</div></div>
        <div class="metric-box"><div class="metric-label">% 1–2★</div><div class="metric-kpi">{pct:.1f}%</div></div>
      </div>
    </div>
    """
st.markdown('<div class="metrics-grid">'+card_html("All Reviews", cnt, avg, low)+'</div>', unsafe_allow_html=True)

# Star distribution
if "Star Rating" in df.columns:
    star_counts = pd.to_numeric(df["Star Rating"], errors="coerce").dropna().value_counts().sort_index()
    percentages = (star_counts / max(1,len(df)) * 100).round(1)
    fig_bar = go.Figure(go.Bar(
        x=star_counts.values, y=[f"{int(s)} stars" for s in star_counts.index], orientation="h",
        text=[f"{v} reviews ({percentages.get(idx,0)}%)" for idx,v in zip(star_counts.index, star_counts.values)],
        textposition="auto"
    ))
    fig_bar.update_layout(title="<b>Star Rating Distribution</b>", template="plotly_white", height=280, margin=dict(l=40,r=40,t=45,b=40))
    st.plotly_chart(fig_bar, use_container_width=True)

# ---------- Export section ----------
st.markdown("---")
st.subheader("Export Updated File")

c1, c2 = st.columns(2)
with c1:
    csv_bytes = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ Download CSV (simple)", csv_bytes, file_name="starwalk_symptomized.csv", mime="text/csv")

with c2:
    try:
        preserved = export_preserving_formatting(
            uploaded_file=uploaded,
            df_current=df,
            main_sheet_name=main_sheet_name,
            symptom_cols=SYMPTOM_COLS_ALL,
            approved_new_delighters=[],  # you can wire this to approvals above if you want to capture them
            approved_new_detractors=[],
        )
        st.download_button("⬇️ Download XLSX (preserve original formatting — beta)",
                           preserved,
                           file_name="starwalk_symptomized_preserved.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.caption("This reuses your uploaded workbook and overwrites only Symptom 1–20 values (styles/widths preserved).")
    except Exception as e:
        st.error(f"Export failed: {e}")



