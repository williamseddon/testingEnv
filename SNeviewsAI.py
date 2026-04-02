"""
SharkNinja + Dyson Review Analyst
─────────────────────────────────────────────────────────────────────────────
Original: SharkNinja Review Analyst (all functionality preserved)
New (Beta): Dyson brand support

Dyson integration notes
───────────────────────
• Dyson PDP URLs do NOT embed the BV product ID in the URL path.
  e.g.  https://www.dyson.com/hair-care/hair-stylers/airwrap-origin/nickel-copper
  vs.   https://www.sharkninja.com/.../AF181.html  ← ID in URL

• Resolution cascade (three strategies, first match wins):
  1. Scan page HTML for any embedded BV product ID pattern.
  2. Match last 1–2 URL path segments against DYSON_URL_PATH_TO_SKU dict.
  3. Prompt user to pick from the SKU catalogue (manual fallback).

• Dyson BV credentials differ from SharkNinja:
    passkey     = caa1NAv81VaHgxw7mDvXGRPl0tPLLgs8B9ZJqrMEy3h6g
    displaycode = 17317-en_us
"""
from __future__ import annotations

import difflib
import gc
import html
import io
import json
import math
import os
import random
import re
import textwrap
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from bs4 import BeautifulSoup
from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from plotly.subplots import make_subplots

try:
    from openai import OpenAI
    _HAS_OPENAI = True
except ImportError:
    OpenAI = None
    _HAS_OPENAI = False

try:
    import tiktoken
    _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
    _HAS_TIKTOKEN = True
except Exception:
    tiktoken = None
    _TIKTOKEN_ENC = None
    _HAS_TIKTOKEN = False

st.set_page_config(page_title="SharkNinja + Dyson Review Analyst", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
:root {
  --navy:#0f172a; --navy-mid:#1e293b; --navy-soft:#334155;
  --slate-600:#475569; --slate-500:#64748b; --slate-400:#94a3b8;
  --slate-200:#e2e8f0; --slate-100:#f1f5f9; --slate-50:#f8fafc; --white:#ffffff;
  --accent:#6366f1; --accent-bg:rgba(99,102,241,.08);
  --success:#059669; --danger:#dc2626; --warning:#d97706; --info:#2563eb;
  --dyson:#7f0442; --dyson-light:#fdf2f8;
  --page-bg:#eef0f4; --surface:#ffffff; --border:#dde1e8; --border-strong:#c8cdd6;
  --shadow-xs:0 1px 2px rgba(15,23,42,.06);
  --shadow-sm:0 1px 4px rgba(15,23,42,.09),0 1px 2px rgba(15,23,42,.05);
  --shadow-md:0 4px 12px rgba(15,23,42,.11),0 2px 4px rgba(15,23,42,.06);
  --shadow-lg:0 8px 28px rgba(15,23,42,.14),0 4px 8px rgba(15,23,42,.07);
  --radius-sm:10px; --radius-md:14px; --radius-lg:18px; --radius-xl:22px;
}
html,body,.stApp{font-family:'Inter',system-ui,-apple-system,sans-serif;color:var(--navy);background:var(--page-bg)!important;}
.main,.block-container,.stMainBlockContainer{background:var(--page-bg)!important;}
.block-container{padding-top:.9rem!important;padding-bottom:2.5rem!important;max-width:1440px!important;}
.hero-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-xl);padding:18px 22px;box-shadow:var(--shadow-sm);margin-bottom:.9rem;}
.hero-card.dyson{border-color:rgba(127,4,66,.25);background:linear-gradient(145deg,var(--dyson-light),var(--surface));}
.metric-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px 18px 14px;box-shadow:var(--shadow-xs);min-height:108px;display:flex;flex-direction:column;gap:4px;transition:box-shadow .15s,border-color .15s;}
.metric-card:hover{box-shadow:var(--shadow-md);border-color:rgba(99,102,241,.30);}
.metric-card.accent{border-color:rgba(99,102,241,.35);background:linear-gradient(145deg,#eef2ff 0%,var(--surface) 100%);}
.hero-kicker{font-size:10.5px;text-transform:uppercase;letter-spacing:.11em;color:var(--accent);font-weight:700;margin-bottom:3px;}
.hero-kicker.dyson{color:var(--dyson);}
.hero-title{font-size:22px;font-weight:800;letter-spacing:-.028em;color:var(--navy);line-height:1.15;}
.metric-label{font-size:10.5px;text-transform:uppercase;letter-spacing:.09em;color:var(--slate-500);font-weight:600;}
.metric-value{font-size:clamp(1.6rem,2.1vw,2.1rem);font-weight:800;color:var(--navy);line-height:1;letter-spacing:-.04em;}
.metric-sub{color:var(--slate-500);font-size:12px;line-height:1.35;margin-top:2px;}
.section-title{font-size:18px;font-weight:800;margin:6px 0 8px;color:var(--navy);letter-spacing:-.025em;}
.section-sub{color:var(--slate-500);font-size:13px;margin:0 0 12px;line-height:1.5;}
.badge-row,.chip-wrap{display:flex;gap:6px;flex-wrap:wrap;align-items:center;}
.chip{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:999px;font-size:11.5px;font-weight:600;line-height:1;border:1.5px solid transparent;letter-spacing:-.01em;}
.chip.blue{background:#eff6ff;border-color:#bfdbfe;color:#1d4ed8;}
.chip.green{background:#f0fdf4;border-color:#86efac;color:#15803d;}
.chip.red{background:#fff1f2;border-color:#fca5a5;color:#b91c1c;}
.chip.yellow{background:#fefce8;border-color:#fde047;color:#854d0e;}
.chip.indigo{background:#eef2ff;border-color:#c7d2fe;color:#4338ca;}
.chip.gray{background:var(--slate-50);border-color:var(--border);color:var(--slate-600);}
.chip.dyson{background:var(--dyson-light);border-color:rgba(127,4,66,.30);color:var(--dyson);}
.hero-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-top:12px;}
.hero-stat{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-md);padding:13px 15px;box-shadow:var(--shadow-xs);}
.hero-stat.accent{border-color:rgba(99,102,241,.40);background:linear-gradient(145deg,#eef2ff,var(--surface));}
.hero-stat .label{color:var(--slate-500);font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;font-weight:600;}
.hero-stat .value{font-size:24px;font-weight:800;margin-top:4px;color:var(--navy);letter-spacing:-.035em;}
.stButton>button{border-radius:var(--radius-sm)!important;font-weight:600!important;font-size:13.5px!important;height:38px!important;border:1.5px solid var(--border-strong)!important;background:var(--surface)!important;color:var(--navy-soft)!important;box-shadow:var(--shadow-xs)!important;transition:all .14s ease!important;letter-spacing:-.01em!important;}
.stButton>button:hover{border-color:var(--accent)!important;box-shadow:0 0 0 3px rgba(99,102,241,.13)!important;color:var(--accent)!important;}
[data-testid="baseButton-primary"],[data-testid="baseButton-primary"]:hover{background:var(--navy)!important;color:var(--surface)!important;border-color:var(--navy)!important;}
[data-testid="baseButton-primary"]:hover{background:var(--navy-mid)!important;border-color:var(--navy-mid)!important;box-shadow:0 0 0 3px rgba(15,23,42,.14)!important;}
[data-testid="stTextInput"] input,[data-testid="stTextArea"] textarea,[data-testid="stNumberInput"] input{border-radius:var(--radius-sm)!important;border-color:var(--border-strong)!important;background:var(--surface)!important;font-family:'Inter',sans-serif!important;font-size:13.5px!important;}
[data-testid="stTextInput"] input:focus,[data-testid="stTextArea"] textarea:focus{border-color:var(--accent)!important;box-shadow:0 0 0 3px rgba(99,102,241,.12)!important;}
[data-testid="stSelectbox"]>div>div,[data-testid="stMultiselect"]>div>div{border-radius:var(--radius-sm)!important;border-color:var(--border-strong)!important;background:var(--surface)!important;}
[data-testid="stContainer"][data-border="true"]{border-radius:var(--radius-lg)!important;border-color:var(--border)!important;background:var(--surface)!important;}
[data-testid="stExpander"]{border-radius:var(--radius-md)!important;border-color:var(--border)!important;background:var(--surface)!important;}
[data-testid="stProgressBar"]>div>div{background:var(--accent)!important;border-radius:999px!important;}
[data-testid="stMetric"]{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-md);padding:14px 16px;box-shadow:var(--shadow-xs);}
[data-testid="stDataFrame"]{border-radius:var(--radius-md);overflow:hidden;border:1px solid var(--border);}
[data-testid="stSidebar"]{background:#f5f7fb!important;border-right:1px solid var(--border)!important;}
[data-testid="stSidebar"] .stButton>button{width:100%;}
.dyson-sku-table{width:100%;border-collapse:collapse;font-size:12px;}
.dyson-sku-table th{background:var(--dyson-light);padding:7px 10px;font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:var(--dyson);font-weight:700;border-bottom:2px solid rgba(127,4,66,.15);}
.dyson-sku-table td{padding:6px 10px;border-bottom:1px solid var(--border);color:var(--navy);font-family:ui-monospace,monospace;}
.dyson-sku-table tr:last-child td{border-bottom:none;}
.dyson-sku-table tr:hover td{background:var(--dyson-light);}
.dyson-beta-banner{background:linear-gradient(135deg,var(--dyson-light),#fff);border:1.5px solid rgba(127,4,66,.22);border-radius:var(--radius-lg);padding:14px 16px;margin-bottom:1rem;}
.review-body{font-size:13.5px;line-height:1.6;color:var(--navy);margin:6px 0 4px;white-space:pre-wrap;word-break:break-word;}
.ev-highlight{background:#fef08a;border-radius:3px;padding:0 .15em;cursor:help;position:relative;}
.ev-highlight::after{content:attr(data-tag);position:absolute;left:50%;top:calc(100% + 6px);transform:translateX(-50%);width:min(260px,60vw);background:var(--navy);color:#f8fafc;border-radius:var(--radius-md);padding:.5rem .65rem;font-size:.72rem;line-height:1.35;box-shadow:var(--shadow-lg);white-space:normal;z-index:1000;pointer-events:none;opacity:0;transition:opacity .12s ease;}
.ev-highlight:hover::after{opacity:1;}
.sw-table-wrap{overflow-y:auto;overflow-x:hidden;border-radius:var(--radius-md);border:1px solid var(--border);}
.sw-table{width:100%;border-collapse:collapse;font-size:12.5px;font-family:'Inter',sans-serif;}
.sw-table thead tr{background:var(--slate-50);border-bottom:2px solid var(--border);}
.sw-table thead th{padding:8px 12px;text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--slate-500);font-weight:700;white-space:nowrap;}
.sw-table tbody tr{border-bottom:1px solid var(--border);}
.sw-table tbody tr:last-child{border-bottom:none;}
.sw-table tbody tr:hover{background:var(--slate-50);}
.sw-table tbody td{padding:7px 12px;color:var(--navy);max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.sw-td-right{text-align:right!important;font-variant-numeric:tabular-nums;}
.sw-star-good{color:var(--success);font-weight:700;}
.sw-star-bad{color:var(--danger);font-weight:700;}
.sw-divider{border:none;border-top:1px solid var(--border);margin:1.4rem 0 1rem;}
.compact-pager-status{display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:var(--navy);height:38px;letter-spacing:-.01em;}
.compact-pager-sub{font-size:11px;font-weight:400;color:var(--slate-400);margin-top:1px;}
.sym-state-banner{background:var(--surface);border:1px dashed var(--border-strong);border-radius:var(--radius-xl);padding:2rem;text-align:center;margin:1rem 0;}
.sym-state-banner .icon{font-size:2.4rem;margin-bottom:.6rem;}
.sym-state-banner .title{font-size:15px;font-weight:800;color:var(--navy);margin-bottom:.4rem;}
.sym-state-banner .sub{font-size:13px;color:var(--slate-500);line-height:1.55;max-width:540px;margin:0 auto;}
.thinking-overlay{position:fixed;inset:0;background:rgba(15,23,42,.38);display:flex;align-items:center;justify-content:center;z-index:99999;}
.thinking-card{width:min(400px,92vw);background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-xl);box-shadow:var(--shadow-lg);padding:1.6rem;text-align:center;}
.thinking-spinner{width:40px;height:40px;border:3px solid var(--slate-100);border-top-color:var(--navy);border-radius:50%;margin:0 auto 1rem;animation:tw-spin .8s linear infinite;}
.thinking-title{color:var(--navy);font-weight:800;font-size:1.05rem;margin-bottom:.25rem;letter-spacing:-.02em;}
.thinking-sub{color:var(--slate-500);font-size:.92rem;line-height:1.4;}
.soft-panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-lg);padding:12px 14px;box-shadow:var(--shadow-xs);margin:.55rem 0 .95rem;}
.pill-row{display:flex;flex-wrap:wrap;gap:7px;margin-top:8px;align-items:center;}
.pill{display:inline-flex;align-items:center;gap:6px;padding:5px 10px;border-radius:999px;background:var(--slate-50);border:1px solid var(--border);font-size:11.5px;font-weight:600;color:var(--navy);}
.pill .muted{color:var(--slate-500);font-weight:700;}
.small-muted{font-size:12px;color:var(--slate-500);}
.ref-wrap{display:inline-flex;position:relative;vertical-align:middle;margin-left:4px;margin-right:2px;}
.ref-tile{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;border-radius:999px;background:#eff6ff;border:1px solid #bfdbfe;color:#1d4ed8;font-size:11.5px;font-weight:700;line-height:1;cursor:help;white-space:nowrap;}
.ref-tip{position:absolute;left:0;top:calc(100% + 8px);width:min(380px,72vw);background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-md);padding:10px 11px;box-shadow:var(--shadow-lg);z-index:1100;opacity:0;pointer-events:none;transition:opacity .12s ease;}
.ref-wrap:hover .ref-tip{opacity:1;}
.ref-item{padding:7px 0;border-bottom:1px solid var(--border);}
.ref-item:last-child{border-bottom:none;padding-bottom:0;}
.ref-item:first-child{padding-top:0;}
.ref-meta{font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--slate-500);font-weight:700;margin-bottom:3px;}
.ref-title{font-size:12px;font-weight:700;color:var(--navy);margin-bottom:3px;line-height:1.35;}
.ref-snippet{font-size:11.5px;line-height:1.45;color:var(--slate-600);white-space:normal;}
.ref-empty{font-size:11.5px;color:var(--slate-500);line-height:1.4;}
@keyframes tw-spin{to{transform:rotate(360deg);}}
@media(max-width:1100px){.hero-grid{grid-template-columns:repeat(2,minmax(0,1fr));}}
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS — SharkNinja
# ═══════════════════════════════════════════════════════════════════════════════
APP_TITLE           = "SharkNinja + Dyson Review Analyst"
DEFAULT_PASSKEY     = "caC6wVBHos09eVeBkLIniLUTzrNMMH2XMADEhpHe1ewUw"
DEFAULT_DISPLAYCODE = "15973_3_0-en_us"
DEFAULT_API_VERSION = "5.5"
DEFAULT_PAGE_SIZE   = 100
DEFAULT_SORT        = "SubmissionTime:desc"
DEFAULT_CONTENT_LOCALES = (
    "en_US,ar*,zh*,hr*,cs*,da*,nl*,en*,et*,fi*,fr*,de*,el*,he*,hu*,"
    "id*,it*,ja*,ko*,lv*,lt*,ms*,no*,pl*,pt*,ro*,sk*,sl*,es*,sv*,th*,"
    "tr*,vi*,en_AU,en_CA,en_GB"
)
BAZAARVOICE_ENDPOINT = "https://api.bazaarvoice.com/data/reviews.json"
DEFAULT_PRODUCT_URL  = "https://www.sharkninja.com/ninja-air-fryer-pro-xl-6-in-1/AF181.html"
SOURCE_MODE_URL      = "Product URL (SharkNinja or Dyson)"
SOURCE_MODE_FILE     = "Uploaded review file"

# ═══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS — Dyson (Beta)
# ═══════════════════════════════════════════════════════════════════════════════
DYSON_PASSKEY      = "caa1NAv81VaHgxw7mDvXGRPl0tPLLgs8B9ZJqrMEy3h6g"
DYSON_DISPLAYCODE  = "17317-en_us"
DYSON_SORT         = "ContentLocale:en_US,en_CA,en_GB,en_AU,en_NZ"
DEFAULT_DYSON_URL  = "https://www.dyson.com/hair-care/hair-stylers/airwrap-origin/nickel-copper"

# Full catalogue of Dyson BV product IDs with human-readable names.
# Key = BV product ID, Value = product name (used in manual picker).
DYSON_SKU_CATALOGUE: Dict[str, str] = {
    # WashG1 wet floor cleaner
    "wr01-bkbu": "WashG1 Wet Floor Cleaner (Black/Blue)",
    "wr03-buco": "WashG1 Wet Floor Cleaner (Prussian Blue/Copper)",
    "wr04-co":   "WashG1 Wet Floor Cleaner (Copper)",
    # Clean+Wash Hygiene
    "wp01-buco": "Clean+Wash Hygiene (Prussian Blue/Copper)",
    "wp01-bubu": "Clean+Wash Hygiene (Blue/Blue)",
    "wp02-co":   "Clean+Wash Hygiene (Copper)",
    # Ball Upright
    "up15-multi":             "Ball Multi Floor 2",
    "up15-total":             "Ball Total Clean",
    "up19-multi":             "Ball Multi Floor",
    "up20-animal-pu":         "Ball Animal 3 (Purple)",
    "up20-animaltotal":       "Ball Animal 3 Total Clean",
    "up20-animalorigin-pupu": "Ball Animal 3 Origin (Purple/Purple)",
    "up24-multi":             "Ball Animal 3 Extra Multi Floor",
    "up30-animal":            "Ball Animal 4",
    "up30-animalcomplete":    "Ball Animal 4 Complete",
    # Purifier Cool
    "tp01-whsv": "Pure Cool Link TP01 (White/Silver)",
    "tp02-sc":   "Pure Cool TP02 (Steel/Copper)",
    "tp02-whsv": "Pure Cool TP02 (White/Silver)",
    "tp04-whsv": "Pure Cool Link TP04 (White/Silver)",
    "tp06-whgd": "Purifier Cool TP06 (White/Gold)",
    "tp07-whsv": "Purifier Cool TP07 (White/Silver)",
    "tp09-whgd": "Purifier Cool Formaldehyde TP09 (White/Gold)",
    "tp10-wh":   "Purifier Cool TP10 (White)",
    "tp10-whsv": "Purifier Cool TP10 (White/Silver)",
    "tp11-whwh": "Purifier Cool TP11 (White/White)",
    "tp12-whgd": "Purifier Cool Formaldehyde TP12 (White/Gold)",
    "tp14-whgd": "Purifier Cool Formaldehyde TP14 (White/Gold)",
    "tp7a-whnk": "Purifier Cool TP07A (White/Nickel)",
    "tp7c-whwh": "Purifier Cool TP07C (White/White)",
    # Purifier Hot+Cool
    "hp01-whsv": "Pure Hot+Cool HP01 (White/Silver)",
    "hp02-bknk": "Pure Hot+Cool HP02 (Black/Nickel)",
    "hp02-irbu": "Pure Hot+Cool HP02 (Iron/Blue)",
    "hp02-scsv": "Pure Hot+Cool HP02 (Steel/Silver)",
    "hp02-whsv": "Pure Hot+Cool HP02 (White/Silver)",
    "hp04-whsv": "Purifier Hot+Cool HP04 (White/Silver)",
    "hp06-whgd": "Purifier Hot+Cool HP06 (White/Gold)",
    "hp07-whsv": "Purifier Hot+Cool HP07 (White/Silver)",
    "hp09-whgd": "Purifier Hot+Cool Formaldehyde HP09 (White/Gold)",
    "hp10-bknk": "Purifier Hot+Cool HP10 (Black/Nickel)",
    "hp10-wh":   "Purifier Hot+Cool HP10 (White)",
    "hp10-whsv": "Purifier Hot+Cool HP10 (White/Silver)",
    "hp11-whwh": "Purifier Hot+Cool HP11 (White/White)",
    "hp12-whgd": "Purifier Hot+Cool Formaldehyde HP12 (White/Gold)",
    "hp7a-whnk": "Purifier Hot+Cool HP07A (White/Nickel)",
    # Purifier Humidify+Cool
    "ph01-whsv": "Pure Humidify+Cool PH01 (White/Silver)",
    "ph02-whgd": "Purifier Humidify+Cool PH02 (White/Gold)",
    "ph03-whsv": "Purifier Humidify+Cool PH03 (White/Silver)",
    "ph04-whgd": "Purifier Humidify+Cool Formaldehyde PH04 (White/Gold)",
    "ph05-whgd": "Purifier Humidify+Cool Formaldehyde PH05 (White/Gold)",
    "ph3a-whnk": "Purifier Humidify+Cool PH03A (White/Nickel)",
    # Purifier Big+Quiet
    "bp01-whsv": "Purifier Big+Quiet BP01 (White/Silver)",
    "bp03-nkbu": "Purifier Big+Quiet Formaldehyde BP03 (Nickel/Blue)",
    "bp04-bugd": "Purifier Big+Quiet BP04 (Blue/Gold)",
    "bp06-whsv": "Purifier Big+Quiet BP06 (White/Silver)",
    # Pure Cool Desk
    "dp01-irbu": "Pure Cool Me DP01 (Iron/Blue)",
    "dp01-nk":   "Pure Cool Me DP01 (Nickel)",
    "dp01-sc":   "Pure Cool Me DP01 (Steel/Copper)",
    "dp01-whsv": "Pure Cool Me DP01 (White/Silver)",
    "dp04-whsv": "Pure Cool Me DP04 (White/Silver)",
    "sp01-whsv": "Purifier SP01 (White/Silver)",
    # Air Multiplier fans
    "am06-10-bknk": "Air Multiplier AM06 10\" (Black/Nickel)",
    "am06-10-irbu": "Air Multiplier AM06 10\" (Iron/Blue)",
    "am06-10-whsv": "Air Multiplier AM06 10\" (White/Silver)",
    "am06-bknk":    "Air Multiplier AM06 (Black/Nickel)",
    "am06-whsv":    "Air Multiplier AM06 (White/Silver)",
    "am07-whsv":    "Air Multiplier AM07 Tower Fan (White/Silver)",
    "am09-whsv":    "Air Multiplier AM09 Hot+Cool (White/Silver)",
    "am10-bknk":    "Air Multiplier AM10 Humidifier (Black/Nickel)",
    "am10-whsv":    "Air Multiplier AM10 Humidifier (White/Silver)",
    "am12-whsv":    "Air Multiplier AM12 (White/Silver)",
    "am15-whsv":    "Air Multiplier AM15 (White/Silver)",
    # Cool Fan
    "cf01-bksv": "Pure Cool Fan CF01 (Black/Silver)",
    "cf04-whsv": "Pure Cool Fan CF04 (White/Silver)",
    "cf06-bkbs": "Pure Cool Fan CF06 (Black/Brushed Steel)",
    "cf06-whsv": "Pure Cool Fan CF06 (White/Silver)",
    # Robot vacuums
    "rb01-nkfu": "360 Eye (Nickel/Fuchsia)",
    "rb02":      "360 Heurist",
    "rb03":      "360 Vis Nav",
    "rb03-dok":  "360 Vis Nav with Dok",
    "rb05-bkbu": "360 Vis Nav (Black/Blue)",
    # DC corded
    "dc33":             "DC33 Multi Floor",
    "dc34-triggerplus": "DC34 Trigger Plus",
    "dc37-origin":      "DC37 Multi Floor Origin",
    "dc39-origin":      "DC39 Multi Floor Origin",
    "dc46-motor":       "DC46 Multi Floor Motorhead",
    "dc55-total":       "DC55 Total Clean",
    "dc66-fullkit":     "DC66 Multi Floor Full Kit",
    "dc75-multi":       "DC75 Multi Floor",
    "dc77-multi":       "DC77 Multi Floor",
    # Cinetic Big Ball
    "cy22-multi":  "Cinetic Big Ball Multi Floor",
    "cy23":        "Cinetic Big Ball Animal",
    "cy23-muscle": "Cinetic Big Ball Musclehead",
    # Handheld
    "hh11-trigger":  "Handheld Trigger HH11",
    "hh12":          "Handheld HH12",
    "hh15-mattress": "Handheld Mattress HH15",
    "hh17":          "Handheld HH17",
    # Cordless SV series
    "sv04-animalorigin":  "V6 Animal Origin",
    "sv06-fluffy":        "V6 Fluffy",
    "sv07-slimorigin":    "V6 Slim Origin",
    "sv09-cordfree":      "V7 Cord-Free",
    "sv09-total":         "V7 Total Clean",
    "sv10-total-rd":      "V8 Total Clean (Red)",
    "sv11-animalpro-fu":  "V8 Animal Pro (Fuchsia)",
    "sv11-hepa":          "V8 HEPA",
    "sv11-motorextra":    "V8 Motor Extra",
    "sv12-total":         "V10 Total Clean",
    "sv14-total":         "V10 Total Clean",
    "sv15-torque":        "V10 Torque Drive",
    "sv16-origin-gd":     "V10 Origin (Gold)",
    "sv17-total":         "V10 Total Clean",
    "sv19":               "V11 Animal",
    "sv19-plus":          "V11 Plus",
    "sv21-nk":            "V11 (Nickel)",
    "sv22":               "V11 Extra",
    "sv22-absolute-gd":   "V11 Absolute (Gold)",
    "sv22-extra":         "V11 Extra",
    "sv23":               "V11",
    "sv23-extra-pu":      "V11 Extra (Purple)",
    "sv24":               "V11 Outsize",
    "sv25":               "V12 Detect Slim",
    "sv25-absolute":      "V12 Detect Slim Absolute",
    "sv25-extra":         "V12 Detect Slim Extra",
    "sv25-originextra":   "V12 Detect Slim Origin Extra",
    "sv25-originplus":    "V12 Detect Slim Origin Plus",
    "sv25-plus":          "V12 Detect Slim Plus",
    "sv27-absolute":      "V12 Detect Slim Absolute",
    "sv27-animalplus":    "V12 Detect Slim Animal Plus",
    "sv28":               "V12 Detect Slim",
    "sv28-absolute":      "V12 Detect Slim Absolute",
    "sv28-extra-ir":      "V12 Detect Slim Extra (Iron)",
    "sv28-extra-rd":      "V12 Detect Slim Extra (Red)",
    "sv28-origin":        "V12 Detect Slim Origin",
    "sv29":               "V15 Detect",
    "sv29-absolute":      "V15 Detect Absolute",
    "sv29-absoluteplus":  "V15 Detect Absolute Plus",
    "sv29-origin":        "V15 Detect Origin",
    "sv30-absolute-gd":   "V15 Detect Absolute (Gold)",
    "sv30-extra":         "V15 Detect Extra",
    "sv37-advanced":      "V15 Detect Advanced",
    "sv46-absolute-gd":   "Gen5detect Absolute (Gold)",
    "sv46-fluffy":        "Gen5detect Fluffy",
    "sv46-gd":            "Gen5detect (Gold)",
    "sv47":               "Gen5detect",
    "sv47-absolute-gd":   "Gen5detect Absolute (Gold)",
    "sv47-absolutecar-yenk": "Gen5detect Absolute + Car Kit (Yellow/Nickel)",
    "sv47-complete-bunk": "Gen5detect Complete (Blue/Nickel)",
    "sv47-complete-nknk": "Gen5detect Complete (Nickel/Nickel)",
    "sv47-dok-yenk":      "Gen5detect with Dok (Yellow/Nickel)",
    "sv47-origin-yenk":   "Gen5detect Origin (Yellow/Nickel)",
    "sv47-plus":          "Gen5detect Plus",
    "sv47-pro-yenk":      "Gen5detect Pro (Yellow/Nickel)",
    "sv47-submarine":     "Gen5detect Submarine",
    "sv50-fluffy-bk":     "Omni-Glide+ Fluffy (Black)",
    "sv50-fluffycones-bk":"Omni-Glide+ Fluffy with Cones (Black)",
    "sv53-coco":                     "Gen5detect (Copper/Copper)",
    "sv53-dok-coco":                 "Gen5detect with Dok (Copper/Copper)",
    "sv53-fluffyoptic-coco":         "Gen5detect Fluffy Optic (Copper/Copper)",
    "sv53-submarine-coco":           "Gen5detect Submarine (Copper/Copper)",
    "sv53-submarinefluffyoptic-coco":"Gen5detect Submarine Fluffy Optic (Copper/Copper)",
    "sv55-motorbar-gnbk":    "Gen5detect Motorbar (Green/Black)",
    "sv55-motorbarcar-gnbk": "Gen5detect Motorbar + Car Kit (Green/Black)",
    "sv57-irnk":             "Gen5detect (Iron/Nickel)",
    "sv57-motorbar-nknk":    "Gen5detect Motorbar (Nickel/Nickel)",
    "sv58-nkco":             "Gen5detect (Nickel/Copper)",
    "sv58-origin-nkco":      "Gen5detect Origin (Nickel/Copper)",
    # Supersonic (HD)
    "hd01-irfu":               "Supersonic (Iron/Fuchsia)",
    "hd01-irfu-brushkit":      "Supersonic (Iron/Fuchsia) + Brush Kit",
    "hd01-nkpu":               "Supersonic (Nickel/Purple)",
    "hd01-origin-bknk-refurb": "Supersonic Origin (Black/Nickel) [Refurbished]",
    "hd01-origin-irbu-refurb": "Supersonic Origin (Iron/Blue) [Refurbished]",
    "hd01-origin-irrd-refurb": "Supersonic Origin (Iron/Red) [Refurbished]",
    "hd01-origin-nkpu-refurb": "Supersonic Origin (Nickel/Purple) [Refurbished]",
    "hd01-origin-rdnk-refurb": "Supersonic Origin (Red/Nickel) [Refurbished]",
    "hd01-originevo-nkpu-refurb": "Supersonic Origin Evo (Nickel/Purple) [Refurbished]",
    "hd04":                    "Supersonic HD04",
    "hd07-buco":               "Supersonic (Blue/Copper)",
    "hd07-buog":               "Supersonic (Blue/Orange Gold)",
    "hd07-buro":               "Supersonic (Blue/Rose Gold)",
    "hd07-curlycoily-buco":    "Supersonic Curly & Coily (Blue/Copper)",
    "hd07-funk":               "Supersonic (Fuchsia/Nickel)",
    "hd07-irfu":               "Supersonic (Iron/Fuchsia)",
    "hd07-lite-bknk":          "Supersonic Lite (Black/Nickel)",
    "hd07-nkco":               "Supersonic (Nickel/Copper)",
    "hd07-origin-bugd-refurb": "Supersonic Origin (Blue/Gold) [Refurbished]",
    "hd07-oxgd":               "Supersonic (Oxide/Gold)",
    "hd07-straightwavy-buco":  "Supersonic Straight & Wavy (Blue/Copper)",
    "hd08-origin-nkco":        "Supersonic Origin (Nickel/Copper)",
    "hd11-vtco":               "Supersonic (Violet/Copper)",
    "hd15":                    "Supersonic r",
    "hd16-curlycoily-aptz":    "Supersonic Curly & Coily (Amethyst/Topaz)",
    "hd16-curlycoily-vegd":    "Supersonic Curly & Coily (Vinca Blue/Gold)",
    "hd16-straightwavy-vegd":  "Supersonic Straight & Wavy (Vinca Blue/Gold)",
    "hd16-vegd":               "Supersonic (Vinca Blue/Gold)",
    "hd17-curlycoily-aptz":    "Supersonic Nural Curly & Coily (Amethyst/Topaz)",
    "hd17-curlycoily-vegd":    "Supersonic Nural Curly & Coily (Vinca Blue/Gold)",
    "hd17-pkro":               "Supersonic Nural (Pink/Rose Gold)",
    "hd17-straightwavy-vegd":  "Supersonic Nural Straight & Wavy (Vinca Blue/Gold)",
    "hd17-vegd":               "Supersonic Nural (Vinca Blue/Gold)",
    "hd18-vtco":               "Supersonic (Violet/Copper)",
    "hd19-pkro":               "Supersonic (Pink/Rose Gold)",
    # Airwrap (HS)
    "hs01-complete-nkfu":          "Airwrap Complete (Nickel/Fuchsia)",
    "hs01-origin-buco-refurb":     "Airwrap Origin (Blue/Copper) [Refurbished]",
    "hs01-origin-nkbk-refurb":     "Airwrap Origin (Nickel/Black) [Refurbished]",
    "hs01-origin-nkfu-refurb":     "Airwrap Origin (Nickel/Fuchsia) [Refurbished]",
    "hs01-origin-nkrd-refurb":     "Airwrap Origin (Nickel/Red) [Refurbished]",
    "hs01-origin-svco-refurb":     "Airwrap Origin (Silver/Copper) [Refurbished]",
    "hs01-refurb":                 "Airwrap Multi-Styler [Refurbished]",
    "hs02-origin-nkco":            "Airwrap Origin (Nickel/Copper)",
    "hs03-funk":                   "Airwrap Complete (Fuchsia/Nickel)",
    "hs03-nkfu":                   "Airwrap Complete (Nickel/Fuchsia)",
    "hs03-nkfu-gift":              "Airwrap Complete (Nickel/Fuchsia) Gift Edition",
    "hs03-rdnk":                   "Airwrap Complete (Red/Nickel)",
    "hs05-complete-nkfu":          "Airwrap Multi-Styler Complete (Nickel/Fuchsia)",
    "hs05-completelong-conk":      "Airwrap Multi-Styler Complete Long (Copper/Nickel)",
    "hs05-completelong-nkco":      "Airwrap Multi-Styler Complete Long (Nickel/Copper)",
    "hs05-completelong-nkco-refurb":"Airwrap Multi-Styler Complete Long (Nickel/Copper) [Refurbished]",
    "hs05-completelong-pkro":      "Airwrap Multi-Styler Complete Long (Pink/Rose Gold)",
    "hs05-dprose":                 "Airwrap Multi-Styler Complete (Deep Rose)",
    "hs05-longe-nkco":             "Airwrap Multi-Styler Long (Nickel/Copper)",
    "hs05-origin-buro-refurb":     "Airwrap Origin (Blue/Rose Gold) [Refurbished]",
    "hs07-bkpu":  "Airwrap Multi-Styler Complete (Black/Purple)",
    "hs07-buco":  "Airwrap Multi-Styler Complete (Blue/Copper)",
    "hs07-buro":  "Airwrap Multi-Styler Complete (Blue/Rose Gold)",
    "hs07-bzpk":  "Airwrap Multi-Styler Complete (Bronze/Pink)",
    "hs07-conk":  "Airwrap Multi-Styler Complete (Copper/Nickel)",
    "hs07-nkfu":  "Airwrap Multi-Styler Complete (Nickel/Fuchsia)",
    "hs08-curlycoily-aptz":    "Airwrap Multi-Styler Curly & Coily (Amethyst/Topaz)",
    "hs08-curlycoily-kzpk":    "Airwrap Multi-Styler Curly & Coily (Khaki/Pink)",
    "hs08-curlycoily-pkro":    "Airwrap Multi-Styler Curly & Coily (Pink/Rose Gold)",
    "hs08-curlycoily-sicp":    "Airwrap Multi-Styler Curly & Coily (Silver/Copper Pink)",
    "hs08-curlycoily-vegd":    "Airwrap Multi-Styler Curly & Coily (Vinca Blue/Gold)",
    "hs08-origin-nkco":        "Airwrap Multi-Styler Origin (Nickel/Copper)",
    "hs08-straightwavy-aptz":  "Airwrap Multi-Styler Straight & Wavy (Amethyst/Topaz)",
    "hs08-straightwavy-pkro":  "Airwrap Multi-Styler Straight & Wavy (Pink/Rose Gold)",
    "hs08-straightwavy-sicp":  "Airwrap Multi-Styler Straight & Wavy (Silver/Copper Pink)",
    "hs08-straightwavy-vegd":  "Airwrap Multi-Styler Straight & Wavy (Vinca Blue/Gold)",
    "hs09-curlycoily-aptz":    "Airwrap Multi-Styler Curly & Coily (Amethyst/Topaz)",
    "hs09-curlycoily-vegd":    "Airwrap Multi-Styler Curly & Coily (Vinca Blue/Gold)",
    "hs09-straightwavy-aptz":  "Airwrap Multi-Styler Straight & Wavy (Amethyst/Topaz)",
    "hs09-straightwavy-vegd":  "Airwrap Multi-Styler Straight & Wavy (Vinca Blue/Gold)",
    # Airstrait (HT)
    "ht01-aptz":  "Airstrait Straightener (Amethyst/Topaz)",
    "ht01-vegd":  "Airstrait Straightener (Vinca Blue/Gold)",
    # Corrale (CD)
    "cd01-whsv":  "Corrale (White/Silver)",
    "cd02-wh":    "Corrale (White)",
    "cd04-whsv":  "Corrale (White/Silver)",
    "cd05-bk":    "Corrale (Black)",
    "cd05-wh":    "Corrale (White)",
    "cd06-bkbs":  "Corrale (Black/Brushed Steel)",
    "cd06-buco":  "Corrale (Blue/Copper)",
    "cd06-whsv":  "Corrale (White/Silver)",
    # Accessories
    "cc01-bk":   "Corrale Carry Case (Black)",
    "cc01-sv":   "Corrale Carry Case (Silver)",
    "cc02-sv":   "Corrale Carry Case v2 (Silver)",
    "ab14-gy":   "Airblade Wash+Dry AB14 (Grey)",
    "ab14-wh":   "Airblade Wash+Dry AB14 (White)",
    "pf01-sebh": "Purifier Fan PF01 (Sepia/Black)",
    # Hair fragrances
    "hf01-4ml":    "Hydrascent Hair Fragrance 01 (4ml)",
    "hf02-4ml":    "Hydrascent Hair Fragrance 02 (4ml)",
    "hf03-4ml":    "Hydrascent Hair Fragrance 03 (4ml)",
    "hf04-4ml":    "Hydrascent Hair Fragrance 04 (4ml)",
    "hf05-50ml":   "Hydrascent Hair Fragrance 05 (50ml)",
    "hf06-4ml":    "Hydrascent Hair Fragrance 06 (4ml)",
    "hf07-30ml":   "Hydrascent Hair Fragrance 07 (30ml)",
    "hf07-50ml":   "Hydrascent Hair Fragrance 07 (50ml)",
    "hf08-75ml":   "Hydrascent Hair Fragrance 08 (75ml)",
    "hf10-2x50ml": "Hydrascent Hair Fragrance 10 Gift Set (2×50ml)",
    "ff03-500ml":  "02 Probiotic Floor Cleaning Fluid (500ml)",
}

# Maps last 1–2 URL path segments (as "product-slug/color-slug") → BV product ID.
# Covers Dyson.com PDP URL patterns. Normalised to lowercase, hyphens.
DYSON_URL_PATH_TO_SKU: Dict[str, str] = {
    # ── WashG1
    "washg1/black-blue":                             "wr01-bkbu",
    "washg1/prussian-blue-copper":                   "wr03-buco",
    "washg1/copper":                                 "wr04-co",
    # ── Clean+Wash Hygiene
    "clean-wash-hygiene/prussian-blue-copper":       "wp01-buco",
    "clean-wash-hygiene/copper-prussian-blue":       "wp01-buco",
    "clean-wash-hygiene/blue-blue":                  "wp01-bubu",
    "clean-wash-hygiene/copper":                     "wp02-co",
    "washg2/prussian-blue-copper":                   "wp01-buco",
    "washg2/copper":                                 "wp02-co",
    # ── Airwrap
    "airwrap-origin/nickel-copper":                  "hs02-origin-nkco",
    "airwrap-multi-styler-complete/nickel-fuchsia":  "hs05-complete-nkfu",
    "airwrap-multi-styler-complete/deep-rose":       "hs05-dprose",
    "airwrap-multi-styler-complete-long/nickel-copper": "hs05-completelong-nkco",
    "airwrap-multi-styler-complete-long/copper-nickel": "hs05-completelong-conk",
    "airwrap-multi-styler-complete-long/pink-rose-gold":"hs05-completelong-pkro",
    "airwrap-complete-long/nickel-copper":            "hs05-completelong-nkco",
    "airwrap-complete-long/copper-nickel":            "hs05-completelong-conk",
    "airwrap-complete/nickel-fuchsia":                "hs03-nkfu",
    "airwrap-complete/fuchsia-nickel":                "hs03-funk",
    "airwrap-complete/red-nickel":                    "hs03-rdnk",
    "airwrap-multi-styler/nickel-fuchsia":            "hs07-nkfu",
    "airwrap-multi-styler/copper-nickel":             "hs07-conk",
    "airwrap-multi-styler/bronze-pink":               "hs07-bzpk",
    "airwrap-multi-styler/blue-rose-gold":            "hs07-buro",
    "airwrap-multi-styler/blue-copper":               "hs07-buco",
    "airwrap-multi-styler/black-purple":              "hs07-bkpu",
    "airwrap-multi-styler-straight-wavy/vinca-blue-gold":    "hs08-straightwavy-vegd",
    "airwrap-multi-styler-straight-wavy/amethyst-topaz":     "hs08-straightwavy-aptz",
    "airwrap-multi-styler-straight-wavy/pink-rose-gold":     "hs08-straightwavy-pkro",
    "airwrap-multi-styler-straight-wavy/silver-copper-pink": "hs08-straightwavy-sicp",
    "airwrap-multi-styler-curly-coily/vinca-blue-gold":      "hs08-curlycoily-vegd",
    "airwrap-multi-styler-curly-coily/amethyst-topaz":       "hs08-curlycoily-aptz",
    "airwrap-multi-styler-curly-coily/pink-rose-gold":       "hs08-curlycoily-pkro",
    "airwrap-multi-styler-curly-coily/silver-copper-pink":   "hs08-curlycoily-sicp",
    "airwrap-multi-styler-curly-coily/khaki-pink":           "hs08-curlycoily-kzpk",
    "airwrap-straight-wavy/vinca-blue-gold":          "hs09-straightwavy-vegd",
    "airwrap-straight-wavy/amethyst-topaz":           "hs09-straightwavy-aptz",
    "airwrap-curly-coily/vinca-blue-gold":            "hs09-curlycoily-vegd",
    "airwrap-curly-coily/amethyst-topaz":             "hs09-curlycoily-aptz",
    # ── Supersonic
    "supersonic/iron-fuchsia":                        "hd07-irfu",
    "supersonic/nickel-copper":                       "hd07-nkco",
    "supersonic/blue-copper":                         "hd07-buco",
    "supersonic/blue-rose-gold":                      "hd07-buro",
    "supersonic/fuchsia-nickel":                      "hd07-funk",
    "supersonic/oxide-gold":                          "hd07-oxgd",
    "supersonic/violet-copper":                       "hd11-vtco",
    "supersonic/vinca-blue-gold":                     "hd17-vegd",
    "supersonic/pink-rose-gold":                      "hd17-pkro",
    "supersonic-origin/nickel-copper":                "hd08-origin-nkco",
    "supersonic-nural/vinca-blue-gold":               "hd17-vegd",
    "supersonic-nural/pink-rose-gold":                "hd17-pkro",
    "supersonic-nural/amethyst-topaz":                "hd17-curlycoily-aptz",
    "supersonic-r":                                   "hd15",
    # ── Airstrait
    "airstrait/vinca-blue-gold":                      "ht01-vegd",
    "airstrait/amethyst-topaz":                       "ht01-aptz",
    # ── Corrale
    "corrale/white-silver":                           "cd06-whsv",
    "corrale/blue-copper":                            "cd06-buco",
    "corrale/black-brushed-steel":                    "cd06-bkbs",
    # ── Purifier Cool
    "purifier-cool-tp07/white-silver":                "tp07-whsv",
    "purifier-cool-formaldehyde-tp09/white-gold":     "tp09-whgd",
    "purifier-cool-tp10/white":                       "tp10-wh",
    "purifier-cool-tp10/white-silver":                "tp10-whsv",
    "purifier-cool-tp11/white-white":                 "tp11-whwh",
    "purifier-cool-formaldehyde-tp12/white-gold":     "tp12-whgd",
    "purifier-cool-formaldehyde-tp14/white-gold":     "tp14-whgd",
    "pure-cool-link-tp04/white-silver":               "tp04-whsv",
    "pure-cool-tp02/white-silver":                    "tp02-whsv",
    # ── Purifier Hot+Cool
    "purifier-hot-cool-hp07/white-silver":            "hp07-whsv",
    "purifier-hot-cool-formaldehyde-hp09/white-gold": "hp09-whgd",
    "purifier-hot-cool-hp10/white-silver":            "hp10-whsv",
    "purifier-hot-cool-hp10/white":                   "hp10-wh",
    "purifier-hot-cool-hp10/black-nickel":            "hp10-bknk",
    "purifier-hot-cool-hp11/white-white":             "hp11-whwh",
    "purifier-hot-cool-formaldehyde-hp12/white-gold": "hp12-whgd",
    "pure-hot-cool-hp04/white-silver":                "hp04-whsv",
    # ── Purifier Humidify+Cool
    "purifier-humidify-cool-ph03/white-silver":       "ph03-whsv",
    "purifier-humidify-cool-formaldehyde-ph04/white-gold": "ph04-whgd",
    "purifier-humidify-cool-formaldehyde-ph05/white-gold": "ph05-whgd",
    # ── Purifier Big+Quiet
    "purifier-big-quiet-formaldehyde-bp03/nickel-blue": "bp03-nkbu",
    "purifier-big-quiet-bp06/white-silver":           "bp06-whsv",
    # ── Gen5detect
    "gen5detect/yellow-nickel":                       "sv47-origin-yenk",
    "gen5detect/gold":                                "sv46-gd",
    "gen5detect/nickel-copper":                       "sv58-nkco",
    "gen5detect-absolute/gold":                       "sv47-absolute-gd",
    "gen5detect-complete/nickel-nickel":              "sv47-complete-nknk",
    "gen5detect-complete/blue-nickel":                "sv47-complete-bunk",
    "gen5detect-submarine/copper-copper":             "sv53-submarine-coco",
    "gen5detect-motorbar/nickel-nickel":              "sv57-motorbar-nknk",
    "gen5detect-motorbar/green-black":                "sv55-motorbar-gnbk",
    # ── V15 Detect
    "v15-detect/nickel-copper":                       "sv29-absolute",
    "v15-detect-absolute/gold":                       "sv30-absolute-gd",
    "v15-detect-extra/copper":                        "sv30-extra",
    # ── V12 Detect Slim
    "v12-detect-slim/nickel-copper":                  "sv28-origin",
    "v12-detect-slim-absolute/nickel-copper":         "sv28-absolute",
    "v12-detect-slim-extra/red":                      "sv28-extra-rd",
    # ── Robot vacuums
    "360-vis-nav/black-blue":                         "rb05-bkbu",
    "360-vis-nav":                                    "rb03",
    "360-heurist":                                    "rb02",
    "360-eye/nickel-fuchsia":                         "rb01-nkfu",
    # ── Air Multiplier fans
    "am07/white-silver":                              "am07-whsv",
    "am09/white-silver":                              "am09-whsv",
    "am10/white-silver":                              "am10-whsv",
    "am10/black-nickel":                              "am10-bknk",
    # ── Pure Cool Me
    "pure-cool-me/white-silver":                      "dp01-whsv",
    "dp04/white-silver":                              "dp04-whsv",
}

# BV HTML extraction patterns — tried against raw page source in order
_DYSON_BV_ID_PATTERNS: List[str] = [
    r'data-bv-product-id=["\']([a-z][a-z0-9\-]{2,40})["\']',
    r'data-bvproductid=["\']([a-z][a-z0-9\-]{2,40})["\']',
    r'bvProductId["\s]*:["\s]*["\']([a-z][a-z0-9\-]{2,40})["\']',
    r'"productId"\s*:\s*"([a-z][a-z0-9\-]{2,40})"',
    r'product_id["\s]*:["\s]*["\']([a-z][a-z0-9\-]{2,40})["\']',
    r'<meta[^>]+property=["\']product:retailer_item_id["\'][^>]+content=["\']([a-z][a-z0-9\-]{2,40})["\']',
    r'<meta[^>]+content=["\']([a-z][a-z0-9\-]{2,40})["\'][^>]+property=["\']product:retailer_item_id["\']',
    r'window\._bvProductId\s*=\s*["\']([a-z][a-z0-9\-]{2,40})["\']',
    r'BV\.core\.RR\.id\s*=\s*["\']([a-z][a-z0-9\-]{2,40})["\']',
    r'passkey=[^&]+&[^"]*filter=productid%3Aeq%3A([a-z][a-z0-9\-]{2,40})',
]

TAB_DASHBOARD      = "📊  Dashboard"
TAB_REVIEW_EXPLORER= "🔍  Review Explorer"
TAB_AI_ANALYST     = "🤖  AI Analyst"
TAB_REVIEW_PROMPT  = "🏷️  Review Prompt"
TAB_SYMPTOMIZER    = "💊  Symptomizer"
WORKSPACE_TABS = [TAB_DASHBOARD, TAB_REVIEW_EXPLORER, TAB_AI_ANALYST, TAB_REVIEW_PROMPT, TAB_SYMPTOMIZER]

MODEL_OPTIONS = [
    "gpt-5.4-mini", "gpt-5.4", "gpt-5.4-pro", "gpt-5.4-nano",
    "gpt-5-chat-latest", "gpt-5-mini", "gpt-5", "gpt-5-nano",
    "gpt-4o-mini", "gpt-4o", "gpt-4.1",
]
DEFAULT_MODEL              = "gpt-5.4-mini"
DEFAULT_REASONING          = "none"
STRUCTURED_FALLBACK_MODEL  = "gpt-5.4-mini"
AI_VISIBLE_CHAT_MESSAGES   = 2
AI_CONTEXT_TOKEN_BUDGET    = 10_000
NON_VALUES = {"<NA>","NA","N/A","NONE","-","","NAN","NULL"}
STOPWORDS = {
    "a","about","after","again","all","also","am","an","and","any","are","as","at",
    "be","because","been","before","being","best","better","but","by","can","could",
    "did","do","does","don","down","even","every","for","from","get","got","great",
    "had","has","have","he","her","here","hers","him","his","how","i","if","in",
    "into","is","it","its","just","like","love","made","make","many","me","more",
    "most","much","my","new","no","not","now","of","on","one","only","or","other",
    "our","out","over","product","really","so","some","than","that","the","their",
    "them","then","there","these","they","this","to","too","use","used","using",
    "very","was","we","well","were","what","when","which","while","with","would",
    "you","your",
}
PERSONAS: Dict[str, Dict[str, Any]] = {
    "Product Development": {
        "blurb": "Translates reviews into product and feature decisions.",
        "prompt": "Create a report for the product development team. Highlight what customers love, unmet needs, feature gaps, usability friction, and concrete roadmap opportunities. End with the top 5 product actions ranked by impact.",
        "instructions": (
            "You are a senior product strategy analyst specialising in consumer appliances.\n"
            "Structure your response with these exact sections:\n"
            "## What Customers Love\n## Unmet Needs & Feature Gaps\n"
            "## Usability Friction\n## Roadmap Opportunities\n## Top 5 Actions (ranked)\n"
            "Cite review IDs inline as (review_ids: 12345, 67890) for every material claim.\n"
            "Be specific — name exact features, not vague categories.\n"
            "Keep each section to 3-5 bullet points. Total response ≤ 500 words."
        ),
    },
    "Quality Engineer": {
        "blurb": "Focuses on failure modes, defects, durability, and root-cause signals.",
        "prompt": "Create a report for a quality engineer. Identify defect patterns, reliability risks, cleaning issues, performance inconsistencies, and probable root-cause hypotheses. Separate confirmed evidence from inference.",
        "instructions": (
            "You are a senior quality and reliability analyst for consumer appliances.\n"
            "Sections:\n"
            "## Confirmed Defect Patterns\n## Reliability & Durability Risks\n"
            "## Root-Cause Hypotheses\n## Cleaning & Maintenance Issues\n"
            "## Risk Severity Matrix (High/Med/Low)\n"
            "Mark speculative claims as [INFERRED]. Cite review IDs for every confirmed finding.\n"
            "Prioritise by frequency × severity. Total ≤ 500 words."
        ),
    },
    "Consumer Insights": {
        "blurb": "Extracts sentiment drivers, purchase motivations, and voice-of-customer insights.",
        "prompt": "Create a report for the consumer insights team. Summarize key sentiment drivers, barriers to adoption, purchase motivations, key use cases, and how tone changes across star ratings and incentivized vs non-incentivized reviews.",
        "instructions": (
            "You are a consumer insights lead specialising in VoC analysis.\n"
            "Sections:\n"
            "## Top Sentiment Drivers (positive)\n## Top Sentiment Drivers (negative)\n"
            "## Purchase Motivations & Jobs-to-be-Done\n## Barriers to Satisfaction\n"
            "## Organic vs Incentivized Tone Differences\n## Key Verbatim Quotes (3-5)\n"
            "Use plain, executive-ready language. Cite review IDs for quotes. Total ≤ 500 words."
        ),
    },
}
DET_LETTERS  = ["K","L","M","N","O","P","Q","R","S","T"]
DEL_LETTERS  = ["U","V","W","X","Y","Z","AA","AB","AC","AD"]
DET_INDEXES  = [column_index_from_string(c) for c in DET_LETTERS]
DEL_INDEXES  = [column_index_from_string(c) for c in DEL_LETTERS]
META_ORDER   = [("Safety","AE"),("Reliability","AF"),("# of Sessions","AG")]
META_INDEXES = {name: column_index_from_string(col) for name,col in META_ORDER}
AI_DET_HEADERS  = [f"AI Symptom Detractor {i}" for i in range(1,11)]
AI_DEL_HEADERS  = [f"AI Symptom Delighter {i}" for i in range(1,11)]
AI_META_HEADERS = ["AI Safety","AI Reliability","AI # of Sessions"]
SAFETY_ENUM      = ["Not Mentioned","Concern","Positive"]
RELIABILITY_ENUM = ["Not Mentioned","Negative","Neutral","Positive"]
SESSIONS_ENUM    = ["0","1","2–3","4–9","10+","Unknown"]
DEFAULT_PRIORITY_DELIGHTERS = ["Overall Satisfaction","Ease Of Use","Effective Results",
    "Visible Improvement","Time Saver","Comfort","Value","Reliability"]
DEFAULT_PRIORITY_DETRACTORS = ["Poor Results","Ease Of Use","Reliability Issue","High Cost",
    "Irritation","Battery Problem","High Noise","Cleaning Difficulty",
    "Setup Issue","Connectivity Issue","Safety Concern"]
REVIEW_PROMPT_STARTER_ROWS = [
    {"column_name":"perceived_loudness",
     "prompt":"How is product loudness described? Positive, Negative, Neutral, or Not Mentioned.",
     "labels":"Positive, Negative, Neutral, Not Mentioned"},
    {"column_name":"reliability_risk_signal",
     "prompt":"Does the review mention a product reliability or durability risk? Risk Mentioned, Positive Reliability, or Not Mentioned.",
     "labels":"Risk Mentioned, Positive Reliability, Not Mentioned"},
]

# ═══════════════════════════════════════════════════════════════════════════════
#  UTILITIES (unchanged from original)
# ═══════════════════════════════════════════════════════════════════════════════
class ReviewDownloaderError(Exception):
    pass


@dataclass
class ReviewBatchSummary:
    product_url: str
    product_id: str
    total_reviews: int
    page_size: int
    requests_needed: int
    reviews_downloaded: int
    brand: str = "SharkNinja"


def _safe_text(v, default=""):
    if v is None: return default
    if isinstance(v, (list, tuple, set, dict, pd.Series, pd.DataFrame, pd.Index)): return default
    try:
        m = pd.isna(v)
    except Exception:
        m = False
    if isinstance(m, bool) and m: return default
    t = str(v).strip()
    return default if t.lower() in {"nan", "none", "null", "<na>"} else t


def _safe_int(v, d=0):
    try: return int(float(v))
    except Exception: return d


def _safe_bool(v, d=False):
    if v is None: return d
    if isinstance(v, bool): return v
    t = _safe_text(v).lower()
    if t in {"true","1","yes","y","t"}: return True
    if t in {"false","0","no","n","f",""}: return False
    return d


def _safe_mean(s):
    if s.empty: return None
    n = pd.to_numeric(s, errors="coerce").dropna()
    return float(n.mean()) if not n.empty else None


def _safe_pct(num, den): return 0.0 if not den else float(num) / float(den)
def _fmt_secs(sec):
    sec = max(0.0, float(sec or 0))
    m = int(sec // 60); s = int(round(sec - m * 60))
    return f"{m}:{s:02d}"
def _canon(s): return " ".join(str(s).split()).lower().strip()
def _canon_simple(s): return "".join(ch for ch in _canon(s) if ch.isalnum())
def _esc(s): return html.escape(str(s or ""))


def _chip_html(items):
    if not items: return "<span class='chip gray'>No active filters</span>"
    return "<div class='chip-wrap'>"+"".join(f"<span class='chip {c}'>{_esc(t)}</span>" for t,c in items)+"</div>"


def _is_missing(v):
    if v is None: return True
    if isinstance(v, (list, tuple, set, dict, pd.Series, pd.DataFrame, pd.Index)): return False
    try:
        m = pd.isna(v)
    except Exception: return False
    return bool(m) if isinstance(m, (bool, int)) else False


def _fmt_num(v, d=2):
    if v is None or _is_missing(v): return "n/a"
    return f"{v:.{d}f}"


def _fmt_pct(v, d=1):
    if v is None or _is_missing(v): return "n/a"
    return f"{100*float(v):.{d}f}%"


def _trunc(text, max_chars=420):
    text = re.sub(r"\s+", " ", _safe_text(text)).strip()
    return text if len(text) <= max_chars else text[:max_chars-3].rstrip()+"…"


def _norm_text(text): return re.sub(r"\s+", " ", str(text).lower()).strip()
def _tokenize(text): return [t for t in re.findall(r"[a-z0-9']+", _norm_text(text)) if len(t) > 2 and t not in STOPWORDS]


def _slugify(text, fallback="custom"):
    c = re.sub(r"[^a-zA-Z0-9]+","_",_safe_text(text).lower())
    c = re.sub(r"_+","_",c).strip("_") or fallback
    return ("prompt_"+c if c[0].isdigit() else c)[:64]


def _first_non_empty(series):
    for v in series.astype(str):
        v = _safe_text(v)
        if v and v.lower() != "nan": return v
    return ""


def _clean_text(x):
    if pd.isna(x): return ""
    return str(x).strip()


def _is_filled(val):
    if pd.isna(val): return False
    s = str(val).strip()
    return s != "" and s.upper() not in NON_VALUES


def _estimate_tokens(text):
    s = str(text or "")
    if not s: return 0
    if _HAS_TIKTOKEN and _TIKTOKEN_ENC is not None:
        try: return int(len(_TIKTOKEN_ENC.encode(s)))
        except Exception: pass
    return int(max(1, math.ceil(len(s)/4)))

# ═══════════════════════════════════════════════════════════════════════════════
#  DYSON URL RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════

def _is_dyson_url(url: str) -> bool:
    """Return True if the URL is a Dyson product page."""
    host = urlparse(str(url or "")).netloc.lower().lstrip("www.")
    return host.startswith("dyson.")


def _extract_dyson_pid_from_html(page_html: str) -> Optional[str]:
    """
    Strategy 1: scan the raw HTML for any embedded BazaarVoice product ID.
    Returns the first match found, or None.
    """
    known_keys = set(DYSON_SKU_CATALOGUE.keys())
    for pattern in _DYSON_BV_ID_PATTERNS:
        try:
            m = re.search(pattern, page_html, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip().lower()
                # Accept if it's a known SKU OR looks like a plausible 2-part slug
                if candidate in known_keys or re.fullmatch(r"[a-z]{2,4}\d*[-a-z0-9]{2,36}", candidate):
                    return candidate
        except Exception:
            continue
    return None


def _resolve_dyson_pid_from_slug(url_path: str) -> Optional[str]:
    """
    Strategy 2: match the URL path against DYSON_URL_PATH_TO_SKU.
    Tries:
      • last-2-segments/color   (e.g. "airwrap-origin/nickel-copper")
      • last-segment only       (e.g. "360-vis-nav")
      • fuzzy difflib match on all keys
    """
    parts = [p for p in url_path.strip("/").split("/") if p]
    if not parts:
        return None

    # Normalise
    parts = [p.lower().replace("_", "-") for p in parts]

    # Try last 2 segments
    if len(parts) >= 2:
        key = f"{parts[-2]}/{parts[-1]}"
        if key in DYSON_URL_PATH_TO_SKU:
            return DYSON_URL_PATH_TO_SKU[key]

    # Try last segment alone
    if parts[-1] in DYSON_URL_PATH_TO_SKU:
        return DYSON_URL_PATH_TO_SKU[parts[-1]]

    # Try last 3→2 and 4→2 combinations (handles extra path depth)
    for n in (3, 4):
        if len(parts) >= n:
            key = f"{parts[-(n-1)]}/{parts[-1]}"
            if key in DYSON_URL_PATH_TO_SKU:
                return DYSON_URL_PATH_TO_SKU[key]

    # Fuzzy match against keys
    all_keys = list(DYSON_URL_PATH_TO_SKU.keys())
    if len(parts) >= 2:
        probe = f"{parts[-2]}/{parts[-1]}"
    else:
        probe = parts[-1]
    close = difflib.get_close_matches(probe, all_keys, n=1, cutoff=0.80)
    if close:
        return DYSON_URL_PATH_TO_SKU[close[0]]

    return None


def _resolve_dyson_pid(url: str, page_html: str) -> Optional[str]:
    """
    Full Dyson PID resolution cascade:
      1. Embedded BV product ID in page HTML
      2. URL slug database lookup
    Returns None if neither strategy succeeds (caller shows manual picker).
    """
    pid = _extract_dyson_pid_from_html(page_html)
    if pid:
        return pid
    return _resolve_dyson_pid_from_slug(urlparse(url).path)


def _render_dyson_manual_picker() -> Optional[str]:
    """
    Fallback UI: group all known Dyson SKUs by product family and let the
    user select one from a dropdown. Returns the selected BV product ID.
    """
    # Group by prefix family
    families: Dict[str, List[Tuple[str, str]]] = {}
    family_map = {
        "wr": "WashG1", "wp": "Clean+Wash", "sv": "Cordless Vacuums",
        "up": "Ball Uprights", "dc": "DC Corded", "cy": "Cinetic Big Ball",
        "rb": "Robot Vacuums", "hh": "Handhelds",
        "hd": "Supersonic Dryers", "hs": "Airwrap Stylers",
        "ht": "Airstrait", "cd": "Corrale",
        "tp": "Purifier Cool", "hp": "Purifier Hot+Cool",
        "ph": "Purifier Humidify+Cool", "bp": "Purifier Big+Quiet",
        "dp": "Pure Cool Desk", "sp": "Purifier SP",
        "am": "Air Multiplier Fans", "cf": "Cool Fans",
        "ab": "Airblade", "pf": "Purifier Fan",
        "hf": "Hair Fragrances", "ff": "Floor Fluid",
        "cc": "Accessories",
    }
    for sku, name in sorted(DYSON_SKU_CATALOGUE.items(), key=lambda x: x[1]):
        prefix = re.match(r"[a-z]+", sku)
        fam = family_map.get(prefix.group() if prefix else "", "Other")
        families.setdefault(fam, []).append((sku, name))

    st.markdown("""<div class='dyson-beta-banner'>
      <b>⚠️ Dyson product ID not found automatically.</b><br>
      The BV product ID could not be extracted from the page HTML or URL pattern.
      Please select your product manually below, or paste the BV product ID directly.
    </div>""", unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    fam_names = sorted(families.keys())
    sel_fam = c1.selectbox("Product family", fam_names, key="dyson_picker_fam")
    options = [(sku, f"{sku}  ·  {name}") for sku, name in families.get(sel_fam, [])]
    sel_item = c2.selectbox("Product", [o[1] for o in options], key="dyson_picker_item")
    matched = next((o[0] for o in options if o[1] == sel_item), None)

    st.markdown("**Or paste the BV product ID directly:**")
    direct = st.text_input("BV product ID (e.g. hs02-origin-nkco)", key="dyson_direct_pid").strip().lower()
    if direct:
        matched = direct

    if matched and st.button("✅ Use this product ID", type="primary", key="dyson_confirm_pid"):
        return matched
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  OPENAI (unchanged)
# ═══════════════════════════════════════════════════════════════════════════════
def _get_api_key():
    try:
        if "OPENAI_API_KEY" in st.secrets: return str(st.secrets["OPENAI_API_KEY"])
        if "openai" in st.secrets and st.secrets["openai"].get("api_key"): return str(st.secrets["openai"]["api_key"])
    except Exception: pass
    return os.getenv("OPENAI_API_KEY")


@st.cache_resource(show_spinner=False)
def _make_openai_client(api_key: str):
    if not (_HAS_OPENAI and api_key): return None
    try: return OpenAI(api_key=api_key, timeout=60, max_retries=3)
    except TypeError:
        try: return OpenAI(api_key=api_key)
        except Exception: return None


def _get_client():
    key = _get_api_key()
    if not (_HAS_OPENAI and key): return None
    return _make_openai_client(key)


def _shared_model(): return st.session_state.get("shared_model", DEFAULT_MODEL)


def _reasoning_options_for_model(model: str) -> List[str]:
    m = _safe_text(model).lower()
    if not m.startswith("gpt-5"): return ["none"]
    if m.startswith("gpt-5.4") or m in {"gpt-5-chat-latest","gpt-5.2","gpt-5.2-pro"}:
        return ["none","low","medium","high","xhigh"]
    if m in {"gpt-5","gpt-5-mini","gpt-5-nano"}: return ["minimal","low","medium","high"]
    return ["none","low","medium","high"]


def _shared_reasoning():
    current_model = _shared_model()
    allowed = _reasoning_options_for_model(current_model)
    cur = _safe_text(st.session_state.get("shared_reasoning", DEFAULT_REASONING)).lower() or DEFAULT_REASONING
    if cur not in allowed:
        cur = "none" if "none" in allowed else allowed[0]
        st.session_state["shared_reasoning"] = cur
    return cur


def _model_supports_reasoning(model: str) -> bool: return _safe_text(model).lower().startswith("gpt-5")


def _normalize_reasoning_effort_for_model(model: str, reasoning_effort: Optional[str]) -> Optional[str]:
    if not _model_supports_reasoning(model): return None
    allowed = _reasoning_options_for_model(model)
    effort = _safe_text(reasoning_effort).lower()
    if effort in allowed: return effort
    if not effort: return allowed[0] if allowed else None
    if effort == "none" and "minimal" in allowed: return "minimal"
    if effort == "minimal" and "none" in allowed: return "none"
    if effort == "xhigh" and "high" in allowed: return "high"
    if effort == "high" and "xhigh" in allowed: return "high"
    return allowed[0] if allowed else None


def _model_accepts_temperature(model: str, reasoning_effort: Optional[str]) -> bool:
    m = _safe_text(model).lower()
    eff = _safe_text(reasoning_effort).lower()
    if not m.startswith("gpt-5"): return True
    if m.startswith("gpt-5.4") or m in {"gpt-5-chat-latest","gpt-5.2","gpt-5.2-pro"}: return eff in {"","none"}
    return False


def _split_chat_messages(messages, keep_last=AI_VISIBLE_CHAT_MESSAGES):
    items = list(messages or []); keep = max(1, int(keep_last or 1))
    if len(items) <= keep: return [], items
    return items[:-keep], items[-keep:]


def _show_thinking(msg):
    ph = st.empty()
    ph.markdown(f"""<div class="thinking-overlay"><div class="thinking-card">
      <div class="thinking-spinner"></div>
      <div class="thinking-title">Working…</div>
      <div class="thinking-sub">{_esc(msg)}</div>
    </div></div>""", unsafe_allow_html=True)
    return ph


def _safe_json_load(s):
    s = (s or "").strip()
    if not s: return {}
    try: return json.loads(s)
    except Exception: pass
    try:
        i = s.find("{"); j = s.rfind("}")
        if i >= 0 and j > i: return json.loads(s[i:j+1])
    except Exception: pass
    return {}


def _prepare_messages_for_model(model: str, messages):
    prepared = []
    use_developer = _safe_text(model).lower().startswith("gpt-5")
    for msg in list(messages or []):
        if not isinstance(msg, dict): continue
        item = dict(msg)
        if use_developer and item.get("role") == "system": item["role"] = "developer"
        prepared.append(item)
    return prepared


def _build_completion_token_kwargs(max_tokens):
    try: limit = int(max_tokens) if max_tokens is not None else None
    except Exception: limit = None
    if limit is None or limit <= 0: return {}
    return {"max_completion_tokens": limit}


def _chat_complete(client, *, model, messages, temperature=0.0, response_format=None,
                   max_tokens=1200, reasoning_effort=None, _max_retries=3):
    if client is None: return ""
    effort = _normalize_reasoning_effort_for_model(model, reasoning_effort)
    kwargs = dict(model=model, messages=_prepare_messages_for_model(model, messages))
    kwargs.update(_build_completion_token_kwargs(max_tokens))
    if response_format: kwargs["response_format"] = response_format
    if effort: kwargs["reasoning_effort"] = effort
    if temperature is not None and _model_accepts_temperature(model, effort): kwargs["temperature"] = temperature
    last_exc = None
    reasoning_enabled = "reasoning_effort" in kwargs
    temperature_enabled = "temperature" in kwargs
    for attempt in range(max(1, _max_retries)):
        try:
            resp = client.chat.completions.create(**kwargs)
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            last_exc = exc; err = str(exc).lower()
            if "max_completion_tokens" in kwargs and any(k in err for k in ("unexpected keyword argument 'max_completion_tokens'","unsupported parameter","unknown parameter: max_completion_tokens","max_completion_tokens is not supported")):
                token_limit = kwargs.pop("max_completion_tokens", None)
                if token_limit is not None: kwargs["max_tokens"] = token_limit
                continue
            if "max_tokens" in kwargs and any(k in err for k in ("unexpected keyword argument 'max_tokens'","unsupported parameter","use 'max_completion_tokens' instead","deprecated","not compatible")):
                token_limit = kwargs.pop("max_tokens", None)
                if token_limit is not None: kwargs["max_completion_tokens"] = token_limit
                continue
            if reasoning_enabled and any(k in err for k in ("reasoning_effort","unknown parameter: reasoning_effort","unsupported parameter","invalid reasoning","does not support reasoning")):
                kwargs.pop("reasoning_effort", None); reasoning_enabled = False; continue
            if temperature_enabled and any(k in err for k in ("temperature","top_p","only supported when","not supported when reasoning")):
                kwargs.pop("temperature", None); temperature_enabled = False; continue
            if any(k in err for k in ("rate_limit","429","500","503","timeout","overloaded")):
                time.sleep(min((2**attempt)+random.uniform(0,1), 30)); continue
            raise
    if last_exc: raise last_exc
    return ""


def _model_candidates_for_task(selected_model: str, *, structured: bool = False) -> List[str]:
    preferred = _safe_text(selected_model) or DEFAULT_MODEL
    fallbacks = [preferred]
    if structured: fallbacks += [STRUCTURED_FALLBACK_MODEL, DEFAULT_MODEL, "gpt-4.1"]
    else: fallbacks += [DEFAULT_MODEL]
    out = []; seen = set()
    for m in fallbacks:
        if m and m not in seen: out.append(m); seen.add(m)
    return out


def _chat_complete_with_fallback_models(client, *, model, messages, structured=False, **kwargs):
    last_exc = None
    for candidate in _model_candidates_for_task(model, structured=structured):
        try: return _chat_complete(client, model=candidate, messages=messages, **kwargs)
        except Exception as exc: last_exc = exc; continue
    if last_exc: raise last_exc
    return ""

# ═══════════════════════════════════════════════════════════════════════════════
#  DATA LAYER — BazaarVoice fetching (brand-aware)
# ═══════════════════════════════════════════════════════════════════════════════
def _get_session():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    return s


def _extract_pid_from_url(url: str) -> Optional[str]:
    """SharkNinja: product ID is embedded in the URL path (e.g. AF181.html)."""
    path = urlparse(url).path
    m = re.search(r"/([A-Za-z0-9_-]+)\.html(?:$|[?#])", path)
    if m:
        c = m.group(1).strip().upper()
        if re.fullmatch(r"[A-Z0-9_-]{3,}", c): return c
    return None


def _extract_pid_from_html(h: str) -> Optional[str]:
    for pat in [
        r'Item\s*No\.?\s*([A-Z0-9_-]{3,})',
        r'"productId"\s*:\s*"([A-Z0-9_-]{3,})"',
        r'"sku"\s*:\s*"([A-Z0-9_-]{3,})"',
        r'"model"\s*:\s*"([A-Z0-9_-]{3,})"',
    ]:
        m = re.search(pat, h, flags=re.IGNORECASE)
        if m: return m.group(1).strip().upper()
    soup = BeautifulSoup(h, "html.parser")
    text = soup.get_text(" ", strip=True)
    for pat in [r"Item\s*No\.?\s*([A-Z0-9_-]{3,})", r"Model\s*:?\s*([A-Z0-9_-]{3,})"]:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m: return m.group(1).strip().upper()
    return None


def _bv_credentials(brand: str) -> Tuple[str, str, str]:
    """Return (passkey, displaycode, sort) for the given brand."""
    if brand == "Dyson":
        return DYSON_PASSKEY, DYSON_DISPLAYCODE, DYSON_SORT
    return DEFAULT_PASSKEY, DEFAULT_DISPLAYCODE, DEFAULT_SORT


def _fetch_reviews_page(session, *, product_id, passkey, displaycode, api_version,
                        page_size, offset, sort, content_locales):
    params = dict(
        resource="reviews",
        action="REVIEWS_N_STATS",
        filter=[
            f"productid:eq:{product_id}",
            f"contentlocale:eq:{content_locales}",
            "isratingsonly:eq:false",
        ],
        filter_reviews=f"contentlocale:eq:{content_locales}",
        include="authors,products,comments",
        filteredstats="reviews",
        Stats="Reviews",
        limit=int(page_size),
        offset=int(offset),
        limit_comments=3,
        sort=sort,
        passkey=passkey,
        apiversion=api_version,
        displaycode=displaycode,
    )
    resp = session.get(BAZAARVOICE_ENDPOINT, params=params, timeout=45)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("HasErrors"):
        raise ReviewDownloaderError(f"BV error: {payload.get('Errors')}")
    return payload


def _is_incentivized(r):
    badges = [str(b).lower() for b in (r.get("BadgesOrder") or [])]
    if any("incentivized" in b for b in badges): return True
    ctx = r.get("ContextDataValues") or {}
    if isinstance(ctx, dict):
        for k, v in ctx.items():
            if "incentivized" in str(k).lower():
                flag = str((v.get("Value","") if isinstance(v,dict) else v)).strip().lower()
                if flag in {"","true","1","yes"}: return True
    return False


def _flatten_review(r):
    photos = r.get("Photos") or []; urls = []
    for p in photos:
        sz = p.get("Sizes") or {}
        for sn in ["large","normal","thumbnail"]:
            u = (sz.get(sn) or {}).get("Url")
            if u: urls.append(u); break
    syn = r.get("SyndicationSource") or {}
    return dict(
        review_id=r.get("Id"), product_id=r.get("ProductId"),
        original_product_name=r.get("OriginalProductName"),
        title=_safe_text(r.get("Title")), review_text=_safe_text(r.get("ReviewText")),
        rating=r.get("Rating"), is_recommended=r.get("IsRecommended"),
        user_nickname=r.get("UserNickname"), author_id=r.get("AuthorId"),
        user_location=r.get("UserLocation"), content_locale=r.get("ContentLocale"),
        submission_time=r.get("SubmissionTime"), moderation_status=r.get("ModerationStatus"),
        campaign_id=r.get("CampaignId"), source_client=r.get("SourceClient"),
        is_featured=r.get("IsFeatured"), is_syndicated=r.get("IsSyndicated"),
        syndication_source_name=syn.get("Name"), is_ratings_only=r.get("IsRatingsOnly"),
        total_positive_feedback_count=r.get("TotalPositiveFeedbackCount"),
        badges=", ".join(str(x) for x in (r.get("BadgesOrder") or [])),
        context_data_json=json.dumps(r.get("ContextDataValues") or {}, ensure_ascii=False),
        photos_count=len(photos), photo_urls=" | ".join(urls),
        incentivized_review=_is_incentivized(r), raw_json=json.dumps(r, ensure_ascii=False),
    )


def _ensure_cols(df, cols):
    for c in cols:
        if c not in df.columns: df[c] = pd.NA
    return df


def _extract_age_group(val):
    if val is None or (isinstance(val, float) and pd.isna(val)): return None
    payload = val
    if isinstance(payload, str):
        stripped = payload.strip()
        if not stripped: return None
        try: payload = json.loads(stripped)
        except Exception: return None
    if not isinstance(payload, dict): return None
    for k, raw in payload.items():
        if "age" not in str(k).lower(): continue
        candidate = raw.get("Value") or raw.get("Label") if isinstance(raw, dict) else raw
        candidate = _safe_text(candidate)
        if candidate and candidate.lower() not in {"nan","none","null","unknown","prefer not to say"}:
            return candidate
    return None


def _finalize_df(df):
    required = [
        "review_id","product_id","base_sku","sku_item","product_or_sku",
        "original_product_name","title","review_text","rating","is_recommended",
        "content_locale","submission_time","submission_date","submission_month",
        "incentivized_review","is_syndicated","photos_count","photo_urls",
        "title_and_text","retailer","post_link","age_group","user_nickname",
        "user_location","total_positive_feedback_count","source_system","source_file",
    ]
    df = _ensure_cols(df.copy(), required)
    if df.empty:
        for c in ["has_photos","has_media","review_length_chars","review_length_words","rating_label","year_month_sort"]:
            if c not in df.columns: df[c] = pd.Series(dtype="object")
        return df
    df["review_id"] = df["review_id"].fillna("").astype(str).str.strip()
    missing = df["review_id"].eq("") | df["review_id"].str.lower().isin({"nan","none","null"})
    if missing.any():
        df.loc[missing,"review_id"] = [f"review_{i+1}" for i in range(int(missing.sum()))]
    if "context_data_json" in df.columns:
        df["age_group"] = df["age_group"].fillna(df["context_data_json"].map(_extract_age_group))
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
    df["incentivized_review"] = df["incentivized_review"].fillna(False).astype(bool)
    df["is_syndicated"] = df["is_syndicated"].fillna(False).astype(bool)
    df["photos_count"] = pd.to_numeric(df["photos_count"], errors="coerce").fillna(0).astype(int)
    df["title"] = df["title"].fillna("").astype(str)
    df["review_text"] = df["review_text"].fillna("").astype(str)
    df["submission_time"] = pd.to_datetime(df["submission_time"], errors="coerce", utc=True).dt.tz_convert(None)
    df["submission_date"] = df["submission_time"].dt.date
    df["submission_month"] = df["submission_time"].dt.to_period("M").astype(str)
    df["content_locale"] = df["content_locale"].fillna("").astype(str).replace({"": pd.NA})
    df["base_sku"] = df.get("base_sku", pd.Series(dtype="str")).fillna("").astype(str).str.strip()
    df["sku_item"] = df.get("sku_item", pd.Series(dtype="str")).fillna("").astype(str).str.strip()
    df["product_id"] = df["product_id"].fillna("").astype(str).str.strip()
    fallback = df["base_sku"].where(df["base_sku"].ne(""), df["product_id"])
    df["product_or_sku"] = df["sku_item"].where(df["sku_item"].ne(""), fallback)
    df["product_or_sku"] = df["product_or_sku"].fillna("").astype(str).str.strip().replace({"": pd.NA})
    df["title_and_text"] = (df["title"].str.strip()+" "+df["review_text"].str.strip()).str.strip()
    df["has_photos"] = df["photos_count"] > 0
    df["has_media"] = df["has_photos"]
    df["review_length_chars"] = df["review_text"].str.len()
    df["review_length_words"] = df["review_text"].str.split().str.len().fillna(0).astype(int)
    df["rating_label"] = df["rating"].map(lambda x: f"{int(x)} star" if pd.notna(x) else "Unknown")
    df["year_month_sort"] = pd.to_datetime(df["submission_month"], format="%Y-%m", errors="coerce")
    sc = [c for c in ["submission_time","review_id"] if c in df.columns]
    if sc: df = df.sort_values(sc, ascending=[False,False], na_position="last").reset_index(drop=True)
    return df


def _pick_col(df, aliases):
    lk = {str(c).strip().lower(): c for c in df.columns}
    for a in aliases:
        c = lk.get(str(a).strip().lower())
        if c: return c
    return None


def _series_alias(df, aliases):
    c = _pick_col(df, aliases)
    if c is None: return pd.Series([pd.NA]*len(df), index=df.index)
    return df[c]


def _parse_flag(v, *, pos, neg):
    t = _safe_text(v).lower()
    if t in {"","nan","none","null","n/a"}: return pd.NA
    if any(t == x.lower() for x in neg): return False
    if any(t == x.lower() for x in pos): return True
    if t.startswith(("not ","non ")): return False
    return True


def _normalize_uploaded_df(raw, *, source_name=""):
    w = raw.copy(); w.columns = [str(c).strip() for c in w.columns]
    n = pd.DataFrame(index=w.index)
    n["review_id"] = _series_alias(w, ["Event Id","Event ID","Review ID","Review Id","Id"])
    n["product_id"] = _series_alias(w, ["Base SKU","Product ID","Product Id","ProductId","BaseSKU"])
    n["base_sku"] = _series_alias(w, ["Base SKU","BaseSKU"])
    n["sku_item"] = _series_alias(w, ["SKU Item","SKU","Child SKU","Variant SKU","Item Number","Item No"])
    n["original_product_name"] = _series_alias(w, ["Product Name","Product","Name"])
    n["review_text"] = _series_alias(w, ["Review Text","Review","Body","Content"])
    n["title"] = _series_alias(w, ["Title","Review Title","Headline"])
    n["post_link"] = _series_alias(w, ["Post Link","URL","Review URL","Product URL"])
    n["rating"] = _series_alias(w, ["Rating (num)","Rating","Stars","Star Rating"])
    n["submission_time"] = _series_alias(w, ["Opened date","Opened Date","Submission Time","Review Date","Date"])
    n["content_locale"] = _series_alias(w, ["Content Locale","Locale","Location","Country"])
    n["retailer"] = _series_alias(w, ["Retailer","Merchant","Channel"])
    n["age_group"] = _series_alias(w, ["Age Group","Age","Age Range"])
    n["user_location"] = _series_alias(w, ["Location","Country"])
    n["user_nickname"] = pd.NA
    n["total_positive_feedback_count"] = pd.NA
    n["is_recommended"] = pd.NA
    n["photos_count"] = 0
    n["photo_urls"] = pd.NA
    n["source_file"] = source_name or pd.NA
    n["source_system"] = "Uploaded file"
    seeded = _series_alias(w, ["Seeded Flag","Seeded","Incentivized"])
    n["incentivized_review"] = seeded.map(lambda v: _parse_flag(v, pos=["seeded","incentivized","yes","true","1"], neg=["not seeded","not incentivized","no","false","0"]))
    syndicated = _series_alias(w, ["Syndicated Flag","Syndicated"])
    n["is_syndicated"] = syndicated.map(lambda v: _parse_flag(v, pos=["syndicated","yes","true","1"], neg=["not syndicated","no","false","0"]))
    return _finalize_df(n)


def _read_uploaded_file(f):
    fname = getattr(f, "name", "uploaded_file")
    raw = f.getvalue()
    suffix = fname.lower().rsplit(".", 1)[-1] if "." in fname else "csv"
    if suffix == "csv":
        try: raw_df = pd.read_csv(io.BytesIO(raw))
        except UnicodeDecodeError: raw_df = pd.read_csv(io.BytesIO(raw), encoding="latin-1")
    elif suffix in {"xlsx","xls","xlsm"}: raw_df = pd.read_excel(io.BytesIO(raw))
    else: raise ReviewDownloaderError(f"Unsupported: {fname}")
    if raw_df.empty: raise ReviewDownloaderError(f"{fname} is empty.")
    return _normalize_uploaded_df(raw_df, source_name=fname)


def _load_uploaded_files(files):
    if not files: raise ReviewDownloaderError("Upload at least one file.")
    with st.spinner("Reading files…"):
        frames = [_read_uploaded_file(f) for f in files]
    combined = pd.concat(frames, ignore_index=True)
    combined["review_id"] = combined["review_id"].astype(str)
    combined = combined.drop_duplicates(subset=["review_id"], keep="first").reset_index(drop=True)
    combined = _finalize_df(combined)
    pid = (_first_non_empty(combined["base_sku"].fillna("")) or
           _first_non_empty(combined["product_id"].fillna("")) or "UPLOADED_REVIEWS")
    names = [getattr(f,"name","file") for f in files]
    src = names[0] if len(names)==1 else f"{len(names)} uploaded files"
    summary = ReviewBatchSummary(product_url="", product_id=pid, total_reviews=len(combined),
        page_size=max(len(combined),1), requests_needed=0, reviews_downloaded=len(combined))
    return dict(summary=summary, reviews_df=combined, source_type="uploaded", source_label=src)


# ─── Brand-aware product review loader ───────────────────────────────────────

def _load_product_reviews(product_url: str) -> Dict[str, Any]:
    """
    Unified review loader for both SharkNinja and Dyson product URLs.
    Auto-detects brand from the URL and uses the correct BV credentials.
    For Dyson, uses the three-strategy PID resolution cascade.
    """
    product_url = product_url.strip()
    if not re.match(r"^https?://", product_url, re.IGNORECASE):
        product_url = "https://" + product_url

    is_dyson = _is_dyson_url(product_url)
    brand = "Dyson" if is_dyson else "SharkNinja"
    passkey, displaycode, sort = _bv_credentials(brand)

    session = _get_session()

    # ── 1. Fetch PDP page
    with st.spinner(f"Loading {'Dyson' if is_dyson else 'SharkNinja'} product page…"):
        resp = session.get(product_url, timeout=30)
        resp.raise_for_status()
        product_html = resp.text

    # ── 2. Resolve product ID
    if is_dyson:
        pid = _resolve_dyson_pid(product_url, product_html)
        if not pid:
            # Render manual picker and wait for user selection
            pid = _render_dyson_manual_picker()
            if not pid:
                st.info("Select a product from the picker above and click **Use this product ID** to continue.")
                st.stop()
            # Store so we skip re-fetching on rerun
            st.session_state["_dyson_pending_pid"] = pid
            st.session_state["_dyson_pending_url"] = product_url
            st.rerun()
    else:
        pid = _extract_pid_from_url(product_url) or _extract_pid_from_html(product_html)
        if not pid:
            raise ReviewDownloaderError("Could not find product ID in URL or page HTML.")

    # ── 3. Check total review count
    with st.spinner(f"Checking review volume for {pid}…"):
        payload = _fetch_reviews_page(
            session, product_id=pid, passkey=passkey, displaycode=displaycode,
            api_version=DEFAULT_API_VERSION, page_size=1, offset=0,
            sort=sort, content_locales=DEFAULT_CONTENT_LOCALES,
        )
        total = int(payload.get("TotalResults", 0))

    if total == 0:
        st.warning(
            f"BazaarVoice returned 0 reviews for **{pid}**.\n\n"
            + ("This is a Dyson Beta feature — the resolved product ID may need adjustment. "
               "Try the manual picker to select a different SKU." if is_dyson else
               "Check the product URL.")
        )
        if is_dyson:
            alt = _render_dyson_manual_picker()
            if alt:
                st.session_state["_dyson_pending_pid"] = alt
                st.session_state["_dyson_pending_url"] = product_url
                st.rerun()
        st.stop()

    # ── 4. Paginate and download all reviews
    progress  = st.progress(0.0, text="Downloading…")
    status    = st.empty()
    offsets   = list(range(0, total, DEFAULT_PAGE_SIZE))
    raw_reviews: List[Dict[str, Any]] = []

    for i, offset in enumerate(offsets, 1):
        status.info(f"Pulling page {i}/{len(offsets)} — {len(raw_reviews)} reviews so far")
        page = _fetch_reviews_page(
            session, product_id=pid, passkey=passkey, displaycode=displaycode,
            api_version=DEFAULT_API_VERSION, page_size=DEFAULT_PAGE_SIZE, offset=offset,
            sort=sort, content_locales=DEFAULT_CONTENT_LOCALES,
        )
        raw_reviews.extend(page.get("Results") or [])
        progress.progress(i / len(offsets))

    status.success(f"Downloaded {len(raw_reviews)} reviews for {pid} ({brand}).")

    df = _finalize_df(pd.DataFrame([_flatten_review(r) for r in raw_reviews]))
    if not df.empty:
        df["review_id"]      = df["review_id"].astype(str)
        df["product_or_sku"] = df.get("product_or_sku", pd.Series(index=df.index, dtype="object")).fillna(pid)
        df["base_sku"]       = df.get("base_sku",       pd.Series(index=df.index, dtype="object")).fillna(pid)
        df["product_id"]     = df["product_id"].fillna(pid)
        df["source_system"]  = brand  # tag which brand the reviews came from

    # Human-readable product name for Dyson
    dyson_product_name = DYSON_SKU_CATALOGUE.get(pid.lower(), "") if is_dyson else ""

    summary = ReviewBatchSummary(
        product_url=product_url, product_id=pid,
        total_reviews=total, page_size=DEFAULT_PAGE_SIZE,
        requests_needed=len(offsets), reviews_downloaded=len(df),
        brand=brand,
    )
    return dict(
        summary=summary, reviews_df=df,
        source_type="bazaarvoice",
        source_label=product_url,
        brand=brand,
        dyson_product_name=dyson_product_name,
    )

# ═══════════════════════════════════════════════════════════════════════════════
#  ANALYTICS (unchanged — omitting for brevity; paste original blocks here)
# ═══════════════════════════════════════════════════════════════════════════════
# NOTE: All analytics functions (_compute_metrics_direct, _rating_dist, etc.)
#       are IDENTICAL to the original file. Paste them in here unchanged.
#
# The only file-level additions are:
#   1. The Dyson constants + DYSON_SKU_CATALOGUE + DYSON_URL_PATH_TO_SKU (above)
#   2. The Dyson resolution functions (above)
#   3. The modified _bv_credentials() and _load_product_reviews() (above)
#   4. The modified main() and _render_workspace_header() (below)
# ═══════════════════════════════════════════════════════════════════════════════

# [PASTE ALL ORIGINAL ANALYTICS, FILTER, AI, SYMPTOMIZER, AND EXPORT
#  FUNCTIONS HERE — THEY ARE COMPLETELY UNCHANGED]

# ═══════════════════════════════════════════════════════════════════════════════
#  RENDER — Workspace header (brand-aware)
# ═══════════════════════════════════════════════════════════════════════════════
def _product_name(summary, df):
    # For Dyson: prefer the catalogue name we resolved at load time
    dataset = st.session_state.get("analysis_dataset") or {}
    dyson_name = dataset.get("dyson_product_name", "")
    if dyson_name:
        return f"Dyson {dyson_name}"
    if not df.empty and "original_product_name" in df.columns:
        n = _first_non_empty(df["original_product_name"].fillna(""))
        if n: return n
    return summary.product_id


def _render_workspace_header(summary, overall_df, prompt_artifacts, *, source_type, source_label):
    bundle = _get_master_bundle(summary, overall_df, prompt_artifacts)
    product_name = _product_name(summary, overall_df)
    organic = int((~overall_df["incentivized_review"].fillna(False)).sum()) if not overall_df.empty else 0
    n = len(overall_df)
    brand = (st.session_state.get("analysis_dataset") or {}).get("brand", "SharkNinja")
    is_dyson = brand == "Dyson"

    if is_dyson:
        src_chip = f"Dyson · {summary.product_id}"
        kicker_cls = "hero-kicker dyson"
        card_cls = "hero-card dyson"
        brand_label = "🌀 Dyson · Beta"
    else:
        src_chip = f"Bazaarvoice · {summary.product_id}"
        kicker_cls = "hero-kicker"
        card_cls = "hero-card"
        brand_label = "🦈 SharkNinja"

    st.markdown(f"""<div class="{card_cls}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;">
        <div>
          <div class="{kicker_cls}">{brand_label} · Review workspace</div>
          <div class="hero-title">{_esc(product_name)}</div>
        </div>
        <div class="badge-row">
          <span class="chip {'dyson' if is_dyson else 'gray'}">{_esc(src_chip)}</span>
          <span class="chip indigo">{n:,} reviews</span>
          <span class="chip green">{organic:,} organic</span>
          {'<span class="chip yellow">Beta Feature</span>' if is_dyson else ''}
        </div>
      </div>
    </div>""", unsafe_allow_html=True)

    if is_dyson:
        st.markdown(f"""<div class="dyson-beta-banner" style="margin-bottom:.8rem;">
          <b>Dyson Review Analyst (Beta)</b> — Product ID resolved as
          <code>{_esc(summary.product_id)}</code> ·
          {_esc(DYSON_SKU_CATALOGUE.get(summary.product_id, "Unknown product"))}<br>
          <span style="font-size:12px;color:#7f0442;">
            If the product name looks wrong, reset and try the URL for a different colour variant,
            or use the BV product ID directly.
          </span>
        </div>""", unsafe_allow_html=True)

    a0, a1, a2 = st.columns([1.2, 1.2, 4])
    a0.download_button("⬇️ Download reviews", data=bundle["excel_bytes"],
        file_name=bundle["excel_name"],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True)
    if a1.button("🔄 Reset workspace", use_container_width=True):
        _reset_workspace_state(reset_source=True)
        st.rerun()
    a2.caption("Export includes Reviews, Rating Distribution, Volume trend, and any AI prompt or Symptomizer columns.")

# ═══════════════════════════════════════════════════════════════════════════════
#  SESSION STATE (unchanged, adds dyson_pending keys)
# ═══════════════════════════════════════════════════════════════════════════════
def _init_state():
    defaults = dict(
        analysis_dataset=None, chat_messages=[], master_export_bundle=None,
        prompt_definitions_df=_default_prompt_df(), prompt_builder_suggestion=None,
        prompt_run_artifacts=None, prompt_run_notice=None,
        chat_scope_signature=None, chat_scope_notice=None,
        review_explorer_page=1, review_explorer_per_page=20,
        review_explorer_sort="Newest", review_filter_signature=None,
        shared_model=DEFAULT_MODEL, shared_reasoning=DEFAULT_REASONING,
        workspace_source_mode=SOURCE_MODE_URL,
        workspace_product_url=DEFAULT_PRODUCT_URL,
        workspace_file_uploader_nonce=0,
        workspace_active_tab=TAB_DASHBOARD, workspace_tab_request=None,
        ai_scroll_to_top=False,
        sym_delighters=[], sym_detractors=[], sym_aliases={},
        sym_symptoms_source="none", sym_processed_rows=[], sym_new_candidates={},
        sym_product_profile="", sym_scope_choice="Missing both",
        sym_n_to_process=10, sym_batch_size=5, sym_max_ev_chars=120,
        sym_run_notice=None, _prompt_defs_cache={}, _prompt_bundle_ready=False,
        # Dyson-specific
        _dyson_pending_pid=None, _dyson_pending_url=None,
    )
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)

# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    _init_state()

    # ── Handle pending Dyson PID (set by manual picker rerun)
    pending_pid = st.session_state.pop("_dyson_pending_pid", None)
    pending_url = st.session_state.pop("_dyson_pending_url", None)
    if pending_pid and pending_url:
        # User confirmed the manual picker — load directly
        session = _get_session()
        passkey, displaycode, sort = _bv_credentials("Dyson")
        with st.spinner(f"Fetching reviews for {pending_pid}…"):
            try:
                payload = _fetch_reviews_page(
                    session, product_id=pending_pid, passkey=passkey,
                    displaycode=displaycode, api_version=DEFAULT_API_VERSION,
                    page_size=1, offset=0, sort=sort,
                    content_locales=DEFAULT_CONTENT_LOCALES,
                )
                total = int(payload.get("TotalResults", 0))
                prog = st.progress(0.0)
                raw_reviews = []
                for i, offset in enumerate(range(0, total, DEFAULT_PAGE_SIZE), 1):
                    page = _fetch_reviews_page(
                        session, product_id=pending_pid, passkey=passkey,
                        displaycode=displaycode, api_version=DEFAULT_API_VERSION,
                        page_size=DEFAULT_PAGE_SIZE, offset=offset, sort=sort,
                        content_locales=DEFAULT_CONTENT_LOCALES,
                    )
                    raw_reviews.extend(page.get("Results") or [])
                    prog.progress(i / max(1, math.ceil(total / DEFAULT_PAGE_SIZE)))
                df = _finalize_df(pd.DataFrame([_flatten_review(r) for r in raw_reviews]))
                if not df.empty:
                    df["review_id"] = df["review_id"].astype(str)
                    df["source_system"] = "Dyson"
                    for col in ["product_or_sku","base_sku","product_id"]:
                        df[col] = df.get(col, pd.Series(index=df.index, dtype="object")).fillna(pending_pid)
                summary = ReviewBatchSummary(
                    product_url=pending_url, product_id=pending_pid,
                    total_reviews=total, page_size=DEFAULT_PAGE_SIZE,
                    requests_needed=math.ceil(total/DEFAULT_PAGE_SIZE),
                    reviews_downloaded=len(df), brand="Dyson",
                )
                nd = dict(summary=summary, reviews_df=df, source_type="bazaarvoice",
                          source_label=pending_url, brand="Dyson",
                          dyson_product_name=DYSON_SKU_CATALOGUE.get(pending_pid, ""))
                _reset_review_filters()
                st.session_state.update(analysis_dataset=nd, chat_messages=[],
                    master_export_bundle=None, prompt_run_artifacts=None,
                    sym_processed_rows=[], sym_new_candidates={},
                    sym_symptoms_source="none", workspace_active_tab=TAB_DASHBOARD)
                st.rerun()
            except Exception as exc:
                st.error(f"Failed to load {pending_pid}: {exc}")

    st.markdown("""<div style="display:flex;align-items:center;gap:12px;margin-bottom:.2rem;">
      <div style="display:flex;gap:6px;">
        <div style="width:36px;height:36px;background:#0f172a;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:18px;">🦈</div>
        <div style="width:36px;height:36px;background:#7f0442;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:18px;">🌀</div>
      </div>
      <div>
        <div style="font-size:20px;font-weight:800;letter-spacing:-.03em;color:#0f172a;">SharkNinja + Dyson Review Analyst</div>
        <div style="font-size:12px;color:#64748b;margin-top:1px;">Voice-of-customer · AI analyst · Symptomizer · <span style="color:#7f0442;font-weight:600;">Dyson Beta</span></div>
      </div>
    </div>""", unsafe_allow_html=True)

    dataset = st.session_state.get("analysis_dataset")
    if dataset:
        bc = st.columns([4.2, 1.0])
        bc[0].caption(f"Active workspace · {dataset.get('brand','SharkNinja')} · {dataset.get('source_label','')}")
        if bc[1].button("Clear workspace", use_container_width=True, key="ws_clear"):
            _reset_workspace_state(reset_source=True); st.rerun()

    if st.session_state.get("workspace_source_mode") not in {SOURCE_MODE_URL, SOURCE_MODE_FILE}:
        st.session_state["workspace_source_mode"] = SOURCE_MODE_URL

    source_mode = st.radio("Workspace source", [SOURCE_MODE_URL, SOURCE_MODE_FILE],
                            horizontal=True, key="workspace_source_mode")

    if source_mode == SOURCE_MODE_URL:
        url_input = st.text_input("Product URL", key="workspace_product_url",
            help="SharkNinja: https://www.sharkninja.com/.../AF181.html\n"
                 "Dyson (Beta): https://www.dyson.com/hair-care/hair-stylers/airwrap-origin/nickel-copper")
        url_val = url_input or ""
        is_dyson_input = _is_dyson_url(url_val)

        # Example URLs helper
        with st.expander("📋 Example URLs", expanded=False):
            ex_col1, ex_col2 = st.columns(2)
            with ex_col1:
                st.markdown("**SharkNinja examples**")
                for label, url in [
                    ("Ninja Air Fryer XL", "https://www.sharkninja.com/ninja-air-fryer-pro-xl-6-in-1/AF181.html"),
                    ("Shark FlexVac", "https://www.sharkninja.com/shark-flexvac-cordless-vacuum/IX140.html"),
                ]:
                    if st.button(f"📎 {label}", key=f"ex_sn_{label}", use_container_width=True):
                        st.session_state["workspace_product_url"] = url; st.rerun()
            with ex_col2:
                st.markdown("**Dyson examples (Beta)**")
                for label, url in [
                    ("Airwrap Origin (Nickel/Copper)", DEFAULT_DYSON_URL),
                    ("WashG1 (Copper)", "https://www.dyson.com/floor-cleaners/wet/washg1/copper"),
                    ("Gen5detect", "https://www.dyson.com/vacuums-and-floorcare/cordless-vacuums/gen5detect/yellow-nickel"),
                    ("Supersonic (Nickel/Copper)", "https://www.dyson.com/hair-care/hair-dryers/supersonic/nickel-copper"),
                    ("Purifier Cool TP07", "https://www.dyson.com/air-treatment/purifiers/purifier-cool-tp07/white-silver"),
                    ("Corrale (Blue/Copper)", "https://www.dyson.com/hair-care/hair-straighteners/corrale/blue-copper"),
                ]:
                    if st.button(f"🌀 {label}", key=f"ex_dy_{label}", use_container_width=True):
                        st.session_state["workspace_product_url"] = url; st.rerun()

        if is_dyson_input:
            st.markdown("""<div class="dyson-beta-banner">
              <b>🌀 Dyson URL detected</b> — Beta feature enabled.<br>
              <span style="font-size:12px;">
              The Dyson BazaarVoice product ID is not in the URL, so the app will try to extract it
              from the page source, then match the URL slug against the built-in SKU database.
              If both fail, a manual product picker will appear.
              </span>
            </div>""", unsafe_allow_html=True)

        if st.button("Build review workspace", type="primary", key="ws_build_url"):
            try:
                nd = _load_product_reviews(st.session_state.get("workspace_product_url", DEFAULT_PRODUCT_URL))
                _reset_review_filters()
                st.session_state.update(analysis_dataset=nd, chat_messages=[],
                    master_export_bundle=None, prompt_run_artifacts=None,
                    sym_processed_rows=[], sym_new_candidates={},
                    sym_symptoms_source="none", workspace_active_tab=TAB_DASHBOARD,
                    workspace_tab_request=None)
                st.rerun()
            except requests.HTTPError as exc: st.error(f"HTTP error: {exc}")
            except ReviewDownloaderError as exc: st.error(str(exc))
            except Exception as exc: st.exception(exc)
    else:
        uploader_key = f"workspace_files_{int(st.session_state.get('workspace_file_uploader_nonce',0))}"
        uploaded_files = st.file_uploader("Upload review export files",
            type=["csv","xlsx","xls"], accept_multiple_files=True,
            help="Supports Axion-style exports and similar CSV/XLSX review files.",
            key=uploader_key)
        st.caption("Mapped columns: Event Id · Base SKU · Review Text · Rating · Opened date · Seeded Flag · Retailer")
        if st.button("Build review workspace from file", type="primary", key="ws_build_file"):
            try:
                nd = _load_uploaded_files(uploaded_files or [])
                _reset_review_filters()
                st.session_state.update(analysis_dataset=nd, chat_messages=[],
                    master_export_bundle=None, prompt_run_artifacts=None,
                    sym_processed_rows=[], sym_new_candidates={},
                    sym_symptoms_source="none", workspace_active_tab=TAB_DASHBOARD,
                    workspace_tab_request=None)
                if uploaded_files and len(uploaded_files) == 1:
                    fname = getattr(uploaded_files[0],"name","")
                    if fname.lower().endswith(".xlsx"):
                        st.session_state["_uploaded_raw_bytes"] = uploaded_files[0].getvalue()
                st.rerun()
            except ReviewDownloaderError as exc: st.error(str(exc))
            except Exception as exc: st.exception(exc)

    # ── All remaining workspace rendering (sidebar, tabs, etc.) unchanged
    dataset = st.session_state.get("analysis_dataset")
    settings = _render_sidebar(dataset["reviews_df"] if dataset else None)
    if not dataset:
        st.markdown("""<div style="margin-top:2rem;padding:2rem;background:var(--surface,#fff);border:1px solid #dde1e8;border-radius:18px;text-align:center;box-shadow:0 1px 4px rgba(15,23,42,.08);">
          <div style="font-size:2.5rem;margin-bottom:.75rem;">📊</div>
          <div style="font-size:16px;font-weight:700;color:#0f172a;margin-bottom:.4rem;">No workspace loaded</div>
          <div style="font-size:13px;color:#64748b;">
            Enter a SharkNinja or Dyson product URL, or upload a review export above
            to unlock the Dashboard, Review Explorer, AI Analyst, Review Prompt, and Symptomizer.
          </div>
        </div>""", unsafe_allow_html=True)
        return

    summary     = dataset["summary"]
    overall_df  = dataset["reviews_df"]
    source_type = dataset.get("source_type","bazaarvoice")
    source_label= dataset.get("source_label","")
    filter_state= settings["review_filters"]
    filtered_df = filter_state["filtered_df"]
    filter_description = filter_state["description"]
    new_filter_sig = json.dumps(filter_state["active_items"], default=str)
    if st.session_state.get("review_filter_signature") != new_filter_sig:
        st.session_state["review_filter_signature"] = new_filter_sig
        st.session_state["review_explorer_page"] = 1

    _render_workspace_header(summary, overall_df,
        st.session_state.get("prompt_run_artifacts"),
        source_type=source_type, source_label=source_label)
    _render_top_metrics(overall_df, filtered_df)
    _render_active_filter_summary(filter_state, overall_df)

    pending_tab = st.session_state.pop("workspace_tab_request", None)
    if pending_tab in WORKSPACE_TABS: st.session_state["workspace_active_tab"] = pending_tab
    elif st.session_state.get("workspace_active_tab") not in WORKSPACE_TABS:
        st.session_state["workspace_active_tab"] = TAB_DASHBOARD

    st.markdown("<div class='nav-tabs-wrap'><div class='nav-tabs-label'>Workspace</div></div>", unsafe_allow_html=True)
    active_tab = st.radio("Workspace tab", WORKSPACE_TABS, horizontal=True,
                           key="workspace_active_tab", label_visibility="collapsed")
    common = dict(settings=settings, overall_df=overall_df, filtered_df=filtered_df,
                  summary=summary, filter_description=filter_description)
    if   active_tab == TAB_DASHBOARD:       _render_dashboard(filtered_df, overall_df)
    elif active_tab == TAB_REVIEW_EXPLORER: _render_review_explorer(summary=summary, overall_df=overall_df, filtered_df=filtered_df, prompt_artifacts=st.session_state.get("prompt_run_artifacts"))
    elif active_tab == TAB_AI_ANALYST:      _render_ai_tab(**common)
    elif active_tab == TAB_REVIEW_PROMPT:   _render_review_prompt_tab(**common)
    elif active_tab == TAB_SYMPTOMIZER:     _render_symptomizer_tab(**common)


if __name__ == "__main__":
    main()
