"""
ECG PDF Report Generator — A4 Landscape ECG printout.
Generates a standard 3x4 + rhythm strip ECG report from parsed DICOM data.
"""
from __future__ import annotations

from datetime import date
from io import BytesIO
from typing import List, Optional

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

# ===== Page Layout =====
PAGE_W, PAGE_H = landscape(A4)  # 297mm x 210mm in points
MARGIN_LEFT = 10 * mm
MARGIN_RIGHT = 10 * mm
MARGIN_TOP = 8 * mm
MARGIN_BOTTOM = 8 * mm

HEADER_HEIGHT = 36 * mm
FOOTER_HEIGHT = 8 * mm

# ===== ECG Paper Constants =====
SPEED = 25          # mm/s
GAIN = 10           # mm/mV
PX_PER_SEC = SPEED * mm
PX_PER_MV = GAIN * mm

SMALL_BOX = 1 * mm   # 1mm minor grid
BIG_BOX = 5 * mm     # 5mm major grid

# ECG grid: exactly 250mm wide (10s at 25mm/s)
ECG_GRID_WIDTH = 250 * mm
COL_WIDTH = 62.5 * mm   # 2.5s per column
COL_DURATION = 2.5       # seconds

# Colors (match existing JS renderers)
COLOR_BG = HexColor('#FFF5F5')
COLOR_GRID_MINOR = HexColor('#F0C8C8')
COLOR_GRID_MAJOR = HexColor('#D4A0A0')
COLOR_WAVEFORM = HexColor('#1D1D1F')
COLOR_LABEL = HexColor('#555555')
COLOR_SEPARATOR = HexColor('#A08080')
COLOR_HEADER_TEXT = HexColor('#333333')
COLOR_HEADER_LABEL = HexColor('#666666')
COLOR_RED = HexColor('#CC0000')

# 3x4 lead layout
LEAD_GRID_3x4 = [
    ['I', 'aVR', 'V1', 'V4'],
    ['II', 'aVL', 'V2', 'V5'],
    ['III', 'aVF', 'V3', 'V6'],
]
RHYTHM_LEAD = 'II'

# Measurement concept name mapping
CONCEPT_MAP = {
    'Heart Rate': 'hr',
    'Ventricular Heart Rate': 'hr',
    'HR': 'hr',                         # Lepu Medical uses abbreviated name
    'PR Interval': 'pr',
    'QRS Duration': 'qrs',
    'QT Interval': 'qt',
    'QTc Interval': 'qtc',
    'P Axis': 'p_axis',
    'QRS Axis': 'qrs_axis',
    'T Axis': 't_axis',
    'RR Interval': 'rr',
}


def generate_ecg_pdf(ecg_data, db_result=None) -> BytesIO:
    """Generate an A4 landscape ECG PDF report.

    Args:
        ecg_data: ECGData from parse_dicom_ecg()
        db_result: Optional ECGResult for diagnosis info

    Returns:
        BytesIO buffer containing the PDF
    """
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(A4))
    c.setTitle("ECG Report")

    # Prepare data
    patient = _format_patient_info(ecg_data, db_result)
    meas = _extract_measurements(ecg_data)
    diag = {}
    if db_result:
        diag = {
            'diagnosis': getattr(db_result, 'diagnosis', '') or '',
            'diagnosed_by': getattr(db_result, 'diagnosed_by', '') or '',
            'diagnosed_at': getattr(db_result, 'diagnosed_at', None),
            'status': getattr(db_result, 'status', 'RECEIVED'),
        }

    # If doctor has submitted diagnosis, use it as interpretation (replacing device's).
    # Otherwise use device interpretation as-is.
    if diag.get('diagnosis'):
        interp = [diag['diagnosis']]
    else:
        interp = ecg_data.interpretation_texts or []

    # Calculate grid area
    available_w = PAGE_W - MARGIN_LEFT - MARGIN_RIGHT
    grid_x = MARGIN_LEFT + (available_w - ECG_GRID_WIDTH) / 2
    grid_y = MARGIN_BOTTOM + FOOTER_HEIGHT
    grid_h = PAGE_H - MARGIN_TOP - MARGIN_BOTTOM - HEADER_HEIGHT - FOOTER_HEIGHT

    # 1. Draw ECG grid (background + lines)
    _draw_ecg_grid(c, grid_x, grid_y, ECG_GRID_WIDTH, grid_h)

    # 2. Draw 3x4 waveform layout
    _draw_3x4_layout(c, ecg_data, grid_x, grid_y, ECG_GRID_WIDTH, grid_h)

    # 3. Draw header above grid
    header_y = grid_y + grid_h + 2 * mm
    header_w = PAGE_W - MARGIN_LEFT - MARGIN_RIGHT
    _draw_header(c, MARGIN_LEFT, header_y, header_w, patient, meas, interp, diag)

    # 4. Draw footer below grid
    _draw_footer(c, MARGIN_LEFT, MARGIN_BOTTOM, header_w, ecg_data)

    c.save()
    buf.seek(0)
    return buf


# ===== Data Extraction =====

def _extract_measurements(ecg_data) -> dict:
    """Extract measurement values from annotations."""
    meas = {
        'hr': '', 'pr': '', 'qrs': '', 'qt': '', 'qtc': '',
        'p_axis': '', 'qrs_axis': '', 't_axis': '',
        'rr': '', 'rv5': '', 'sv1': '', 'rv5_sv1': '',
        'qtc_method': '',
    }

    for ann in (ecg_data.annotations or []):
        concept = ann.get('concept', '')
        value = ann.get('value', '')

        if concept in CONCEPT_MAP and value:
            meas[CONCEPT_MAP[concept]] = value

        # RV5 / SV1 amplitudes (concept names may vary)
        clower = concept.lower()
        if 'rv5' in clower and value:
            meas['rv5'] = value
        elif 'sv1' in clower and value:
            meas['sv1'] = value

    # QTc method from interpretation text
    for text in (ecg_data.interpretation_texts or []):
        tl = text.lower()
        for method in ['Bazett', 'Fridericia', 'Hodges', 'Framingham']:
            if method.lower() in tl:
                meas['qtc_method'] = method
                break

    # Compute RV5+SV1
    if meas['rv5'] and meas['sv1']:
        try:
            meas['rv5_sv1'] = str(round(float(meas['rv5']) + float(meas['sv1']), 3))
        except (ValueError, TypeError):
            pass

    return meas


def _format_patient_info(ecg_data, db_result=None) -> dict:
    """Format patient demographics for display."""
    name = ecg_data.patient_name.replace('^', ' ').strip()
    sex_map = {'M': 'Male', 'F': 'Female', 'O': 'Other'}
    sex = sex_map.get(ecg_data.patient_sex, ecg_data.patient_sex or '')

    dob = ecg_data.patient_birth_date or ''
    if len(dob) == 8:
        dob = f"{dob[6:8]}/{dob[4:6]}/{dob[:4]}"

    age = ecg_data.patient_age or ''
    if not age and ecg_data.patient_birth_date and len(ecg_data.patient_birth_date) == 8:
        try:
            bd = date(
                int(ecg_data.patient_birth_date[:4]),
                int(ecg_data.patient_birth_date[4:6]),
                int(ecg_data.patient_birth_date[6:8]),
            )
            age = str((date.today() - bd).days // 365)
        except (ValueError, TypeError):
            pass

    # Acquisition time — prefer AcquisitionDateTime over StudyDate/StudyTime
    acq_dt = (ecg_data.acquisition_datetime or '').split('.')[0]
    if len(acq_dt) >= 14:
        acq_date = acq_dt[:8]
        acq_time_raw = acq_dt[8:14]
    else:
        acq_date = ecg_data.study_date or ''
        acq_time_raw = (ecg_data.study_time or '').split('.')[0]
    acq_str = ''
    if len(acq_date) == 8:
        acq_str = f"{acq_date[6:8]}-{acq_date[4:6]}-{acq_date[:4]}"
    if len(acq_time_raw) >= 6:
        acq_str += f" {acq_time_raw[:2]}:{acq_time_raw[2:4]}:{acq_time_raw[4:6]}"
    elif len(acq_time_raw) >= 4:
        acq_str += f" {acq_time_raw[:2]}:{acq_time_raw[2:4]}"

    patient_id = ecg_data.patient_id or ''
    if db_result and hasattr(db_result, 'patient') and db_result.patient:
        patient_id = db_result.patient.patient_id or patient_id

    return {
        'id': patient_id,
        'name': name,
        'sex': sex,
        'dob': dob,
        'age': age,
        'paced': 'Unspecified',
        'acquisition_time': acq_str,
    }


# ===== Drawing Functions =====

def _draw_ecg_grid(c, x, y, w, h):
    """Draw ECG paper: pink background + minor/major grid lines."""
    # Background
    c.setFillColor(COLOR_BG)
    c.rect(x, y, w, h, fill=1, stroke=0)

    # Minor grid (1mm)
    c.setStrokeColor(COLOR_GRID_MINOR)
    c.setLineWidth(0.25)
    p = c.beginPath()
    gx = x
    while gx <= x + w + 0.1:
        p.moveTo(gx, y)
        p.lineTo(gx, y + h)
        gx += SMALL_BOX
    gy = y
    while gy <= y + h + 0.1:
        p.moveTo(x, gy)
        p.lineTo(x + w, gy)
        gy += SMALL_BOX
    c.drawPath(p, stroke=1, fill=0)

    # Major grid (5mm)
    c.setStrokeColor(COLOR_GRID_MAJOR)
    c.setLineWidth(0.5)
    p = c.beginPath()
    gx = x
    while gx <= x + w + 0.1:
        p.moveTo(gx, y)
        p.lineTo(gx, y + h)
        gx += BIG_BOX
    gy = y
    while gy <= y + h + 0.1:
        p.moveTo(x, gy)
        p.lineTo(x + w, gy)
        gy += BIG_BOX
    c.drawPath(p, stroke=1, fill=0)


def _draw_waveform(c, data, fs, start_sample, end_sample, x0, baseline_y,
                   px_per_sec, px_per_mv, max_width=None):
    """Draw a single lead waveform segment."""
    if not data:
        return

    actual_start = max(0, int(start_sample))
    actual_end = min(len(data), int(end_sample))
    if actual_end <= actual_start:
        return

    c.setStrokeColor(COLOR_WAVEFORM)
    c.setLineWidth(0.6)
    c.setLineJoin(1)
    c.setLineCap(1)

    p = c.beginPath()
    first = True
    total = actual_end - actual_start
    step = max(1, total // 3000)

    for i in range(actual_start, actual_end, step):
        t = (i - start_sample) / fs
        px = x0 + t * px_per_sec
        if max_width and px > x0 + max_width:
            break
        # ReportLab: y up = positive mV up (natural mapping)
        py = baseline_y + data[i] * px_per_mv

        if first:
            p.moveTo(px, py)
            first = False
        else:
            p.lineTo(px, py)

    c.drawPath(p, stroke=1, fill=0)


def _draw_calibration_mark(c, x, baseline_y, px_per_mv):
    """Draw 1mV calibration pulse."""
    pulse_h = px_per_mv    # 1mV height
    pulse_w = BIG_BOX      # 5mm width

    c.setStrokeColor(COLOR_WAVEFORM)
    c.setLineWidth(0.8)
    p = c.beginPath()
    p.moveTo(x, baseline_y)
    p.lineTo(x, baseline_y + pulse_h)
    p.lineTo(x + pulse_w, baseline_y + pulse_h)
    p.lineTo(x + pulse_w, baseline_y)
    c.drawPath(p, stroke=1, fill=0)


def _draw_lead_label(c, name, x, y):
    """Draw lead name label."""
    c.setFillColor(COLOR_WAVEFORM)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x, y, name)


def _draw_3x4_layout(c, ecg_data, grid_x, grid_y, grid_w, grid_h):
    """Draw standard 3x4 + rhythm strip ECG layout."""
    if not ecg_data.waveforms:
        return

    wf = ecg_data.waveforms[0]
    fs = wf.sampling_frequency
    channels = {ch.name: ch.data for ch in wf.channels}

    # Calculate column duration dynamically from actual data
    duration = wf.duration_seconds if wf.duration_seconds else 10.0
    col_duration = duration / 4
    col_width = col_duration * PX_PER_SEC

    num_rows = 4  # 3 lead rows + rhythm strip
    row_height = grid_h / num_rows
    samples_per_col = int(col_duration * fs)

    # Row separators
    c.setStrokeColor(COLOR_SEPARATOR)
    c.setLineWidth(0.75)
    for r in range(num_rows + 1):
        ry = grid_y + r * row_height
        c.line(grid_x, ry, grid_x + grid_w, ry)

    # Column separators (only in the 3-lead rows, not rhythm strip)
    for col in range(5):  # 0,1,2,3,4 lines
        cx = grid_x + col * col_width
        c.line(cx, grid_y + row_height, cx, grid_y + grid_h)  # from top of rhythm strip to top

    # Calibration marks: draw OUTSIDE grid (left margin), matching web viewer
    cal_x = grid_x - BIG_BOX - 1 * mm

    # Draw 3x4 leads
    # ReportLab: y=0 at bottom. Row index 0 in layout = top of page
    # Row 0 of layout (I, aVR, V1, V4) should be at the TOP of the 3-lead section
    # The rhythm strip is at the BOTTOM row
    for row_idx in range(3):
        # rl_row: 0=bottom, so row_idx=0 (top) maps to rl_row = 3 (highest)
        rl_row = (num_rows - 1) - row_idx
        baseline_y = grid_y + rl_row * row_height + row_height / 2

        # Calibration mark for this row (outside grid, first column only)
        _draw_calibration_mark(c, cal_x, baseline_y, PX_PER_MV)

        for col_idx in range(4):
            lead_name = LEAD_GRID_3x4[row_idx][col_idx]
            data = channels.get(lead_name)

            x0 = grid_x + col_idx * col_width
            start_sample = col_idx * samples_per_col
            end_sample = start_sample + samples_per_col

            # Lead label (top-left of cell)
            label_y = grid_y + rl_row * row_height + row_height - 3 * mm
            _draw_lead_label(c, lead_name, x0 + 2 * mm, label_y)

            # Waveform — starts at column edge (no offset), matching web viewer
            if data:
                _draw_waveform(c, data, fs, start_sample, end_sample,
                               x0, baseline_y, PX_PER_SEC, PX_PER_MV,
                               max_width=col_width)

    # Rhythm strip: Lead II, full width, bottom row (rl_row = 0)
    rhythm_data = channels.get(RHYTHM_LEAD)
    rhythm_baseline = grid_y + row_height / 2
    label_y = grid_y + row_height - 3 * mm
    _draw_lead_label(c, RHYTHM_LEAD, grid_x + 2 * mm, label_y)
    _draw_calibration_mark(c, cal_x, rhythm_baseline, PX_PER_MV)

    if rhythm_data:
        _draw_waveform(c, rhythm_data, fs, 0, len(rhythm_data),
                       grid_x, rhythm_baseline, PX_PER_SEC, PX_PER_MV,
                       max_width=grid_w)


def _draw_header(c, x, y, w, patient, meas, interpretations, diag):
    """Draw 3-column header: patient info | measurements | interpretation."""
    col1_w = w * 0.28
    col2_w = w * 0.35
    col3_w = w * 0.37

    line_h = 4 * mm  # line spacing

    # === Column 1: Patient Info ===
    cx = x
    cy = y + HEADER_HEIGHT - 2 * mm

    # Acquisition time (top)
    c.setFont("Helvetica", 7)
    c.setFillColor(COLOR_HEADER_LABEL)
    c.drawString(cx, cy, "Acquisition Time:")
    c.setFont("Helvetica-Bold", 7)
    c.setFillColor(COLOR_HEADER_TEXT)
    c.drawString(cx + 28 * mm, cy, patient.get('acquisition_time', ''))
    cy -= line_h + 1 * mm

    fields = [
        ("ID:", patient.get('id', '')),
        ("Patient Name:", patient.get('name', '')),
        ("Gender:", patient.get('sex', '')),
        ("DOB:", patient.get('dob', '')),
        ("Age:", patient.get('age', '')),
        ("Paced:", patient.get('paced', 'Unspecified')),
    ]
    for label, val in fields:
        c.setFont("Helvetica", 7)
        c.setFillColor(COLOR_HEADER_LABEL)
        c.drawString(cx, cy, label)
        c.setFont("Helvetica-Bold", 7)
        c.setFillColor(COLOR_HEADER_TEXT)
        c.drawString(cx + 25 * mm, cy, str(val))
        cy -= line_h

    # === Column 2: Measurements ===
    cx = x + col1_w
    cy = y + HEADER_HEIGHT - 2 * mm

    hr = meas.get('hr', '---')
    pr = meas.get('pr', '---')
    qrs_val = meas.get('qrs', '---')
    qt = meas.get('qt', '---')
    qtc = meas.get('qtc', '---')
    p_ax = meas.get('p_axis', '---')
    qrs_ax = meas.get('qrs_axis', '---')
    t_ax = meas.get('t_axis', '---')
    rv5 = meas.get('rv5', '---')
    sv1 = meas.get('sv1', '---')
    rv5_sv1 = meas.get('rv5_sv1', '---')
    qtc_method = meas.get('qtc_method', '')

    # Format: ensure "---" for empty values
    def v(val):
        return val if val else '---'

    meas_lines = [
        ("Vent Rate", f"{v(hr)} bpm"),
        ("PR Interval", f"{v(pr)} ms"),
        ("QRS Duration", f"{v(qrs_val)} ms"),
        ("QT/QTc Interval", f"{v(qt)}/{v(qtc)} ms"),
        ("P/QRS/T Axes", f"{v(p_ax)}/{v(qrs_ax)}/{v(t_ax)} deg"),
        ("RV5/SV1", f"{v(rv5)}/{v(sv1)} mV"),
        ("RV5+SV1", f"{v(rv5_sv1)} mV"),
    ]
    if qtc_method:
        meas_lines.append(("QTc:", qtc_method))

    for label, val in meas_lines:
        c.setFont("Helvetica", 7)
        c.setFillColor(COLOR_HEADER_LABEL)
        c.drawString(cx, cy, label)
        c.setFont("Helvetica-Bold", 7)
        c.setFillColor(COLOR_HEADER_TEXT)
        c.drawString(cx + 28 * mm, cy, val)
        cy -= line_h

    # === Column 3: Interpretation & Diagnosis ===
    cx = x + col1_w + col2_w
    cy = y + HEADER_HEIGHT - 2 * mm
    max_cx = x + w

    # Interpretation texts (skip exact "Abnormal ECG" — rendered separately as red label)
    has_abnormal = False
    col3_max_w = col3_w - 4 * mm  # available width for text
    c.setFont("Helvetica", 7)
    c.setFillColor(COLOR_HEADER_TEXT)
    for text in interpretations:
        # Split on newlines (DICOM may embed \n in single text value)
        for line in text.split('\n'):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.lower() == 'abnormal ecg':
                has_abnormal = True
                continue  # will be shown as red label below
            # Wrap long lines to fit column width
            while stripped:
                fit = stripped
                while c.stringWidth(fit, "Helvetica", 7) > col3_max_w and len(fit) > 10:
                    fit = fit[:len(fit) - 1]
                if len(fit) < len(stripped):
                    # Break at last space if possible
                    sp = fit.rfind(' ')
                    if sp > 5:
                        fit = fit[:sp]
                    c.drawString(cx, cy, fit)
                    cy -= line_h
                    stripped = stripped[len(fit):].strip()
                else:
                    c.drawString(cx, cy, fit)
                    cy -= line_h
                    break

    # Spacer
    if interpretations:
        cy -= 1 * mm

    # Abnormal ECG red label
    if has_abnormal:
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(COLOR_RED)
        c.drawString(cx, cy, "Abnormal ECG")
        cy -= line_h + 1 * mm

    # Diagnosis/confirmation status and physician info
    status = diag.get('status', 'RECEIVED')
    if diag.get('diagnosis'):
        # Doctor has submitted diagnosis — it's already shown as interpretation above,
        # so just show confirmation status and physician info
        if status == 'APPROVED':
            c.setFont("Helvetica-Bold", 7)
            c.setFillColor(HexColor('#007700'))
            c.drawString(cx, cy, "Confirmed Diagnosis")
        else:
            c.setFont("Helvetica", 7)
            c.setFillColor(COLOR_HEADER_LABEL)
            c.drawString(cx, cy, "Draft Diagnosis")
        cy -= line_h

        if diag.get('diagnosed_by'):
            c.setFont("Helvetica", 6)
            c.setFillColor(COLOR_HEADER_LABEL)
            by_text = f"By: {diag['diagnosed_by']}"
            if diag.get('diagnosed_at'):
                by_text += f" | {diag['diagnosed_at'].strftime('%d/%m/%Y %H:%M')}"
            c.drawString(cx, cy, by_text)
    else:
        # No doctor diagnosis yet
        c.setFont("Helvetica", 7)
        c.setFillColor(COLOR_HEADER_LABEL)
        c.drawString(cx, cy, "Unconfirmed Diagnosis")

    # Vertical separator lines between columns
    c.setStrokeColor(HexColor('#CCCCCC'))
    c.setLineWidth(0.5)
    c.line(x + col1_w - 2 * mm, y, x + col1_w - 2 * mm, y + HEADER_HEIGHT)
    c.line(x + col1_w + col2_w - 2 * mm, y, x + col1_w + col2_w - 2 * mm, y + HEADER_HEIGHT)


def _draw_footer(c, x, y, w, ecg_data):
    """Draw footer with speed, gain, device info, timestamp."""
    c.setFont("Helvetica", 6)
    c.setFillColor(COLOR_HEADER_LABEL)

    parts = [f"{SPEED}mm/s", f"{GAIN}mm/mV"]

    if ecg_data.manufacturer:
        parts.append(ecg_data.manufacturer)
    if ecg_data.model_name:
        parts.append(ecg_data.model_name)
    if ecg_data.device_serial:
        parts.append(f"SN: {ecg_data.device_serial}")

    # Date/time — prefer AcquisitionDateTime over StudyDate/StudyTime
    acq_dt = (ecg_data.acquisition_datetime or '').split('.')[0]
    if len(acq_dt) >= 14:
        f_date = acq_dt[:8]
        f_time = acq_dt[8:14]
    else:
        f_date = ecg_data.study_date or ''
        f_time = (ecg_data.study_time or '').split('.')[0]
    if len(f_date) == 8:
        dt_str = f"{f_date[6:8]}/{f_date[4:6]}/{f_date[:4]}"
        if len(f_time) >= 6:
            dt_str += f" {f_time[:2]}:{f_time[2:4]}:{f_time[4:6]}"
        parts.append(dt_str)

    parts.append("ECG Report")

    footer_text = "    ".join(parts)
    c.drawString(x, y + 1 * mm, footer_text)

    # Right-aligned: page info
    c.drawRightString(x + w, y + 1 * mm, "1/1")
