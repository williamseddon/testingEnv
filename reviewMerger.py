import io
import json
import time
from typing import List, Optional, Dict
from datetime import date, datetime

import numpy as np
import pandas as pd
import streamlit as st

# Optional accelerators (used if installed; guarded)
try:
    import polars as pl
    HAS_POLARS = True
except Exception:
    HAS_POLARS = False

try:
    import pyarrow as pa  # noqa: F401
    HAS_ARROW = True
except Exception:
    HAS_ARROW = False

st.set_page_config(
    page_title="Bazaarvoice Merger — Ultra/Chunked + Pressure Test",
    layout="wide"
)

# ------------------ Tunables ------------------
HEAVY_MB   = 80         # show info banner ≥ this
GIANT_MB   = 200        # show warning banner ≥ this
PREVIEW_N  = 200        # preview head rows
CACHE_TTL  = 60 * 30    # cache read() seconds
DEFAULT_CHUNK = 200_000 # Ultra chunk rows
# ----------------------------------------------

TRUE_SET  = {"true","t","1","yes","y"}
FALSE_SET = {"false","f","0","no","n"}

# --------------------------- Utilities ---------------------------

def canonicalize(name: str) -> str:
    s = str(name).lower().strip()
    return "".join(ch for ch in s if ch.isalnum())

def yes_no_from_any(series: pd.Series) -> pd.Series:
    s = series.astype("string", copy=False)
    norm = s.str.strip().str.lower()
    out = s.astype("object")
    out[norm.isin(TRUE_SET)]  = "Yes"
    out[norm.isin(FALSE_SET)] = "No"
    return out

def is_boolean_like(series: pd.Series) -> bool:
    s = series.dropna()
    if s.empty:
        return False
    vals = set(s.astype("string").str.strip().str.lower().unique())
    return vals.issubset(TRUE_SET | FALSE_SET)

def parse_short_date_col(series: pd.Series) -> pd.Series:
    """
    Robust parse → mm/dd/yyyy; invalids -> None.
    Uses vectorized path if available, else safe per-value fallback.
    """
    s = pd.Series(series).astype("object")
    dt = pd.to_datetime(s, errors="coerce", utc=False)
    try:
        out = dt.dt.strftime("%m/%d/%Y")
        return out.where(~dt.isna(), None)
    except AttributeError:
        def fmt_one(x):
            if x is None:
                return None
            try:
                if pd.isna(x) or (isinstance(x, str) and not x.strip()):
                    return None
            except Exception:
                pass
            try:
                d = pd.to_datetime([x], errors="coerce", utc=False)[0]
                if pd.isna(d):
                    return None
                try:
                    return d.tz_localize(None).strftime("%m/%d/%Y")
                except Exception:
                    return d.strftime("%m/%d/%Y")
            except Exception:
                return None
        return s.map(fmt_one)

def to_serializable(x):
    """Make any scalar JSON-safe for dumps()."""
    try:
        if x is None or pd.isna(x):
            return None
    except Exception:
        pass
    if isinstance(x, str):
        return x
    if isinstance(x, (bool, int, float)):
        return x
    if isinstance(x, (pd.Timestamp, datetime, date)):
        return x.strftime("%Y-%m-%d %H:%M:%S") if isinstance(x, datetime) else x.strftime("%Y-%m-%d")
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, (bytes, bytearray)):
        try:
            return x.decode("utf-8", "replace")
        except Exception:
            return str(x)
    if isinstance(x, (list, tuple)):
        return [to_serializable(v) for v in x]
    if isinstance(x, dict):
        return {str(k): to_serializable(v) for k, v in x.items()}
    return str(x)

def infer_country_series(product_id: pd.Series, source_name_series: pd.Series) -> pd.Series:
    pid = product_id.fillna("").astype("string")
    src = source_name_series.fillna("").astype("string")
    up_pid = pid.str.upper()
    up_src = src.str.upper()

    country = pd.Series([None]*len(pid), dtype="object")
    country = country.mask(up_pid.str.contains("UK", na=False), "UK")
    country = country.mask(up_pid.str.contains("EU", na=False), "EU")
    country = country.mask(up_src.str.contains("US|USA", na=False), "USA")
    country = country.mask(up_src.str.contains("UK", na=False) & country.isna(), "UK")
    country = country.mask(up_src.str.contains("EU", na=False) & country.isna(), "EU")
    return country

# Bazaarvoice / RR header synonyms
CANDIDATES = {
    "product_id": [
        "Product ID","ProductId","ProductID","ProductExternalId","SKU","ProductSKU","Model (SKU)","Model",
        "PRODUCTID","PRODUCT_ID","Product_External_Id"
    ],
    "review_id": [
        "Review ID","ReviewId","ReviewID","Id","id","VERBATIM ID","Verbatim Id"
    ],
    "submission_time": [
        "Submission date","Submission Date","SubmissionTime","Submission Time","SubmittedDate",
        "Review Submission Date","ReviewSubmissionDate","Date","Created At","CreatedAt","Initial publish date"
    ],
    "review_text": [
        "Review text","Review Text","ReviewText","ReviewBody","Text","content","Review","Verbatim (Review)"
    ],
    "rating": [
        "Rating","Star Rating","StarRating","Stars","RatingValue","Overall Rating","OverallRating"
    ],
    "incentivized": [
        "IncentivizedReview","Incentivized review","Incentivized","IsIncentivized","Seeded",
        "Incentivised review","IncentivisedReview","IsSeeded"
    ],
}

def get_col(df_or_cols, candidates: List[str]) -> Optional[str]:
    if isinstance(df_or_cols, (list, tuple, pd.Index)):
        cols = list(map(str, df_or_cols))
    else:
        cols = list(map(str, getattr(df_or_cols, "columns")))
    lookup = {canonicalize(c): c for c in cols}
    for cand in candidates:
        key = canonicalize(cand)
        if key in lookup:
            return lookup[key]
    return None

# ---------------------- Fast CSV readers (cached) ----------------------

@st.cache_data(show_spinner=False, ttl=CACHE_TTL, max_entries=16)
def _read_with_python_engine(raw: bytes, encoding: Optional[str]) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(raw), sep=None, engine="python", dtype=object, encoding=encoding)

@st.cache_data(show_spinner=False, ttl=CACHE_TTL, max_entries=16)
def _read_with_pyarrow_engine(raw: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(raw), sep=None, engine="pyarrow", dtype_backend="pyarrow")

@st.cache_data(show_spinner=False, ttl=CACHE_TTL, max_entries=16)
def _read_with_polars(raw: bytes, sep: Optional[str]) -> pd.DataFrame:
    import polars as pl  # local import for caching
    df_pl = pl.read_csv(io.BytesIO(raw), separator=sep, infer_schema_length=1000, ignore_errors=True)
    # use Arrow-backed EA only if pyarrow present
    return df_pl.to_pandas(use_pyarrow_extension_array=HAS_ARROW)

def try_read_csv_fast(raw: bytes, compat_mode: bool) -> Optional[pd.DataFrame]:
    if compat_mode:
        for enc in [None, "utf-8", "utf-16", "cp1252", "latin-1"]:
            try:
                return _read_with_python_engine(raw, enc)
            except Exception:
                continue
        return None

    if HAS_POLARS:
        for sep in [None, ",", "\t", ";"]:
            try:
                return _read_with_polars(raw, sep)
            except Exception:
                continue

    if HAS_ARROW:
        try:
            return _read_with_pyarrow_engine(raw)
        except Exception:
            pass

    for enc in [None, "utf-8", "utf-16", "cp1252", "latin-1"]:
        try:
            return _read_with_python_engine(raw, enc)
        except Exception:
            continue
    return None

def read_any(uploaded_file, compat_mode: bool) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    raw = uploaded_file.getvalue()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(raw), dtype=object)
    elif name.endswith((".csv", ".tsv", ".txt")):
        out = try_read_csv_fast(raw, compat_mode=compat_mode)
        if out is None:
            raise ValueError(f"Could not read delimited file {uploaded_file.name}")
        return out
    else:
        raise ValueError(f"Unsupported file type for {uploaded_file.name}")

def file_size_mb(f) -> float:
    try:
        return (getattr(f, "size", None) or 0) / (1024 * 1024)
    except Exception:
        try:
            return len(f.getvalue()) / (1024 * 1024)
        except Exception:
            return 0.0

# ----------------------- ULTRA (CSV/TSV streaming) -----------------------

def stream_formatted_csv_from_chunks(
    uploaded_files,
    constant_source: str,
    force_yes_new_review: bool,
    bool_to_yesno_flag: bool,
    compat_mode: bool,
    chunk_rows: int
) -> bytes:
    """
    Build the Formatted CSV directly in chunks — never holds full data in memory.
    CSV/TSV only.
    """
    buf = io.StringIO()
    header_written = False

    def reader_for_file(f, chunksize):
        raw = f.getvalue()
        # pandas engine="pyarrow" does NOT support chunksize; use python engine with sniffing + encoding fallbacks
        for enc in [None, "utf-8", "utf-16", "cp1252", "latin-1"]:
            try:
                return pd.read_csv(io.BytesIO(raw), sep=None, engine="python", dtype=object, encoding=enc, chunksize=chunksize)
            except Exception:
                continue
        raise ValueError(f"Could not stream-read CSV: {f.name}")

    for f in uploaded_files:
        name = f.name.lower()
        if not name.endswith((".csv", ".tsv", ".txt")):
            raise ValueError("Ultra mode supports CSV/TSV only. Convert Excel to CSV to use Ultra.")
        r = reader_for_file(f, chunk_rows)
        for chunk in r:
            chunk["_source_file"] = f.name
            pid_col  = get_col(chunk, CANDIDATES["product_id"])
            rid_col  = get_col(chunk, CANDIDATES["review_id"])
            sub_col  = get_col(chunk, CANDIDATES["submission_time"])
            txt_col  = get_col(chunk, CANDIDATES["review_text"])
            rat_col  = get_col(chunk, CANDIDATES["rating"])
            inc_col  = get_col(chunk, CANDIDATES["incentivized"])

            def safe(name):
                return chunk[name] if name in chunk.columns and name is not None else pd.Series([None]*len(chunk), dtype="object")

            product_id_s  = safe(pid_col)
            review_id_s   = safe(rid_col)
            submission_s  = safe(sub_col)
            review_text_s = safe(txt_col)
            rating_s      = safe(rat_col)
            incent_s      = safe(inc_col)
            source_file_s = chunk["_source_file"]

            seeded_s     = yes_no_from_any(incent_s) if bool_to_yesno_flag and inc_col is not None else incent_s
            country_s    = infer_country_series(product_id_s, source_file_s)
            new_review_s = pd.Series(["Yes" if force_yes_new_review else ""]*len(chunk), dtype="object")
            review_date_s= parse_short_date_col(submission_s)
            source_s     = pd.Series([constant_source or "DTC"]*len(chunk), dtype="object")

            formatted_chunk = pd.DataFrame({
                "Source": source_s,
                "Model (SKU)": product_id_s,
                "Seeded": seeded_s,
                "Country": country_s,
                "New Review": new_review_s,
                "Review Date": review_date_s,
                "Verbatim Id": review_id_s,
                "Verbatim (Review)": review_text_s,
                "Star Rating": rating_s,
            })

            buf.write(formatted_chunk.to_csv(index=False, header=(not header_written)))
            header_written = True

    return buf.getvalue().encode("utf-8-sig")  # BOM helps Excel

# ================================ UI =================================

tab_merge, tab_pressure = st.tabs(["🔀 Merge", "🧪 Pressure Test"])

# ------------------------------ MERGE TAB ------------------------------
with tab_merge:
    st.title("Bazaarvoice Merger — Ultra/Chunked")

    uploaded_files = st.file_uploader(
        "Upload Bazaarvoice files (US / UK / EU, including RR_Export)",
        type=["csv","tsv","txt","xlsx","xls"],
        accept_multiple_files=True
    )

    with st.expander("Options"):
        force_yes_new_review = st.checkbox("Set 'New Review' to Yes for all rows", value=True)
        constant_source = st.text_input("Source value", value="DTC")
        bool_to_yesno = st.checkbox("Convert boolean-like columns to Yes/No", value=True)
        include_raw_json_download = st.checkbox("Include 'Raw Data (JSON)' in downloaded Raw Excel (memory heavy)", value=False)
        compat_mode = st.checkbox("Compatibility mode (force classic pandas object dtypes)", value=False)
        skip_preview = st.checkbox("Skip preview tables (faster)", value=True)
        use_ultra = st.checkbox("ULTRA mode (CSV/TSV-only, streamed formatted CSV)", value=True)
        chunk_rows = st.number_input("Chunk size (rows per chunk, CSV/TSV only)", min_value=50_000, max_value=1_000_000, step=50_000, value=DEFAULT_CHUNK)

    if not uploaded_files:
        st.info("⬆️ Add one or more files to begin.")
        st.stop()

    # Big-file heads-up
    sizes = [(f.name, max(file_size_mb(f), 0.0)) for f in uploaded_files]
    total_mb = sum(mb for _, mb in sizes)
    st.write("**Selected files:**")
    for name, mb in sizes:
        st.write(f"• {name} — ~{mb:,.1f} MB")

    if total_mb >= GIANT_MB:
        st.warning(f"**HUGE total (~{total_mb:,.1f} MB)**. Ultra mode is recommended; consider skipping previews and JSON.")
    elif total_mb >= HEAVY_MB:
        st.info(f"**Large total (~{total_mb:,.1f} MB)**. Consider Ultra mode and skipping previews.")

    # Auto-fallback when Excel files present (Ultra supports CSV/TSV only)
    excel_files = [f.name for f in uploaded_files if f.name.lower().endswith((".xlsx", ".xls"))]
    if use_ultra and excel_files:
        st.warning(
            "Ultra mode is CSV/TSV-only. Found Excel file(s): "
            + ", ".join(excel_files)
            + ". Falling back to Standard merge. Convert Excel to CSV to use Ultra speed."
        )
        use_ultra = False

    start = st.button("🚀 Merge files")
    if not start:
        st.stop()

    t0 = time.perf_counter()

    # --------------------------- ULTRA PATH ---------------------------
    if use_ultra:
        with st.status("Streaming formatted CSV…", expanded=True) as status:
            try:
                data_bytes = stream_formatted_csv_from_chunks(
                    uploaded_files=uploaded_files,
                    constant_source=constant_source,
                    force_yes_new_review=force_yes_new_review,
                    bool_to_yesno_flag=bool_to_yesno,
                    compat_mode=compat_mode,
                    chunk_rows=int(chunk_rows),
                )
                status.update(label="Done ✅", state="complete")
            except Exception as e:
                status.update(label="Failed", state="error")
                st.exception(e)
                st.stop()

        elapsed = time.perf_counter() - t0
        st.success(f"Formatted CSV built via streaming in {elapsed:,.2f}s")
        st.download_button(
            "Download CSV (Formatted, streamed)",
            data=data_bytes,
            file_name="bv_formatted_export.csv",
            mime="text/csv",
        )
        st.info("Ultra mode skips Raw output for maximum speed & stability.")
        st.stop()

    # ------------------------- STANDARD PATH -------------------------
    with st.status("Reading & merging…", expanded=True) as status:
        status.update(label="Reading files (1/6)…")
        frames = []
        for f in uploaded_files:
            try:
                df = read_any(f, compat_mode=compat_mode)
                df["_source_file"] = f.name
                frames.append(df)
                st.write(f"✅ {f.name}: {df.shape[0]:,} rows, {df.shape[1]:,} cols")
            except Exception as e:
                st.error(f"❌ Failed to read {f.name}")
                st.exception(e)
                st.stop()

        if not frames:
            st.error("No readable files after parsing step.")
            st.stop()

        status.update(label="Union schema (2/6)…")
        union_cols: List[str] = []
        for df in frames:
            for col in map(str, df.columns):
                if col not in union_cols:
                    union_cols.append(col)

        status.update(label="Align + concat (3/6)…")
        aligned = [df.reindex(columns=union_cols) for df in frames]
        merged = pd.concat(aligned, ignore_index=True)

        status.update(label="Normalize booleans (4/6)…")
        if bool_to_yesno:
            for c in merged.columns:
                try:
                    if is_boolean_like(merged[c]):
                        merged[c] = yes_no_from_any(merged[c])
                except Exception:
                    pass

        status.update(label="Map target fields (5/6)…")
        # Manual mapping UI (overrides auto)
        st.sidebar.header("Manual Mapping (optional)")
        all_cols_sorted = ["— auto —"] + sorted(union_cols, key=lambda x: canonicalize(x))

        def mapping_control(label: str, key_name: str, default_actual: Optional[str]):
            idx = all_cols_sorted.index(default_actual) if default_actual in all_cols_sorted else 0
            return st.sidebar.selectbox(label, options=all_cols_sorted, index=idx, key=key_name)

        # auto-detect on union schema
        auto_map = {
            "product_id":      get_col(union_cols, CANDIDATES["product_id"]),
            "review_id":       get_col(union_cols, CANDIDATES["review_id"]),
            "submission_time": get_col(union_cols, CANDIDATES["submission_time"]),
            "review_text":     get_col(union_cols, CANDIDATES["review_text"]),
            "rating":          get_col(union_cols, CANDIDATES["rating"]),
            "incentivized":    get_col(union_cols, CANDIDATES["incentivized"]),
        }

        user_map = {
            "product_id":      mapping_control("Product ID → Model (SKU)", "map_product_id",      auto_map["product_id"]),
            "review_id":       mapping_control("Review ID → Verbatim Id", "map_review_id",        auto_map["review_id"]),
            "submission_time": mapping_control("Submission Date → Review Date", "map_submission", auto_map["submission_time"]),
            "review_text":     mapping_control("Review Text → Verbatim (Review)", "map_reviewtext", auto_map["review_text"]),
            "rating":          mapping_control("Rating → Star Rating", "map_rating",              auto_map["rating"]),
            "incentivized":    mapping_control("Incentivized → Seeded", "map_incent",             auto_map["incentivized"]),
        }

        final_map: Dict[str, Optional[str]] = {}
        for k, v in user_map.items():
            final_map[k] = None if v == "— auto —" else v
            if final_map[k] is None:
                final_map[k] = auto_map.get(k)

        def safe_col(name: Optional[str]) -> pd.Series:
            if name is None or name not in merged.columns:
                return pd.Series([None]*len(merged), dtype="object")
            return merged[name]

        product_id_s  = safe_col(final_map["product_id"])
        review_id_s   = safe_col(final_map["review_id"])
        submission_s  = safe_col(final_map["submission_time"])
        review_text_s = safe_col(final_map["review_text"])
        rating_s      = safe_col(final_map["rating"])
        incent_s      = safe_col(final_map["incentivized"])
        source_file_s = merged.get("_source_file", pd.Series([None]*len(merged), dtype="object"))

        seeded_s     = yes_no_from_any(incent_s) if bool_to_yesno and final_map["incentivized"] else incent_s
        country_s    = infer_country_series(product_id_s, source_file_s)
        new_review_s = pd.Series(["Yes" if force_yes_new_review else ""]*len(merged), dtype="object")
        review_date_s= parse_short_date_col(submission_s)
        source_s     = pd.Series([constant_source or "DTC"]*len(merged), dtype="object")

        formatted = pd.DataFrame({
            "Source": source_s,
            "Model (SKU)": product_id_s,
            "Seeded": seeded_s,
            "Country": country_s,
            "New Review": new_review_s,
            "Review Date": review_date_s,
            "Verbatim Id": review_id_s,
            "Verbatim (Review)": review_text_s,
            "Star Rating": rating_s,
        })

        status.update(label="Done ✅", state="complete")

    if not skip_preview:
        st.subheader(f"Preview (first {PREVIEW_N} rows)")
        t1, t2 = st.tabs(["Raw (head)", "Formatted (head)"])
        with t1:
            st.dataframe(merged.head(PREVIEW_N), use_container_width=True)
        with t2:
            st.dataframe(formatted.head(PREVIEW_N), use_container_width=True)

    st.markdown("---")
    colA, colB = st.columns(2)

    with colA:
        st.markdown("### Download Raw + Formatted (Excel)")
        if include_raw_json_download:
            merged_with_raw = merged.copy()
            merged_with_raw["Raw Data (JSON)"] = merged.apply(
                lambda r: json.dumps({str(k): to_serializable(r[k]) for k in merged.columns}, ensure_ascii=False),
                axis=1
            )
        else:
            merged_with_raw = merged

        def to_excel_bytes(sheets: Dict[str, pd.DataFrame]) -> bytes:
            bio = io.BytesIO()
            with pd.ExcelWriter(bio, engine="openpyxl") as writer:
                for name, df in sheets.items():
                    df.to_excel(writer, index=False, sheet_name=name[:31])
            bio.seek(0)
            return bio.read()

        st.download_button(
            label="Download Excel (Raw + Formatted)",
            data=to_excel_bytes({"Raw": merged_with_raw, "Formatted": formatted}),
            file_name="bv_merged_outputs.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    with colB:
        st.markdown("### Download Formatted CSV")
        st.download_button(
            label="Download CSV (Formatted)",
            data=formatted.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
            file_name="bv_formatted_export.csv",
            mime="text/csv"
        )

# --------------------------- PRESSURE TEST TAB ---------------------------
with tab_pressure:
    st.title("Pressure Test (Synthetic)")
    c1, c2, c3 = st.columns(3)
    with c1:
        files_n = st.number_input("# synthetic files", 1, 5, 3, 1)
        rows_n  = st.number_input("rows per file", 50_000, 1_500_000, 300_000, 50_000)
    with c2:
        chunk_rows_pt = st.number_input("chunk size (Ultra)", 50_000, 1_000_000, DEFAULT_CHUNK, 50_000)
        use_ultra_bench = st.checkbox("Benchmark Ultra (streamed CSV)", value=True)
    with c3:
        bool_to_yesno_pt = st.checkbox("Normalize booleans", value=True)

    def make_synth_df(n: int, i: int) -> pd.DataFrame:
        rng = np.random.default_rng(seed=1234 + i)
        df = pd.DataFrame({
            "Product ID": [f"SKU{i}-{j}{'UK' if j%11==0 else ('EU' if j%17==0 else '')}" for j in range(n)],
            "Review ID": [f"R{i}-{j}" for j in range(n)],
            "Review text": ["lorem ipsum dolor sit amet " + str(j) for j in range(n)],
            "Rating": rng.integers(1, 6, size=n),
            "IncentivizedReview": rng.choice(["true","false","yes","no","0","1"], size=n),
            "Submission date": pd.to_datetime("2024-01-01") + pd.to_timedelta(rng.integers(0, 365, size=n), unit="D"),
        })
        return df

    if st.button("Run pressure test"):
        # Build synthetic CSV files in-memory
        synth_files = []
        for i in range(int(files_n)):
            df = make_synth_df(int(rows_n), i)
            bio = io.BytesIO()
            df.to_csv(bio, index=False)
            bio.seek(0)
            class _FakeFile:
                def __init__(self, name, data):
                    self.name=name; self._data=data
                def getvalue(self):
                    return self._data.getvalue()
        # create instances after inner class
            synth_files.append(_FakeFile(f"Synth_{i}.csv", bio))

        if use_ultra_bench:
            t0 = time.perf_counter()
            data_bytes = stream_formatted_csv_from_chunks(
                uploaded_files=synth_files,
                constant_source="DTC",
                force_yes_new_review=True,
                bool_to_yesno_flag=bool_to_yesno_pt,
                compat_mode=False,
                chunk_rows=int(chunk_rows_pt),
            )
            t1 = time.perf_counter()
            st.success(f"Ultra streamed CSV size = {len(data_bytes)/1_000_000:,.2f} MB, time = {t1-t0:,.2f}s")
            st.download_button("Download Ultra output (CSV)", data=data_bytes, file_name="pressure_ultra.csv", mime="text/csv")
        else:
            st.info("Enable Ultra to benchmark the streamed path.")










