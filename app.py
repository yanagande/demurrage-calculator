import io
import streamlit as st
from datetime import datetime, timedelta
import pandas as pd
import re
import easyocr
from pdf2image import convert_from_bytes
import numpy as np
import platform
import shutil

# -----------------------------
# CONFIG: POPPLER PATH
# -----------------------------
if platform.system() == "Windows":
    POPPLER_PATH = r"C:\poppler\Library\bin"
    st.info("Using Windows local Poppler.")
else:
    # On Linux / Streamlit Cloud
    pdfinfo_path = shutil.which("pdfinfo")
    pdftoppm_path = shutil.which("pdftoppm")
    if pdfinfo_path and pdftoppm_path:
        POPPLER_PATH = None  # pdf2image uses system binaries if poppler_path=None
        st.info("Poppler detected on system (Streamlit Cloud/Linux).")
    else:
        POPPLER_PATH = None
        st.error("Poppler not found! PDF conversion will fail. Please install poppler-utils.")

# -----------------------------
# CACHED OCR READER
# -----------------------------
@st.cache_resource
def load_reader():
    return easyocr.Reader(['en'], gpu=False)

reader = load_reader()

# -----------------------------
# OCR CLEANING FUNCTION
# -----------------------------
def clean_ocr(text):
    text = text.upper()
    replacements = {
        "FA5T": "FAST",
        "FA$T": "FAST",
        "L0ADING": "LOADING",
        "DISC0NNECTED": "DISCONNECTED",
        "IINE": "LINE",
        "AL1": "ALL",
        "H0SE": "HOSE",
        "0FF": "OFF",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    text = re.sub(r'\s+', ' ', text)
    return text

# -----------------------------
# OCR PDF FUNCTION
# -----------------------------
def ocr_pdf(file):
    file.seek(0)
    try:
        images = convert_from_bytes(file.read(), poppler_path=POPPLER_PATH)
    except Exception as e:
        st.error(f"Failed to convert PDF pages: {e}")
        return ""
    all_text = []
    for img in images:
        img_np = np.array(img)
        result = reader.readtext(img_np, detail=0)
        all_text.extend(result)
    text = " ".join(all_text)
    return clean_ocr(text)

# -----------------------------
# SPLIT EVENTS FUNCTION
# -----------------------------
def split_into_events(text):
    pattern = re.compile(
        r'([A-Z\s\-\(\)/]+?)\s+(\d{4}/\d{1,2}/\d{1,2})\s+((?:\d{1,2}[:.]\d{2})|(?:\d{3,4}(?:[-–]\d{3,4})?))'
    )
    events = []
    for m in pattern.finditer(text):
        event_text = m.group(1).strip()
        date_str = m.group(2)
        time_raw = m.group(3)
        if ":" in time_raw or "." in time_raw:
            time_str = time_raw.replace(".", ":")
        else:
            if '-' in time_raw:
                time_raw = time_raw.split('-')[0]
            if len(time_raw) == 4:
                time_str = f"{time_raw[:2]}:{time_raw[2:]}"
            elif len(time_raw) == 3:
                time_str = f"{time_raw[0]}:{time_raw[1:]}"
            else:
                continue
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y/%m/%d %H:%M")
            events.append((event_text, dt))
        except:
            continue
    return events

# -----------------------------
# EXTRACT EVENTS
# -----------------------------
def extract_events(text):
    raw_rows = split_into_events(text)
    rows = [{"event": r[0], "dt": r[1]} for r in raw_rows]
    found = {}
    keywords = {
        "nor_tendered": ["NOR TENDERED", "NOTICE OF READINESS", "NOTICE OF READINESS TENDERED", "NOR PRESENTED"],
        "all_fast": ["ALL FAST", "FINISHED MOORING", "ALL LINES FAST", "ALL LINES MADE FAST", "VESSEL BERTHED", "ALL LINE MADE FAST"],
        "hoses_off": ["HOSE OFF", "ARM DISCONNECTED", "HOSES DISCONNECTED", "DISCONNECTED HOSE", "DISCONNECTED ARM","ARM OFF", "HOSE DISCONNECTED"],
        "shift_start": ["ANCHOR AWEIGH", "PILOT ON BOARD", "POB", "COMMENCED SHIFTING"],
    }
    for item in rows:
        e = item["event"].upper()
        for key, kws in keywords.items():
            if key not in found:
                for kw in kws:
                    if kw in e:
                        found[key] = item["dt"]
                        break
    all_fast_events = [r["dt"] for r in rows if any(kw in r["event"].upper() for kw in keywords["all_fast"])]
    if len(all_fast_events) > 0:
        found["shift_ended"] = all_fast_events[0]
        if len(all_fast_events) > 1:
            found["all_fast_second"] = all_fast_events[1]
    return found, rows

# -----------------------------
# LAYTIME CALCULATION
# -----------------------------
def calculate_laytime_asbatankvoy(events, allowed_hours, rate_per_day, manual_weather_hrs=0.0):
    nor = events.get("nor_tendered")
    shift_ended = events.get("shift_ended")
    hoses_off = events.get("hoses_off")
    if not (nor and shift_ended and hoses_off):
        return None
    nor_plus_6 = nor + timedelta(hours=6)
    laytime_start = min(nor_plus_6, shift_ended)
    shift_hrs = 0.0
    if "shift_start" in events and "all_fast_second" in events:
        shift_hrs = (events["all_fast_second"] - events["shift_start"]).total_seconds() / 3600
    total_duration_hrs = (hoses_off - laytime_start).total_seconds() / 3600
    net_used_hrs = max(total_duration_hrs - shift_hrs, 0)
    excess_total_hrs = max(net_used_hrs - allowed_hours, 0)
    half_rate_hrs = min(manual_weather_hrs, excess_total_hrs)
    full_rate_hrs = max(excess_total_hrs - half_rate_hrs, 0)
    hourly_full = rate_per_day / 24.0
    hourly_half = hourly_full / 2.0
    total_demurrage = (full_rate_hrs * hourly_full) + (half_rate_hrs * hourly_half)
    return {
        "Laytime Start": laytime_start,
        "Hoses Disconnected": hoses_off,
        "Shifting Deducted": round(shift_hrs, 2),
        "Net Used": round(net_used_hrs, 2),
        "Total Excess": round(excess_total_hrs, 2),
        "Full Rate Hours": round(full_rate_hrs, 2),
        "Half Rate Hours (Weather)": round(half_rate_hrs, 2),
        "Demurrage USD": round(total_demurrage, 2)
    }

# -----------------------------
# STREAMLIT UI
# -----------------------------
PRIMARY_COLOR = "#00A79D"
ACCENT_COLOR = "#005f5f"

st.markdown(f"""
    <h1 style='text-align: center; color: {PRIMARY_COLOR};'>⚓ Demurrage Calculator</h1>
    <p style='text-align: center; color: {ACCENT_COLOR}; font-size:16px;'>ASBATANKVOY Laytime & Demurrage</p>
""", unsafe_allow_html=True)

with st.expander("📄 Upload SOF PDFs"):
    load_pdf = st.file_uploader("Upload LOADING SOF PDF", type="pdf")
    dis_pdf = st.file_uploader("Upload DISCHARGING SOF PDF", type="pdf")

st.sidebar.header("⚓ Parameters & Adjustments")
rate = st.sidebar.number_input("Demurrage Rate per day (USD)", value=10000.0)
allowed_load = st.sidebar.number_input("Allowed Laytime Loading (hrs)", value=32.5)
allowed_dis = st.sidebar.number_input("Allowed Laytime Discharging (hrs)", value=32.5)
weather_load = st.sidebar.number_input("Loading: Weather/Storm Duration (hrs)", min_value=0.0, step=0.5)
weather_dis = st.sidebar.number_input("Discharging: Weather/Storm Duration (hrs)", min_value=0.0, step=0.5)

# -----------------------------
# PROCESS PDF FUNCTION
# -----------------------------
def process_pdf(file, label):
    text = ocr_pdf(file)
    st.subheader(f"{label} OCR Text")
    st.text_area("OCR Output", text, height=200)
    events, rows = extract_events(text)
    st.subheader(f"{label} Parsed Events")
    st.write([{"Event": r["event"], "Datetime": r["dt"].strftime("%Y-%m-%d %H:%M")} for r in rows])
    st.write("Detected Events:", {k: v.strftime("%Y-%m-%d %H:%M") for k, v in events.items()})
    return events

# -----------------------------
# RUN LOADING & DISCHARGING
# -----------------------------
load_result = None
if load_pdf:
    load_events = process_pdf(load_pdf, "Loading")
    required_keys = {"nor_tendered", "shift_ended", "hoses_off"}
    if required_keys <= load_events.keys():
        load_result = calculate_laytime_asbatankvoy(load_events, allowed_load, rate, manual_weather_hrs=weather_load)
    else:
        st.warning("Missing Loading Events")

dis_result = None
if dis_pdf:
    dis_events = process_pdf(dis_pdf, "Discharging")
    required_keys = {"nor_tendered", "shift_ended", "hoses_off"}
    if required_keys <= dis_events.keys():
        dis_result = calculate_laytime_asbatankvoy(dis_events, allowed_dis, rate, manual_weather_hrs=weather_dis)
    else:
        st.warning("Missing Discharging Events")

# -----------------------------
# RESULTS TABLE & DOWNLOAD
# -----------------------------
results = []
if load_result:
    results.append([
        "Loading Port",
        allowed_load,
        load_result["Net Used"],
        load_result["Total Excess"],
        load_result["Demurrage USD"]
    ])
if dis_result:
    results.append([
        "Discharging Port",
        allowed_dis,
        dis_result["Net Used"],
        dis_result["Total Excess"],
        dis_result["Demurrage USD"]
    ])

if results:
    df = pd.DataFrame(
        results,
        columns=["Port", "Laytime Allowed", "Laytime Used", "Excess Hours", "Demurrage (USD)"]
    )
    total_excess = df["Excess Hours"].sum()
    total_dem = df["Demurrage (USD)"].sum()
    df.loc["TOTAL"] = ["-", "-", "-", total_excess, total_dem]

    st.subheader("📊 Demurrage Summary")
    st.dataframe(
        df.style
          .format({"Demurrage (USD)": "${:,.2f}"} )
          .set_table_styles([{
              "selector": "th",
              "props": [("background-color", "#00bfa5"), ("color", "white"), ("font-weight", "bold")]
          }])
    )

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Demurrage', index=True)
    output.seek(0)

    st.download_button(
        label="📥 Download Excel",
        data=output,
        file_name="demurrage_summary.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
