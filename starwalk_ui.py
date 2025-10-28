# Create a simplified, single-review Streamlit app per user's template, with Symptoms tab support
code = r'''# ---------- Shark Glossi Review Analyzer — Simple, One-Review Mode (Uses Symptoms Tab) ----------
# Streamlit 1.38+
#
# What this does (simple & accurate, no fancy scoring):
# • One review at a time (like your Custom GPT)
# • Uses the "Symptoms" sheet (Delighters/Detractors columns). If not present, falls back to the Glossi preset lists below.
# • Sends FULL review text to the model (no truncation).
# • LLM must pick ONLY from the allowed lists, and must return Hair Type + Confidence + Notes.
# • If model proposes new items, the UI asks you to approve; approved items are added to the in-session allowed lists (and can be appended to the Symptoms sheet on download).
# • Optional: load a workbook to prefill a review and apply picked tags to Symptom 1–20 columns, then download the updated file.
#
# To run:
#   pip install streamlit openpyxl openai pandas
#   export OPENAI_API_KEY=YOUR_KEY
#   streamlit run star_glossi_simple.py

import io
import os
import re
import json
import time
from typing import List, Tuple, Dict, Any

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
st.set_page_config(layout="wide", page_title="Shark Glossi Review Analyzer")

# ---------------- Force Light Mode ----------------
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
  new MutationObserver(setLight).observe(
    document.documentElement,
    { attributes: true, attributeFilter: ['data-theme'] }
  );
})();
</script>
""",
    height=0,
)

# ---------------- Compact CSS for 14" screens ----------------
st.markdown(
    """
<style>
  :root { scroll-behavior: smooth; scroll-padding-top: 68px; }
  .block-container { padding-top:.6rem; padding-bottom:.9rem; max-width: 1200px; }
  .hero-wrap{ position:relative; overflow:hidden; border-radius:12px; min-height:82px; margin:.1rem 0 .6rem 0; box-shadow:0 0 0 1px #cbd5e1, 0 6px 12px rgba(15,23,42,.05); background:linear-gradient(90deg, #fff 0% 60%, transparent 60% 100%); }
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:8px 14px; color:#0f172a; }
  .hero-title{ font-size:clamp(18px,2.2vw,28px); font-weight:800; margin:0; line-height:1.1; }
  .hero-sub{ margin:2px 0 0 0; color:#475569; font-size:clamp(11px,1vw,14px); }
  .pill{ padding:6px 10px; border-radius:999px; border:1px solid #cbd5e1; background:#f8fafc; font-weight:700; font-size:12px; }
  .chips{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0}
  .chip{padding:6px 10px;border-radius:999px;border:1px solid #cbd5e1;background:#f8fafc;font-weight:700;font-size:.86rem}
  .chip.pos{border-color:#CDEFE1;background:#EAF9F2;color:#065F46}
  .chip.neg{border-color:#F7D1D1;background:#FDEBEB;color:#7F1D1D}
  .review-box { white-space:pre-wrap; background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:10px; font-size:13px; }
</style>
""",
    unsafe_allow_html=True,
)

# ---------------- Header ----------------
st.markdown(
    """
    <div class="hero-wrap">
      <div class="hero-inner">
        <div>
          <div class="hero-title">Shark Glossi Review Analyzer — Simple</div>
          <div class="hero-sub">One review at a time. Uses your Symptoms tab (with fallback to the Glossi lists).</div>
        </div>
        <div><img src="https://upload.wikimedia.org/wikipedia/commons/e/ea/SharkNinja_logo.svg" alt="SharkNinja" style="height:32px;opacity:.92"/></div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ========================== PRESET FALLBACK LISTS ==========================
GLOSSI_DELIGHTERS = [
    "Auto shutoff – Built-in safety feature",
    "Cool-touch areas – Safe to handle",
    "Light indicators – Clear heat/mode display",
    "Dual voltage – Travel ready",
    "Custom settings – Adjustable modes",
    "Grip design– Easy/comfortable to hold",
    "Storage loop – Easy to hang/store",
    "Swivel cord – Moves freely",
    "Brush quality – Strong, smooth bristles",
    "Brand loyalty – Reinforces trust in Shark",
    "No hair snagging – Glides through easily, gentle",
    "Smell-free styling – No burnt scent",
    "Quick transitions – Easy wet-to-dry switch",
    "Lightweight – Easy to handle",
    "Balanced weight – Doesn’t strain wrist",
    "Compact shape – Minimal counter space",
    "Long-Lasting Results  – Style lasts longer",
    "Low heat effectiveness – Works without high temps",
    "Versatile - works w/ wet or dry hair",
    "End Result - Soft/sleek/shiny/glossy/smooth hair",
    "Performance - fast drying / high speed",
    "Effectiveness - Frizz Free",
    "Heater - quick / hot-temp",
    "Better than others - upgrade from competitors",
    "Brush design - front and ceramic plate: smoothing/finishing",
    "All-in-one tool",
    "Ease of use - simple / intuitive / convenient",
    "End Result - Volume",
    "Family friendly - good for family use",
    "Works w/ different hair types",
    "Best tool",
    "No hair damage - no burnt hair",
    "Time-saving / quick",
    "End result styling - satisfied / delighted",
    "Salon quality results",
    "Build quality - durable",
    "Design / Aesthetics",
    "Color",
    "Safe product",
    "Travel friendly - convenient",
    "Doesn't tangle hair",
    "Game-changer",
    "Routine - daily use",
    "Design - brush prongs hold tool at elevated position",
    "End result - Curl/Waves",
    "Money savings - results w/o spending",
    "Temp control",
    "Fast straightening",
    "Quiet",
    "Price/value",
    "End Result - Blowout",
    "Healthy Hair",
    "Would Recommend",
    "End Result - Bounce",
    "Delighted - Dry Mode",
    "Delighted - Wet Mode",
    "End Result - Hair definition/shape",
]

GLOSSI_DETRACTORS = [
    "Weight – Feels heavy during use",
    "Cool tip – Lacks a cool-touch area",
    "Power button – Easy to hit by mistake",
    "Startup time – Takes too long to heat",
    "Dry mode performance – Weak airflow",
    "Attachment confusion – Hard to identify brush sides",
    "Plug Size",
    "Grip texture – Slippery handle",
    "Heat distribution – Uneven heating",
    "Finish durability – Scratches or marks easily",
    "No auto shutoff – Missing safety feature",
    "No travel case – Needs protective bag",
    "Price mismatch – Doesn’t justify cost",
    "Hair results – Doesn’t hold style",
    "Startup noise – Loud on power-up",
    "Hair type mismatch – Not ideal for thick/coily hair",
    "Not travel friendly - big/bulky",
    "Not effective - frizz fighting",
    "Loud",
    "Time savings - doesn't save time",
    "End result - unsatisfied, results/goal not met",
    "End result - no volume",
    "Plug Quality- falls out of socket",
    "Product lifespan - no longer working properly",
    "Electric short - shorting other electronics in socket",
    "Heat/Temp settings - temp specs",
    "Cord length  - too short",
    "Learning curve",
    "Overheating",
    "worse than others",
    "Not suitable for short hair",
    "Burnt hair",
]

# ========================== SIDEBAR ==========================
with st.sidebar:
    st.header("📁 Workbook (optional)")
    uploaded = st.file_uploader("Star Walk workbook (.xlsx)", type=["xlsx"], accept_multiple_files=False)
    use_glossi_fallback = st.checkbox("Use Glossi preset if Symptoms sheet missing", value=True)
    model_choice = st.selectbox("Model", ["gpt-4o", "gpt-4.1", "gpt-5"], index=1)
    st.caption("No review truncation. One-click, one-review analysis.")

# Persist bytes so we can write back later
if uploaded and "uploaded_bytes" not in st.session_state:
    uploaded.seek(0)
    st.session_state["uploaded_bytes"] = uploaded.read()
    uploaded.seek(0)

# ========================== LOAD SYMPTOM LISTS ==========================
import io as _io

def _norm(s: str) -> str:
    if s is None: return ""
    return re.sub(r"[^a-z]+", "", str(s).lower()).strip()

def _looks_like_symptom_sheet(name: str) -> bool:
    return "symptom" in _norm(name)

def load_symptom_lists_from_workbook(raw: bytes) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Load allowed delighters & detractors from a Symptoms-like sheet."""
    meta = {"sheet": None, "strategy": None, "columns": [], "note": ""}
    try:
        xls = pd.ExcelFile(_io.BytesIO(raw))
    except Exception as e:
        meta["note"] = f"Could not open Excel: {e}"
        return [], [], meta
    # choose a symptoms sheet if present
    pick = None
    for sname in xls.sheet_names:
        if _looks_like_symptom_sheet(sname):
            pick = sname; break
    if pick is None:
        # fallback to first sheet if user intended to store lists there
        pick = xls.sheet_names[0] if xls.sheet_names else None
    if not pick:
        meta["note"] = "No sheets found"
        return [], [], meta
    meta["sheet"] = pick
    try:
        s = pd.read_excel(xls, sheet_name=pick)
    except Exception as e:
        meta["note"] = f"Could not read sheet '{pick}': {e}"
        return [], [], meta

    # find columns by fuzzy header
    dels_col = None; dets_col = None
    for c in s.columns:
        name = _norm(str(c))
        if any(k in name for k in ["delight","pros","positive"]): dels_col = c if dels_col is None else dels_col
        if any(k in name for k in ["detract","cons","negative","issues","problems"]): dets_col = c if dets_col is None else dets_col
    dels = [str(x).strip() for x in s.get(dels_col, pd.Series(dtype=str)).dropna().tolist()] if dels_col is not None else []
    dets = [str(x).strip() for x in s.get(dets_col, pd.Series(dtype=str)).dropna().tolist()] if dets_col is not None else []
    meta["strategy"] = "fuzzy-headers"
    meta["columns"] = list(s.columns)
    return dels, dets, meta

ALLOWED_DELIGHTERS: List[str] = []
ALLOWED_DETRACTORS: List[str] = []
SYM_META = {"sheet": None, "strategy": None, "columns": [], "note": ""}

raw_bytes = st.session_state.get("uploaded_bytes", b"")
if raw_bytes:
    dels, dets, meta = load_symptom_lists_from_workbook(raw_bytes)
    if dels or dets:
        ALLOWED_DELIGHTERS = dels
        ALLOWED_DETRACTORS = dets
        SYM_META = meta

if (not ALLOWED_DELIGHTERS and not ALLOWED_DETRACTORS) and use_glossi_fallback:
    ALLOWED_DELIGHTERS = GLOSSI_DELIGHTERS[:]
    ALLOWED_DETRACTORS = GLOSSI_DETRACTORS[:]
    SYM_META["note"] = "Using Glossi preset lists (fallback)."

ALLOWED_DELIGHTERS = [x for x in ALLOWED_DELIGHTERS if x]
ALLOWED_DETRACTORS = [x for x in ALLOWED_DETRACTORS if x]
ALLOWED_DELIGHTERS_SET = set(ALLOWED_DELIGHTERS)
ALLOWED_DETRACTORS_SET = set(ALLOWED_DETRACTORS)

if ALLOWED_DELIGHTERS or ALLOWED_DETRACTORS:
    st.success(f"Loaded {len(ALLOWED_DELIGHTERS)} delighters, {len(ALLOWED_DETRACTORS)} detractors. {('('+SYM_META.get('note','')+')') if SYM_META.get('note') else ''}")
else:
    st.warning("No allowed lists found. Add a Symptoms sheet or enable the Glossi preset in the sidebar.")

# ========================== ONE-REVIEW ANALYZER ==========================
st.markdown("### Analyze One Review")

# Optional helper: prefill from workbook
prefill_text = ""
row_to_apply = None
SYMPTOM_COLS = [f"Symptom {i}" for i in range(1,21)]
if raw_bytes:
    try:
        df = pd.read_excel(_io.BytesIO(raw_bytes), sheet_name="Star Walk scrubbed verbatims")
    except Exception:
        df = pd.read_excel(_io.BytesIO(raw_bytes))
    # pick first row missing symptoms
    is_empty = df[SYMPTOM_COLS].isna() | (df[SYMPTOM_COLS].astype(str).applymap(lambda x: str(x).strip().upper() in {"","NA","N/A","NONE","NULL","-"}))
    mask_empty = is_empty.all(axis=1)
    missing_idx = df.index[mask_empty].tolist()
    if missing_idx:
        row_to_apply = int(missing_idx[0])
        prefill_text = str(df.loc[row_to_apply].get("Verbatim","") or "")

col1, col2 = st.columns([2,1])
with col1:
    review_text = st.text_area("Paste a single customer review", value=prefill_text, height=180, placeholder="Paste the review here…")
with col2:
    stars_in = st.selectbox("Star rating (optional)", options=["(none)",1,2,3,4,5], index=0)
    stars = None if stars_in == "(none)" else int(stars_in)
    analyze = st.button("Analyze Review", type="primary", use_container_width=True)
    clear = st.button("Clear", use_container_width=True)

if clear:
    st.experimental_rerun()

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if analyze:
    if not review_text.strip():
        st.warning("Please paste a review first.")
    elif not _HAS_OPENAI or not api_key:
        st.error("OpenAI client not available. Install `openai` and set OPENAI_API_KEY.")
    else:
        with st.spinner("Thinking…"):
            client = OpenAI(api_key=api_key)

            sys_prompt = """You are Shark Glossi Review Analyzer, an AI assistant designed to evaluate and process customer reviews specifically for the Shark Glossi (similar to SmoothStyle) hot tool.
Your job is to extract and clearly list all delighters and detractors from each review, using only the predefined items in the provided lists.
Also infer the Hair Type (1 straight, 2 wavy, 3 curly, 4 coily/kinky, or NA) with a confidence level (High/Medium/Low) and provide short notes.

Return ONLY valid JSON like:
{
  "delighters": ["...", "..."],
  "detractors": ["...", "..."],
  "hair_type": {"type":"1|2|3|4|NA","confidence":"High|Medium|Low"},
  "notes": "short clarifications or conflicts",
  "proposed_new": { "delighters": ["..."], "detractors": ["..."] }
}

Rules:
- Choose ONLY from the allowed lists (provided below) for delighters/detractors.
- If something strong is present but not in the lists, put it under proposed_new (do NOT mix into the main lists).
- Keep delighters/detractors concise and exactly as they appear in the lists.
- If star rating is 1–2, likely more detractors; 4–5 likely more delighters; 3 neutral.
- If hair type is unclear, use NA with a confidence.
"""

            user_payload = {
                "review": review_text,
                "stars": stars,
                "allowed_delighters": ALLOWED_DELIGHTERS[:200],
                "allowed_detractors": ALLOWED_DETRACTORS[:200],
            }

            req = {
                "model": model_choice,
                "messages": [
                    {"role":"system","content": sys_prompt},
                    {"role":"user","content": json.dumps(user_payload)},
                ],
                "response_format": {"type":"json_object"},
            }

            # temperature 0 for consistency (works for 4.1/4o; omit for 5 if needed)
            if not str(model_choice).startswith("gpt-5"):
                req["temperature"] = 0.0

            # simple retry
            out = None
            for _ in range(3):
                try:
                    out = client.chat.completions.create(**req)
                    break
                except Exception:
                    time.sleep(0.6)
            if out is None:
                st.error("API call failed. Try again.")
            else:
                content = out.choices[0].message.content or "{}"
                try:
                    data = json.loads(content)
                except Exception:
                    st.error("Model did not return valid JSON.")
                    data = {}

                picked_del = [x for x in data.get("delighters", []) if x in ALLOWED_DELIGHTERS_SET]
                picked_det = [x for x in data.get("detractors", []) if x in ALLOWED_DETRACTORS_SET]
                proposed = data.get("proposed_new", {}) or {}
                prop_del = [x for x in proposed.get("delighters", []) if x]
                prop_det = [x for x in proposed.get("detractors", []) if x]

                # ---- Render Output (your exact format) ----
                st.markdown("#### Delighters:")
                if picked_del:
                    st.markdown("<div class='chips'>" + "".join([f"<span class='chip pos'>{x}</span>" for x in picked_del]) + "</div>", unsafe_allow_html=True)
                else:
                    st.code("–")

                st.markdown("#### Detractors:")
                if picked_det:
                    st.markdown("<div class='chips'>" + "".join([f"<span class='chip neg'>{x}</span>" for x in picked_det]) + "</div>", unsafe_allow_html=True)
                else:
                    st.code("–")

                st.markdown("#### Hair Type Guess:")
                hair = data.get("hair_type", {}) or {}
                st.write(f"Type: **{hair.get('type','NA')}**")
                st.write(f"Confidence Level: **{hair.get('confidence','Medium')}**")

                notes = (data.get("notes") or "").strip()
                st.markdown("#### Notes:")
                st.write(notes if notes else "—")

                # ---- Approve new items (optional) ----
                if prop_del or prop_det:
                    st.info("New symptoms detected. Approve to add to allowed lists?")
                    add_box = st.container()
                    with add_box:
                        cols = st.columns(2)
                        with cols[0]:
                            if prop_det:
                                st.write("**Proposed Detractors**")
                                add_det = []
                                for i, n in enumerate(prop_det):
                                    if st.checkbox(n, key=f"new_det_{i}"):
                                        add_det.append(n)
                            else:
                                add_det = []
                        with cols[1]:
                            if prop_del:
                                st.write("**Proposed Delighters**")
                                add_del = []
                                for i, n in enumerate(prop_del):
                                    if st.checkbox(n, key=f"new_del_{i}"):
                                        add_del.append(n)
                            else:
                                add_del = []

                        if st.button("Add approved to allowed lists"):
                            for n in add_det:
                                if n not in ALLOWED_DETRACTORS_SET:
                                    ALLOWED_DETRACTORS.append(n); ALLOWED_DETRACTORS_SET.add(n)
                            for n in add_del:
                                if n not in ALLOWED_DELIGHTERS_SET:
                                    ALLOWED_DELIGHTERS.append(n); ALLOWED_DELIGHTERS_SET.add(n)
                            st.success("Approved items added for this session. They’ll be included in the download if you choose.")

                # ---- Apply to workbook row (optional) ----
                if raw_bytes and prefill_text and row_to_apply is not None:
                    st.markdown("---")
                    st.markdown(f"**Workbook target row:** {row_to_apply}  (first row with empty symptoms)")
                    apply_now = st.button("Apply picks to row (Symptoms 1–20)")
                    if apply_now:
                        # Reload to ensure we write correctly
                        try:
                            df = pd.read_excel(_io.BytesIO(raw_bytes), sheet_name="Star Walk scrubbed verbatims")
                        except Exception:
                            df = pd.read_excel(_io.BytesIO(raw_bytes))
                        # Write dets to 1..10, dels to 11..20
                        dets_final = picked_det[:10]
                        dels_final = picked_del[:10]
                        for j, name in enumerate(dets_final, start=1):
                            col = f"Symptom {j}"
                            if col in df.columns:
                                df.at[row_to_apply, col] = name
                        for j, name in enumerate(dels_final, start=11):
                            col = f"Symptom {j}"
                            if col in df.columns:
                                df.at[row_to_apply, col] = name
                        # Offer download with optional append of approved newly added items into Symptoms sheet
                        def _download(df_inner: pd.DataFrame):
                            if _HAS_OPENPYXL:
                                try:
                                    bio = io.BytesIO(st.session_state["uploaded_bytes"])
                                    wb = load_workbook(bio)
                                    data_sheet = "Star Walk scrubbed verbatims"
                                    if data_sheet not in wb.sheetnames:
                                        data_sheet = wb.sheetnames[0]
                                    ws = wb[data_sheet]
                                    headers = {ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column+1)}
                                    def col_idx(name): return headers.get(name)
                                    # Write row (Symptoms only)
                                    excel_row = 2 + row_to_apply
                                    for c in SYMPTOM_COLS:
                                        ci = col_idx(c)
                                        if ci:
                                            ws.cell(row=excel_row, column=ci).value = df_inner.at[row_to_apply, c]
                                    # Append newly approved items into Symptoms sheet if present
                                    symptoms_sheet_name = None
                                    for n in wb.sheetnames:
                                        if n.strip().lower() in {"symptoms","symptom","symptom sheet","symptom tab"}:
                                            symptoms_sheet_name = n; break
                                    if symptoms_sheet_name:
                                        ss = wb[symptoms_sheet_name]
                                        sh = {ss.cell(row=1, column=ci).value: ci for ci in range(1, ss.max_column+1)}
                                        del_col = sh.get("Delighters") or sh.get("delighters")
                                        det_col = sh.get("Detractors") or sh.get("detractors")
                                        if del_col:
                                            existing = set()
                                            for r in range(2, ss.max_row+1):
                                                v = ss.cell(row=r, column=del_col).value
                                                if v and str(v).strip(): existing.add(str(v).strip())
                                            for item in sorted(ALLOWED_DELIGHTERS):
                                                if item not in existing:
                                                    ss.append([None]*(del_col-1) + [item])
                                        if det_col:
                                            existing = set()
                                            for r in range(2, ss.max_row+1):
                                                v = ss.cell(row=r, column=det_col).value
                                                if v and str(v).strip(): existing.add(str(v).strip())
                                            for item in sorted(ALLOWED_DETRACTORS):
                                                if item not in existing:
                                                    rowx = [None]*(det_col-1) + [item]
                                                    ss.append(rowx)
                                    out = io.BytesIO()
                                    wb.save(out)
                                    st.download_button("Download updated workbook (.xlsx)", data=out.getvalue(),
                                                       file_name="StarWalk_updated.xlsx",
                                                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                                    return
                                except Exception:
                                    pass
                            # Fallback writer
                            out2 = io.BytesIO()
                            with pd.ExcelWriter(out2, engine="xlsxwriter") as w:
                                df_inner.to_excel(w, index=False, sheet_name="Star Walk scrubbed verbatims")
                            st.download_button("Download updated workbook (.xlsx) — basic", data=out2.getvalue(),
                                               file_name="StarWalk_updated_basic.xlsx",
                                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                        _download(df)
                        st.success("Applied to the first missing row. Download your updated workbook above.")
'''
path = "/mnt/data/star_glossi_simple.py"
with open(path, "w", encoding="utf-8") as f:
    f.write(code)

# Create a zip too
import zipfile, hashlib, json
zip_path = "/mnt/data/star_glossi_simple.zip"
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
    z.write(path, arcname="star_glossi_simple.py")

def sha256(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

print(json.dumps({
    "py_path": path,
    "py_sha256": sha256(path),
    "zip_path": zip_path,
    "zip_sha256": sha256(zip_path),
}, indent=2))


