# app.py — Streamlit UI for Willow (FW300) BDP control over UART
# pip install streamlit pyserial

import streamlit as st
import time, re
import serial
from serial.tools import list_ports

BAUD = 115200
READ_TIMEOUT = 1.0
INTER_CMD_DELAY = 0.20

LED_TYPES = {"0": "Red", "1": "IR", "2": "Blue", "3": "MSI"}  # *LG<option><state> / ?LG
LED_STATES = {
    "0": "Off", "1": "Dim", "2": "On",
    "3": "Skin Sustain", "4": "Skin Clearing 1",
    "5": "Skin Clearing 2", "6": "Skin Clearing 3", "7": "Better Aging"
}
NTC_OPTIONS = {"1": "Peltier 1", "2": "Peltier 2", "3": "Battery", "4": "Q2"}

WX_SERIAL_RE = re.compile(r"\(21\)([A-Za-z0-9]+)")

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

def open_serial(port_name: str):
    ser = serial.Serial(
        win_port_name(port_name), BAUD,
        timeout=READ_TIMEOUT, write_timeout=READ_TIMEOUT,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE
    )
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    return ser

def write_cmd(ser, cmd: str):
    # BDP requires CR line ending per spec
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
        if resp:
            return resp
        time.sleep(0.1)
    return ""

def parse_hex_word(raw: str, prefix: str) -> int | None:
    m = re.match(rf"^{re.escape(prefix)}([0-9A-Fa-f]{{4}})$", raw or "")
    return int(m.group(1), 16) if m else None

# ---- Streamlit state ----
if "ser" not in st.session_state:
    st.session_state.ser = None
if "log" not in st.session_state:
    st.session_state.log = []

def log(msg: str):
    st.session_state.log.append(msg)
    # keep last ~400 lines
    if len(st.session_state.log) > 400:
        st.session_state.log = st.session_state.log[-400:]

st.set_page_config(page_title="Willow BDP Controller", layout="wide")
st.title("Willow (FW300) – BDP UART Controller")

with st.sidebar:
    st.header("Connection")
    ports = list_serial_ports()
    friendly = [f"{p.device} — {p.description}" for p in ports]
    selected = st.selectbox("Serial Port", friendly, index=0 if friendly else None)
    port_name = ports[friendly.index(selected)].device if friendly else None

    colA, colB = st.columns(2)
    with colA:
        if st.button("Refresh"):
            st.rerun()
    with colB:
        if st.session_state.ser is None:
            connect_clicked = st.button("Connect", type="primary", use_container_width=True)
            if connect_clicked:
                try:
                    if not port_name:
                        st.error("No ports found.")
                    else:
                        st.session_state.ser = open_serial(port_name)
                        st.success(f"Connected to {port_name} @ {BAUD} 8N1")
                        log(f"[INFO] Connected to {port_name}")
                except Exception as e:
                    st.error(f"Open failed: {e}")
        else:
            if st.button("Disconnect", use_container_width=True):
                try:
                    st.session_state.ser.close()
                except Exception:
                    pass
                st.session_state.ser = None
                st.warning("Disconnected.")
                log("[INFO] Disconnected")

    st.divider()
    st.caption("Per spec: 115200 8N1; commands end with CR (\\r). Try `?WZ` for a quick smoke test.")

if st.session_state.ser is None:
    st.info("Connect to a serial port to begin.")
    st.stop()

# ----- Quick Actions Row -----
cols = st.columns(5)
with cols[0]:
    if st.button("Get Version  (?WZ)"):
        resp = xfer(st.session_state.ser, "?WZ", wait=0.2)  # software version
        log(f"> ?WZ\n{resp}")
with cols[1]:
    if st.button("Serial Number"):
        raw = xfer(st.session_state.ser, "?WX", wait=0.2)
        sn = None
        if raw:
            m = WX_SERIAL_RE.search(raw)
            if m:
                sn = m.group(1)
                log(f"> ?WX\n{raw}\n[Serial]: {sn}")
        if not sn:
            log(f"> ?WX\n{raw}\n[Serial]: not found in WX; trying WWx")
            for child in "0123":
                ww = xfer(st.session_state.ser, f"?WW{child}", wait=0.2)
                if ww.startswith("$WW"):
                    log(f"> ?WW{child}\n{ww}\n[PCBA Serial (last 5)]: {ww[-5:]}")
                    break
with cols[2]:
    if st.button("Fan ON (*KF1)"):
        xfer(st.session_state.ser, "*KF1")
        log("> *KF1 (fan ON)")
with cols[3]:
    if st.button("Fan OFF (*KF0)"):
        xfer(st.session_state.ser, "*KF0")
        log("> *KF0 (fan OFF)")
with cols[4]:
    if st.button("Fan current (?KF)"):
        raw = xfer(st.session_state.ser, "?KF", wait=0.2)   # $KF<hex mA>
        mA = parse_hex_word(raw, "$KF")
        log(f"> ?KF\n{raw}\n[parsed mA]: {mA}")

st.divider()

# ----- LEDs -----
st.subheader("LED Control (*LG / ?LG)")
c1, c2, c3 = st.columns(3)
with c1:
    led_opt = st.selectbox("LED Type", options=list(LED_TYPES.keys()),
                           format_func=lambda k: f"{k} – {LED_TYPES[k]}")
with c2:
    led_state = st.selectbox("State", options=list(LED_STATES.keys()),
                             format_func=lambda k: f"{k} – {LED_STATES[k]}")
with c3:
    if st.button("Set LEDs"):
        cmd = f"*LG{led_opt}{led_state}"
        xfer(st.session_state.ser, cmd)
        log(f"> {cmd} (LED set: {LED_TYPES[led_opt]} -> {LED_STATES[led_state]})")
if st.button("Read LED current (?LG)"):
    raw = xfer(st.session_state.ser, "?LG", wait=0.2)  # $LG<current mA>
    log(f"> ?LG\n{raw}")

st.divider()

# ----- Peltiers & Cooling Plate -----
st.subheader("Peltiers / Cooling Plate (*KP / ?KP / *KV / ?KV)")
pc1, pc2, pc3, pc4 = st.columns(4)
with pc1:
    idx = st.selectbox("Peltier index", options=["0", "1"])
with pc2:
    if st.button("Peltier ON"):
        xfer(st.session_state.ser, f"*KP{idx}1")
        log(f"> *KP{idx}1 (Peltier {idx} ON)")
with pc3:
    if st.button("Peltier OFF"):
        xfer(st.session_state.ser, f"*KP{idx}0")
        log(f"> *KP{idx}0 (Peltier {idx} OFF)")
with pc4:
    if st.button("Read Peltier current (?KP)"):
        raw = xfer(st.session_state.ser, "?KP", wait=0.2)  # $KP<hex mA>
        mA = parse_hex_word(raw, "$KP")
        log(f"> ?KP\n{raw}\n[parsed mA]: {mA}")

kv1, kv2 = st.columns(2)
with kv1:
    mv = st.number_input("Cooling plate target (mV, 0..5500)", min_value=0, max_value=5500, step=50, value=2000)
    if st.button("Set Cooling Plate (*KVxxxx)"):
        cmd = f"*KV{mv:04X}"
        xfer(st.session_state.ser, cmd)
        log(f"> {cmd} (Set cooling plate to {mv} mV)")
with kv2:
    if st.button("Read Cooling Plate (?KV)"):
        raw = xfer(st.session_state.ser, "?KV", wait=0.2)  # $KV<hex mV> (some docs show $KP in example)
        mv_parsed = parse_hex_word(raw, "$KV") or parse_hex_word(raw, "$KP")
        log(f"> ?KV\n{raw}\n[parsed mV]: {mv_parsed}")

st.divider()

# ----- Sensors, Keys, Battery, Faults -----
st.subheader("Sensors / Inputs")
s1, s2, s3, s4 = st.columns(4)
with s1:
    opt = st.selectbox("NTC option", options=list(NTC_OPTIONS.keys()),
                       format_func=lambda k: f"{k} – {NTC_OPTIONS[k]}")
with s2:
    if st.button("Read NTC (?KNx)"):
        raw = xfer(st.session_state.ser, f"?KN{opt}", wait=0.2)
        # try parse signed 16-bit (°C*10) from last 4 hex chars
        temp_c = None
        m = re.match(r"^\$KN[0-9A-Fa-f]([0-9A-Fa-f]{4})$", raw or "")
        if m:
            v = int(m.group(1), 16)
            if v >= 0x8000: v -= 0x10000
            temp_c = v / 10.0
        log(f"> ?KN{opt}\n{raw}\n[parsed °C]: {temp_c}")
with s3:
    if st.button("Cap sensor (?KC)"):
        raw = xfer(st.session_state.ser, "?KC", wait=0.2)  # $KC0/1
        log(f"> ?KC\n{raw}")
with s4:
    if st.button("Key status (?UI)"):
        raw = xfer(st.session_state.ser, "?UI", wait=0.2)  # $UI...
        log(f"> ?UI\n{raw}")

st.subheader("Battery / Faults")
b1, b2, b3, b4 = st.columns(4)
with b1:
    if st.button("Battery Info (?BA)"):
        raw = xfer(st.session_state.ser, "?BA", wait=0.2)
        log(f"> ?BA\n{raw}")
with b2:
    if st.button("Present State (?BP)"):
        raw = xfer(st.session_state.ser, "?BP", wait=0.2)
        log(f"> ?BP\n{raw}")
with b3:
    if st.button("Charging Info (?BC)"):
        raw = xfer(st.session_state.ser, "?BC", wait=0.2)
        log(f"> ?BC\n{raw}")
with b4:
    if st.button("Fault Flags (?BF)"):
        raw = xfer(st.session_state.ser, "?BF", wait=0.2)
        log(f"> ?BF\n{raw}")

st.divider()

# ----- Cooling Test -----
st.subheader("Cooling Test (*CT / ?CT)")
ct1, ct2, ct3 = st.columns(3)
with ct1:
    target_c = st.number_input("Target °C", value=16.0, step=0.5)
with ct2:
    if st.button("Start Cooling Test"):
        val = int(round(target_c * 10))
        raw = xfer(st.session_state.ser, f"*CT1{val:04X}", wait=0.2)
        log(f"> *CT1{val:04X}\n{raw}")
with ct3:
    if st.button("Read Cooling Test (?CT)"):
        raw = xfer(st.session_state.ser, "?CT", wait=0.2)
        log(f"> ?CT\n{raw}")

st.divider()

# ----- Raw command box -----
st.subheader("Raw Command")
raw_cmd = st.text_input("Enter a BDP command (without \\r), e.g., ?WZ or *KF1")
if st.button("Send"):
    resp = xfer(st.session_state.ser, raw_cmd, wait=0.2)
    log(f"> {raw_cmd}\n{resp}")

# ----- Log viewer -----
st.subheader("Log")
st.text_area("Console", value="\n".join(st.session_state.log), height=320)

