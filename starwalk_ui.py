# starwalk_ui.py
# Streamlit 1.38+

from __future__ import annotations

import io
import os
import re
import json
import textwrap
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

# Optional OpenAI
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    OpenAI = None
    _HAS_OPENAI = False

# ---------------- Page setup ----------------
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

# Global CSS (light-first, Helvetica)
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
    --gap-sm:12px; --gap-md:20px; --gap-lg:32px;
  }
  html, body, .stApp {
    background: var(--bg-app);
    color: var(--text);
    font-family: "Helvetica Neue", Helvetica, Arial, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Liberation Sans", sans-serif;
  }
  .block-container { padding-top:.9rem; padding-bottom:1.2rem; }
  mark{ background:#fff2a8; padding:0 .25em; border-radius:3px; }
  .card{ background:var(--bg-card); border-radius:14px; padding:16px; box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06); }
  .cta{ font-weight:700; }
  .pill{ display:inline-flex; align-items:center; gap:.5ch; padding:6px 10px; background:var(--bg-tile); border:1.5px solid var(--border); border-radius:999px; }
  .ok{ color:#065F46; background:#E7F8EE; border-color:#CDEFE1; }
  .warn{ color:#7C2D12; background:#FFF7ED; border-color:#FBD1A7; }
  .muted{ color:var(--muted); }
  .sn-logo{ height:48px; width:auto; display:block; }
  .hero-wrap{
    position:relative; overflow:hidden; border-radius:14px; min-height:140px; margin: .25rem 0 1rem 0;
    box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06);
    background:linear-gradient(90deg, var(--bg-card) 0% 55%, transparent 55% 100%);
  }
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:0 18px; }
  .hero-title{ font-size:clamp(22px,3.3vw,40px); font-weight:800; margin:0; }
  .hero-sub{ margin:4px 0 0 0; color:var(--muted); font-size:clamp(12px,1.1vw,16px); }
</style>
""", unsafe_allow_html=True)

# ---------------- Utilities ----------------
SYMPTOM_COLS = [f"Symptom {i}" for i in range(1, 21)]
DETRACTOR_COLS = [f"Symptom {i}" for i in range(1, 11)]
DELIGHTER_COLS = [f"Symptom {i}" for i in range(11, 21)]

def _strip(x):
    if pd.isna(x): return ""
    s = str(x).strip()
    if s.upper() in {"<NA>", "NA", "N/A", "NONE", "NULL", "-"}:
        return ""
    return s

def is_row_unsymptomized(row: pd.Series) -> bool:
    return all(_strip(row.get(c, "")) == "" for c in SYMPTOM_COLS)

def shorten(s: str, n: int = 220) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n-1] + "…"

# ---------------- Header ----------------
st_html(f"""
<div class="hero-wrap">
  <div class="hero-inner">
    <div>
      <h1 class="hero-title">Star Walk Analysis Dashboard</h1>
      <div class="hero-sub">Insights, trends, and ratings — fast.</div>
    </div>
    <div>
      <img class="sn-logo" src="https://upload.wikimedia.org/wikipedia/commons/e/ea/SharkNinja_logo.svg" alt="SharkNinja"/>
    </div>
  </div>
</div>
""", height=160)

# ---------------- Sidebar: Upload ----------------
st.sidebar.header("Upload Star Walk File")
uploaded = st.sidebar.file_uploader("Choose Excel (.xlsx)", type=["xlsx"])
if uploaded:
    st.sidebar.success("File uploaded successfully. Ready to proceed with analysis.")
else:
    st.sidebar.info("Please upload the Star Walk Excel to begin.")

# ---------------- Load workbook ----------------
@st.cache_data(show_spinner=False)
def load_workbook(uploaded_file) -> Tuple[pd.DataFrame, Dict[str, List[str]], str, str]:
    """
    Returns: (reviews_df, symptoms_dict{'delighters':[], 'detractors':[]}, reviews_sheet_name, symptoms_sheet_name)
    """
    if uploaded_file is None:
        return pd.DataFrame(), {"delighters": [], "detractors": []}, "", ""

    xls = pd.ExcelFile(uploaded_file)
    # Reviews sheet
    guess_names = [
        "Star Walk scrubbed verbatims",
        "Star Walk Scrubbed Verbatims",
        "Reviews",
        xls.sheet_names[0],
    ]
    rev_sheet = next((n for n in guess_names if n in xls.sheet_names), xls.sheet_names[0])
    reviews = pd.read_excel(xls, sheet_name=rev_sheet)

    # Normalize Symptom cols to string
    for c in SYMPTOM_COLS:
        if c in reviews.columns:
            reviews[c] = reviews[c].map(_strip).astype("string")
        else:
            reviews[c] = ""  # ensure presence

    # Symptoms sheet
    sym_sheet_candidates = ["Symptoms", "Symptom List", "Symptomlist", "Delighters/Detractors"]
    sym_sheet = next((n for n in sym_sheet_candidates if n in xls.sheet_names), None)

    delighters, detractors = [], []
    if sym_sheet:
        raw = pd.read_excel(xls, sheet_name=sym_sheet)
        # Robust parse: gather text-like cells from columns that look like delighters/detractors,
        # otherwise use the first two columns.
        cols = [c.lower() for c in raw.columns.astype(str)]
        dl_candidates = [i for i, c in enumerate(cols) if "delight" in c or "positive" in c]
        dt_candidates = [i for i, c in enumerate(cols) if "detract" in c or "negative" in c]

        if dl_candidates: delighters = raw.iloc[:, dl_candidates[0]].dropna().astype(str).map(_strip).tolist()
        if dt_candidates: detractors = raw.iloc[:, dt_candidates[0]].dropna().astype(str).map(_strip).tolist()

        # Fallback: first col = delighters, second col = detractors (if not found)
        if not delighters and raw.shape[1] >= 1:
            delighters = raw.iloc[:, 0].dropna().astype(str).map(_strip).tolist()
        if not detractors and raw.shape[1] >= 2:
            detractors = raw.iloc[:, 1].dropna().astype(str).map(_strip).tolist()

        delighters = [s for s in delighters if s]
        detractors = [s for s in detractors if s]
    else:
        sym_sheet = "Symptoms"

    return reviews, {"delighters": delighters, "detractors": detractors}, rev_sheet, sym_sheet

df, symptom_lists, reviews_sheet, symptoms_sheet = load_workbook(uploaded)

# ---------------- Top notice + counters ----------------
if uploaded and not df.empty:
    total = len(df)
    unsymptomized_mask = df.apply(is_row_unsymptomized, axis=1)
    to_do = int(unsymptomized_mask.sum())

    st.markdown(
        f"""
        <div class="card" style="display:flex;justify-content:space-between;align-items:center;gap:16px;">
          <div>
            <div class="pill ok">Total reviews: <b>{total:,}</b></div>
            <span class="pill warn" style="margin-left:8px;">Need symptoms: <b id="need-count">{to_do:,}</b></span>
          </div>
          <div>
            <span class="muted">Using sheet <b>{reviews_sheet}</b>. Symptoms from <b>{symptoms_sheet}</b>. </span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
else:
    st.stop()

# ---------------- Symptomizer controls ----------------
st.subheader("AI Symptomizer")

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if not _HAS_OPENAI:
    st.info("Install `openai` and redeploy to enable AI symptomization.")
elif not api_key:
    st.info("Add `OPENAI_API_KEY` to `.streamlit/secrets.toml` (or env).")
else:
    with st.expander("Settings", expanded=True):
        model = st.selectbox(
            "Model",
            ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-5"],
            index=0,
            help="Balanced small model is recommended for cost/speed.",
        )
        batch_size = st.slider("Batch size", 1, 30, 10, help="How many reviews to send per LLM request.")
        max_per_type = st.slider("Max per type (delighters/detractors)", 1, 10, 10)
        preview_text_len = st.slider("Preview length", 80, 400, 220)

    # Prepare data to process
    todo_idx = df.index[unsymptomized_mask].tolist()
    st.write(
        f"**{len(todo_idx):,} reviews** need symptoms. "
        "Click the button below to generate **AI suggestions** (no data is written yet)."
    )

    # Session stores
    if "sympto_suggestions" not in st.session_state:
        st.session_state["sympto_suggestions"] = {}  # id -> {"delighters":[...], "detractors":[...], "new_delighters":[...], "new_detractors":[...]}

    def build_prompt(delighters: List[str], detractors: List[str]) -> str:
        return textwrap.dedent(f"""
        You are SharkNinja's **Star Walk Review Symptomizer**.

        Task:
        - Read each review's text and propose **up to {max_per_type} Delighters and up to {max_per_type} Detractors**.
        - Choose **only** from the allowed lists when possible.
        - If nothing fits, leave that array empty.
        - If you detect a **new** item not in the lists, put it in `new_delighters`/`new_detractors` (keep these concise, title-case).

        Allowed Delighters (examples; choose only from this list unless truly new):
        {json.dumps(delighters, ensure_ascii=False, indent=2)}

        Allowed Detractors (examples; choose only from this list unless truly new):
        {json.dumps(detractors, ensure_ascii=False, indent=2)}

        Strictly return JSON with the following schema:
        {{
          "items": [
            {{
              "row_id": <int>,              // the DataFrame index I give you
              "delighters": [<string>],     // max {max_per_type}
              "detractors": [<string>],     // max {max_per_type}
              "new_delighters": [<string>], // optional new suggestions
              "new_detractors": [<string>]
            }},
            ...
          ]
        }}
        No commentary, no markdown — **JSON only**.
        """)

    def call_llm(batch: List[Tuple[int, str]], delighters: List[str], detractors: List[str]) -> Dict[int, dict]:
        """Return mapping row_id -> suggestion dict"""
        client = OpenAI(api_key=api_key)
        # Build one user message with compact JSON inputs
        items = [{"row_id": int(i), "text": t} for i, t in batch]
        user_msg = "REVIEWS:\n" + json.dumps({"items": items}, ensure_ascii=False)

        messages = [
            {"role": "system", "content": build_prompt(delighters, detractors)},
            {"role": "user", "content": user_msg},
        ]
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content
            data = json.loads(content)
            out = {}
            for it in data.get("items", []):
                rid = int(it.get("row_id"))
                dls = [s.strip() for s in it.get("delighters", []) if s and isinstance(s, str)]
                dts = [s.strip() for s in it.get("detractors", []) if s and isinstance(s, str)]
                ndl = [s.strip() for s in it.get("new_delighters", []) if s and isinstance(s, str)]
                ndt = [s.strip() for s in it.get("new_detractors", []) if s and isinstance(s, str)]
                out[rid] = {
                    "delighters": dls[:max_per_type],
                    "detractors": dts[:max_per_type],
                    "new_delighters": ndl,
                    "new_detractors": ndt,
                }
            return out
        except Exception as e:
            st.error(f"LLM error while generating suggestions: {e}")
            return {}

    # Generate suggestions
    if st.button(f"✨ Symptomize {len(todo_idx):,} Reviews with OpenAI", type="primary", disabled=len(todo_idx) == 0):
        if not symptom_lists["delighters"] and not symptom_lists["detractors"]:
            st.warning("No symptoms found on the 'Symptoms' sheet — please add lists first.")
        else:
            suggestions_all: Dict[int, dict] = {}
            with st.spinner("Contacting OpenAI and analyzing reviews..."):
                # Prepare text column best guess
                text_col = next((c for c in ["Verbatim", "Review", "Text", "Review Text"] if c in df.columns), None)
                if not text_col:
                    st.error("Could not find a review text column (expected 'Verbatim').")
                else:
                    rows = [(int(i), str(df.loc[i, text_col])) for i in todo_idx]
                    for s in range(0, len(rows), batch_size):
                        chunk = rows[s : s + batch_size]
                        out = call_llm(chunk, symptom_lists["delighters"], symptom_lists["detractors"])
                        suggestions_all.update(out)
            st.session_state["sympto_suggestions"] = suggestions_all
            st.success(f"Suggestions ready for {len(suggestions_all):,} review(s). Review below and approve to apply.")

# ---------------- Review suggestions ----------------
sug = st.session_state.get("sympto_suggestions", {})
if sug:
    st.subheader("Review AI Suggestions (Human Approval Required)")
    text_col = next((c for c in ["Verbatim", "Review", "Text", "Review Text"] if c in df.columns), None)

    # Build table view
    records = []
    for rid, v in sug.items():
        rec = {
            "Row ID": rid,
            "Preview": shorten(str(df.loc[rid, text_col]), preview_text_len),
            "Delighters (AI)": ", ".join(v.get("delighters", [])),
            "Detractors (AI)": ", ".join(v.get("detractors", [])),
            "New Delighters": ", ".join(v.get("new_delighters", [])),
            "New Detractors": ", ".join(v.get("new_detractors", [])),
        }
        records.append(rec)
    st.dataframe(pd.DataFrame(records).sort_values("Row ID"), use_container_width=True, hide_index=True)

    # Approval of brand-new symptoms
    proposed_new_del = sorted({x for v in sug.values() for x in v.get("new_delighters", []) if x})
    proposed_new_det = sorted({x for v in sug.values() for x in v.get("new_detractors", []) if x})
    st.markdown("#### New Symptoms Detected")
    c1, c2 = st.columns(2)
    with c1:
        approved_del = st.multiselect("Approve NEW Delighters to add to list", proposed_new_del, default=[])
    with c2:
        approved_det = st.multiselect("Approve NEW Detractors to add to list", proposed_new_det, default=[])

    # Which rows to apply
    st.markdown("#### Apply To Reviews")
    chosen_ids = st.multiselect(
        "Select Row IDs to apply",
        sorted([int(k) for k in sug.keys()]),
        default=sorted([int(k) for k in sug.keys()])[: min(50, len(sug))],
        help="Choose which rows to write into Symptom 1–20 (you can apply in batches).",
    )

    def apply_suggestions_to_df(ids: List[int]):
        for rid in ids:
            v = sug.get(rid, {})
            detractors = [x for x in v.get("detractors", []) if x]
            delighters = [x for x in v.get("delighters", []) if x]
            # Write into Symptom 1..10 (detractors), 11..20 (delighters)
            for i in range(10):
                df.at[rid, f"Symptom {i+1}"] = detractors[i] if i < len(detractors) else ""
            for i in range(10):
                df.at[rid, f"Symptom {10+i+1}"] = delighters[i] if i < len(delighters) else ""

    if st.button("✅ Apply Selected Suggestions"):
        if not chosen_ids:
            st.warning("Select at least one row to apply.")
        else:
            apply_suggestions_to_df(chosen_ids)
            # Update symptom master lists with approved new items
            if approved_del:
                symptom_lists["delighters"].extend([x for x in approved_del if x not in symptom_lists["delighters"]])
            if approved_det:
                symptom_lists["detractors"].extend([x for x in approved_det if x not in symptom_lists["detractors"]])
            st.success(f"Applied to {len(chosen_ids)} row(s). You can download the updated workbook below.")

# ---------------- Validation & Download ----------------
def validate_symptom_grid(df_: pd.DataFrame, allowed: Dict[str, List[str]]) -> pd.DataFrame:
    """Return a report of out-of-list values (after approvals)."""
    allowed_all = set([*allowed["delighters"], *allowed["detractors"]])
    issues = []
    for idx, row in df_.iterrows():
        for c in SYMPTOM_COLS:
            v = _strip(row.get(c, ""))
            if v and v not in allowed_all:
                issues.append({"Row ID": idx, "Column": c, "Value": v})
    return pd.DataFrame(issues)

if st.checkbox("Show validation report (out-of-list items)", value=False):
    report = validate_symptom_grid(df, symptom_lists)
    if report.empty:
        st.success("No out-of-list values detected in Symptom 1–20.")
    else:
        st.warning(f"{len(report)} out-of-list value(s) detected.")
        st.dataframe(report, use_container_width=True, hide_index=True)

def make_download_bytes(
    df_reviews: pd.DataFrame,
    symptoms: Dict[str, List[str]],
    reviews_sheet_name: str,
    symptoms_sheet_name: str,
) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # Reviews sheet (keep same sheet name)
        df_reviews.to_excel(writer, index=False, sheet_name=reviews_sheet_name)

        # Rebuild a tidy Symptoms sheet with two columns
        max_len = max(len(symptoms["delighters"]), len(symptoms["detractors"]))
        dl = symptoms["delighters"] + [""] * (max_len - len(symptoms["delighters"]))
        dt = symptoms["detractors"] + [""] * (max_len - len(symptoms["detractors"]))
        pd.DataFrame({"Delighters": dl, "Detractors": dt}).to_excel(
            writer, index=False, sheet_name=symptoms_sheet_name or "Symptoms"
        )
    return buf.getvalue()

st.markdown("### Export")
if st.button("🧪 Final quick check (optional)"):
    problems = validate_symptom_grid(df, symptom_lists)
    if problems.empty:
        st.success("Looks good — nothing out of list.")
    else:
        st.warning("Found out-of-list entries. Review the validation report above before exporting.")

download_data = make_download_bytes(df, symptom_lists, reviews_sheet, symptoms_sheet)
st.download_button(
    "⬇️ Download Symptomized Workbook (.xlsx)",
    data=download_data,
    file_name="StarWalk_symptomized.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# ---------------- Footer help ----------------
with st.expander("How it works (quick reference)", expanded=False):
    st.markdown("""
- We detect rows where **Symptom 1–20** are empty.
- Clicking **Symptomize** sends review text to OpenAI with your **Symptoms** sheet as the allowed source of truth.
- The AI returns up to **10 Delighters** and **10 Detractors** per review (can be fewer).
- If it proposes **new** symptoms, you’ll see them listed for **approval** before they’re added to the master list.
- You choose which rows to **apply**. Nothing is written until you click **Apply**.
- Use **Validation** to catch any out-of-list values, then **Download** your updated workbook.
""")
