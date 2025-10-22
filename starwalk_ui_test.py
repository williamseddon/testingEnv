# starwalk_ui.py  — Streamlit 1.38+
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from googletrans import Translator
import io, re, html, os, warnings, json, asyncio
from streamlit.components.v1 import html as st_html

# --- silence openpyxl warnings ---
warnings.filterwarnings(
    "ignore",
    message="Data Validation extension is not supported and will be removed",
    module="openpyxl",
)

# --- optional text repair (ftfy) ---
try:
    from ftfy import fix_text as _ftfy_fix
    _HAS_FTFY = True
except Exception:
    _HAS_FTFY = False
    _ftfy_fix = None

# --- optional OpenAI SDK (LLM) ---
try:
    from openai import OpenAI
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False

NO_TEMP_MODELS = {"gpt-5", "gpt-5-chat-latest"}
def model_supports_temperature(model_id: str) -> bool:
    return model_id not in NO_TEMP_MODELS and not model_id.startswith("gpt-5")

st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# ---------- Global CSS ----------
st.markdown(
    """
    <style>
      :root { scroll-behavior: smooth; scroll-padding-top: 96px; }
      .block-container { padding-top: .5rem; padding-bottom: 1rem; }
      section[data-testid="stSidebar"] .block-container { padding-top: .25rem; padding-bottom: .6rem; }
      section[data-testid="stSidebar"] label { font-size: .95rem; }

      mark { background:#fff2a8; padding:0 .2em; border-radius:3px; }
      .review-card { border:1px solid #e6e6e6; background:#fafafa; border-radius:12px; padding:16px; }
      .review-card p { margin:.25rem 0; line-height:1.45; }
      .badges { display:flex; flex-wrap:wrap; gap:8px; margin-top:6px; }
      .badge { display:inline-block; padding:6px 10px; border-radius:8px; font-weight:600; font-size:.95rem; }
      .badge.pos { background:#CFF7D6; color:#085a2a; }
      .badge.neg { background:#FBD3D0; color:#7a0410; }

      /* Hero band */
      .hero-wrap {
        position: relative;
        overflow: hidden;
        border-radius: 14px;
        background: radial-gradient(1100px 320px at 8% -18%, #fff8d9 0%, #ffffff 55%, #ffffff 100%);
        border: 1px solid #eee;
        height: 150px;
        margin-top: .25rem;
        margin-bottom: 1rem;
      }
      #hero-canvas { position:absolute; inset:0; width:100%; height:100%; z-index:1; }
      .hero-inner { position:absolute; inset:0; display:grid; align-content:center; justify-items:center; text-align:center; pointer-events:none; z-index:2; }
      .hero-title-row { display:flex; align-items:center; gap:14px; justify-content:space-between; width: min(1100px, 92%); margin: 0 auto; }
      .hero-title { font-size: clamp(24px, 4vw, 44px); font-weight: 800; letter-spacing:.4px; margin:0; }
      .hero-sub { margin: 4px 0 0 0; color:#667085; font-size: clamp(12px, 1.1vw, 16px); }
      .sn-logo { width: 148px; height:auto; }

      /* Pagination row buffer */
      .pager { margin: 10px 0 22px 0; }

      /* Responsive symptom tables */
      .table-wrap { width: 100%; overflow-x: auto; }
      .table-wrap table { width: 100% !important; border-collapse: collapse; }
      .symptom-table th, .symptom-table td { padding: 8px 10px; }
      @media (max-width: 1400px) {
        .symptom-table { font-size: 0.95rem; }
      }
      @media (max-width: 1200px) {
        .symptom-table { font-size: 0.9rem; }
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------- HERO (stars left, logo right) ----------
def render_hero():
    sharkninja_svg = """
    <svg class="sn-logo" viewBox="0 0 520 90" fill="none" xmlns="http://www.w3.org/2000/svg" aria-label="SharkNinja">
      <g fill="#111">
        <text x="0" y="62" font-family="Inter,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial" font-weight="800" font-size="52">Shark</text>
        <rect x="225" y="12" width="4" height="66" rx="2" fill="#222"/>
        <text x="245" y="62" font-family="Inter,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial" font-weight="900" font-size="52">NINJA</text>
      </g>
    </svg>
    """.strip()

    st_html(
        f"""
        <div class="hero-wrap" id="top-hero">
          <canvas id="hero-canvas"></canvas>
          <div class="hero-inner">
            <div class="hero-title-row">
              <div style="width:220px"></div>
              <h1 class="hero-title">Star Walk Analysis Dashboard</h1>
              <div>{sharkninja_svg}</div>
            </div>
            <p class="hero-sub">Insights, trends, and ratings — fast.</p>
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
            w = Math.max(300, r.width|0);
            h = Math.max(120, r.height|0);
            c.width = w * DPR; c.height = h * DPR;
            ctx.setTransform(DPR,0,0,DPR,0,0);
          }}
          window.addEventListener('resize', resize, {{passive:true}});
          resize();
          // star cluster biased to the left
          const N = Math.max(120, Math.floor(w/12));
          function randLeft(){{
            return Math.pow(Math.random(), 1.9) * (w*0.7);
          }}
          const stars = Array.from({{length:N}}, () => ({{
            x: randLeft(), y: Math.random()*h, r: .6 + Math.random()*1.4, s: .3 + Math.random()*0.9
          }}));
          function tick(){{
            ctx.clearRect(0,0,w,h);
            for(const s of stars){{
              ctx.beginPath();
              ctx.arc((s.x%w+w)%w, (s.y%h+h)%h, s.r, 0, Math.PI*2);
              ctx.fillStyle = 'rgba(255,200,50,.9)';
              ctx.fill();
              s.x = (s.x + 0.10*s.s) % w;
            }}
            requestAnimationFrame(tick);
          }}
          tick();
        }})();
        </script>
        """,
        height=160,
    )

render_hero()

# ---------- Utils ----------
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
    for bad, good in {
        "â€™": "'", "â€˜": "‘", "â€œ": "“", "â€\x9d": "”",
        "â€“": "–", "â€”": "—", "Â": ""
    }.items():
        s = s.replace(bad, good)
    s = s.strip()
    if s.upper() in {"<NA>","NA","N/A","NULL","NONE"}:
        return pd.NA if keep_na else ""
    return s

def style_rating_cells(value):
    if isinstance(value, (float,int)):
        if value >= 4.5: return "color: green;"
        if value < 4.5:  return "color: red;"
    return ""

def apply_filter(df: pd.DataFrame, column_name: str, label: str, key: str|None=None):
    options = ["ALL"]
    if column_name in df.columns:
        col = df[column_name].astype("string")
        options += sorted([x for x in col.dropna().unique().tolist() if str(x).strip() != ""])
    selected = st.multiselect(f"Select {label}", options=options, default=["ALL"], key=key)
    if "ALL" not in selected and column_name in df.columns:
        return df[df[column_name].astype("string").isin(selected)], selected
    return df, ["ALL"]

def collect_unique_symptoms(df: pd.DataFrame, cols: list[str]) -> list[str]:
    vals, seen = [], set()
    for c in cols:
        if c in df.columns:
            s = df[c].astype("string").str.strip().dropna()
            for v in pd.unique(s.to_numpy()):
                item = str(v).strip()
                if item and item not in seen:
                    seen.add(item); vals.append(item)
    return vals

def is_valid_symptom_value(x) -> bool:
    if pd.isna(x): return False
    s = str(x).strip()
    if not s or s.upper() in {"<NA>","NA","N/A","NULL","NONE"}: return False
    return not bool(re.fullmatch(r"[\W_]+", s))

def analyze_delighters_detractors(filtered_df: pd.DataFrame, symptom_columns: list[str]) -> pd.DataFrame:
    cols = [c for c in symptom_columns if c in filtered_df.columns]
    if not cols: return pd.DataFrame(columns=["Item","Avg Star","Mentions","% Total"])
    s = (filtered_df[cols].stack(dropna=True)
         .map(lambda v: clean_text(v, keep_na=True)).dropna().astype("string").str.strip())
    s = s[s.map(is_valid_symptom_value)]
    if s.empty: return pd.DataFrame(columns=["Item","Avg Star","Mentions","% Total"])
    unique_items = pd.unique(s.to_numpy())
    results, total_rows = [], len(filtered_df)
    for item in unique_items:
        mask = filtered_df[cols].isin([item]).any(axis=1)
        count = int(mask.sum())
        if count == 0: continue
        avg_star = filtered_df.loc[mask, "Star Rating"].mean()
        pct = (count/total_rows*100) if total_rows else 0
        results.append({"Item": str(item).title(),
                        "Avg Star": round(avg_star,1) if pd.notna(avg_star) else None,
                        "Mentions": count,
                        "% Total": f"{round(pct,1)}%"})
    if not results: return pd.DataFrame(columns=["Item","Avg Star","Mentions","% Total"])
    return pd.DataFrame(results).sort_values(by="Mentions", ascending=False, ignore_index=True)

def highlight_html(text: str, keyword: str|None) -> str:
    safe = html.escape(text or "")
    if keyword:
        try:
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            safe = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe)
        except re.error: pass
    return safe

async def _translate_async_call(translator: Translator, text: str) -> str:
    try:
        res = translator.translate(text, dest="en")
        if asyncio.iscoroutine(res): res = await res
        return getattr(res, "text", text)
    except Exception:
        return text

def safe_translate(translator: Translator, text: str) -> str:
    try:
        res = translator.translate(text, dest="en")
        if hasattr(res, "text"): return res.text
        if asyncio.iscoroutine(res):
            try: return asyncio.run(_translate_async_call(translator, text))
            except RuntimeError:
                loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
                try: return loop.run_until_complete(_translate_async_call(translator, text))
                finally: loop.close()
    except Exception: pass
    return text

def apply_keyword_filter(df: pd.DataFrame, keyword: str) -> pd.DataFrame:
    if not keyword or keyword.strip() == "": return df
    if "Verbatim" not in df.columns: return df
    verb = df["Verbatim"].astype("string").fillna("").map(clean_text)
    mask = verb.str.contains(keyword.strip(), case=False, na=False)
    return df[mask]

# ---------- Upload ----------
st.markdown("### 📁 File Upload")
uploaded_file = st.file_uploader("Upload your Excel file", type=["xlsx"])

# Track fresh upload to prevent any auto-scroll after load
if uploaded_file and st.session_state.get("last_uploaded_name") != uploaded_file.name:
    st.session_state["last_uploaded_name"] = uploaded_file.name
    st.session_state["force_scroll_top_once"] = True

if uploaded_file:
    try:
        st.markdown("---")
        df = pd.read_excel(uploaded_file, sheet_name="Star Walk scrubbed verbatims")

        for col in ["Country","Source","Model (SKU)","Seeded","New Review"]:
            if col in df.columns:
                df[col] = df[col].astype("string").str.upper()

        if "Star Rating" in df.columns:
            df["Star Rating"] = pd.to_numeric(df["Star Rating"], errors="coerce")

        symptom_cols_all = [c for c in df.columns if c.startswith("Symptom")]
        for c in symptom_cols_all:
            df[c] = df[c].apply(lambda v: clean_text(v, keep_na=True)).astype("string")

        if "Verbatim" in df.columns:
            df["Verbatim"] = df["Verbatim"].astype("string").map(clean_text)
        if "Review Date" in df.columns:
            df["Review Date"] = pd.to_datetime(df["Review Date"], errors="coerce")

        # ---------- SIDEBAR ----------
        st.sidebar.header("🔍 Filters")

        # Timeframe
        with st.sidebar.expander("🗓️ Timeframe", expanded=False):
            timeframe = st.selectbox("Select Timeframe",
                                     options=["All Time","Last Week","Last Month","Last Year","Custom Range"],
                                     key="tf")
            today = datetime.today()
            start_date = end_date = None
            if timeframe == "Custom Range":
                start_date, end_date = st.date_input(
                    "Date Range",
                    value=(datetime.today() - timedelta(days=30), datetime.today()),
                    min_value=datetime(2000,1,1), max_value=datetime.today()
                )
            elif timeframe == "Last Week":  start_date, end_date = today - timedelta(days=7), today
            elif timeframe == "Last Month": start_date, end_date = today - timedelta(days=30), today
            elif timeframe == "Last Year":  start_date, end_date = today - timedelta(days=365), today

        filtered = df.copy()
        if start_date and end_date and "Review Date" in filtered.columns:
            filtered = filtered[(filtered["Review Date"] >= pd.Timestamp(start_date)) &
                                (filtered["Review Date"] <= pd.Timestamp(end_date))]

        # Star rating
        with st.sidebar.expander("🌟 Star Rating", expanded=False):
            selected_ratings = st.multiselect("Select Star Ratings", options=["All"]+[1,2,3,4,5],
                                              default=["All"], key="sr")
        if "All" not in selected_ratings and "Star Rating" in filtered.columns:
            filtered = filtered[filtered["Star Rating"].isin(selected_ratings)]

        # Standard filters
        with st.sidebar.expander("🌍 Standard Filters", expanded=False):
            filtered, _ = apply_filter(filtered, "Country", "Country", key="f_Country")
            filtered, _ = apply_filter(filtered, "Source", "Source", key="f_Source")
            filtered, _ = apply_filter(filtered, "Model (SKU)", "Model (SKU)", key="f_Model (SKU)")
            filtered, _ = apply_filter(filtered, "Seeded", "Seeded", key="f_Seeded")
            filtered, _ = apply_filter(filtered, "New Review", "New Review", key="f_New_Review")

        # Symptoms
        detractor_cols = [f"Symptom {i}" for i in range(1,11)]
        delighter_cols = [f"Symptom {i}" for i in range(11,21)]
        ex_det = [c for c in detractor_cols if c in filtered.columns]
        ex_del = [c for c in delighter_cols if c in filtered.columns]
        det_opts = collect_unique_symptoms(filtered, ex_det)
        del_opts = collect_unique_symptoms(filtered, ex_del)

        with st.sidebar.expander("🩺 Review Symptoms", expanded=False):
            sel_del = st.multiselect("Select Delighter Symptoms",
                                     options=["All"]+sorted(del_opts), default=["All"], key="delight")
            sel_det = st.multiselect("Select Detractor Symptoms",
                                     options=["All"]+sorted(det_opts), default=["All"], key="detract")
        if "All" not in sel_del and ex_del:
            filtered = filtered[filtered[ex_del].isin(sel_del).any(axis=1)]
        if "All" not in sel_det and ex_det:
            filtered = filtered[filtered[ex_det].isin(sel_det).any(axis=1)]

        # Keyword
        with st.sidebar.expander("🔎 Keyword", expanded=False):
            keyword = st.text_input("Keyword to search (in review text)", value="", key="kw",
                                    help="Case-insensitive match in Review text. Cleans â€™ → '")
            if keyword: filtered = apply_keyword_filter(filtered, keyword)

        # Additional filters (post-core, non-symptom)
        core_cols = {"Country","Source","Model (SKU)","Seeded","New Review","Star Rating","Review Date","Verbatim"}
        symptom_set = set([f"Symptom {i}" for i in range(1,21)])
        with st.sidebar.expander("📋 Additional Filters", expanded=False):
            extra_cols = [c for c in df.columns if c not in (core_cols | symptom_set)]
            if extra_cols:
                for c in extra_cols:
                    filtered, _ = apply_filter(filtered, c, c, key=f"f_{c}")
            else:
                st.info("No additional filters available.")

        # Reviews per page
        with st.sidebar.expander("📄 Review List", expanded=False):
            rpp_options = [10,20,50,100]
            default_rpp = st.session_state.get("reviews_per_page", 10)
            rpp_index = rpp_options.index(default_rpp) if default_rpp in rpp_options else 0
            rpp = st.selectbox("Reviews per page", options=rpp_options, index=rpp_index, key="rpp")
            if rpp != default_rpp:
                st.session_state["reviews_per_page"] = rpp
                st.session_state["review_page"] = 0

        # ---- Clear filters right below Review List ----
        if st.sidebar.button("🧹 Clear all filters", help="Reset all filters to defaults."):
            for k in ["tf","sr","kw","delight","detract","rpp","review_page",
                      "llm_model","llm_model_label","llm_temp","ask_text"] + \
                     [k for k in list(st.session_state.keys()) if k.startswith("f_")]:
                if k in st.session_state: del st.session_state[k]
            st.rerun()

        # LLM settings + Ask input in expander
        with st.sidebar.expander("🤖 AI Assistant (LLM)", expanded=False):
            _choices = [
                ("Fast & economical – 4o-mini", "gpt-4o-mini"),
                ("Balanced – 4o", "gpt-4o"),
                ("Advanced – 4.1", "gpt-4.1"),
                ("Most advanced – GPT-5", "gpt-5"),
                ("GPT-5 (Chat latest)", "gpt-5-chat-latest"),
            ]
            _default_model = st.session_state.get("llm_model", "gpt-4o-mini")
            _idx = next((i for i,(_,mid) in enumerate(_choices) if mid == _default_model), 0)
            label = st.selectbox("Model", options=[l for (l,_) in _choices], index=_idx, key="llm_model_label")
            st.session_state["llm_model"] = dict(_choices)[label]

            temp_supported = model_supports_temperature(st.session_state["llm_model"])
            st.session_state["llm_temp"] = st.slider(
                "Creativity (temperature)",
                min_value=0.0, max_value=1.0, value=float(st.session_state.get("llm_temp", 0.2)),
                step=0.1, disabled=not temp_supported,
                help=("Controls randomness: lower = more deterministic, higher = more creative. "
                      "Some models (e.g., GPT-5 family) use a fixed temperature and ignore this setting.")
            )
            if not temp_supported:
                st.caption("ℹ️ This model uses a fixed temperature; slider disabled.")

            st.text_input(
                "Hey! Ask anything about the CURRENTLY FILTERED reviews 🙂",
                key="ask_text", placeholder="e.g., What do first-time users in UK dislike most?"
            )
            ask_clicked = st.button("Ask")
            jump_clicked = st.button("Jump to responses")

        # Sidebar jump to feedback anchor
        if st.sidebar.button("💬 Go to feedback"):
            st.session_state["feedback_scroll_pending"] = True

        if jump_clicked:
            st.session_state["ask_scroll_pending"] = True

        # ---------------------------
        # ⭐ Star Rating Metrics (All / Organic / Seeded + % 1–2★)
        # ---------------------------
        st.markdown("### ⭐ Star Rating Metrics")

        def _calc_metrics(df_: pd.DataFrame) -> tuple[int, float, float]:
            total = len(df_)
            avg = float(df_["Star Rating"].mean()) if total else 0.0
            denom = int(df_["Star Rating"].notna().sum())
            low_cnt = int(df_.loc[df_["Star Rating"].isin([1, 2])].shape[0]) if denom else 0
            pct_low = (low_cnt / denom * 100.0) if denom else 0.0
            return total, avg, pct_low

        if "Seeded" in filtered.columns:
            seeded_mask = filtered["Seeded"].astype("string").str.upper() == "YES"
        else:
            seeded_mask = pd.Series(False, index=filtered.index)

        df_all = filtered
        df_org = filtered.loc[~seeded_mask]
        df_seed = filtered.loc[seeded_mask]

        tot_all,  avg_all,  low_all  = _calc_metrics(df_all)
        tot_org,  avg_org,  low_org  = _calc_metrics(df_org)
        tot_seed, avg_seed, low_seed = _calc_metrics(df_seed)

        st.caption("All metrics below reflect the **currently filtered** dataset.")

        cA1, cA2, cA3 = st.columns(3)
        with cA1: st.metric("All Reviews — Count", f"{tot_all:,}")
        with cA2: st.metric("All Reviews — Avg ★", f"{avg_all:.1f}")
        with cA3: st.metric("All Reviews — % 1–2★", f"{low_all:.1f}%")

        cB1, cB2, cB3 = st.columns(3)
        with cB1: st.metric("Organic (non-Seeded) — Count", f"{tot_org:,}")
        with cB2: st.metric("Organic — Avg ★", f"{avg_org:.1f}")
        with cB3: st.metric("Organic — % 1–2★", f"{low_org:.1f}%")

        cC1, cC2, cC3 = st.columns(3)
        with cC1: st.metric("Seeded — Count", f"{tot_seed:,}")
        with cC2: st.metric("Seeded — Avg ★", f"{avg_seed:.1f}")
        with cC3: st.metric("Seeded — % 1–2★", f"{low_seed:.1f}%")

        # Keep distribution chart for the current filtered set
        star_counts = filtered["Star Rating"].value_counts().sort_index()
        total_reviews = len(filtered)
        percentages = ((star_counts / total_reviews * 100).round(1)) if total_reviews else (star_counts * 0)
        star_labels = [f"{int(star)} stars" for star in star_counts.index]

        fig = go.Figure(go.Bar(
            x=star_counts.values, y=star_labels, orientation="h",
            text=[f"{value} reviews ({percentages.get(idx, 0)}%)"
                  for idx, value in zip(star_counts.index, star_counts.values)],
            textposition="auto",
            marker=dict(color=["#FFA07A","#FA8072","#FFD700","#ADFF2F","#32CD32"]),
            hoverinfo="y+x+text"
        ))
        fig.update_layout(
            title="<b>Star Rating Distribution</b>",
            xaxis=dict(title="Number of Reviews", showgrid=False),
            yaxis=dict(title="Star Ratings", showgrid=False),
            template="plotly_white", margin=dict(l=40,r=40,t=45,b=40)
        )
        st.plotly_chart(fig, use_container_width=True)

        # ---------------------------
        # 🌍 Country Breakdown (unchanged layout)
        # ---------------------------
        st.markdown("### 🌍 Country-Specific Breakdown")
        if "Country" in filtered.columns and "Source" in filtered.columns:
            new_rev = filtered[filtered["New Review"].astype("string").str.upper() == "YES"]

            cs = (filtered.groupby(["Country","Source"])
                          .agg(Average_Rating=("Star Rating","mean"),
                               Review_Count=("Star Rating","count")).reset_index())
            nrs = (new_rev.groupby(["Country","Source"])
                          .agg(New_Review_Average=("Star Rating","mean"),
                               New_Review_Count=("Star Rating","count")).reset_index())
            cs = cs.merge(nrs, on=["Country","Source"], how="left")

            overall = (filtered.groupby("Country")
                               .agg(Average_Rating=("Star Rating","mean"),
                                    Review_Count=("Star Rating","count")).reset_index())
            overall_new = (new_rev.groupby("Country")
                                  .agg(New_Review_Average=("Star Rating","mean"),
                                       New_Review_Count=("Star Rating","count")).reset_index())
            overall = overall.merge(overall_new, on="Country", how="left")
            overall["Source"] = "Overall"

            def color_num(v):
                if pd.isna(v): return ""
                try: v=float(v)
                except: return ""
                if v>=4.5: return "color: green;"
                if v<4.5:  return "color: red;"
                return ""

            def fmt_r(v): return "-" if pd.isna(v) else f"{v:.1f}"
            def fmt_c(v): return "-" if pd.isna(v) else f"{int(v):,}"

            for country in overall["Country"].unique():
                st.markdown(f"#### {country}")
                cd = cs[cs["Country"]==country]
                ov = overall[overall["Country"]==country]
                comb = pd.concat([cd, ov], ignore_index=True)
                comb["Sort_Order"] = comb["Source"].apply(lambda x: 1 if x=="Overall" else 0)
                comb = comb.sort_values("Sort_Order").drop(columns=["Sort_Order"])
                comb = comb.drop(columns=["Country"]).rename(columns={
                    "Average_Rating":"Avg Rating","Review_Count":"Review Count",
                    "New_Review_Average":"New Review Average","New_Review_Count":"New Review Count"
                })

                def bold_overall(row):
                    if row["Source"]=="Overall": return ["font-weight:bold;"]*len(row)
                    return [""]*len(row)

                styled = (comb.style
                          .format({"Avg Rating":fmt_r,"Review Count":fmt_c,
                                   "New Review Average":fmt_r,"New Review Count":fmt_c})
                          .applymap(color_num, subset=["Avg Rating","New Review Average"])
                          .apply(bold_overall, axis=1)
                          .set_properties(**{"text-align":"center"})
                          .set_table_styles([
                              {"selector":"th","props":[("text-align","center")]},
                              {"selector":"td","props":[("text-align","center")]}]))
                st.markdown(styled.to_html(escape=False, index=False), unsafe_allow_html=True)
        else:
            st.warning("Country or Source data is missing in the uploaded file.")

        st.markdown("---")

        # ---------------------------
        # 🩺 Symptom Tables (responsive / scrollable)
        # ---------------------------
        st.markdown("### 🩺 Symptom Tables")
        det_tbl = analyze_delighters_detractors(filtered, ex_det).head(20)
        del_tbl = analyze_delighters_detractors(filtered, ex_del).head(20)

        def _styled_html(df_: pd.DataFrame) -> str:
            if df_.empty: 
                return "<em>No data.</em>"
            styled = (df_.style
                         .applymap(style_rating_cells, subset=["Avg Star"])
                         .format({"Avg Star":"{:.1f}","Mentions":"{:.0f}"})
                         .hide(axis="index"))
            return f"<div class='table-wrap symptom-table'>{styled.to_html(escape=False)}</div>"

        view_mode = st.radio("View mode", ["Split","Tabs"], horizontal=True, index=0)

        if view_mode=="Split":
            c1,c2 = st.columns([1,1])
            with c1:
                st.subheader("All Detractors")
                st.markdown(_styled_html(det_tbl), unsafe_allow_html=True)
            with c2:
                st.subheader("All Delighters")
                st.markdown(_styled_html(del_tbl), unsafe_allow_html=True)
        else:
            t1,t2 = st.tabs(["All Detractors","All Delighters"])
            with t1:
                st.markdown(_styled_html(det_tbl), unsafe_allow_html=True)
            with t2:
                st.markdown(_styled_html(del_tbl), unsafe_allow_html=True)

        st.markdown("---")

        # ---------------------------
        # 📝 All Reviews (with pagination)
        # ---------------------------
        st.markdown("### 📝 All Reviews")
        translator = Translator()

        if not filtered.empty:
            csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Download ALL filtered reviews (CSV)", csv_bytes,
                               file_name="filtered_reviews.csv", mime="text/csv")

        translate_all = st.button("Translate All Reviews to English")

        reviews_per_page = st.session_state.get("reviews_per_page", 10)
        if "review_page" not in st.session_state: st.session_state["review_page"]=0

        total = len(filtered)
        total_pages = max((total + reviews_per_page - 1)//reviews_per_page, 1)
        current_page = min(max(st.session_state["review_page"], 0), total_pages-1)
        start, end = current_page*reviews_per_page, current_page*reviews_per_page+reviews_per_page
        page_df = filtered.iloc[start:end]

        if page_df.empty:
            st.warning("No reviews match the selected criteria.")
        else:
            for _, row in page_df.iterrows():
                review_text = row.get("Verbatim", pd.NA)
                review_text = "" if pd.isna(review_text) else clean_text(review_text)
                translated = safe_translate(translator, review_text) if translate_all else review_text

                date_val = row.get("Review Date", pd.NaT)
                if pd.isna(date_val): date_str = "-"
                else:
                    try: date_str = pd.to_datetime(date_val).strftime("%Y-%m-%d")
                    except Exception: date_str = "-"

                display_html = highlight_html(translated, st.session_state.get("kw",""))

                def chips(row, columns, css):
                    items=[]
                    for c in columns:
                        v=row.get(c, pd.NA)
                        if pd.isna(v): continue
                        s=str(v).strip()
                        if not s or s.upper() in {"<NA>","NA","N/A","-"}: continue
                        items.append(f'<span class="badge {css}">{html.escape(s)}</span>')
                    return f'<div class="badges">{"".join(items)}</div>' if items else "<i>None</i>"

                del_msgs = chips(row, ex_del, "pos")
                det_msgs = chips(row, ex_det, "neg")

                star_val = row.get("Star Rating", 0)
                try: star_int = int(star_val) if pd.notna(star_val) else 0
                except: star_int = 0

                st.markdown(
                    f"""
                    <div class="review-card">
                      <p><strong>Source:</strong> {row.get('Source','')} | <strong>Model:</strong> {row.get('Model (SKU)','')}</p>
                      <p><strong>Country:</strong> {row.get('Country','')}</p>
                      <p><strong>Date:</strong> {date_str}</p>
                      <p><strong>Rating:</strong> {'⭐'*star_int} ({row.get('Star Rating','')}/5)</p>
                      <p><strong>Review:</strong> {display_html}</p>
                      <div><strong>Delighter Symptoms:</strong> {del_msgs}</div>
                      <div><strong>Detractor Symptoms:</strong> {det_msgs}</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

        # Pagination row with buffer spacing
        c1,c2,c3,c4,c5 = st.columns([1,1,2,1,1])
        with c1:
            st.markdown("<div class='pager'>", unsafe_allow_html=True)
            if st.button("⏮ First", disabled=current_page==0):
                st.session_state["review_page"]=0; st.rerun()
        with c2:
            if st.button("⬅ Prev", disabled=current_page==0):
                st.session_state["review_page"]=max(current_page-1,0); st.rerun()
        with c3:
            showing_from = 0 if total==0 else start+1
            showing_to = min(end, total)
            st.markdown(
                f"<div class='pager' style='text-align:center;font-weight:bold;'>Page {current_page+1} of {total_pages} • Showing {showing_from}–{showing_to} of {total}</div>",
                unsafe_allow_html=True,
            )
        with c4:
            if st.button("Next ➡", disabled=current_page>=total_pages-1):
                st.session_state["review_page"]=min(current_page+1,total_pages-1); st.rerun()
        with c5:
            if st.button("Last ⏭", disabled=current_page>=total_pages-1):
                st.session_state["review_page"]=total_pages-1; st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("---")

        # ---------------------------
        # 🤖 AI Assistant — Responses
        # ---------------------------
        st.markdown("<div id='askdata-anchor'></div>", unsafe_allow_html=True)
        st.markdown("### 🤖 AI Assistant — Responses")

        api_key = st.secrets.get("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
        if not _HAS_OPENAI:
            st.info("To enable Q&A, add `openai` to requirements and redeploy, then set `OPENAI_API_KEY`.")
        elif not api_key:
            st.info("Set your `OPENAI_API_KEY` (env or .streamlit/secrets.toml) to chat with the filtered data.")
        else:
            client = OpenAI(api_key=api_key)

            st.session_state.setdefault("qa_messages", [
                {"role":"system","content":"You are a helpful analyst. Use ONLY the provided context from the CURRENT filtered dataset. Prefer exact numbers. If unknown, say so."}
            ])

            # context builder with head+tail+sample for recall
            def context_blob(df_: pd.DataFrame, n_small=25, n_tail=10) -> str:
                if df_.empty: return "No rows after filters."
                parts = [f"ROW_COUNT={len(df_)}"]
                if "Star Rating" in df_.columns:
                    parts.append(f"STAR_COUNTS={df_['Star Rating'].value_counts().sort_index().to_dict()}")
                keep_cols = [c for c in ["Review Date","Country","Source","Model (SKU)","Star Rating","Verbatim"] if c in df_.columns]
                pool = pd.concat(
                    [df_.head(n_small), df_.tail(n_tail), df_.sample(min(n_small, len(df_)), random_state=7)]
                ).drop_duplicates()
                for _, r in pool[keep_cols].iterrows():
                    try: date_str = pd.to_datetime(r.get("Review Date")).strftime("%Y-%m-%d")
                    except Exception: date_str = str(r.get("Review Date","")) or ""
                    parts.append(str({
                        "date": date_str,
                        "country": str(r.get("Country","")),
                        "source": str(r.get("Source","")),
                        "model": str(r.get("Model (SKU)","")),
                        "stars": str(r.get("Star Rating","")),
                        "text": clean_text(str(r.get("Verbatim","")))
                    }))
                return "\n".join(parts)

            def pandas_count(query: str) -> dict:
                try:
                    if ";" in query or "__" in query: return {"error":"disallowed pattern"}
                    res = filtered.query(query, engine="python")
                    return {"count": int(len(res))}
                except Exception as e:
                    return {"error": str(e)}

            def pandas_mean(column: str, query: str|None=None) -> dict:
                try:
                    if column not in filtered.columns: return {"error": f"Unknown column {column}"}
                    dfq = filtered.query(query, engine="python") if query else filtered
                    return {"mean": float(dfq[column].mean())}
                except Exception as e:
                    return {"error": str(e)}

            tools = [
                {"type":"function","function":{
                    "name":"pandas_count",
                    "description":"Count rows matching a pandas query over the CURRENT filtered dataset. Wrap columns with spaces in backticks.",
                    "parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}
                }},
                {"type":"function","function":{
                    "name":"pandas_mean",
                    "description":"Compute mean of a numeric column (optionally with a pandas query).",
                    "parameters":{"type":"object","properties":{"column":{"type":"string"},"query":{"type":"string"}},"required":["column"]}
                }},
            ]

            if 'ask_text' in st.session_state and (ask_clicked and st.session_state['ask_text'].strip()):
                user_q = st.session_state['ask_text'].strip()
                st.session_state['qa_messages'].append({"role":"user","content": user_q})

                sys_ctx = ("CONTEXT:\n" + context_blob(filtered) +
                           "\n\nINSTRUCTIONS: Prefer calling tools for exact numbers. "
                           "If unknown from context+tools, say you don't know.")
                selected_model = st.session_state.get("llm_model","gpt-4o-mini")
                llm_temp = float(st.session_state.get("llm_temp", 0.2))

                first_kwargs = {
                    "model": selected_model,
                    "messages": [*st.session_state["qa_messages"], {"role":"system","content": sys_ctx}],
                    "tools": tools,
                }
                if model_supports_temperature(selected_model):
                    first_kwargs["temperature"] = llm_temp
                try:
                    first = client.chat.completions.create(**first_kwargs)
                except Exception as e:
                    if "temperature" in str(e).lower() and ("unsupported" in str(e).lower() or "does not support" in str(e).lower()):
                        first_kwargs.pop("temperature", None)
                        first = client.chat.completions.create(**first_kwargs)
                    else:
                        raise

                msg = first.choices[0].message
                if msg.tool_calls:
                    tool_msgs=[]
                    for call in msg.tool_calls:
                        name = call.function.name
                        args = json.loads(call.function.arguments or "{}")
                        out={"error":"unknown tool"}
                        if name=="pandas_count": out = pandas_count(args.get("query",""))
                        if name=="pandas_mean":  out = pandas_mean(args.get("column",""), args.get("query"))
                        tool_msgs.append({"tool_call_id": call.id, "role":"tool", "name":name, "content":json.dumps(out)})

                    follow_kwargs = {
                        "model": selected_model,
                        "messages": [
                            *st.session_state["qa_messages"],
                            {"role":"system","content": sys_ctx},
                            {"role":"assistant","tool_calls": msg.tool_calls, "content": None},
                            *tool_msgs
                        ],
                    }
                    if model_supports_temperature(selected_model):
                        follow_kwargs["temperature"] = llm_temp
                    try:
                        follow = client.chat.completions.create(**follow_kwargs)
                    except Exception as e:
                        if "temperature" in str(e).lower() and ("unsupported" in str(e).lower() or "does not support" in str(e).lower()):
                            follow_kwargs.pop("temperature", None)
                            follow = client.chat.completions.create(**follow_kwargs)
                        else:
                            raise
                    final_text = follow.choices[0].message.content
                else:
                    final_text = msg.content

                st.session_state["qa_messages"].append({"role":"assistant","content": final_text})
                st.session_state["ask_scroll_pending"] = True

            # Render history (main page)
            for m in st.session_state["qa_messages"]:
                if m["role"] != "system":
                    with st.chat_message(m["role"]):
                        st.markdown(m["content"])

        # ---------------------------
        # 💬 Feedback
        # ---------------------------
        st.markdown("---")
        st.markdown("<div id='feedback-anchor'></div>", unsafe_allow_html=True)
        st.markdown("### 💬 Submit Feedback / Feature Requests")
        with st.form("feedback_form"):
            fb = st.text_area("Tell us what to improve or build next:", height=140,
                              placeholder="We care about making this tool user-centric — share anything!")
            email = st.text_input("Your email (optional)", value="")
            submitted = st.form_submit_button("Send feedback")
        if submitted:
            # Hook up to SMTP/SendGrid/SES as desired; acknowledge for now.
            st.success("Thanks for the feedback! We'll review it soon. 🙌")

        # ---------- One-time scroll behaviors ----------
        if st.session_state.get("force_scroll_top_once"):
            st.session_state["force_scroll_top_once"] = False
            st.markdown("<script>window.scrollTo({top:0,behavior:'auto'});</script>", unsafe_allow_html=True)

        if st.session_state.get("ask_scroll_pending"):
            st.session_state["ask_scroll_pending"] = False
            st.markdown(
                "<script>const el=document.getElementById('askdata-anchor'); if(el){el.scrollIntoView({behavior:'smooth',block:'start'});}</script>",
                unsafe_allow_html=True,
            )

        if st.session_state.get("feedback_scroll_pending"):
            st.session_state["feedback_scroll_pending"] = False
            st.markdown(
                "<script>const el=document.getElementById('feedback-anchor'); if(el){el.scrollIntoView({behavior:'smooth',block:'start'});}</script>",
                unsafe_allow_html=True,
            )

    except Exception as e:
        st.error(f"An error occurred: {e}")

else:
    st.info("Please upload an Excel file to get started.")
