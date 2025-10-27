# ---------- Imports ----------
import streamlit as st
import pandas as pd

# ---------- Page Config ----------
st.set_page_config(layout="wide", page_title="Star Walk Analysis Dashboard")

# ---------- Force Light Mode ----------
from streamlit.components.v1 import html as st_html
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
GLOBAL_CSS = """
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
  html[data-theme="dark"], body[data-theme="dark"]{
    --text:rgba(255,255,255,.92); --muted:rgba(255,255,255,.72); --muted-2:rgba(255,255,255,.64);
    --border-strong:rgba(255,255,255,.22); --border:rgba(255,255,255,.16); --border-soft:rgba(255,255,255,.10);
    --bg-app:#0b0e14; --bg-card:rgba(255,255,255,.06); --bg-tile:rgba(255,255,255,.04);
    --ring:#60a5fa; --ok:#34d399; --bad:#f87171;
  }
  html, body, .stApp {
    background: var(--bg-app);
    font-family: "Helvetica Neue", Helvetica, Arial, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", "Liberation Sans", sans-serif;
    color: var(--text);
  }
  .block-container { padding-top:.9rem; padding-bottom:1.2rem; }
  section[data-testid="stSidebar"] .block-container { padding-top:.6rem; }
  mark{ background:#fff2a8; padding:0 .2em; border-radius:3px; }
  .hero-wrap{
    position:relative; overflow:hidden; border-radius:14px; min-height:150px; margin:.25rem 0 1rem 0;
    box-shadow:0 0 0 1.5px var(--border-strong), 0 8px 14px rgba(15,23,42,0.06);
    background:linear-gradient(90deg, var(--bg-card) 0% 55%, transparent 55% 100%);
  }
  .hero-inner{ position:absolute; inset:0; display:flex; align-items:center; justify-content:space-between; padding:0 18px; color:var(--text); }
  .hero-title{ font-size:clamp(22px,3.3vw,42px); font-weight:800; margin:0; font-family:inherit; }
  .hero-sub{ margin:4px 0 0 0; color:var(--muted); font-size:clamp(12px,1.1vw,16px); font-family:inherit; }
  .hero-right{ display:flex; align-items:center; justify-content:flex-end; width:40%; }
  .sn-logo{ height:48px; width:auto; display:block; }
</style>
"""
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# ---------- Sidebar Upload ----------
st.sidebar.header("Upload Star Walk File")
uploaded_file = st.sidebar.file_uploader("Choose Excel File", type=["xlsx"])

if uploaded_file:
    st.success("File uploaded successfully. Ready to proceed with analysis.")
    df = pd.read_excel(uploaded_file)

    # Count symptomless reviews (columns K to AD = 10 to 30 in 0-based index)
    symptom_cols = df.columns[10:30]
    missing_symptom_count = df[symptom_cols].isnull().all(axis=1).sum()

    st.markdown(f"### {missing_symptom_count} Reviews Missing Symptoms")
    if missing_symptom_count > 0:
        st.button(f"✨ Symptomize {missing_symptom_count} Reviews with OpenAI")


