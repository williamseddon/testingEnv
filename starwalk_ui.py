# -*- coding: utf-8 -*-
# Star Walk — Symptomize Reviews (v5.2)
# One-review-per-call • strict JSON • timeouts • manual sheet/column selection
# POSITONAL WRITE RULE: Detractors -> K..T (cols 11..20) • Delighters -> U..AD (cols 21..30)
#
# To run:
#   pip install streamlit openpyxl openai pandas
#   export OPENAI_API_KEY=YOUR_KEY
#   streamlit run star_walk_app_v5_2.py

import io
import os
import re
import json
from typing import List, Tuple, Dict, Any
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

import pandas as pd
import streamlit as st
from streamlit.components.v1 import html as st_html

# ===== Optional OpenAI =====
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False
    OpenAI = None  # type: ignore

# ===== Optional openpyxl (preserve formatting) =====
try:
    from openpyxl import load_workbook
    _HAS_OPENPYXL = True
except Exception:
    _HAS_OPENPYXL = False


# ---------------- Page Config ----------------
st.set_page_config(layout="wide", page_title="Star Walk — Symptomize (v5.2)")

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
    document.documentElement,{ attributes:true, attributeFilter:['data-theme'] }
  );
})();
</script>
""",
    height=0,
)

# ---------------- Styles ----------------
st.markdown("""
<style>
  :root { scroll-behavior:smooth; scroll-padding-top:72px }
  .pill{ padding:6px 10px; border-radius:999px; border:1px solid #cbd5e1; background:#f8fafc; font-weight:700; font-size:12px }
  .review-quote { white-space:pre-wrap; background:#f8fafc; border:1px solid #cbd5e1; border-radius:10px; padding:8px 10px; font-size:13px }
  .chips{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0}
  .chip{padding:6px 10px;border-radius:999px;border:1px solid #cbd5e1;background:#f8fafc;font-weight:700;font-size:.86rem}
  .chip.pos{border-color:#CDEFE1;background:#EAF9F2;color:#065F46}
  .chip.neg{border-color:#F7D1D1;background:#FDEBEB;color:#7F1D1D}
  .evd{display:block;margin-top:3px;font-size:12px;color:#334155}
</style>
""", unsafe_allow_html=True)

# ---------------- Small helpers ----------------
def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def _clean_review_text(s: str) -> str:
    if s is None: return ""
    s = str(s)
    replacements = {
        "â€™": "’", "Ã—": "×", "â€“": "–", "â€”": "—", "â€œ": "“", "â€": "”", "â€¦": "…", "Â": "",
        "Ita€™s": "It’s", "doesnd€™t": "doesn’t", "Ia€™m": "I’m",
    }
    for k,v in replacements.items(): s = s.replace(k,v)
    return s

NEGATORS = {
    "no","not","never","without","lacks","lack","isn't","wasn't","aren't","don't",
    "doesn't","didn't","can't","couldn't","won't","wouldn't","hardly","barely","rarely",
    "scarcely","little","few","free","free-of"
}

def _detect_negated_quote(quote: str, full_text: str) -> bool:
    if not quote: return False
    qn = _normalize(quote); tn = _normalize(full_text)
    i = tn.find(qn)
    if i < 0:
        toks = [t for t in qn.split() if len(t) > 2]
        for t in toks:
            j = tn.find(t)
            if j >= 0: i = j; break
    if i < 0: return False
    left_ctx = tn[max(0, i-120): i]
    toks = [t for t in left_ctx.split() if len(t) > 1][-8:]
    return any(t in NEGATORS for t in toks)

# ---------------- Prompting ----------------
PROMPT_INSTRUCTIONS = """You are Shark Glossi Review Analyzer, an AI assistant designed to evaluate and process customer reviews for the Shark Glossi (similar to SmoothStyle) hot tool.

Your job is to extract and clearly list all delighters and detractors from the review, using only the predefined items in the provided lists. Use semantics: synonyms and paraphrases may map to the closest item from the list, but DO NOT invent new items or use labels not present in the lists.

If the review mentions a concept only to state it did NOT occur (e.g., "no overheating"), do NOT mark that as a detractor.

Return STRICT JSON with this schema:
{
  "delighters": [
    {"name": "<item from delighters list>", "quote": "<short evidence snippet from the review>", "confidence": "High|Medium|Low"}
  ],
  "detractors": [
    {"name": "<item from detractors list>", "quote": "<short evidence snippet from the review>", "confidence": "High|Medium|Low"}
  ],
  "clarifications": "<optional short notes about edge cases or conflicts>",
  "confidence_overall": "High|Medium|Low"
}

Rules:
- Only choose items from the provided lists below.
- Prefer precision over recall; avoid stretches.
- Include a short verbatim evidence snippet for each selected item (exact or close paraphrase).
- If uncertain about a possible item, you may include it with confidence = "Low"; do not invent items that are not in the lists.
"""

def _build_system_message(delighters: List[str], detractors: List[str]) -> str:
    dlist = "\n".join(f"- {x}" for x in delighters)
    tlist = "\n".join(f"- {x}" for x in detractors)
    return (
        PROMPT_INSTRUCTIONS
        + "\n\n🟢 Delighters List (Look for positive mentions of):\n" + dlist
        + "\n\n🔴 Detractors List (Look for negative mentions of):\n" + tlist + "\n"
    )

def _build_user_message(review_text: str, stars: Any = None) -> str:
    s = f"Review:\n\"\"\"\n{review_text.strip()}\n\"\"\"\n"
    if stars is not None and stars != "":
        s += f"\nStar Rating: {stars}\n"
    s += "\nExtract all applicable delighters and detractors from the lists."
    return s

def _confidence_to_score(c: str) -> float:
    c = (c or "").strip().lower()
    if c.startswith("h"): return 0.9
    if c.startswith("m"): return 0.6
    if c.startswith("l"): return 0.3
    return 0.5

# ---------------- OpenAI helpers ----------------
@st.cache_resource(show_spinner=False)
def _get_openai_client_cached(key: str):
    return OpenAI(api_key=key) if (_HAS_OPENAI and key) else None

def _call_openai_for_review(client, model: str, system_prompt: str, user_prompt: str,
                            n_samples: int = 2, max_tokens: int = 700, timeout_s: int = 45) -> Dict[str, Any]:
    samples: List[Dict[str, Any]] = []
    for k in range(max(1, n_samples)):
        req = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "temperature": 0.2 + 0.1 * (k % 2),
            "timeout": timeout_s,
        }
        out = client.chat.completions.create(**req)
        content = out.choices[0].message.content or "{}"
        try:
            data = json.loads(content)
        except Exception:
            data = {"delighters": [], "detractors": [], "clarifications": "", "confidence_overall": "Medium"}
        data.setdefault("delighters", []); data.setdefault("detractors", [])
        data.setdefault("clarifications", ""); data.setdefault("confidence_overall", "Medium")
        samples.append(data)

    # Merge by vote + mean confidence; drop negated quotes lightly
    from collections import defaultdict
    agg_del: Dict[str, List[tuple]] = defaultdict(list)
    agg_det: Dict[str, List[tuple]] = defaultdict(list)
    clar_notes: List[str] = []; overall_scores: List[float] = []

    for s in samples:
        clar = (s.get("clarifications") or "").strip()
        if clar: clar_notes.append(clar)
        overall_scores.append(_confidence_to_score(s.get("confidence_overall","Medium")))
        for bucket, target in [("delighters", agg_del), ("detractors", agg_det)]:
            for it in s.get(bucket, []) or []:
                name = (it.get("name") or "").strip()
                quote = (it.get("quote") or "").strip()
                conf = _confidence_to_score(it.get("confidence") or "Medium")
                if not name: continue
                target[name].append((conf, quote))

    # pull review text for negation guard
    m = re.search(r'Review:\s*"""(.*?)"""', user_prompt, flags=re.DOTALL)
    review_text = m.group(1) if m else user_prompt

    def finalize(agg: Dict[str, List[tuple]]) -> List[Dict[str, Any]]:
        out = []
        for name, lst in agg.items():
            confs = [c for c,_ in lst]
            quotes = [q for _,q in lst if q]
            penalty = 0.0
            for q in quotes:
                if _detect_negated_quote(q, review_text):
                    penalty = max(penalty, 0.1)
            score = max(0.0, min(1.0, sum(confs)/max(1,len(confs)) - penalty))
            out.append({"name": name, "score": score, "quotes": quotes[:2]})
        out.sort(key=lambda x: (-x["score"], x["name"]))
        return out[:10]

    return {
        "delighters": finalize(agg_del),
        "detractors": finalize(agg_det),
        "clarifications": " | ".join(clar_notes)[:2000],
        "confidence_overall": ("High" if sum(overall_scores)/max(1,len(overall_scores)) >= 0.75
                               else ("Medium" if sum(overall_scores)/max(1,len(overall_scores)) >= 0.5 else "Low"))
    }

# ---------------- Excel write helpers ----------------
SYMPTOM_COLS = [f"Symptom {i}" for i in range(1, 21)]

def _write_symptoms_into_workbook_bytes(raw_bytes: bytes,
                                        review_sheet: str,
                                        df: pd.DataFrame,
                                        results: Dict[int, Dict[str, Any]],
                                        prefer_positional: bool = True) -> bytes:
    """
    Writes (POSITIVE CONTROLLED BY POSITION):
      - Detractors -> columns K..T (11..20)
      - Delighters -> columns U..AD (21..30)
    If header names are weird or out of order, positional mapping still wins.
    """
    # Fallback if openpyxl unavailable (keeps header-based writing only)
    if not _HAS_OPENPYXL:
        out = io.BytesIO()
        for idx, res in results.items():
            dets = [x["name"] for x in res.get("detractors", [])][:10]
            dels = [x["name"] for x in res.get("delighters", [])][:10]
            for j, name in enumerate(dets, start=1):
                col = f"Symptom {j}"
                if col in df.columns:
                    df.at[idx, col] = name
            for j, name in enumerate(dels, start=11):
                col = f"Symptom {j}"
                if col in df.columns:
                    df.at[idx, col] = name
        with pd.ExcelWriter(out, engine="xlsxwriter") as w:
            df.to_excel(w, index=False, sheet_name=review_sheet)
        return out.getvalue()

    bio = io.BytesIO(raw_bytes)
    wb = load_workbook(bio)
    if review_sheet not in wb.sheetnames:
        review_sheet = wb.sheetnames[0]
    ws = wb[review_sheet]

    # Build header map (for safety/clearing), but we will prioritize positions.
    headers = {ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column + 1)}

    # Resolve the 20 symptom columns by position:
    # 1) Try to find 20 "Symptom" headers and sort by column index
    # 2) Otherwise fall back to fixed K..AD (11..30)
    def _resolve_symptom_positions():
        pos_by_name = []
        for ci in range(1, ws.max_column + 1):
            val = ws.cell(row=1, column=ci).value
            if isinstance(val, str) and val.strip():
                if re.search(r"\bsymptom\b", val, flags=re.IGNORECASE):
                    pos_by_name.append(ci)
        pos_by_name = sorted(pos_by_name)
        if len(pos_by_name) >= 20:
            pos_by_name = pos_by_name[:20]  # keep left-to-right
            return pos_by_name[:10], pos_by_name[10:]
        # fallback: fixed K..AD
        detr_cols = list(range(11, 21))   # K..T
        del_cols  = list(range(21, 31))   # U..AD
        return detr_cols, del_cols

    detr_positions, del_positions = _resolve_symptom_positions()

    # If preferring positional mapping, clear K..AD globally first
    if prefer_positional:
        for excel_row in range(2, ws.max_row + 1):
            for ci in range(11, 31):  # K..AD
                ws.cell(row=excel_row, column=ci).value = None

    # Write each processed row
    for df_row_idx, res in results.items():
        excel_row = 2 + df_row_idx

        # defensive clear for this row's K..AD
        for ci in range(11, 31):
            ws.cell(row=excel_row, column=ci).value = None

        # Detractors -> K..T
        dets = [x["name"] for x in res.get("detractors", [])][:10]
        for j, name in enumerate(dets):
            if j < len(detr_positions):
                ws.cell(row=excel_row, column=detr_positions[j]).value = name

        # Delighters -> U..AD
        dels = [x["name"] for x in res.get("delighters", [])][:10]
        for j, name in enumerate(dels):
            if j < len(del_positions):
                ws.cell(row=excel_row, column=del_positions[j]).value = name

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

def _write_review_tagging_sheet(raw_bytes: bytes, tagging_rows: List[Dict[str, Any]]) -> bytes:
    if not _HAS_OPENPYXL:
        return raw_bytes
    bio = io.BytesIO(raw_bytes)
    wb = load_workbook(bio)
    if "Review Tagging" in wb.sheetnames:
        del wb["Review Tagging"]
    ws = wb.create_sheet("Review Tagging")

    headers = ["row_index","delighters","detractors","clarifications","confidence_overall","evidence_examples"]
    for ci, h in enumerate(headers, start=1):
        ws.cell(row=1, column=ci).value = h

    for ri, row in enumerate(tagging_rows, start=2):
        dels = ", ".join([x["name"] for x in row.get("delighters", [])])
        dets = ", ".join([x["name"] for x in row.get("detractors", [])])
        quotes = []
        for x in row.get("delighters", []):
            for q in x.get("quotes", [])[:1]:
                quotes.append(f"[DEL] {x['name']}: {q}")
        for x in row.get("detractors", []):
            for q in x.get("quotes", [])[:1]:
                quotes.append(f"[DET] {x['name']}: {q}")
        values = [
            row.get("row_index"),
            dels, dets,
            row.get("clarifications",""),
            row.get("confidence_overall",""),
            " | ".join(quotes)
        ]
        for ci, v in enumerate(values, start=1):
            ws.cell(row=ri, column=ci).value = v

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

# ---------------- Sidebar: Upload ----------------
with st.sidebar:
    st.header("📁 Upload Star Walk File")
    uploaded = st.file_uploader("Choose Excel File", type=["xlsx"], accept_multiple_files=False)

    st.markdown("---")
    st.subheader("⚙️ Model & Run Settings")
    model_choice = st.selectbox("Model", ["gpt-4o", "gpt-4.1", "gpt-5", "gpt-4o-mini"], index=0)
    n_samples = st.slider("Self-consistency samples", 1, 4, 2, 1)
    api_concurrency = st.slider("API concurrency", 1, 8, 3)
    max_output_tokens = st.number_input("LLM max tokens", 128, 2000, 700, 10)

    st.caption("Positional write rule is enforced: K–T = detractors, U–AD = delighters.")

    st.markdown("---")
    if st.button("🩺 Health check"):
        api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
        if not api_key:
            st.error("OPENAI_API_KEY is missing. Set it in env or `.streamlit/secrets.toml`.")
        else:
            try:
                client = _get_openai_client_cached(api_key)
                ping = client.chat.completions.create(
                    model=model_choice,
                    messages=[{"role":"system","content":"ping"},{"role":"user","content":"ping"}],
                    max_tokens=1, temperature=0, timeout=20,
                )
                st.success(f"Health OK • model `{model_choice}` reachable • key present")
            except Exception as e:
                st.exception(e)

# cache original bytes to preserve formatting when saving
if uploaded and "uploaded_bytes" not in st.session_state:
    uploaded.seek(0)
    st.session_state["uploaded_bytes"] = uploaded.read()
    uploaded.seek(0)

if not uploaded:
    st.info("Upload a .xlsx workbook to begin.")
    st.stop()

api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
if _HAS_OPENAI and not api_key:
    st.error("OPENAI_API_KEY not set. The app cannot call the model without it.")
    st.stop()

raw_bytes = st.session_state.get("uploaded_bytes", b"")

# ---------------- Config panels: pick sheets/columns explicitly ----------------
xls = pd.ExcelFile(io.BytesIO(raw_bytes))
all_sheets = xls.sheet_names

st.markdown("## 1) Configure Sources")

# Symptoms sheet + columns
with st.expander("Symptoms configuration (Delighters/Detractors)", expanded=True):
    symp_default = "Symptoms" if "Symptoms" in all_sheets else all_sheets[0]
    symp_sheet = st.selectbox("Symptoms sheet", options=all_sheets, index=all_sheets.index(symp_default))
    df_symp = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=symp_sheet)
    st.caption("Detected columns:")
    st.write(", ".join(map(str, df_symp.columns)))

    del_col = st.selectbox("Delighters column", options=list(df_symp.columns))
    det_col = st.selectbox("Detractors column", options=list(df_symp.columns))

    # build lists
    delighters = [str(x).strip() for x in df_symp[del_col].dropna().tolist() if str(x).strip()]
    detractors = [str(x).strip() for x in df_symp[det_col].dropna().tolist() if str(x).strip()]
    st.success(f"Loaded {len(delighters)} delighters, {len(detractors)} detractors from '{symp_sheet}'.")

# Reviews sheet + columns
with st.expander("Reviews configuration (text + optional stars)", expanded=True):
    default_rev_sheet = "Star Walk scrubbed verbatims" if "Star Walk scrubbed verbatims" in all_sheets else all_sheets[0]
    review_sheet = st.selectbox("Reviews sheet", options=all_sheets, index=all_sheets.index(default_rev_sheet))
    df_reviews = pd.read_excel(io.BytesIO(raw_bytes), sheet_name=review_sheet)

    st.caption("Detected columns:")
    st.write(", ".join(map(str, df_reviews.columns)))

    # let user choose explicitly
    candidate_text_cols = list(df_reviews.columns)
    try:
        idx_guess = candidate_text_cols.index("Verbatim")
    except ValueError:
        idx_guess = 0
    review_col = st.selectbox("Review text column", options=candidate_text_cols, index=idx_guess)

    star_col = st.selectbox("Star rating column (optional)", options=["(none)"] + list(df_reviews.columns))
    if star_col == "(none)":
        star_col = ""

    # preview
    if not df_reviews.empty:
        st.caption("Preview first review text:")
        preview_txt = _clean_review_text(str(df_reviews.iloc[0][review_col]))
        st.code(preview_txt[:600] + ("..." if len(preview_txt) > 600 else ""))

# Guardrails
if len(delighters) == 0 and len(detractors) == 0:
    st.error("Your delighters/detractors lists are empty. Pick the correct columns in the Symptoms configuration.")
    st.stop()
if review_col not in df_reviews.columns:
    st.error("Invalid review text column selected.")
    st.stop()

# ---------------- Prompt preview ----------------
system_msg = _build_system_message(delighters, detractors)
user_preview = _build_user_message(_clean_review_text(str(df_reviews.iloc[0][review_col])), df_reviews.iloc[0].get(star_col) if star_col else None)

with st.expander("🔎 Prompt Preview (first review)", expanded=False):
    st.markdown("**System message (top of every call):**")
    st.code(system_msg[:2000] + ("..." if len(system_msg) > 2000 else ""))
    st.markdown("**User message (example):**")
    st.code(user_preview[:2000] + ("..." if len(user_preview) > 2000 else ""))

# ---------------- KPIs ----------------
# Ensure Symptom 1..20 exist in df (for missing-row detection only; writer uses position)
SYMPTOM_COLS_DF = [c for c in [f"Symptom {i}" for i in range(1,21)] if c in df_reviews.columns]
if not SYMPTOM_COLS_DF:
    for c in [f"Symptom {i}" for i in range(1,21)]:
        df_reviews[c] = None
    SYMPTOM_COLS_DF = [f"Symptom {i}" for i in range(1,21)]

is_empty = df_reviews[SYMPTOM_COLS_DF].isna() | (
    df_reviews[SYMPTOM_COLS_DF].astype(str).applymap(lambda x: str(x).strip().upper() in {"", "NA", "N/A", "NONE", "NULL", "-"})
)
mask_empty = is_empty.all(axis=1)
missing_idx = df_reviews.index[mask_empty].tolist()
missing_count = len(missing_idx)

colA, colB, colC = st.columns([1,1,2])
with colA:
    st.markdown(f"<span class='pill'>🧾 Total reviews: <b>{len(df_reviews)}</b></span>", unsafe_allow_html=True)
with colB:
    st.markdown(f"<span class='pill'>❌ Missing symptoms: <b>{missing_count}</b></span>", unsafe_allow_html=True)
with colC:
    st.markdown(f"<span class='pill'>📚 Lists loaded: <b>{len(delighters)}</b> delighters • <b>{len(detractors)}</b> detractors</span>", unsafe_allow_html=True)

# ---------------- Session State ----------------
st.session_state.setdefault("updated_bytes", None)

# ---------------- Single-row test (debug-first) ----------------
st.markdown("## 2) Test on a single row (debug)")
row_to_test = st.number_input("Row index to test (0-based)", min_value=0, max_value=max(0, len(df_reviews)-1), value=0, step=1)
if st.button("🧪 Run test on selected row"):
    client = _get_openai_client_cached(api_key)
    if not client:
        st.error("OpenAI client unavailable.")
    else:
        text = _clean_review_text(str(df_reviews.loc[row_to_test][review_col]))
        stars = df_reviews.loc[row_to_test].get(star_col) if star_col else None
        data = _call_openai_for_review(
            client=client,
            model=model_choice,
            system_prompt=system_msg,
            user_prompt=_build_user_message(text, stars),
            n_samples=int(n_samples),
            max_tokens=int(max_output_tokens),
            timeout_s=45,
        )
        st.success(f"Found {len(data.get('delighters',[]))} delighters, {len(data.get('detractors',[]))} detractors")
        st.json(data)

# ---------------- Batch processing ----------------
st.markdown("## 3) Batch process")
batch_n = st.slider("How many to process this run", 1, 50, min(16, max(1, missing_count)) if missing_count else 16)

colX, colY = st.columns(2)
with colX:
    run = st.button(f"✨ Symptomize next {min(batch_n, missing_count)}")
with colY:
    enable_all = st.checkbox("Enable ALL (bulk)")
    run_all = st.button(f"⚡ Symptomize ALL {missing_count}", disabled=(missing_count==0 or not enable_all))

def _process_one(idx: int, client, system_msg: str):
    row = df_reviews.loc[idx]
    review_txt = _clean_review_text(str(row.get(review_col, "") or ""))
    stars = row.get(star_col, "") if star_col else ""
    data = _call_openai_for_review(
        client=client,
        model=model_choice,
        system_prompt=system_msg,
        user_prompt=_build_user_message(review_txt, stars),
        n_samples=int(n_samples),
        max_tokens=int(max_output_tokens),
        timeout_s=45,
    )
    return idx, data

if (run or run_all) and missing_idx:
    client = _get_openai_client_cached(api_key)
    if not client:
        st.error("OpenAI client unavailable.")
        st.stop()

    todo = missing_idx if run_all else missing_idx[:batch_n]
    results_by_idx: Dict[int, Dict[str, Any]] = {}
    tagging_rows: List[Dict[str, Any]] = []

    with st.status("Processing reviews...", expanded=True) as status_box:
        completed = 0
        with ThreadPoolExecutor(max_workers=int(api_concurrency)) as ex:
            futures = {ex.submit(_process_one, idx, client, system_msg): idx for idx in todo}
            while futures:
                done, not_done = wait(list(futures.keys()), timeout=15, return_when=FIRST_COMPLETED)
                if not done:
                    st.write("…working…")
                    continue
                for fut in done:
                    idx = futures.pop(fut)
                    try:
                        idx, data = fut.result()
                        results_by_idx[idx] = data
                        tagging_rows.append({"row_index": idx, **data})
                        st.write(f"Row {idx}: {len(data.get('delighters',[]))} delighters, {len(data.get('detractors',[]))} detractors")
                    except Exception as e:
                        st.error(f"Row {idx} failed: {e}")
                    finally:
                        completed += 1
        status_box.update(label="Batch finished", state="complete")

    # Write outputs back to workbook bytes (POSitional mapping enforced)
    try:
        # write symptoms into the review sheet
        updated_bytes = _write_symptoms_into_workbook_bytes(
            raw_bytes, review_sheet, df_reviews.copy(), results_by_idx, prefer_positional=True
        )
        # write the detailed tagging sheet
        updated_bytes = _write_review_tagging_sheet(updated_bytes, tagging_rows)
        st.session_state["updated_bytes"] = updated_bytes
        st.success("Workbook updated in memory. Use the download button below.")
    except Exception as e:
        st.error(f"Failed to write workbook: {e}")

# ---------------- Download ----------------
st.markdown("## 4) Download updated workbook")
if st.session_state.get("updated_bytes"):
    st.download_button(
        "Download updated workbook (.xlsx)",
        data=st.session_state["updated_bytes"],
        file_name="StarWalk_updated.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
else:
    st.caption("Run a test or batch to enable download.")



