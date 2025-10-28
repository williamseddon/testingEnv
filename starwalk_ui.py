#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
StarWalk Batch Tagger — one-by-one, model-first extraction

- Loads Delighters/Detractors from the "Symptoms" tab (no hard-coded synonyms)
- Processes reviews ONE AT A TIME (fresh prompt per review) for high accuracy
- Uses a JSON output schema for robust parsing
- Writes Detractors to Symptom 1–10, Delighters to Symptom 11–20
- Adds a "Review Tagging" sheet with detailed outputs (including evidence)
- Leaves all other workbook content intact

Usage:
  export OPENAI_API_KEY=YOUR_KEY
  python starwalk_batch_v5.py /path/to/StarWalk.xlsx --model gpt-5 --samples 3 --workers 4

Dependencies:
  pip install openai pandas openpyxl
"""

import argparse
import os
import sys
import json
import re
from typing import List, Tuple, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

# ---------------------------
# OpenAI client (official SDK)
# ---------------------------
try:
    from openai import OpenAI
    HAS_OPENAI = True
except Exception:
    HAS_OPENAI = False
    OpenAI = None  # type: ignore


# ---------------------------
# Utilities
# ---------------------------

NEGATORS = {
    "no","not","never","without","lacks","lack","isn't","wasn't","aren't","don't",
    "doesn't","didn't","can't","couldn't","won't","wouldn't","hardly","barely","rarely",
    "scarcely","little","few","free","free-of"
}

def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def looks_like_symptom_sheet(name: str) -> bool:
    n = normalize(name)
    return any(tok in n for tok in ["symptom", "palette", "taxonomy", "glossi"])

def find_symptoms_sheet(xls: pd.ExcelFile) -> str:
    # prefer sheet that "looks like" symptoms
    cands = [n for n in xls.sheet_names if looks_like_symptom_sheet(n)]
    if cands:
        # shortest name often the right one (e.g., "Symptoms")
        return min(cands, key=len)
    # else fallback
    return xls.sheet_names[0]

def load_symptom_lists_from_excel(path: str) -> Tuple[List[str], List[str], str]:
    """
    Reads the workbook and returns (delighters, detractors, sheet_name).
    Tries to auto-detect the two columns by fuzzy header matching.
    """
    xls = pd.ExcelFile(path)
    sheet = find_symptoms_sheet(xls)
    df = pd.read_excel(xls, sheet_name=sheet)

    # find likely delighter/detractor columns
    def score(col: str, want: str) -> int:
        n = normalize(col)
        wants = {
            "delighters": ["delight","delighters","pros","positive","positives","likes","good"],
            "detractors": ["detract","detractors","cons","negative","negatives","dislikes","bad","issues","problems"],
        }
        return max((1 for t in wants[want] if t in n), default=0)

    del_col = None
    det_col = None
    for c in df.columns:
        if del_col is None and score(str(c), "delighters"):
            del_col = c
        if det_col is None and score(str(c), "detractors"):
            det_col = c

    # basic fallbacks: first two non-empty columns
    if del_col is None or det_col is None:
        non_empty = []
        for c in df.columns:
            vals = [str(x).strip() for x in df[c].dropna().tolist() if str(x).strip()]
            if vals:
                non_empty.append(c)
            if len(non_empty) >= 2:
                break
        if del_col is None and non_empty:
            del_col = non_empty[0]
        if det_col is None and len(non_empty) > 1:
            det_col = non_empty[1]

    dels = []
    dets = []
    if del_col in df.columns:
        dels = [str(x).strip() for x in df[del_col].dropna().tolist() if str(x).strip()]
    if det_col in df.columns:
        dets = [str(x).strip() for x in df[det_col].dropna().tolist() if str(x).strip()]

    # de-dup while preserving order
    def dedupe(seq: List[str]) -> List[str]:
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    return dedupe(dels), dedupe(dets), sheet

def find_review_sheet_and_columns(path: str) -> Tuple[str, str, str]:
    """
    Heuristics to find the main reviews sheet and relevant columns.
    Returns (sheet_name, review_text_col, star_rating_col_or_None).
    """
    xls = pd.ExcelFile(path)
    # prioritize common names
    preferred_sheets = [
        "Star Walk scrubbed verbatims",
        "Reviews",
        "Verbatims",
        "Data",
        xls.sheet_names[0]
    ]
    sheet = None
    for cand in preferred_sheets:
        if cand in xls.sheet_names:
            sheet = cand
            break
    sheet = sheet or xls.sheet_names[0]
    df = pd.read_excel(xls, sheet_name=sheet)

    # find review text column
    text_candidates = ["Verbatim","Review Text","review","text","comments","feedback"]
    rev_col = None
    for c in df.columns:
        n = normalize(str(c))
        if any(tc in n for tc in [normalize(x) for x in text_candidates]):
            rev_col = c
            break
    if rev_col is None:
        # fallback to the longest-text column
        lens = df.astype(str).applymap(len).sum().sort_values(ascending=False)
        rev_col = lens.index[0]

    # optional star rating column
    star_col = None
    for c in df.columns:
        n = normalize(str(c))
        if any(tok in n for tok in ["star", "rating", "stars", "score"]):
            star_col = c
            break

    return sheet, rev_col, star_col or ""

def clean_review_text(s: str) -> str:
    """
    Fix common mojibake like `â€™` -> `'` to help the model parse cleanly.
    """
    if s is None:
        return ""
    s = str(s)
    replacements = {
        "â€™": "’",
        "Ã—": "×",
        "â€“": "–",
        "â€”": "—",
        "â€œ": "“",
        "â€": "”",
        "â€¦": "…",
        "Â": "",
        "Ita€™s": "It’s",
        "doesnd€™t": "doesn’t",
        "Ia€™m": "I’m",
    }
    for k,v in replacements.items():
        s = s.replace(k, v)
    return s

# ---------------------------
# Prompting
# ---------------------------

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

def build_system_message(delighters: List[str], detractors: List[str]) -> str:
    # Render the lists exactly for the model; no hard-coded synonyms in code
    dlist = "\n".join(f"- {x}" for x in delighters)
    tlist = "\n".join(f"- {x}" for x in detractors)
    return (
        PROMPT_INSTRUCTIONS
        + "\n\n🟢 Delighters List (Look for positive mentions of):\n"
        + dlist
        + "\n\n🔴 Detractors List (Look for negative mentions of):\n"
        + tlist
        + "\n"
    )

def build_user_message(review_text: str, stars: Any = None) -> str:
    s = f"Review:\n\"\"\"\n{review_text.strip()}\n\"\"\"\n"
    if stars is not None and stars != "":
        s += f"\nStar Rating: {stars}\n"
    s += "\nExtract all applicable delighters and detractors from the lists."
    return s

def confidence_to_score(c: str) -> float:
    c = (c or "").strip().lower()
    if c.startswith("h"):
        return 0.9
    if c.startswith("m"):
        return 0.6
    if c.startswith("l"):
        return 0.3
    return 0.5

def detect_negated_quote(quote: str, full_text: str) -> bool:
    """
    Light, model-agnostic negation guard (no domain rules). If a quoted span
    appears surrounded by nearby negators, we can optionally down-rank it.
    """
    if not quote:
        return False
    qn = normalize(quote)
    tn = normalize(full_text)
    i = tn.find(qn)
    if i < 0:
        # try token match
        toks = [t for t in qn.split() if len(t) > 2]
        for t in toks:
            j = tn.find(t)
            if j >= 0:
                i = j
                break
    if i < 0:
        return False
    left_ctx = tn[max(0, i-120): i]
    toks = [t for t in left_ctx.split() if len(t) > 1][-8:]
    return any(t in NEGATORS for t in toks)

# ---------------------------
# OpenAI call (one review)
# ---------------------------

def call_openai_for_review(
    client: Any,
    model: str,
    system_prompt: str,
    user_prompt: str,
    n_samples: int = 3,
    max_tokens: int = 700,
) -> Dict[str, Any]:
    """
    Calls the model multiple times (self-consistency) and merges results.
    Expects STRICT JSON as per schema. Returns merged JSON dict.
    """
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
            "temperature": 0.2 + 0.1 * (k % 2),  # tiny variation across samples
        }
        out = client.chat.completions.create(**req)
        content = out.choices[0].message.content or "{}"
        try:
            data = json.loads(content)
        except Exception:
            data = {"delighters": [], "detractors": [], "clarifications": "", "confidence_overall": "Medium"}
        # sanitize fields
        data.setdefault("delighters", [])
        data.setdefault("detractors", [])
        data.setdefault("clarifications", "")
        data.setdefault("confidence_overall", "Medium")
        samples.append(data)

    # Merge by vote + mean confidence; drop negated quotes lightly
    agg_del: Dict[str, List[Tuple[float, str]]] = {}
    agg_det: Dict[str, List[Tuple[float, str]]] = {}
    clar_notes: List[str] = []
    overall_scores: List[float] = []

    for s in samples:
        clar = (s.get("clarifications") or "").strip()
        if clar:
            clar_notes.append(clar)
        overall_scores.append(confidence_to_score(s.get("confidence_overall", "Medium")))

        for bucket, target in [("delighters", agg_del), ("detractors", agg_det)]:
            for it in s.get(bucket, []) or []:
                name = (it.get("name") or "").strip()
                quote = (it.get("quote") or "").strip()
                conf = confidence_to_score(it.get("confidence") or "Medium")
                if not name:
                    continue
                target.setdefault(name, []).append((conf, quote))

    def finalize(agg: Dict[str, List[Tuple[float, str]]], full_text: str) -> List[Dict[str, Any]]:
        out = []
        for name, lst in agg.items():
            confs = [c for c,_ in lst]
            quotes = [q for _,q in lst if q]
            # simple quote quality guard: if clearly negated nearby, down-rank a bit
            penalty = 0.0
            for q in quotes:
                if detect_negated_quote(q, full_text):
                    penalty = max(penalty, 0.1)
            score = max(0.0, min(1.0, sum(confs)/max(1,len(confs)) - penalty))
            out.append({"name": name, "score": score, "quotes": quotes[:2]})
        # sort by score desc, then name
        out.sort(key=lambda x: (-x["score"], x["name"]))
        return out

    # We need the review text for negation guard; pass via user_prompt
    review_text_match = re.search(r'Review:\s*"""(.*?)"""', user_prompt, flags=re.DOTALL)
    review_text = review_text_match.group(1) if review_text_match else user_prompt

    dels = finalize(agg_del, review_text)
    dets = finalize(agg_det, review_text)

    merged = {
        "delighters": dels,
        "detractors": dets,
        "clarifications": " | ".join(clar_notes)[:2000],
        "confidence_overall": "High" if sum(overall_scores)/max(1,len(overall_scores)) >= 0.75 else ("Medium" if sum(overall_scores)/max(1,len(overall_scores)) >= 0.5 else "Low")
    }
    return merged

# ---------------------------
# Excel writing
# ---------------------------

SYMPTOM_COLS = [f"Symptom {i}" for i in range(1, 21)]

def write_symptoms_to_sheet(
    wb_path: str,
    review_sheet: str,
    review_row_indices: List[int],
    merged_results: Dict[int, Dict[str, Any]],
):
    """
    Writes:
      - Detractors -> Symptom 1..10
      - Delighters -> Symptom 11..20
    for each row index (0-based in DataFrame, 2-based in Excel).
    Preserves the rest of workbook content.
    """
    wb = load_workbook(wb_path)
    if review_sheet not in wb.sheetnames:
        raise RuntimeError(f"Sheet '{review_sheet}' not found in workbook.")

    ws: Worksheet = wb[review_sheet]

    # Build header -> column index map
    headers = {ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column + 1)}
    # Ensure Symptom columns exist; if not, append them
    last_col = ws.max_column
    for name in SYMPTOM_COLS:
        if name not in headers or headers[name] is None:
            last_col += 1
            ws.cell(row=1, column=last_col).value = name
            headers[name] = last_col

    # write per-row
    for df_row_idx in review_row_indices:
        excel_row = 2 + df_row_idx
        res = merged_results.get(df_row_idx, {})
        dets = [x["name"] for x in res.get("detractors", [])][:10]
        dels = [x["name"] for x in res.get("delighters", [])][:10]

        # clear existing cells
        for i in range(1, 21):
            ci = headers.get(f"Symptom {i}")
            if ci:
                ws.cell(row=excel_row, column=ci).value = None

        # write dets 1..10
        for j, name in enumerate(dets, start=1):
            ci = headers.get(f"Symptom {j}")
            if ci:
                ws.cell(row=excel_row, column=ci).value = name

        # write dels 11..20
        for j, name in enumerate(dels, start=11):
            ci = headers.get(f"Symptom {j}")
            if ci:
                ws.cell(row=excel_row, column=ci).value = name

    wb.save(wb_path)

def write_review_tagging_sheet(
    wb_path: str,
    tagging_rows: List[Dict[str, Any]],
):
    """
    Writes/overwrites a sheet named 'Review Tagging' with the raw merged output:
    row_index, delighters(list), detractors(list), clarifications, confidence_overall, evidence
    """
    # Convert to DataFrame
    rows = []
    for r in tagging_rows:
        row_index = r["row_index"]
        dels = ", ".join([x["name"] for x in r["delighters"]])
        dets = ", ".join([x["name"] for x in r["detractors"]])
        # flatten a couple of example quotes
        quotes = []
        for x in r["delighters"]:
            for q in x.get("quotes", [])[:1]:
                quotes.append(f"[DEL] {x['name']}: {q}")
        for x in r["detractors"]:
            for q in x.get("quotes", [])[:1]:
                quotes.append(f"[DET] {x['name']}: {q}")
        rows.append({
            "row_index": row_index,
            "delighters": dels,
            "detractors": dets,
            "clarifications": r.get("clarifications",""),
            "confidence_overall": r.get("confidence_overall",""),
            "evidence_examples": " | ".join(quotes)
        })
    df = pd.DataFrame(rows)

    # Write with openpyxl to preserve other sheets
    wb = load_workbook(wb_path)
    if "Review Tagging" in wb.sheetnames:
        del wb["Review Tagging"]
    ws = wb.create_sheet("Review Tagging")
    # header
    headers = list(df.columns)
    for ci, h in enumerate(headers, start=1):
        ws.cell(row=1, column=ci).value = h
    # rows
    for ri, (_, row) in enumerate(df.iterrows(), start=2):
        for ci, h in enumerate(headers, start=1):
            ws.cell(row=ri, column=ci).value = row[h]
    wb.save(wb_path)

# ---------------------------
# Main
# ---------------------------

def main():
    parser = argparse.ArgumentParser(description="StarWalk batch tagger (one-by-one, JSON, no hard-coded synonyms).")
    parser.add_argument("workbook", help="Path to StarWalk Excel workbook (.xlsx)")
    parser.add_argument("--model", default=os.environ.get("STARWALK_MODEL", "gpt-5"),
                        help="OpenAI chat model (e.g., gpt-5, gpt-4.1, gpt-4o, gpt-4)")
    parser.add_argument("--samples", type=int, default=int(os.environ.get("STARWALK_SAMPLES", "3")),
                        help="Self-consistency samples per review (default 3)")
    parser.add_argument("--workers", type=int, default=int(os.environ.get("STARWALK_WORKERS", "4")),
                        help="Parallel workers (independent calls; default 4)")
    parser.add_argument("--max_tokens", type=int, default=int(os.environ.get("STARWALK_MAXTOK", "700")),
                        help="Max output tokens per call (default 700)")
    parser.add_argument("--only_missing", action="store_true",
                        help="Only process rows where Symptom 1–20 are all empty")
    args = parser.parse_args()

    if not HAS_OPENAI:
        print("ERROR: openai package not available. pip install openai", file=sys.stderr)
        sys.exit(1)
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in environment.", file=sys.stderr)
        sys.exit(1)
    client = OpenAI(api_key=api_key)

    wb_path = args.workbook
    if not os.path.exists(wb_path):
        print(f"ERROR: File not found: {wb_path}", file=sys.stderr)
        sys.exit(1)

    # Load lists
    delighters, detractors, symp_sheet = load_symptom_lists_from_excel(wb_path)
    if not delighters and not detractors:
        print("ERROR: Could not load delighters/detractors from Symptoms tab.", file=sys.stderr)
        sys.exit(1)

    # Identify review sheet & columns
    review_sheet, review_col, star_col = find_review_sheet_and_columns(wb_path)
    df = pd.read_excel(wb_path, sheet_name=review_sheet)

    # Build per-review plan
    # Determine which rows to process
    # If --only_missing, we look for Symptom 1..20 empty
    process_indices = df.index.tolist()
    if args.only_missing:
        # read with openpyxl to check existing Symptom columns
        wb = load_workbook(wb_path)
        ws = wb[review_sheet]
        headers = {ws.cell(row=1, column=ci).value: ci for ci in range(1, ws.max_column + 1)}
        sym_cols_idx = [headers.get(f"Symptom {i}") for i in range(1, 21)]
        # Build a quick map from excel to df row
        max_row = ws.max_row
        to_do = []
        for df_row in df.index:
            excel_row = df_row + 2
            if excel_row > max_row:
                to_do.append(df_row)
                continue
            # collect values
            vals = []
            for ci in sym_cols_idx:
                v = None
                if ci:
                    v = ws.cell(row=excel_row, column=ci).value
                vals.append((v or "") if v is not None else "")
            if not any(str(v).strip() for v in vals):
                to_do.append(df_row)
        process_indices = to_do

    if not process_indices:
        print("Nothing to process.")
        sys.exit(0)

    # Prepare system prompt once per run
    system_msg = build_system_message(delighters, detractors)

    # Run in parallel batches (independent calls; still one review per call)
    merged_results: Dict[int, Dict[str, Any]] = {}
    tagging_rows: List[Dict[str, Any]] = []

    def process_one(idx: int) -> Tuple[int, Dict[str, Any]]:
        row = df.loc[idx]
        text = clean_review_text(str(row.get(review_col, "") or ""))
        stars = row.get(star_col, "") if star_col else ""
        user_msg = build_user_message(text, stars)

        data = call_openai_for_review(
            client=client,
            model=args.model,
            system_prompt=system_msg,
            user_prompt=user_msg,
            n_samples=max(1, args.samples),
            max_tokens=args.max_tokens,
        )
        return idx, data

    print(f"Processing {len(process_indices)} review(s) from sheet '{review_sheet}' using model '{args.model}' with {args.samples} samples...")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futures = {ex.submit(process_one, i): i for i in process_indices}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                idx, data = fut.result()
                merged_results[idx] = data
                tagging_rows.append({
                    "row_index": idx,
                    **data
                })
                print(f"✓ Row {idx} processed: {len(data.get('delighters', []))} delighters, {len(data.get('detractors', []))} detractors")
            except Exception as e:
                print(f"✗ Row {idx} failed: {e}", file=sys.stderr)

    # Write back to workbook
    write_symptoms_to_sheet(
        wb_path=wb_path,
        review_sheet=review_sheet,
        review_row_indices=list(merged_results.keys()),
        merged_results=merged_results,
    )
    write_review_tagging_sheet(
        wb_path=wb_path,
        tagging_rows=tagging_rows,
    )

    print(f"Done. Updated workbook saved in place: {wb_path}")
    print("• Symptom 1–10 = Detractors, Symptom 11–20 = Delighters")
    print("• Detailed outputs in 'Review Tagging' sheet")

if __name__ == "__main__":
    main()
