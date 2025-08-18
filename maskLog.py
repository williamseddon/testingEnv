# app.py — Streamlined Streamlit UI for Willow (FW300) BDP control over UART
# Run: streamlit run app.py

import time, re, io
import streamlit as st
import serial
from serial.tools import list_ports

# ---------- Protocol constants ----------
BAUD = 115200
READ_TIMEOUT = 1.0
INTER_CMD_DELAY = 0.18

LED_TYPES = {"0": "Red", "1": "IR", "2": "Blue", "3": "MSI"}
LED_STATES = {
    "0":"Off","1":"Dim","2":"On",
    "3":"Skin Sustain","4":"Skin Clearing 1","5":"Skin Clearing 2",
    "6":"Skin Clearing 3","7":"Better Aging"
}
NTC_OPTIONS = {"1":"Peltier 1", "2":"Peltier 2", "3":"Battery", "4":"Q2"}
WX_SERIAL_RE = re.compile(r"\(21\)([A-Za-z0-9]+)")

# ---------- Helpers ----------
def win_port_name(name: str) -> str:
    up = name.upper()
    if up.startswith("COM"):
        try:
            if int(up[3:]) >= 10:
                return r"\\.\{}".format(name)
        except ValueError:
            pass
    return name

def list_serial_ports():
    return list_ports.comports()

def open_serial(port_name: str) -> serial.Serial:
    ser = serial.Serial(
        win_port_name(port_name), BAUD,
        timeout=READ_TIMEOUT, write_timeout=READ_TIMEOUT,
        bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE
    )
    ser.reset_input_buffer(); ser.reset_output_buffer()
    return ser

def write_cmd(ser, cmd: str):
    ser.write((cmd + "\r").encode("ascii", errors="ignore"))
    ser.flush()
    time.sleep(INTER_CMD_DELAY)

def read_reply(ser) -> str:
    data = ser.read_until(b"\r", 4096)
    if not data:
        data = ser.read_all()
    return data.decode(errors="ignore").strip()

def xfer(ser, cmd: str, wait=0.0, retries=1) -> str:
    for _ in range(retries + 1):
        write_cmd(ser, cmd)
        if wait: time.sleep(wait)
        resp = read_reply(ser)
        if resp: return resp
        time.sleep(0.1)
    return ""

def parse_hex_word(raw: str, prefix: str) -> int | None:
    m = re.match(rf"^{re.escape(prefix)}([0-9A-Fa-f]{{4}})$", raw or "")
    return int(m.group(1), 16) if m else None

def parse_signed_16_hex(word_hex: str) -> int:
    v = int(word_hex, 16)
    return v - 0x10000 if v >= 0x8000 else v

# ---- Specific decoders (based on your BDP doc) ----
def decode_BA(raw: str):
    # $BA <volt:word><dischg:word><soc:byte><ntc:word>
    m = re.match(r"^\$BA([0-9A-Fa-f]{4})([0-9A-Fa-f]{4})([0-9A-Fa-f]{2})([0-9A-Fa-f]{4})$", raw or "")
    if not m: return None
    volt_mv  = int(m.group(1), 16)
    d_ma     = int(m.group(2), 16)
    soc_pct  = int(m.group(3), 16)  # duty 0..100 (0x64)
    ntc_mv   = int(m.group(4), 16)
    return {"Voltage (mV)": volt_mv, "Discharge (mA)": d_ma, "SoC (%)": soc_pct, "NTC (mV)": ntc_mv}

def decode_BC(raw: str):
    # $BC <chargerDetect:char><chargerV:word><chargeI:byte/word>
    m = re.match(r"^\$BC([01])([0-9A-Fa-f]{4})([0-9A-Fa-f]{1,4})$", raw or "")
    if not m: return None
    return {
        "Charger Detected": (m.group(1) == "1"),
        "Charger V (mV)": int(m.group(2), 16),
        "Charge I (mA)": int(m.group(3), 16)
    }

def decode_BP(raw: str):
    # $BP <cell1:word><cell2:word><state:C/D/F/I><chg:word><dis:word><chargerV:word>
    m = re.match(r"^\$BP([0-9A-Fa-f]{4})([0-9A-Fa-f]{4})([CDFI])([0-9A-Fa-f]{4})([0-9A-Fa-f]{4})([0-9A-Fa-f]{4})$", raw or "")
    if not m: return None
    return {
        "Cell1 (mV)": int(m.group(1), 16),
        "Cell2 (mV)": int(m.group(2), 16),
        "State": {"C":"Charge","D":"Discharge","F":"Fault","I":"Idle"}[m.group(3)],
        "Charge I (mA)": int(m.group(4), 16),
        "Discharge I (mA)": int(m.group(5), 16),
        "Charger V (mV)": int(m.group(6), 16),
    }

FAULT_BITS = [
    "OVP","OCP","Battery NTC S/O","Plate1 NTC S/O","Plate2 NTC S/O","Mask board not detected","Battery OTP","Battery UTP",
    "Fan blocked/not detected",">1V cell diff","UVP","USB input V out of range","Charging current abnormal","Peltier circuit abnormal",
    "LED driver hard fault","LED current abnormal","LED 12V abnormal","Charging timeout"
]
def decode_BF(raw: str):
    m = re.match(r"^\$BF([0-9A-Fa-f]{8})$", raw or "")
    if not m: return None
    flags = int(m.group(1), 16)
    active = []
    for i, name in enumerate(FAULT_BITS):
        if flags & (1 << i): active.append(f"{i}: {name}")
    return {"Raw": f"0x{flags:08X}", "Active Faults": active or ["None"]}

def decode_KN(raw: str):
    # $KN<option><word>  word = signed (°C*10)
    m = re.match(r"^\$KN([0-9A-Fa-f])([0-9A-Fa-f]{4})$", raw or "")
    if not m: return None
    temp_c = parse_signed_16_hex(m.group(2)) / 10.0
    return {"Option": m.group(1), "Temp (°C)": temp_c}

def decode_WX(raw: str):
    m = WX_SERIAL_RE.search(raw or "")
    return m.group(1) if m else None

def decode_WW_last5(raw: str):
    return raw[-5:] if raw and raw.startswith("$WW") and len(raw) >= 5 else None

# ---------- Streamlit state ----------
st.set_page_config(page_title="Willow BDP Controller", layout="wide")
if "ser" not in st.session_state: st.session_state.ser = None
if "log" not in st.session_state: st.session_state.log = []
if "port_name" not in st.session_state: st.session_state.port_name = None

def log(msg: str):
    stamp = time.strftime("%H:%M:%S")
    st.session_state.log.append(f"[{stamp}] {msg}")
    if len(st.session_state.log) > 500:
        st.session_state.log = st.session_state.log[-500:]

st.title("Willow (FW300) — BDP UART Controller")

# ---------- Sidebar: connection ----------
with st.sidebar:
    st.header("Connection")
    ports = list_serial_ports()
    if not ports:
        st.warning("No serial ports found. Plug the FT232 and click Refresh.")
    chosen = st.selectbox(
        "Serial Port",
        options=[p.device for p in ports],
        format_func=lambda d: next((f"{p.device} — {p.description}" for p in ports if p.device==d), d),
        index=0 if ports else None,
        key="port_select"
    )
    cols = st.columns(2)
    with cols[0]:
        if st.button("Refresh"):
            st.rerun()
    with cols[1]:
        if st.session_state.ser is None:
            if st.button("Connect", type="primary", use_container_width=True, disabled=not ports):
                try:
                    st.session_state.ser = open_serial(chosen)
                    st.session_state.port_name = chosen
                    st.success(f"Connected: {chosen} @ {BAUD} 8N1")
                    log(f"Connected to {chosen}")
                except Exception as e:
                    st.error(f"Open failed: {e}")
        else:
            if st.button("Disconnect", use_container_width=True):
                try: st.session_state.ser.close()
                except: pass
                st.session_state.ser = None
                st.session_state.port_name = None
                st.warning("Disconnected.")
                log("Disconnected")

    st.caption("Spec: 115200 8N1, commands end with CR (\\r). Try ?WZ first to confirm comms.")

if st.session_state.ser is None:
    st.info("Connect to a serial port to begin.")
    st.stop()

# ---------- Top metrics / quick actions ----------
q1, q2, q3, q4, q5 = st.columns(5)
with q1:
    if st.button("Version (?WZ)", use_container_width=True):
        resp = xfer(st.session_state.ser, "?WZ", wait=0.2)
        st.toast("Version requested.")
        log(f"> ?WZ\n{resp}")
with q2:
    if st.button("Serial Number (WX/WW)", use_container_width=True):
        raw = xfer(st.session_state.ser, "?WX", wait=0.2)
        sn = decode_WX(raw)
        if sn:
            st.success(f"Serial: {sn}")
            log(f"> ?WX\n{raw}\n[Serial]: {sn}")
        else:
            log(f"> ?WX\n{raw}\nNo (21) in WX; trying WWx...")
            for child in "0123":
                ww = xfer(st.session_state.ser, f"?WW{child}", wait=0.2)
                if ww.startswith("$WW"):
                    st.info(f"PCBA {child} serial (last 5): {ww[-5:]}")
                    log(f"> ?WW{child}\n{ww}\n[PCBA serial]: {ww[-5:]}")
                    break
with q3:
    if st.button("Fan ON (*KF1)", use_container_width=True):
        xfer(st.session_state.ser, "*KF1"); st.toast("Fan ON"); log("> *KF1")
with q4:
    if st.button("Fan OFF (*KF0)", use_container_width=True):
        xfer(st.session_state.ser, "*KF0"); st.toast("Fan OFF"); log("> *KF0")
with q5:
    if st.button("Fan Current (?KF)", use_container_width=True):
        raw = xfer(st.session_state.ser, "?KF", wait=0.2)
        mA = parse_hex_word(raw, "$KF")
        st.metric("Fan current (mA)", mA if mA is not None else "—")
        log(f"> ?KF\n{raw}\n[parsed mA]: {mA}")

st.divider()

# ---------- Tabs for organized controls ----------
tab_led, tab_peltier, tab_sensors, tab_batt, tab_faults, tab_cooling, tab_raw = st.tabs(
    ["LEDs", "Peltiers / Plate", "Sensors & Keys", "Battery", "Faults", "Cooling Test", "Raw"]
)

with tab_led:
    c1, c2, c3, c4 = st.columns([1,1,1,1])
    with c1:
        led_opt = st.selectbox("LED Type", options=list(LED_TYPES.keys()),
                               format_func=lambda k: f"{k} – {LED_TYPES[k]}")
    with c2:
        led_state = st.selectbox("State", options=list(LED_STATES.keys()),
                                 format_func=lambda k: f"{k} – {LED_STATES[k]}")
    with c3:
        if st.button("Set LEDs (*LG)", use_container_width=True):
            cmd = f"*LG{led_opt}{led_state}"
            xfer(st.session_state.ser, cmd)
            st.toast(f"LED {LED_TYPES[led_opt]} → {LED_STATES[led_state]}")
            log(f"> {cmd}")
    with c4:
        if st.button("Read LED current (?LG)", use_container_width=True):
            raw = xfer(st.session_state.ser, "?LG", wait=0.2)
            log(f"> ?LG\n{raw}")
    st.caption("LED types: 0=Red, 1=IR, 2=Blue, 3=MSI. States: 0..7 presets.")

with tab_peltier:
    pc1, pc2, pc3, pc4 = st.columns(4)
    with pc1:
        idx = st.selectbox("Peltier index", options=["0","1"])
    with pc2:
        if st.button("Peltier ON", use_container_width=True):
            xfer(st.session_state.ser, f"*KP{idx}1"); st.toast(f"Peltier {idx} ON"); log(f"> *KP{idx}1")
    with pc3:
        if st.button("Peltier OFF", use_container_width=True):
            xfer(st.session_state.ser, f"*KP{idx}0"); st.toast(f"Peltier {idx} OFF"); log(f"> *KP{idx}0")
    with pc4:
        if st.button("Read Peltier current (?KP)", use_container_width=True):
            raw = xfer(st.session_state.ser, "?KP", wait=0.2)
            mA = parse_hex_word(raw, "$KP")
            st.metric("Peltier current (mA)", mA if mA is not None else "—")
            log(f"> ?KP\n{raw}\n[parsed mA]: {mA}")

    st.markdown("---")
    kv1, kv2 = st.columns(2)
    with kv1:
        mv = st.slider("Cooling plate mV (0..5500)", min_value=0, max_value=5500, step=50, value=2000)
        if st.button("Set Cooling Plate (*KV)", use_container_width=True):
            cmd = f"*KV{mv:04X}"
            xfer(st.session_state.ser, cmd)
            st.toast(f"Cooling plate set to {mv} mV")
            log(f"> {cmd}")
    with kv2:
        if st.button("Read Cooling Plate (?KV)", use_container_width=True):
            raw = xfer(st.session_state.ser, "?KV", wait=0.2)  # some examples show $KP; handle both
            mv_parsed = parse_hex_word(raw, "$KV") or parse_hex_word(raw, "$KP")
            st.metric("Plate voltage (mV)", mv_parsed if mv_parsed is not None else "—")
            log(f"> ?KV\n{raw}\n[parsed mV]: {mv_parsed}")

with tab_sensors:
    s1, s2, s3, s4 = st.columns(4)
    with s1:
        opt = st.selectbox("NTC", options=list(NTC_OPTIONS.keys()), format_func=lambda k: f"{k} – {NTC_OPTIONS[k]}")
        if st.button("Read NTC (?KNx)", use_container_width=True):
            raw = xfer(st.session_state.ser, f"?KN{opt}", wait=0.2)
            dec = decode_KN(raw)
            st.metric(f"{NTC_OPTIONS[opt]} (°C)", f"{dec['Temp (°C)']:.1f}" if dec else "—")
            log(f"> ?KN{opt}\n{raw}\n{dec}")
    with s2:
        if st.button("Cap sensor (?KC)", use_container_width=True):
            raw = xfer(st.session_state.ser, "?KC", wait=0.2)
            st.metric("Cap Touch", "Pressed" if raw == "$KC1" else "Not pressed" if raw == "$KC0" else "—")
            log(f"> ?KC\n{raw}")
    with s3:
        if st.button("Key status (?UI)", use_container_width=True):
            raw = xfer(st.session_state.ser, "?UI", wait=0.2)
            st.code(raw or "—")
            log(f"> ?UI\n{raw}")
    with s4:
        if st.button("Encoder (?KS)", use_container_width=True):
            raw = xfer(st.session_state.ser, "?KS", wait=0.2)
            st.code(raw or "—")
            log(f"> ?KS\n{raw}")

with tab_batt:
    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("Battery Info (?BA)", use_container_width=True):
            raw = xfer(st.session_state.ser, "?BA", wait=0.2)
            dec = decode_BA(raw)
            if dec:
                c1, c2 = st.columns(2)
                c1.metric("Voltage (mV)", dec["Voltage (mV)"])
                c2.metric("SoC (%)", dec["SoC (%)"])
                st.caption(f"Discharge: {dec['Discharge (mA)']} mA • NTC: {dec['NTC (mV)']} mV")
            st.code(raw or "—")
            log(f"> ?BA\n{raw}\n{dec}")
    with b2:
        if st.button("Present State (?BP)", use_container_width=True):
            raw = xfer(st.session_state.ser, "?BP", wait=0.2)
            dec = decode_BP(raw)
            if dec:
                c1, c2, c3 = st.columns(3)
                c1.metric("State", dec["State"])
                c2.metric("Charge I (mA)", dec["Charge I (mA)"])
                c3.metric("Discharge I (mA)", dec["Discharge I (mA)"])
                st.caption(f"Cell1 {dec['Cell1 (mV)']} mV • Cell2 {dec['Cell2 (mV)']} mV • Charger {dec['Charger V (mV)']} mV")
            st.code(raw or "—")
            log(f"> ?BP\n{raw}\n{dec}")
    with b3:
        if st.button("Charging Info (?BC)", use_container_width=True):
            raw = xfer(st.session_state.ser, "?BC", wait=0.2)
            dec = decode_BC(raw)
            if dec:
                c1, c2 = st.columns(2)
                c1.metric("Charger Detected", "Yes" if dec["Charger Detected"] else "No")
                c2.metric("Charger V (mV)", dec["Charger V (mV)"])
                st.caption(f"Charge current: {dec['Charge I (mA)']} mA")
            st.code(raw or "—")
            log(f"> ?BC\n{raw}\n{dec}")

with tab_faults:
    if st.button("Fault Flags (?BF)", use_container_width=True):
        raw = xfer(st.session_state.ser, "?BF", wait=0.2)
        dec = decode_BF(raw)
        if dec:
            st.metric("Raw", dec["Raw"])
            st.write("Active:", ", ".join(dec["Active Faults"]))
        st.code(raw or "—")
        log(f"> ?BF\n{raw}\n{dec}")

with tab_cooling:
    cc1, cc2, cc3 = st.columns([1,1,1])
    with cc1:
        target_c = st.number_input("Target °C", value=16.0, step=0.5)
    with cc2:
        if st.button("Start (*CT)", use_container_width=True):
            val = int(round(target_c * 10))
            raw = xfer(st.session_state.ser, f"*CT1{val:04X}", wait=0.2)
            st.toast("Cooling test started")
            st.code(raw or "—")
            log(f"> *CT1{val:04X}\n{raw}")
    with cc3:
        if st.button("Read (?CT)", use_container_width=True):
            raw = xfer(st.session_state.ser, "?CT", wait=0.2)
            st.code(raw or "—")
            log(f"> ?CT\n{raw}")

with tab_raw:
    st.caption("Send any BDP command (without \\r).")
    raw_cmd = st.text_input("Command", placeholder="?WZ, *KF1, ?KN2 ...")
    if st.button("Send", use_container_width=True):
        resp = xfer(st.session_state.ser, raw_cmd, wait=0.2)
        st.code(resp or "—")
        log(f"> {raw_cmd}\n{resp}")

st.divider()
st.subheader("Session Log")
st.text_area("Console", value="\n".join(st.session_state.log), height=260)
colL, colR = st.columns([1,1])
with colL:
    if st.button("Clear Log"):
        st.session_state.log = []
with colR:
    if st.button("Download Log"):
        buf = io.StringIO("\n".join(st.session_state.log))
        st.download_button("Save log.txt", buf.getvalue(), file_name="willow_bdp_log.txt", mime="text/plain")
c
