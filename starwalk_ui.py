# ======================= Symptomization Review+ (drop-in module) =======================
from contextlib import contextmanager

# --- Utilities for review state & undo ---
st.session_state.setdefault("REVIEW_SELECTION", set())
st.session_state.setdefault("UNDO_STACK", [])

@contextmanager
def undoable(action_name: str):
    """Push a deep copy of columns likely to change, so we can undo a batch."""
    cols = SYMPTOM_COLS + [
        APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
        APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
        APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
        APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"],
    ]
    snapshot = df[cols].copy(deep=True)
    try:
        yield
        st.session_state["UNDO_STACK"].append((action_name, snapshot))
        if len(st.session_state["UNDO_STACK"]) > 10:
            st.session_state["UNDO_STACK"] = st.session_state["UNDO_STACK"][-10:]
        st.success(f"✔ {action_name} (undo available)")
    except Exception as e:
        st.error(f"{action_name} failed: {e}")

def undo_last():
    if not st.session_state["UNDO_STACK"]:
        st.info("Nothing to undo."); return
    name, snap = st.session_state["UNDO_STACK"].pop()
    cols = list(snap.columns)
    df[cols] = snap
    st.warning(f"↩ Undid: {name}")

# --- Helpers to read current tagging state ---
def row_symptoms(row):
    detr = []; deli = []
    for j in range(1, 11):
        c = f"Symptom {j}"
        if c in row and str(row[c]).strip():
            detr.append(str(row[c]).strip())
    for j in range(11, 21):
        c = f"Symptom {j}"
        if c in row and str(row[c]).strip():
            deli.append(str(row[c]).strip())
    return detr, deli

def has_evidence(row):
    # treat any quote/voice evidence as positive
    voc = str(row.get(APP["VOC_QUOTE_COL"], "") or "").strip()
    relq = str(row.get(APP["RELIABILITY_QUOTE_COL"], "") or "").strip()
    safq = str(row.get(APP["SAFETY_EVIDENCE_COL"], "") or "").strip()
    return bool(voc or relq or safq)

def stars_bucket(v):
    try:
        s = float(v)
    except Exception:
        return "NA"
    if s <= 2.0: return "1–2"
    if s >= 4.0: return "4–5"
    return "3"

# --- Build review metrics (fast; no LLM calls) ---
def build_review_metrics(df: pd.DataFrame):
    rows = []
    empty_rows = 0
    conflicts = 0
    lowstar_only_delighters = 0
    highstar_only_detractors = 0
    ev_count = 0

    for i, r in df.iterrows():
        det, deL = row_symptoms(r)
        empty = (len(det) + len(deL) == 0)
        if empty: empty_rows += 1
        if has_evidence(r): ev_count += 1

        # conflict heuristic: same normalized token in detractors+delighters
        norm_det = {_normalize_name(x) for x in det}
        norm_del = {_normalize_name(x) for x in deL}
        if norm_det & norm_del:
            conflicts += 1

        # star alignment checks (heuristics)
        sb = stars_bucket(r.get("Star Rating", None))
        if sb == "1–2" and len(det) == 0 and len(deL) > 0:
            lowstar_only_delighters += 1
        if sb == "4–5" and len(deL) == 0 and len(det) > 0:
            highstar_only_detractors += 1

        rows.append({
            "Row": i,
            "Stars": r.get("Star Rating", None),
            "StarsBin": sb,
            "DetractorsCount": len(det),
            "DelightersCount": len(deL),
            "Evidence": has_evidence(r),
            "Safety": str(r.get(APP["SAFETY_FLAG_COL"], "")).strip().lower() == "yes",
            "Reliability": str(r.get(APP["RELIABILITY_FLAG_COL"], "")).strip().lower() == "yes",
            "Conflict": bool(norm_det & norm_del),
            "LowStarOnlyDelighters": (sb == "1–2" and len(det) == 0 and len(deL) > 0),
            "HighStarOnlyDetractors": (sb == "4–5" and len(deL) == 0 and len(det) > 0),
        })

    base = len(df) if len(df) else 1
    kpis = {
        "Rows": len(df),
        "Empty rows": empty_rows,
        "Evidence rate": round(ev_count / base, 3),
        "Conflict rate": round(conflicts / base, 3),
        "Low★ only delighters": lowstar_only_delighters,
        "High★ only detractors": highstar_only_detractors,
    }
    return pd.DataFrame(rows), kpis

def per_label_table(df: pd.DataFrame, label_side: str):
    # label_side: "Detractor" or "Delighter"
    col_range = range(1, 11) if label_side == "Detractor" else range(11, 21)
    label_counts = Counter()
    evidence_counts = Counter()
    lowstar_ct = Counter()
    highstar_ct = Counter()

    for _, r in df.iterrows():
        sb = stars_bucket(r.get("Star Rating", None))
        ev = has_evidence(r)
        for j in col_range:
            c = f"Symptom {j}"
            if c in df.columns:
                val = str(r.get(c, "")).strip()
                if val:
                    label_counts[val] += 1
                    if ev: evidence_counts[val] += 1
                    if sb == "1–2": lowstar_ct[val] += 1
                    if sb == "4–5": highstar_ct[val] += 1

    out = []
    for lab, ct in label_counts.most_common():
        evr = (evidence_counts[lab] / ct) if ct else 0.0
        out.append({
            label_side: lab,
            "Count": ct,
            "Evidence%": round(evr * 100, 1),
            "Low★%": round((lowstar_ct[lab] / ct) * 100, 1) if ct else 0.0,
            "High★%": round((highstar_ct[lab] / ct) * 100, 1) if ct else 0.0,
        })
    return pd.DataFrame(out)

# --- Quick actions on rows ---
def remove_label(row_idx: int, label: str):
    with undoable(f"Remove '{label}' from row {row_idx}"):
        # try all columns; clear first hit
        for j in range(1, 21):
            c = f"Symptom {j}"
            if c in df.columns and str(df.at[row_idx, c]).strip() == label:
                df.at[row_idx, c] = ""
                break

def move_label(row_idx: int, label: str, to_side: str):
    # to_side: "Detractor" or "Delighter"
    with undoable(f"Move '{label}' to {to_side} (row {row_idx})"):
        # remove anywhere it appears
        for j in range(1, 21):
            c = f"Symptom {j}"
            if c in df.columns and str(df.at[row_idx, c]).strip() == label:
                df.at[row_idx, c] = ""
        # add to first empty slot on target side
        if to_side == "Detractor":
            rng = range(1, 11)
        else:
            rng = range(11, 21)
        for j in rng:
            c = f"Symptom {j}"
            if c in df.columns and not str(df.at[row_idx, c]).strip():
                df.at[row_idx, c] = label
                break

# --- The Review+ UI ---
st.divider()
tab_rev, tab_labels = st.tabs(["🔎 Symptomization Review+", "🏷️ Label Drilldown"])

# ===== Tab 1: Symptomization Review+
with tab_rev:
    meta_df, kpis = build_review_metrics(df)
    # KPIs
    kpi_cols = st.columns(6)
    kpi_pairs = [
        ("Rows", kpis["Rows"]),
        ("Empty rows", kpis["Empty rows"]),
        ("Evidence rate", f"{int(kpis['Evidence rate']*100)}%"),
        ("Conflict rate", f"{int(kpis['Conflict rate']*100)}%"),
        ("Low★ only delighters", kpis["Low★ only delighters"]),
        ("High★ only detractors", kpis["High★ only detractors"]),
    ]
    for c, (k, v) in zip(kpi_cols, kpi_pairs):
        c.metric(k, v)

    st.markdown("**Anomalies & Conflicts (auto-triage)**")
    # deterministic ordering + fixed height to avoid jitter
    anomalies = meta_df[
        (meta_df["Conflict"]) |
        (meta_df["LowStarOnlyDelighters"]) |
        (meta_df["HighStarOnlyDetractors"]) |
        (meta_df["DetractorsCount"] + meta_df["DelightersCount"] == 0)
    ].sort_values(["Conflict","LowStarOnlyDelighters","HighStarOnlyDetractors","Row"], ascending=[False, False, False, True])

    if anomalies.empty:
        st.success("No anomalies detected.")
    else:
        st.dataframe(anomalies, use_container_width=True, height=260)

    st.markdown("**Evidence Gaps (rows with labels but no evidence)**")
    gaps = meta_df[
        (~meta_df["Evidence"]) &
        ((meta_df["DetractorsCount"] + meta_df["DelightersCount"]) > 0)
    ].sort_values("Row")
    st.dataframe(gaps.head(300), use_container_width=True, height=220)

    # Row inspector
    st.markdown("### Row Evidence Inspector")
    ridx = st.number_input("Row index", min_value=0, max_value=max(0, len(df)-1), value=0, step=1)
    if 0 <= ridx < len(df):
        row = df.iloc[int(ridx)]
        det, deL = row_symptoms(row)
        st.write("**Verbatim**")
        st.code(str(row.get("Verbatim",""))[:1200], language="text")
        eq = str(row.get(APP["VOC_QUOTE_COL"], "") or "") or str(row.get(APP["RELIABILITY_QUOTE_COL"], "") or "") or str(row.get(APP["SAFETY_EVIDENCE_COL"], "") or "")
        st.write("**Evidence (first available)**")
        st.info(eq if eq else "—")

        c1, c2 = st.columns(2)
        with c1:
            st.write("**Detractors**")
            if det:
                for lab in det:
                    cc1, cc2, cc3 = st.columns([4,1,1])
                    cc1.write(lab)
                    if cc2.button("➡️ to Delighter", key=f"mv_d_{ridx}_{lab}"):
                        move_label(int(ridx), lab, "Delighter")
                    if cc3.button("🗑️", key=f"rm_d_{ridx}_{lab}"):
                        remove_label(int(ridx), lab)
            else:
                st.caption("—")
        with c2:
            st.write("**Delighters**")
            if deL:
                for lab in deL:
                    cc1, cc2, cc3 = st.columns([4,1,1])
                    cc1.write(lab)
                    if cc2.button("➡️ to Detractor", key=f"mv_l_{ridx}_{lab}"):
                        move_label(int(ridx), lab, "Detractor")
                    if cc3.button("🗑️", key=f"rm_l_{ridx}_{lab}"):
                        remove_label(int(ridx), lab)
            else:
                st.caption("—")

    # Undo bar
    ucol1, ucol2, ucol3 = st.columns([1,1,6])
    with ucol1:
        if st.button("↩ Undo last"):
            undo_last()
    with ucol2:
        if st.button("Clear selection"):
            st.session_state["REVIEW_SELECTION"] = set()

# ===== Tab 2: Label Drilldown
with tab_labels:
    colA, colB = st.columns(2)

    det_tbl = per_label_table(df, "Detractor")
    del_tbl = per_label_table(df, "Delighter")

    with colA:
        st.markdown("**Top Detractors — Evidence & Star mix**")
        if det_tbl.empty:
            st.info("No detractors yet.")
        else:
            det_tbl = det_tbl.sort_values(["Count", "Detractor"], ascending=[False, True])
            st.dataframe(det_tbl, use_container_width=True, height=360)

    with colB:
        st.markdown("**Top Delighters — Evidence & Star mix**")
        if del_tbl.empty:
            st.info("No delighters yet.")
        else:
            del_tbl = del_tbl.sort_values(["Count", "Delighter"], ascending=[False, True])
            st.dataframe(del_tbl, use_container_width=True, height=360)

    st.markdown("### Quick Triage by Label")
    tri_col1, tri_col2 = st.columns([3,2])
    with tri_col1:
        target_side = st.radio("Choose label side", ["Detractor","Delighter"], horizontal=True, index=0)
        label_to_fix = st.text_input("Exact label to triage (case sensitive as written in cells)", "")
    with tri_col2:
        action = st.selectbox("Action", ["Remove from selected rows", "Move to Detractor", "Move to Delighter"], index=0)
        idx_range = st.text_input("Row indices (e.g., 0-50 or 5,8,10)", "")

    if st.button("Apply triage"):
        # parse row indices
        idxs = set()
        s = idx_range.replace(" ", "")
        try:
            if "-" in s:
                a,b = s.split("-",1); idxs = set(range(int(a), int(b)+1))
            elif "," in s:
                idxs = {int(x) for x in s.split(",") if x}
            else:
                if s.strip(): idxs = {int(s)}
        except Exception:
            st.error("Row index format invalid."); idxs = set()

        if not label_to_fix.strip():
            st.error("Provide a label."); idxs = set()

        if idxs:
            with undoable(f"Triage '{label_to_fix}' on {len(idxs)} rows"):
                for i in idxs:
                    if i<0 or i>=len(df): continue
                    if action == "Remove from selected rows":
                        remove_label(i, label_to_fix)
                    elif action == "Move to Detractor":
                        move_label(i, label_to_fix, "Detractor")
                    elif action == "Move to Delighter":
                        move_label(i, label_to_fix, "Delighter")
# ======================= End Symptomization Review+ module =======================
