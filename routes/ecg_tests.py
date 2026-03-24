from datetime import datetime, date

from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_required

from models import db, WorklistItem, Patient, ECGResult, User
from services.dicom_helpers import stable_uid_from_text

ecg_tests_bp = Blueprint("ecg_tests", __name__, url_prefix="/ecg-tests")


@ecg_tests_bp.route("/")
@login_required
def index():
    """Main ECG Test Management page."""
    today_str = date.today().strftime("%Y%m%d")

    stats = {
        "awaiting": WorklistItem.query.filter_by(status="SCHEDULED").count(),
        "in_progress": WorklistItem.query.filter_by(status="IN_PROGRESS").count(),
        "completed": WorklistItem.query.filter_by(status="COMPLETED").count(),
        "total_today": WorklistItem.query.filter_by(scheduled_date=today_str).count(),
    }

    doctors = (
        User.query
        .filter_by(role="doctor", is_active_user=True)
        .order_by(User.display_name)
        .all()
    )

    return render_template("ecg_tests/index.html", stats=stats, doctors=doctors)


@ecg_tests_bp.route("/api/data")
@login_required
def api_data():
    """DataTables server-side JSON API with advanced filtering."""
    draw = request.args.get("draw", 1, type=int)
    start = request.args.get("start", 0, type=int)
    length = request.args.get("length", 25, type=int)
    search_value = request.args.get("search[value]", "").strip()

    # Custom filters
    status_filter = request.args.get("status", "").strip()
    source_filter = request.args.get("source", "").strip()
    date_filter = request.args.get("date", "").strip()

    query = WorklistItem.query.join(Patient)

    # Apply custom filters
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",") if s.strip()]
        if statuses:
            query = query.filter(WorklistItem.status.in_(statuses))

    if source_filter:
        sources = [s.strip() for s in source_filter.split(",") if s.strip()]
        if sources:
            query = query.filter(WorklistItem.patient_source.in_(sources))

    if date_filter:
        query = query.filter(WorklistItem.scheduled_date == date_filter.replace("-", ""))

    # Search
    if search_value:
        like = f"%{search_value}%"
        query = query.filter(
            db.or_(
                Patient.patient_id.ilike(like),
                Patient.patient_name.ilike(like),
                WorklistItem.accession_number.ilike(like),
                WorklistItem.requested_procedure_desc.ilike(like),
                WorklistItem.ordering_physician.ilike(like),
                WorklistItem.performing_physician.ilike(like),
            )
        )

    total = WorklistItem.query.count()
    filtered = query.count()

    # Order
    order_col = request.args.get("order[0][column]", "0", type=int)
    order_dir = request.args.get("order[0][dir]", "desc")

    col_map = {
        0: Patient.patient_id,                       # HN
        1: Patient.patient_name,                     # Patient Name
        2: Patient.sex,                              # Sex
        3: Patient.birth_date,                       # Age (sort by DOB)
        4: WorklistItem.requested_procedure_desc,    # Procedure
        5: WorklistItem.status,                      # Status
        # 6: priority — orderable: false, skip
        7: WorklistItem.patient_source,              # Type
        8: WorklistItem.performing_physician,        # Physician
        9: WorklistItem.accession_number,            # Accession No.
    }
    order_column = col_map.get(order_col, WorklistItem.id)
    if order_dir == "desc":
        query = query.order_by(order_column.desc())
    else:
        query = query.order_by(order_column.asc())

    items = query.offset(start).limit(length).all()

    data = []
    for item in items:
        # Calculate age from birth_date
        age = _calc_age(item.patient.birth_date) if item.patient.birth_date else ""

        # Check if ECG result exists
        has_result = ECGResult.query.filter_by(
            accession_number=item.accession_number
        ).first() is not None

        data.append({
            "id": item.id,
            "accession_number": item.accession_number,
            "patient_name": item.patient.patient_name,
            "patient_id": item.patient.patient_id,
            "sex": item.patient.sex or "",
            "procedure_desc": item.requested_procedure_desc or "Standard 12-lead ECG",
            "age": age,
            "status": item.status,
            "patient_source": item.patient_source or "Outpatient",
            "performing_physician": item.performing_physician or "",
            "priority": item.requested_procedure_priority,
            "ordering_department": item.ordering_department or "",
            "ordering_physician": item.ordering_physician or "",
            "scheduled_date": _format_date(item.scheduled_date),
            "scheduled_time": _format_time(item.scheduled_time),
            "has_result": has_result,
            "source": getattr(item, 'source', None) or "MANUAL",
        })

    return jsonify({
        "draw": draw,
        "recordsTotal": total,
        "recordsFiltered": filtered,
        "data": data,
    })


@ecg_tests_bp.route("/api/sync-mwl", methods=["POST"])
@login_required
def sync_mwl():
    """Trigger MWL sync from ECG Tests page."""
    from flask import current_app
    from services.mwl_scu import sync_from_external_mwl
    result = sync_from_external_mwl(current_app._get_current_object())
    return jsonify(result)


@ecg_tests_bp.route("/create", methods=["POST"])
@login_required
def create():
    """Create a new ECG test (AJAX)."""
    f = request.form

    # Patient - find or create
    hn = f.get("patient_id", "").strip()
    if not hn:
        return jsonify({"success": False, "error": "HN is required"}), 400

    patient = Patient.query.filter_by(patient_id=hn).first()
    if not patient:
        patient = Patient(
            patient_id=hn,
            patient_name=f.get("patient_name", "").strip(),
            sex=f.get("sex", ""),
            birth_date=f.get("birth_date", "").replace("-", ""),
        )
        db.session.add(patient)
        db.session.flush()
    else:
        # Update patient info if provided
        name = f.get("patient_name", "").strip()
        if name:
            patient.patient_name = name
        sex = f.get("sex", "")
        if sex:
            patient.sex = sex
        bd = f.get("birth_date", "").replace("-", "")
        if bd:
            patient.birth_date = bd

    # Auto-generate accession number
    accession = f.get("accession_number", "").strip()
    if not accession:
        accession = _generate_accession()

    sched_date = f.get("scheduled_date", "").replace("-", "")
    if not sched_date:
        sched_date = date.today().strftime("%Y%m%d")

    sched_time = f.get("scheduled_time", "")
    if sched_time:
        sched_time = sched_time.replace(":", "") + "00"
    else:
        sched_time = datetime.now().strftime("%H%M%S")

    item = WorklistItem(
        patient_id=patient.id,
        accession_number=accession,
        requested_procedure_id=f.get("procedure_id", "RP-ECG").strip(),
        requested_procedure_desc=f.get("procedure_desc", "Standard 12-lead ECG").strip(),
        requested_procedure_priority=f.get("priority", "ROUTINE"),
        scheduled_station_ae=f.get("station_ae", "CP150").strip(),
        scheduled_station_name=f.get("station_name", "ECG-ROOM1").strip(),
        modality="ECG",
        sps_id=f"SPS-{accession}",
        sps_desc=f.get("procedure_desc", "Standard 12-lead ECG").strip(),
        scheduled_date=sched_date,
        scheduled_time=sched_time,
        study_instance_uid=stable_uid_from_text(accession),
        status="SCHEDULED",
        ordering_department=f.get("ordering_department", "").strip(),
        ordering_physician=f.get("ordering_physician", "").strip(),
        performing_physician=f.get("performing_physician", "").strip(),
        patient_source=f.get("patient_source", "Outpatient"),
        bed_number=f.get("bed_number", "").strip(),
        phone=f.get("phone", "").strip(),
        clinical_info=f.get("clinical_info", "").strip(),
    )
    db.session.add(item)
    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Test {accession} created successfully",
        "id": item.id,
        "accession_number": accession,
    })


@ecg_tests_bp.route("/<int:item_id>/update-status", methods=["POST"])
@login_required
def update_status(item_id):
    """Update test status (AJAX)."""
    item = WorklistItem.query.get_or_404(item_id)
    new_status = request.json.get("status", "").strip()

    valid = ["SCHEDULED", "IN_PROGRESS", "COMPLETED", "CANCELLED"]
    if new_status not in valid:
        return jsonify({"success": False, "error": "Invalid status"}), 400

    item.status = new_status
    db.session.commit()

    return jsonify({"success": True, "status": new_status})


@ecg_tests_bp.route("/<int:item_id>/update-priority", methods=["POST"])
@login_required
def update_priority(item_id):
    """Toggle priority to URGENT (AJAX)."""
    item = WorklistItem.query.get_or_404(item_id)
    new_priority = request.json.get("priority", "URGENT").strip()
    if new_priority not in ("ROUTINE", "URGENT"):
        return jsonify({"success": False, "error": "Invalid priority"}), 400

    item.requested_procedure_priority = new_priority
    db.session.commit()

    return jsonify({"success": True, "priority": new_priority})


@ecg_tests_bp.route("/api/patient/<hn>")
@login_required
def lookup_patient(hn):
    """Lookup patient by HN for auto-fill."""
    patient = Patient.query.filter_by(patient_id=hn).first()
    if not patient:
        return jsonify({"found": False})

    return jsonify({
        "found": True,
        "patient_name": patient.patient_name,
        "sex": patient.sex or "",
        "birth_date": _format_date_iso(patient.birth_date),
    })


@ecg_tests_bp.route("/<int:item_id>/view-report")
@login_required
def view_report(item_id):
    """Redirect to ECG result detail page."""
    item = WorklistItem.query.get_or_404(item_id)
    result = ECGResult.query.filter_by(accession_number=item.accession_number).first()
    if result:
        return redirect(url_for("results.detail", result_id=result.id))

    # No result yet - redirect back
    return redirect(url_for("ecg_tests.index"))


@ecg_tests_bp.route("/<int:item_id>/delete", methods=["POST"])
@login_required
def delete(item_id):
    """Delete a worklist item (AJAX)."""
    item = WorklistItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    return jsonify({"success": True})


@ecg_tests_bp.route("/api/item/<int:item_id>")
@login_required
def get_item(item_id):
    """Get single worklist item JSON for edit modal."""
    item = WorklistItem.query.get_or_404(item_id)
    return jsonify({
        "id": item.id,
        "patient_id": item.patient.patient_id,
        "patient_name": item.patient.patient_name,
        "sex": item.patient.sex or "M",
        "birth_date": _format_date_iso(item.patient.birth_date),
        "accession_number": item.accession_number or "",
        "procedure_desc": item.requested_procedure_desc or "",
        "priority": item.requested_procedure_priority or "ROUTINE",
        "status": item.status or "SCHEDULED",
        "station_ae": item.scheduled_station_ae or "CP150",
        "station_name": item.scheduled_station_name or "ECG-ROOM1",
        "scheduled_date": _format_date_iso(item.scheduled_date),
        "scheduled_time": _format_time(item.scheduled_time),
        "ordering_department": item.ordering_department or "",
        "ordering_physician": item.ordering_physician or "",
        "patient_source": item.patient_source or "Outpatient",
        "bed_number": item.bed_number or "",
        "phone": item.phone or "",
        "clinical_info": item.clinical_info or "",
    })


@ecg_tests_bp.route("/<int:item_id>/edit", methods=["POST"])
@login_required
def edit(item_id):
    """Edit an existing worklist item (AJAX)."""
    item = WorklistItem.query.get_or_404(item_id)
    f = request.form

    # Update patient info
    hn = f.get("patient_id", "").strip()
    if not hn:
        return jsonify({"success": False, "error": "HN is required"}), 400

    patient = Patient.query.filter_by(patient_id=hn).first()
    if not patient:
        patient = Patient(
            patient_id=hn,
            patient_name=f.get("patient_name", "").strip(),
            sex=f.get("sex", ""),
            birth_date=f.get("birth_date", "").replace("-", ""),
        )
        db.session.add(patient)
        db.session.flush()
    else:
        name = f.get("patient_name", "").strip()
        if name:
            patient.patient_name = name
        sex = f.get("sex", "")
        if sex:
            patient.sex = sex
        bd = f.get("birth_date", "").replace("-", "")
        if bd:
            patient.birth_date = bd

    sched_date = f.get("scheduled_date", "").replace("-", "")
    sched_time = f.get("scheduled_time", "")
    if sched_time:
        sched_time = sched_time.replace(":", "") + "00"

    item.patient_id = patient.id
    accession = f.get("accession_number", "").strip()
    if accession:
        item.accession_number = accession
    item.requested_procedure_desc = f.get("procedure_desc", "Standard 12-lead ECG").strip()
    item.requested_procedure_priority = f.get("priority", "ROUTINE")
    item.scheduled_station_ae = f.get("station_ae", "CP150").strip()
    item.scheduled_station_name = f.get("station_name", "ECG-ROOM1").strip()
    if sched_date:
        item.scheduled_date = sched_date
    if sched_time:
        item.scheduled_time = sched_time
    item.ordering_department = f.get("ordering_department", "").strip()
    item.ordering_physician = f.get("ordering_physician", "").strip()
    item.patient_source = f.get("patient_source", "Outpatient")
    item.bed_number = f.get("bed_number", "").strip()
    item.phone = f.get("phone", "").strip()
    item.clinical_info = f.get("clinical_info", "").strip()

    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Test {item.accession_number} updated successfully",
    })


# ---- Helpers ----

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


def _calc_age(birth_date_str):
    """Calculate age from YYYYMMDD string."""
    if not birth_date_str or len(birth_date_str) != 8:
        return ""
    try:
        bd = date(int(birth_date_str[:4]), int(birth_date_str[4:6]), int(birth_date_str[6:8]))
        today = date.today()
        age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        return str(age)
    except (ValueError, TypeError):
        return ""


def _format_date(d):
    """YYYYMMDD -> DD/MM/YYYY"""
    if d and len(d) == 8:
        return f"{d[6:8]}/{d[4:6]}/{d[0:4]}"
    return d or ""


def _format_time(t):
    """HHMMSS -> HH:MM"""
    if t and len(t) >= 4:
        return f"{t[0:2]}:{t[2:4]}"
    return t or ""


def _format_date_iso(d):
    """YYYYMMDD -> YYYY-MM-DD for HTML date input."""
    if d and len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return ""
