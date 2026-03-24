"""
External API for hospital system (HIS) integration.
Authentication: API Key via X-API-Key header.
"""

import uuid
from datetime import datetime, date
from functools import wraps

from flask import Blueprint, request, jsonify

from models import db, WorklistItem, Patient, get_setting, SystemSetting
from services.dicom_helpers import stable_uid_from_text

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


# ---------------------------------------------------------------------------
# Auth decorator
# ---------------------------------------------------------------------------

def api_key_required(f):
    """Validate X-API-Key header against stored API key."""
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key", "")
        stored_key = get_setting("api_key", "")
        if not key or not stored_key or key != stored_key:
            return jsonify({"success": False, "error": "Invalid or missing API key"}), 401
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Helpers (reused from ecg_tests.py)
# ---------------------------------------------------------------------------

def _generate_accession():
    """Auto-generate accession number: ECG-YYYYMMDD-NNNN."""
    today = date.today().strftime("%Y%m%d")
    prefix = f"ECG-{today}-"

    last = (
        WorklistItem.query
        .filter(WorklistItem.accession_number.like(f"{prefix}%"))
        .order_by(WorklistItem.id.desc())
        .first()
    )

    if last:
        try:
            num = int(last.accession_number.split("-")[-1]) + 1
        except ValueError:
            num = 1
    else:
        num = 1

    return f"{prefix}{num:04d}"


# ---------------------------------------------------------------------------
# POST /api/v1/create-test
# ---------------------------------------------------------------------------

@api_bp.route("/create-test", methods=["POST"])
@api_key_required
def create_test():
    """Create a new ECG test from external system (HIS)."""

    # Accept JSON body
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "error": "Request body must be JSON"}), 400

    # --- Validate required fields ---
    hn = (data.get("patient_id") or "").strip()
    if not hn:
        return jsonify({"success": False, "error": "patient_id is required"}), 400

    patient_name = (data.get("patient_name") or "").strip()
    if not patient_name:
        return jsonify({"success": False, "error": "patient_name is required"}), 400

    # --- Check duplicate accession ---
    accession = (data.get("accession_number") or "").strip()
    if accession:
        existing = WorklistItem.query.filter_by(accession_number=accession).first()
        if existing:
            return jsonify({
                "success": False,
                "error": f"Accession number {accession} already exists",
            }), 409

    # --- Patient: find or create ---
    patient = Patient.query.filter_by(patient_id=hn).first()
    sex = (data.get("sex") or "").strip()
    birth_date = (data.get("birth_date") or "").replace("-", "").strip()

    if not patient:
        patient = Patient(
            patient_id=hn,
            patient_name=patient_name,
            sex=sex,
            birth_date=birth_date,
        )
        db.session.add(patient)
        db.session.flush()
    else:
        # Update patient info if provided
        if patient_name:
            patient.patient_name = patient_name
        if sex:
            patient.sex = sex
        if birth_date:
            patient.birth_date = birth_date

    # --- Auto-generate accession if not provided ---
    if not accession:
        accession = _generate_accession()

    # --- Scheduled date/time ---
    sched_date = (data.get("scheduled_date") or "").replace("-", "").strip()
    if not sched_date:
        sched_date = date.today().strftime("%Y%m%d")

    sched_time = (data.get("scheduled_time") or "").replace(":", "").strip()
    if not sched_time:
        sched_time = datetime.now().strftime("%H%M%S")
    elif len(sched_time) == 4:
        sched_time += "00"  # HHMM → HHMMSS

    # --- Create WorklistItem ---
    item = WorklistItem(
        patient_id=patient.id,
        accession_number=accession,
        requested_procedure_id=(data.get("procedure_id") or "RP-ECG").strip(),
        requested_procedure_desc=(data.get("procedure_desc") or "Standard 12-lead ECG").strip(),
        requested_procedure_priority=(data.get("priority") or "ROUTINE").strip().upper(),
        scheduled_station_ae=(data.get("station_ae") or "CP150").strip(),
        scheduled_station_name=(data.get("station_name") or "ECG-ROOM1").strip(),
        modality="ECG",
        sps_id=f"SPS-{accession}",
        sps_desc=(data.get("procedure_desc") or "Standard 12-lead ECG").strip(),
        scheduled_date=sched_date,
        scheduled_time=sched_time,
        study_instance_uid=stable_uid_from_text(accession),
        status="SCHEDULED",
        ordering_department=(data.get("ordering_department") or "").strip(),
        ordering_physician=(data.get("ordering_physician") or "").strip(),
        performing_physician=(data.get("performing_physician") or "").strip(),
        patient_source=(data.get("patient_source") or "Outpatient").strip(),
        bed_number=(data.get("bed_number") or "").strip(),
        phone=(data.get("phone") or "").strip(),
        clinical_info=(data.get("clinical_info") or "").strip(),
        source="API",
    )
    db.session.add(item)
    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Test created successfully",
        "data": {
            "id": item.id,
            "accession_number": accession,
            "patient_id": hn,
            "patient_name": patient_name,
            "status": "SCHEDULED",
            "scheduled_date": sched_date,
            "scheduled_time": sched_time,
            "study_instance_uid": item.study_instance_uid,
        },
    }), 201
