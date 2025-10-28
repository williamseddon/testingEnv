# -*- coding: utf-8 -*-
# Star Walk — Symptomize Reviews (v5.4)
# One-review-per-call • strict JSON • timeouts
# gpt-5 via Responses API (max_output_tokens) with fallback to gpt-4o
# POSITONAL WRITE RULE: Detractors -> K..T (11..20) • Delighters -> U..AD (21..30)
# NEW: Human-in-the-loop "New Symptoms" detection + approval to append to Symptoms tab
#
# To run:
#   pip install streamlit openpyxl openai pandas
#   export OPENAI_API_KEY=YOUR_KEY
#   streamlit run star_walk_app_v5_4.py

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
st.set_page_config(layout="wide", page_title="Star Walk — Symptomize (v5.4)")

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
  "novel_candidates": [
    {"name": "<not in lists>", "polarity": "delighter|detractor", "quote": "<evidence>", "confidence": "High|Medium|Low"}
  ],
  "clarifications": "<optional short notes about edge cases or conflicts>",
  "confidence_overall": "High|Medium|Low"
}

Rules:
- Only choose items from the provided lists for the delighters/detractors arrays.
- If a concept is present but not in the lists, propose it in 'novel_candidates' with polarity and evidence.
- Prefer precision over recall; avoid stretches. Include short verbatim evidence for every selection.
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
    s += "\nExtract all applicable delighters/detractors from the lists; propose truly new items in 'novel_candidates'."
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

def _call_model_for_review(client, model: str, system_prompt: str, user_prompt: str,
                           n_samples: int = 2, max_new_tokens: int = 700, timeout_s: int = 45) -> Dict[str, Any]:
    """
    gpt-5 → Responses API + max_output_tokens
    gpt-4x → Chat Completions + max_tokens
    Returns merged JSON: {delighters[], detractors[], novel[], clarifications, confidence_overall}
    """
    use_responses = str(model).lower().startswith("gpt-5")
    samples: List[Dict[str, Any]] = []

    for k in range(max(1, n_samples)):
        temp = 0.2 + 0.1 * (k % 2)
        try:
            if use_responses:
                resp = client.responses.create(
                    model=model,
                    input=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    max_output_tokens=int(max_new_tokens),
                    temperature=float(temp),
                    timeout=timeout_s,
                )
                content = getattr(resp, "output_text", None)
                if not content:
                    try:
                        first = resp.output[0].content[0]
                        content = getattr(first, "text", None) or getattr(first, "content", None) or "{}"
                    except Exception:
                        content = "{}"
            else:
                out = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=int(max_new_tokens),
                    temperature=float(temp),
                    timeout=timeout_s,
                )
                content = out.choices[0].message.content or "{}"
        except Exception as e:
            if use_responses:
                st.warning(f"gpt-5 call failed ({e}). Falling back to gpt-4o for this call.")
                model = "gpt-4o"
                use_responses = False
                out = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=int(max_new_tokens),
                    temperature=float(temp),
                    timeout=timeout_s,
                )
                content = out.choices[0].message.content or "{}"
            else:
                content = "{}"

        try:
            data = json.loads(content)
        except Exception:
            data = {"delighters": [], "detractors": [], "novel_candidates": [], "clarifications": "", "confidence_overall": "Medium"}
        data.setdefault("delighters", []); data.setdefault("detractors", [])
        data.setdefault("novel_candidates", [])
        data.setdefault("clarifications", ""); data.setdefault("confidence_overall", "Medium")
        samples.append(data)

    # ---- merge multiple samples (vote + mean confidence), guard negations ----
    from collections import defaultdict
    agg_del: Dict[str, List[tuple]] = defaultdict(list)
    agg_det: Dict[str, List[tuple]] = defaultdict(list)
    agg_novel: Dict[Tuple[str,str], List[tuple]] = defaultdict(list)  # key=(name_norm, polarity)
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
        for it in s.get("novel_candidates", []) or []:
            name = (it.get("name") or "").strip()
            pol  = (it.get("polarity") or "").strip().lower()
            quote = (it.get("quote") or "").strip()
            conf = _confidence_to_score(it.get("confidence") or "Medium")
            if name and pol in {"delighter","detractor"}:
                agg_novel[(name.lower(), pol)].append((conf, quote, name))

    # pull review text for negation guard
    m = re.search(r'Review:\s*"""(.*?)"""', user_prompt, flags=re.DOTALL)
    review_text = m.group(1) if m else user_prompt

    def finalize_main(agg: Dict[str, List[tuple]]) -> List[Dict[str, Any]]:
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

    def finalize_novel(agg: Dict[Tuple[str,str], List[tuple]]) -> List[Dict[str, Any]]:
        out = []
        for (name_norm, pol), lst in agg.items():
            confs = [c for c,_,_ in lst]
            names = [orig for _,_,orig in lst if orig]
            quotes = [q for _,q,_ in lst if q]
            score = max(0.0, min(1.0, sum(confs)/max(1,len(confs))))
            # pick the most common original casing/spelling
            display = pd.Series(names).mode()[0] if names else name_norm
            out.append({
                "name": display,
                "polarity": pol,
                "score": score,
                "votes": len(lst),
                "quotes": quotes[:2]
            })
        out.sort(key=lambda x: (-x["score"], -x["votes"], x["name"]))
        return out[:20]

    return {
        "delighters": finalize_main(agg_del),
        "detractors": finalize_main(agg_det),
        "novel": finalize_novel(agg_novel),
        "clarifications": " | ".join(clar_notes)[:2000],
        "confidence_overall": ("High" if sum(overall_scores)/max(1,len(overall_scores)) >= 0.75
                               else ("Medium" if sum(overall_scores)/max(1,len(overall_scores)) >= 0.5 else "Low"))
    }

# ---------------- Excel write helpers ----------------
def _write_symptoms_into_workbook_bytes(raw_bytes: bytes,
                                        review_sheet: str,
                                        df_reviews: pd.DataFrame,
                                        results: Dict[int, Dict[str, Any]],
                                        prefer_positional: bool = True) -> bytes:
    """
    Writes (controlled by position):
      - Detractors -> columns K..T (11..20)
      - Delighters -> columns U..AD (21..30)
    """
    if not _HAS_OPENPYXL:
        # header-based fallback
        out = io.BytesIO()
        df = df_reviews.copy()
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

    def _resolve_symptom_positions():
        pos_by_name = []
        for ci in range(1, ws.max_column + 1):
            val = ws.cell(row=1, column=ci).value
            if isinstance(val, str) and val.strip():
                if re.search(r"\bsymptom\b", val, flags=re.IGNORECASE):
                    pos_by_name.append(ci)
        pos_by_name = sorted(pos_by_name)
        if len(pos_by_name) >= 20:
            pos_by_name = pos_by_name[:20]
            return pos_by_name[:10], pos_by_name[10:]
        return list(range(11, 21)), list(range(21, 31))

    detr_positions, del_positions = _resolve_symptom_positions()

    if prefer_positional:
        for excel_row in range(2, ws.max_row + 1):
            for ci in range(11, 31):  # K..AD
                ws.cell(row=excel_row, column=ci).value = None

    for df_row_idx, res in results.items():
        excel_row = 2 + df_row_idx
        for ci in range(11, 31):
            ws.cell(row=excel_row, column=ci).value = None

        dets = [x["name"] for x in res.get("detractors", [])][:10]
        for j, name in enumerate(dets):
            if j < len(detr_positions):
                ws.cell(row=excel_row, column=detr_positions[j]).value = name

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

def _append_new_symptoms_to_workbook_bytes(raw_bytes: bytes,
                                           symp_sheet: str,
                                           del_col: str,
                                           det_col: str,
                                           add_dels: List[str],
                                           add_dets: List[str]) -> bytes:
    """
    Append approved new delighters/detractors to the Symptoms sheet (columns chosen by user).
    Skips duplicates already present (case-insensitive).
    """
    if not _HAS_OPENPYXL:
        raise RuntimeError("openpyxl is required to append to the Symptoms sheet.")

    bio = io.BytesIO(raw_bytes)
    wb = load_workbook(bio)
    if symp_sheet not in wb.sheetnames:
        raise RuntimeError(f"Symptoms sheet '{symp_sheet}' not found.")
    ws = wb[symp_sheet]

    # find col indexes
    header_row = 1
    headers = {ws.cell(row=header_row, column=ci).value: ci for ci in range(1, ws.max_column + 1)}
    if del_col not in headers or det_col not in headers:
        raise RuntimeError("Selected Symptoms columns not found in header row.")

    c_del = headers[del_col]
    c_det = headers[det_col]

    # collect existing sets (case-insensitive, non-empty)
    def col_values(ci: int) -> List[str]:
        vals = []
        for r in range(2, ws.max_row + 1):
            v = ws.cell(row=r, column=ci).value
            if v is not None and str(v).strip():
                vals.append(str(v).strip())
        return vals

    exist_dels = {v.lower() for v in col_values(c_del)}
    exist_dets = {v.lower() for v in col_values(c_det)}

    new_dels = [x for x in add_dels if x and x.lower() not in exist_dels]
    new_dets = [x for x in add_dets if x and x.lower() not in exist_dets]

    # append at bottom (keep same row count by writing after last data row)
    last_row = ws.max_row
    # Ensure at least one row for placement
    row_ptr_del = last_row + 1
    row_ptr_det = last_row + 1

    # Append by writing next empty rows beneath existing content
    # A more robust approach is to compute last non-empty row per column:
    def last_non_empty_row(ci: int) -> int:
        for r in range(ws.max_row, 1, -1):
            v = ws.cell(row=r, column=ci).value
            if v is not None and str(v).strip():
                return r
        return 1

    row_ptr_del = last_non_empty_row(c_del) + 1
    row_ptr_det = last_non_empty_row(c_det) + 1

    for v in new_dels:
        ws.cell(row=row_ptr_del, column=c_del).value = v
        row_ptr_del += 1

    for v in new_dets:
        ws.cell(row=row_ptr_det, column=c_det).value = v
        row_ptr_det += 1

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

# ---------------- Sidebar: Upload & Settings ----------------
with st.sidebar:
    st.header("📁 Upload Star Walk File")
    uploaded = st.file_uploader("Choose Excel File", type=["xlsx"], accept_multiple_files=False)

    st.markdown("---")
    st.subheader("⚙️ Model & Run Settings")
    model_choice = st.selectbox("Model", ["gpt-4o", "gpt-4.1", "gpt-5", "gpt-4o-mini"], index=0)
    n_samples = st.slider("Self-consistency samples", 1, 4, 2, 1)
    api_concurrency = st.slider("API concurrency", 1, 8, 3)
    max_output_tokens = st.number_input("Max new tokens", 128, 2000, 700, 10)
    st.caption("If gpt-5 isn't enabled for your org, the app will fall back to gpt-4o automatically.")

    st.markdown("---")
    if st.button("🩺 Health check"):
        api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
        if not api_key:
            st.error("OPENAI_API_KEY is missing. Set it in env or `.streamlit/secrets.toml`.")
        else:
            try:
                client = _get_openai_client_cached(api_key)
                if str(model_choice).lower().startswith("gpt-5"):
                    client.responses.create(
                        model=model_choice, input="ping", max_output_tokens=1, temperature=0, timeout=20,
                    )
                else:
                    client.chat.completions.create(
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

    candidate_text_cols = list(df_reviews.columns)
    try:
        idx_guess = candidate_text_cols.index("Verbatim")
    except ValueError:
        idx_guess = 0
    review_col = st.selectbox("Review text column", options=candidate_text_cols, index=idx_guess)

    star_col = st.selectbox("Star rating column (optional)", options=["(none)"] + list(df_reviews.columns))
    if star_col == "(none)":
        star_col = ""

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
st.session_state.setdefault("last_batch_rows", [])
st.session_state.setdefault("last_batch_summary", [])
st.session_state.setdefault("novel_pool", {})  # key -> dict

# ---------------- Single-row test (debug) ----------------
st.markdown("## 2) Test on a single row (debug)")
row_to_test = st.number_input("Row index to test (0-based)", min_value=0, max_value=max(0, len(df_reviews)-1), value=0, step=1)
if st.button("🧪 Run test on selected row"):
    client = _get_openai_client_cached(api_key)
    if not client:
        st.error("OpenAI client unavailable.")
    else:
        text = _clean_review_text(str(df_reviews.loc[row_to_test][review_col]))
        stars = df_reviews.loc[row_to_test].get(star_col) if star_col else None
        data = _call_model_for_review(
            client=client,
            model=model_choice,
            system_prompt=system_msg,
            user_prompt=_build_user_message(text, stars),
            n_samples=int(n_samples),
            max_new_tokens=int(max_output_tokens),
            timeout_s=45,
        )
        st.success(f"Found {len(data.get('delighters',[]))} delighters, {len(data.get('detractors',[]))} detractors, {len(data.get('novel',[]))} novel candidates")
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
    data = _call_model_for_review(
        client=client,
        model=model_choice,
        system_prompt=system_msg,
        user_prompt=_build_user_message(review_txt, stars),
        n_samples=int(n_samples),
        max_new_tokens=int(max_output_tokens),
        timeout_s=45,
    )
    return idx, data

def _novel_pool_key(name: str, pol: str) -> str:
    return f"{name.strip().lower()}|||{pol.strip().lower()}"

if (run or run_all) and missing_idx:
    client = _get_openai_client_cached(api_key)
    if not client:
        st.error("OpenAI client unavailable.")
        st.stop()

    todo = missing_idx if run_all else missing_idx[:batch_n]
    results_by_idx: Dict[int, Dict[str, Any]] = {}
    tagging_rows: List[Dict[str, Any]] = []

    long_rows = []
    summary_rows = []
    novel_pool = st.session_state.get("novel_pool", {}).copy()

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

                        # viewer rows
                        for x in data.get("delighters", []):
                            long_rows.append({
                                "row_index": idx, "type": "Delighter", "name": x["name"],
                                "score": round(float(x.get("score", 0.0)), 3),
                                "quote_1": (x.get("quotes") or [""])[0] if x.get("quotes") else "",
                                "quote_2": (x.get("quotes") or ["",""])[1] if (x.get("quotes") and len(x["quotes"])>1) else "",
                            })
                        for x in data.get("detractors", []):
                            long_rows.append({
                                "row_index": idx, "type": "Detractor", "name": x["name"],
                                "score": round(float(x.get("score", 0.0)), 3),
                                "quote_1": (x.get("quotes") or [""])[0] if x.get("quotes") else "",
                                "quote_2": (x.get("quotes") or ["",""])[1] if (x.get("quotes") and len(x["quotes"])>1) else "",
                            })

                        summary_rows.append({
                            "row_index": idx,
                            "n_delighters": len(data.get("delighters", [])),
                            "n_detractors": len(data.get("detractors", [])),
                            "n_novel": len(data.get("novel", [])),
                            "confidence_overall": data.get("confidence_overall",""),
                            "clarifications": data.get("clarifications","")[:300],
                        })

                        # collect novel candidates (not in current lists)
                        for n in data.get("novel", []):
                            name = (n.get("name") or "").strip()
                            pol  = (n.get("polarity") or "").strip().lower()
                            if not name or pol not in {"delighter","detractor"}:
                                continue
                            # skip if already in palette
                            if pol == "delighter" and any(name.lower() == x.lower() for x in delighters):
                                continue
                            if pol == "detractor" and any(name.lower() == x.lower() for x in detractors):
                                continue
                            key = _novel_pool_key(name, pol)
                            item = novel_pool.get(key, {
                                "name": name,
                                "polarity": pol,
                                "votes": 0,
                                "score_sum": 0.0,
                                "example_quotes": [],
                                "rows": set(),
                            })
                            item["votes"] += int(n.get("votes", 1) or 1)
                            item["score_sum"] += float(n.get("score", 0.5) or 0.5)
                            for q in (n.get("quotes") or []):
                                if q and q not in item["example_quotes"]:
                                    item["example_quotes"].append(q)
                            item["rows"].add(int(idx))
                            novel_pool[key] = item

                        st.write(
                            f"Row {idx}: {len(data.get('delighters',[]))} delighters, "
                            f"{len(data.get('detractors',[]))} detractors, "
                            f"{len(data.get('novel',[]))} novel"
                        )
                    except Exception as e:
                        st.error(f"Row {idx} failed: {e}")
                    finally:
                        completed += 1
        status_box.update(label="Batch finished", state="complete")

    # store viewer + novel pool
    st.session_state["last_batch_rows"] = long_rows
    st.session_state["last_batch_summary"] = summary_rows
    st.session_state["novel_pool"] = novel_pool

    # write tagging + symptoms to workbook
    try:
        updated_bytes = _write_symptoms_into_workbook_bytes(
            raw_bytes, review_sheet, df_reviews.copy(), results_by_idx, prefer_positional=True
        )
        updated_bytes = _write_review_tagging_sheet(updated_bytes, tagging_rows)
        st.session_state["updated_bytes"] = updated_bytes
        st.success("Workbook updated in memory. Use the download button below or approve new symptoms next.")
    except Exception as e:
        st.error(f"Failed to write workbook: {e}")

# ---------------- Results Viewer ----------------
st.markdown("## 4) Processed Results Viewer")
if st.session_state.get("last_batch_summary"):
    st.markdown("**Per-review summary**")
    df_sum = pd.DataFrame(st.session_state["last_batch_summary"]).sort_values("row_index")
    st.dataframe(df_sum, use_container_width=True, hide_index=True)

if st.session_state.get("last_batch_rows"):
    st.markdown("**Per-item detail (with quotes)**")
    df_long = pd.DataFrame(st.session_state["last_batch_rows"]).sort_values(["row_index","type","name"])
    st.dataframe(df_long, use_container_width=True, hide_index=True)

# ---------------- New Symptoms Approval ----------------
st.markdown("## 5) New Candidate Symptoms — Approval")

novel_pool = st.session_state.get("novel_pool", {})
if not novel_pool:
    st.caption("No novel candidates proposed in the last batch.")
else:
    # Build editable approval UI
    keys_sorted = sorted(novel_pool.keys(), key=lambda k: (-novel_pool[k]["votes"], -novel_pool[k]["score_sum"], novel_pool[k]["name"]))
    approved_dels: List[str] = []
    approved_dets: List[str] = []

    with st.form("approve_novel_form", clear_on_submit=False):
        st.write("Check the items to add. You can edit the name and change the polarity before approving.")
        for key in keys_sorted:
            item = novel_pool[key]
            cols = st.columns([0.2, 0.4, 0.2, 0.2])
            with cols[0]:
                chk = st.checkbox("Approve", key=f"approve_{key}")
            with cols[1]:
                name_edit = st.text_input("Name", value=item["name"], key=f"name_{key}")
            with cols[2]:
                pol_edit = st.selectbox("Polarity", options=["delighter","detractor"], index=0 if item["polarity"]=="delighter" else 1, key=f"pol_{key}")
            with cols[3]:
                st.markdown(f"Votes: **{item['votes']}** • Avg score: **{round(item['score_sum']/max(1,item['votes']),3)}**")

            q = item.get("example_quotes", [])
            if q:
                st.caption("Examples:")
                st.code(" | ".join(q[:2]))

            # keep temporary edits in session for re-render
            item["name"] = name_edit
            item["polarity"] = pol_edit
            novel_pool[key] = item

        # Submit section
        colL, colR = st.columns([1,1])
        with colL:
            submitted = st.form_submit_button("➕ Add selected to Symptoms tab")
        with colR:
            clear_pool = st.form_submit_button("🗑️ Clear suggestions (keep workbook)")

    st.session_state["novel_pool"] = novel_pool

    if clear_pool:
        st.session_state["novel_pool"] = {}
        st.success("Cleared novel suggestions.")

    if 'submitted' in locals() and submitted:
        if not _HAS_OPENPYXL:
            st.error("openpyxl is required to append to the Symptoms sheet. Install it and retry.")
        else:
            # gather selections
            add_dels, add_dets = [], []
            for key in keys_sorted:
                if st.session_state.get(f"approve_{key}", False):
                    name = (st.session_state.get(f"name_{key}") or "").strip()
                    pol  = st.session_state.get(f"pol_{key}")
                    if name and pol == "delighter":
                        add_dels.append(name)
                    elif name and pol == "detractor":
                        add_dets.append(name)

            if not add_dels and not add_dets:
                st.warning("No items selected.")
            else:
                try:
                    # write into Symptoms tab
                    base_bytes = st.session_state.get("updated_bytes", raw_bytes)
                    new_bytes = _append_new_symptoms_to_workbook_bytes(
                        base_bytes, symp_sheet, del_col, det_col, add_dels, add_dets
                    )
                    st.session_state["updated_bytes"] = new_bytes

                    # refresh palettes from updated file so future runs use the new items
                    df_symp2 = pd.read_excel(io.BytesIO(new_bytes), sheet_name=symp_sheet)
                    delighters = [str(x).strip() for x in df_symp2[del_col].dropna().tolist() if str(x).strip()]
                    detractors = [str(x).strip() for x in df_symp2[det_col].dropna().tolist() if str(x).strip()]

                    st.session_state["novel_pool"] = {}
                    st.success(f"Added {len(add_dels)} delighter(s) and {len(add_dets)} detractor(s) to '{symp_sheet}'. Palettes refreshed.")
                except Exception as e:
                    st.error(f"Failed to append new symptoms: {e}")

# ---------------- Download ----------------
st.markdown("## 6) Download updated workbook")
if st.session_state.get("updated_bytes"):
    st.download_button(
        "Download updated workbook (.xlsx)",
        data=st.session_state["updated_bytes"],
        file_name="StarWalk_updated.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
else:
    st.caption("Run a test or batch to enable download.")




