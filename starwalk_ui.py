# ---- Crash-proof Symptomization Review+ (self-contained) ----
def render_symptomization_review(df, SYMPTOM_COLS, APP):
    import re
    import pandas as pd
    import streamlit as st
    from collections import Counter
    from contextlib import contextmanager

    # ---------- SAFE SESSION KEYS ----------
    ss = st.session_state
    if "REVIEW_UNDO_STACK" not in ss: ss["REVIEW_UNDO_STACK"] = []   # list of (name, snapshot DataFrame)
    if "REVIEW_SELECTION" not in ss: ss["REVIEW_SELECTION"] = set()   # reserved

    # ---------- LOCAL HELPERS (no external globals) ----------
    def _normalize_name(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (str(s) if s is not None else "").lower()).strip()

    def _stars_bucket(v):
        try:
            s = float(v)
        except Exception:
            return "NA"
        if s <= 2.0: return "1–2"
        if s >= 4.0: return "4–5"
        return "3"

    def _row_symptoms(row):
        detr, deli = [], []
        for j in range(1, 11):
            c = f"Symptom {j}"
            if c in row and str(row[c]).strip():
                detr.append(str(row[c]).strip())
        for j in range(11, 21):
            c = f"Symptom {j}"
            if c in row and str(row[c]).strip():
                deli.append(str(row[c]).strip())
        return detr, deli

    def _has_evidence(row):
        voc = str(row.get(APP["VOC_QUOTE_COL"], "") or "").strip()
        relq = str(row.get(APP["RELIABILITY_QUOTE_COL"], "") or "").strip()
        safq = str(row.get(APP["SAFETY_EVIDENCE_COL"], "") or "").strip()
        return bool(voc or relq or safq)

    @contextmanager
    def _undoable(action_name: str):
        try:
            cols = list(SYMPTOM_COLS) + [
                APP["SAFETY_FLAG_COL"], APP["SAFETY_EVIDENCE_COL"], APP["RELIABILITY_FLAG_COL"],
                APP["RELIABILITY_MODE_COL"], APP["RELIABILITY_COMP_COL"], APP["RELIABILITY_SEV_COL"],
                APP["RELIABILITY_RPN_COL"], APP["RELIABILITY_QUOTE_COL"], APP["SUGGESTION_SUM_COL"],
                APP["SUGGESTION_TYPE_COL"], APP["SUGGESTION_OWNER_COL"], APP["CSAT_IMPACT_COL"], APP["VOC_QUOTE_COL"],
            ]
            cols = [c for c in cols if c in df.columns]
            snapshot = df[cols].copy(deep=True)
            yield
            ss["REVIEW_UNDO_STACK"].append((action_name, snapshot))
            if len(ss["REVIEW_UNDO_STACK"]) > 10:
                ss["REVIEW_UNDO_STACK"] = ss["REVIEW_UNDO_STACK"][-10:]
            st.success(f"✔ {action_name} (undo available)")
        except Exception as e:
            st.error(f"{action_name} failed: {e}")

    def _undo_last():
        if not ss["REVIEW_UNDO_STACK"]:
            st.info("Nothing to undo."); return
        name, snap = ss["REVIEW_UNDO_STACK"].pop()
        for c in snap.columns:
            df[c] = snap[c]
        st.warning(f"↩ Undid: {name}")

    def _build_metrics(df_):
        rows = []
        empty_rows = conflicts = low_only = high_only = ev_ct = 0
        for i, r in df_.iterrows():
            det, deL = _row_symptoms(r)
            if (len(det) + len(deL)) == 0:
                empty_rows += 1
            if _has_evidence(r):
                ev_ct += 1
            nd, nl = {_normalize_name(x) for x in det}, {_normalize_name(x) for x in deL}
            if nd & nl:
                conflicts += 1
            sb = _stars_bucket(r.get("Star Rating", None))
            if sb == "1–2" and len(det) == 0 and len(deL) > 0:
                low_only += 1
            if sb == "4–5" and len(deL) == 0 and len(det) > 0:
                high_only += 1
            rows.append({
                "Row": i,
                "Stars": r.get("Star Rating", None),
                "StarsBin": sb,
                "DetractorsCount": len(det),
                "DelightersCount": len(deL),
                "Evidence": _has_evidence(r),
                "Safety": str(r.get(APP["SAFETY_FLAG_COL"], "")).strip().lower() == "yes" if APP["SAFETY_FLAG_COL"] in df_.columns else False,
                "Reliability": str(r.get(APP["RELIABILITY_FLAG_COL"], "")).strip().lower() == "yes" if APP["RELIABILITY_FLAG_COL"] in df_.columns else False,
                "Conflict": bool(nd & nl),
                "LowStarOnlyDelighters": (sb == "1–2" and len(det) == 0 and len(deL) > 0),
                "HighStarOnlyDetractors": (sb == "4–5" and len(deL) == 0 and len(det) > 0),
            })
        base = len(df_) if len(df_) else 1
        kpis = {
            "Rows": len(df_),
            "Empty rows": empty_rows,
            "Evidence rate": round(ev_ct / base, 3),
            "Conflict rate": round(conflicts / base, 3),
            "Low★ only delighters": low_only,
            "High★ only detractors": high_only,
        }
        return pd.DataFrame(rows), kpis

    def _per_label(df_, side: str):
        # side: "Detractor" or "Delighter"
        rng = range(1, 11) if side == "Detractor" else range(11, 21)
        label_counts, evid_counts, low_ct, high_ct = Counter(), Counter(), Counter(), Counter()
        for _, r in df_.iterrows():
            sb = _stars_bucket(r.get("Star Rating", None))
            ev = _has_evidence(r)
            for j in rng:
                c = f"Symptom {j}"
                if c in df_.columns:
                    v = str(r.get(c, "")).strip()
                    if v:
                        label_counts[v] += 1
                        if ev: evid_counts[v] += 1
                        if sb == "1–2": low_ct[v] += 1
                        if sb == "4–5": high_ct[v] += 1
        out = []
        for lab, ct in label_counts.most_common():
            evr = (evid_counts[lab] / ct) if ct else 0.0
            out.append({
                side: lab,
                "Count": ct,
                "Evidence%": round(evr * 100, 1),
                "Low★%": round((low_ct[lab] / ct) * 100, 1) if ct else 0.0,
                "High★%": round((high_ct[lab] / ct) * 100, 1) if ct else 0.0,
            })
        return pd.DataFrame(out)

    def _remove_label(row_idx: int, label: str):
        with _undoable(f"Remove '{label}' from row {row_idx}"):
            for j in range(1, 21):
                c = f"Symptom {j}"
                if c in df.columns and str(df.at[row_idx, c]).strip() == label:
                    df.at[row_idx, c] = ""
                    break

    def _move_label(row_idx: int, label: str, to_side: str):
        with _undoable(f"Move '{label}' to {to_side} (row {row_idx})"):
            # clear any appearances
            for j in range(1, 21):
                c = f"Symptom {j}"
                if c in df.columns and str(df.at[row_idx, c]).strip() == label:
                    df.at[row_idx, c] = ""
            # add to first empty slot on target side
            rng = range(1, 11) if to_side == "Detractor" else range(11, 21)
            for j in rng:
                c = f"Symptom {j}"
                if c in df.columns and not str(df.at[row_idx, c]).strip():
                    df.at[row_idx, c] = label
                    break

    # ---------- UI (all guarded) ----------
    try:
        st.divider()
        tab_rev, tab_labels = st.tabs(["🔎 Symptomization Review+", "🏷️ Label Drilldown"])

        # ===== Tab 1: Review =====
        with tab_rev:
            meta_df, kpis = _build_metrics(df)

            kpi_cols = st.columns(6)
            pairs = [("Rows", kpis["Rows"]),
                     ("Empty rows", kpis["Empty rows"]),
                     ("Evidence rate", f"{int(kpis['Evidence rate']*100)}%"),
                     ("Conflict rate", f"{int(kpis['Conflict rate']*100)}%"),
                     ("Low★ only delighters", kpis["Low★ only delighters"]),
                     ("High★ only detractors", kpis["High★ only detractors"])]
            for c, (k, v) in zip(kpi_cols, pairs):
                c.metric(k, v)

            st.markdown("**Anomalies & Conflicts**")
            anomalies = meta_df[
                (meta_df["Conflict"]) |
                (meta_df["LowStarOnlyDelighters"]) |
                (meta_df["HighStarOnlyDetractors"]) |
                (meta_df["DetractorsCount"] + meta_df["DelightersCount"] == 0)
            ].sort_values(["Conflict","LowStarOnlyDelighters","HighStarOnlyDetractors","Row"], ascending=[False, False, False, True])
            st.dataframe(anomalies, use_container_width=True, height=240)

            st.markdown("**Evidence Gaps**")
            gaps = meta_df[
                (~meta_df["Evidence"]) &
                ((meta_df["DetractorsCount"] + meta_df["DelightersCount"]) > 0)
            ].sort_values("Row")
            st.dataframe(gaps.head(300), use_container_width=True, height=220)

            # Inspector
            st.markdown("### Row Evidence Inspector")
            ridx = st.number_input("Row index", min_value=0, max_value=max(0, len(df)-1), value=0, step=1)
            if 0 <= ridx < len(df):
                row = df.iloc[int(ridx)]
                det, deL = _row_symptoms(row)
                st.write("**Verbatim**")
                st.code(str(row.get("Verbatim",""))[:1200], language="text")
                eq = (str(row.get(APP.get("VOC_QUOTE_COL",""), "") or "") if APP.get("VOC_QUOTE_COL","") in df.columns else "")
                if not eq:
                    eq = (str(row.get(APP.get("RELIABILITY_QUOTE_COL",""), "") or "") if APP.get("RELIABILITY_QUOTE_COL","") in df.columns else "")
                if not eq:
                    eq = (str(row.get(APP.get("SAFETY_EVIDENCE_COL",""), "") or "") if APP.get("SAFETY_EVIDENCE_COL","") in df.columns else "")
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
                                _move_label(int(ridx), lab, "Delighter")
                            if cc3.button("🗑️", key=f"rm_d_{ridx}_{lab}"):
                                _remove_label(int(ridx), lab)
                    else:
                        st.caption("—")
                with c2:
                    st.write("**Delighters**")
                    if deL:
                        for lab in deL:
                            cc1, cc2, cc3 = st.columns([4,1,1])
                            cc1.write(lab)
                            if cc2.button("➡️ to Detractor", key=f"mv_l_{ridx}_{lab}"):
                                _move_label(int(ridx), lab, "Detractor")
                            if cc3.button("🗑️", key=f"rm_l_{ridx}_{lab}"):
                                _remove_label(int(ridx), lab)
                    else:
                        st.caption("—")

                u1, u2 = st.columns([1,5])
                if u1.button("↩ Undo last"):
                    _undo_last()

        # ===== Tab 2: Label Drilldown =====
        with tab_labels:
            colA, colB = st.columns(2)
            det_tbl = _per_label(df, "Detractor")
            del_tbl = _per_label(df, "Delighter")

            with colA:
                st.markdown("**Top Detractors — Evidence & Star mix**")
                if det_tbl.empty:
                    st.info("No detractors yet.")
                else:
                    det_tbl = det_tbl.sort_values(["Count","Detractor"], ascending=[False, True])
                    st.dataframe(det_tbl, use_container_width=True, height=360)

            with colB:
                st.markdown("**Top Delighters — Evidence & Star mix**")
                if del_tbl.empty:
                    st.info("No delighters yet.")
                else:
                    del_tbl = del_tbl.sort_values(["Count","Delighter"], ascending=[False, True])
                    st.dataframe(del_tbl, use_container_width=True, height=360)

            st.markdown("### Quick Triage by Label")
            t1, t2 = st.columns([3,2])
            with t1:
                target_side = st.radio("Side", ["Detractor","Delighter"], horizontal=True, index=0)
                label_to_fix = st.text_input("Exact label (as it appears in cells)", "")
            with t2:
                action = st.selectbox("Action", ["Remove from selected rows", "Move to Detractor", "Move to Delighter"], index=0)
                idx_range = st.text_input("Row indices (e.g., 0-50 or 5,8,10)", "")

            if st.button("Apply triage"):
                # parse indices safely
                idxs = set()
                s = (idx_range or "").replace(" ", "")
                try:
                    if "-" in s:
                        a, b = s.split("-", 1); idxs = set(range(int(a), int(b) + 1))
                    elif "," in s:
                        idxs = {int(x) for x in s.split(",") if x}
                    elif s != "":
                        idxs = {int(s)}
                except Exception:
                    st.error("Row index format invalid."); idxs = set()

                if not label_to_fix.strip():
                    st.error("Provide a label name exactly as in cells.")
                elif idxs:
                    with _undoable(f"Triage '{label_to_fix}' on {len(idxs)} rows"):
                        for i in idxs:
                            if i < 0 or i >= len(df): continue
                            if action == "Remove from selected rows":
                                _remove_label(i, label_to_fix)
                            elif action == "Move to Detractor":
                                _move_label(i, label_to_fix, "Detractor")
                            elif action == "Move to Delighter":
                                _move_label(i, label_to_fix, "Delighter")
    except Exception as e:
        # Final safety net so the app NEVER hard-crashes
        st.error("Symptomization Review module encountered an issue but continued running.")
        try:
            st.write(str(e))
        except Exception:
            pass
# ---- End Review+ ----
