from datetime import datetime, date

from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required

from models import db, WorklistItem, Patient
from services.dicom_helpers import stable_uid_from_text

worklist_bp = Blueprint("worklist", __name__, url_prefix="/worklist")


@worklist_bp.route("/")
@login_required
def index():
    return render_template("worklist/index.html")


@worklist_bp.route("/api/data")
@login_required
def api_data():
    """Server-side DataTables JSON API."""
    draw = request.args.get("draw", 1, type=int)
    start = request.args.get("start", 0, type=int)
    length = request.args.get("length", 25, type=int)
    search_value = request.args.get("search[value]", "").strip()

    query = WorklistItem.query.join(Patient)

    # Search
    if search_value:
        like = f"%{search_value}%"
        query = query.filter(
            db.or_(
                Patient.patient_id.ilike(like),
                Patient.patient_name.ilike(like),
                WorklistItem.accession_number.ilike(like),
                WorklistItem.requested_procedure_desc.ilike(like),
                WorklistItem.status.ilike(like),
            )
        )

    total = WorklistItem.query.count()
    filtered = query.count()

    # Order
    order_col = request.args.get("order[0][column]", "0", type=int)
    order_dir = request.args.get("order[0][dir]", "desc")

    col_map = {
        0: WorklistItem.id,
        1: Patient.patient_id,
        2: Patient.patient_name,
        3: WorklistItem.accession_number,
        4: WorklistItem.requested_procedure_desc,
        5: WorklistItem.scheduled_date,
        6: WorklistItem.scheduled_time,
        7: WorklistItem.requested_procedure_priority,
        8: WorklistItem.status,
    }
    order_column = col_map.get(order_col, WorklistItem.id)
    if order_dir == "desc":
        query = query.order_by(order_column.desc())
    else:
        query = query.order_by(order_column.asc())

    items = query.offset(start).limit(length).all()

    data = []
    for item in items:
        data.append({
            "id": item.id,
            "patient_id": item.patient.patient_id,
            "patient_name": item.patient.patient_name,
            "accession_number": item.accession_number,
            "procedure_desc": item.requested_procedure_desc,
            "scheduled_date": _format_dicom_date(item.scheduled_date),
            "scheduled_time": _format_dicom_time(item.scheduled_time),
            "priority": item.requested_procedure_priority,
            "status": item.status,
            "station_name": item.scheduled_station_name,
        })

    return jsonify({
        "draw": draw,
        "recordsTotal": total,
        "recordsFiltered": filtered,
        "data": data,
    })


@worklist_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        return _save_worklist_item(None)

    today = date.today()
    return render_template("worklist/form.html", item=None, today=today)


@worklist_bp.route("/edit/<int:item_id>", methods=["GET", "POST"])
@login_required
def edit(item_id):
    item = WorklistItem.query.get_or_404(item_id)

    if request.method == "POST":
        return _save_worklist_item(item)

    return render_template("worklist/form.html", item=item)


@worklist_bp.route("/delete/<int:item_id>", methods=["POST"])
@login_required
def delete(item_id):
    item = WorklistItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True})

    flash("ลบรายการเรียบร้อยแล้ว", "success")
    return redirect(url_for("worklist.index"))


def _save_worklist_item(item):
    """Create or update a worklist item."""
    f = request.form

    # Patient - find or create
    hn = f.get("patient_id", "").strip()
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
        patient.patient_name = f.get("patient_name", "").strip()
        patient.sex = f.get("sex", "")
        patient.birth_date = f.get("birth_date", "").replace("-", "")

    accession = f.get("accession_number", "").strip()
    sched_date = f.get("scheduled_date", "").replace("-", "")
    sched_time = f.get("scheduled_time", "").replace(":", "") + "00" if f.get("scheduled_time") else ""

    if item is None:
        item = WorklistItem()
        db.session.add(item)

    item.patient_id = patient.id
    item.accession_number = accession
    item.requested_procedure_id = f.get("procedure_id", "").strip()
    item.requested_procedure_desc = f.get("procedure_desc", "").strip()
    item.admission_id = f.get("admission_id", "").strip()
    item.requested_procedure_priority = f.get("priority", "ROUTINE")
    item.scheduled_station_ae = f.get("station_ae", "CP150").strip()
    item.scheduled_station_name = f.get("station_name", "ECG-ROOM1").strip()
    item.modality = f.get("modality", "ECG").strip()
    item.sps_id = f.get("sps_id", "").strip()
    item.sps_desc = f.get("sps_desc", "").strip()
    item.scheduled_date = sched_date
    item.scheduled_time = sched_time
    item.study_instance_uid = stable_uid_from_text(accession)
    item.status = f.get("status", "SCHEDULED")

    db.session.commit()
    flash("บันทึกรายการเรียบร้อยแล้ว", "success")
    return redirect(url_for("worklist.index"))


def _format_dicom_date(d):
    """YYYYMMDD -> DD/MM/YYYY"""
    if d and len(d) == 8:
        return f"{d[6:8]}/{d[4:6]}/{d[0:4]}"
    return d or ""


def _format_dicom_time(t):
    """HHMMSS -> HH:MM"""
    if t and len(t) >= 4:
        return f"{t[0:2]}:{t[2:4]}"
    return t or ""
