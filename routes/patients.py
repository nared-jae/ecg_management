from datetime import date

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required

from models import db, Patient, WorklistItem, ECGResult
from utils.decorators import roles_required

patients_bp = Blueprint("patients", __name__, url_prefix="/patients")


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------
@patients_bp.route("/")
@login_required
@roles_required("admin", "it_admin", "nurse")
def index():
    total = Patient.query.count()
    with_results = (
        db.session.query(Patient.id)
        .join(ECGResult, db.and_(ECGResult.patient_db_id == Patient.id, ECGResult.is_deleted == False))
        .distinct()
        .count()
    )
    stats = {
        "total": total,
        "with_results": with_results,
        "without_results": total - with_results,
    }
    return render_template("patients/index.html", stats=stats)


# ---------------------------------------------------------------------------
# DataTable server-side API
# ---------------------------------------------------------------------------
@patients_bp.route("/api/data")
@login_required
@roles_required("admin", "it_admin", "nurse")
def api_data():
    draw = request.args.get("draw", 1, type=int)
    start = request.args.get("start", 0, type=int)
    length = request.args.get("length", 25, type=int)
    search_value = request.args.get("search[value]", "").strip()

    # Subqueries for counts
    tests_count = (
        db.session.query(
            WorklistItem.patient_id,
            db.func.count(WorklistItem.id).label("cnt"),
        )
        .group_by(WorklistItem.patient_id)
        .subquery()
    )
    results_count = (
        db.session.query(
            ECGResult.patient_db_id,
            db.func.count(ECGResult.id).label("cnt"),
        )
        .filter(ECGResult.is_deleted == False)
        .group_by(ECGResult.patient_db_id)
        .subquery()
    )

    query = (
        db.session.query(
            Patient,
            db.func.coalesce(tests_count.c.cnt, 0).label("test_count"),
            db.func.coalesce(results_count.c.cnt, 0).label("result_count"),
        )
        .outerjoin(tests_count, tests_count.c.patient_id == Patient.id)
        .outerjoin(results_count, results_count.c.patient_db_id == Patient.id)
    )

    total = Patient.query.count()

    # Custom filters
    sex_filter = request.args.get("sex", "").strip()
    status_filter = request.args.get("status", "").strip()

    if sex_filter:
        sexes = [s.strip() for s in sex_filter.split(",") if s.strip()]
        if sexes:
            query = query.filter(Patient.sex.in_(sexes))

    if status_filter == "with_results":
        query = query.filter(results_count.c.cnt > 0)
    elif status_filter == "without_results":
        query = query.filter(
            db.or_(results_count.c.cnt == 0, results_count.c.cnt.is_(None))
        )
    elif status_filter == "with_tests":
        query = query.filter(tests_count.c.cnt > 0)
    elif status_filter == "no_data":
        query = query.filter(
            db.or_(tests_count.c.cnt == 0, tests_count.c.cnt.is_(None))
        ).filter(
            db.or_(results_count.c.cnt == 0, results_count.c.cnt.is_(None))
        )

    if search_value:
        like = f"%{search_value}%"
        query = query.filter(
            db.or_(
                Patient.patient_id.ilike(like),
                Patient.patient_name.ilike(like),
            )
        )

    filtered = query.count()

    # Ordering
    order_col = request.args.get("order[0][column]", "0", type=int)
    order_dir = request.args.get("order[0][dir]", "asc")
    col_map = {
        0: Patient.patient_id,
        1: Patient.patient_name,
        2: Patient.sex,
        3: Patient.birth_date,
        6: Patient.created_at,
    }
    order_column = col_map.get(order_col, Patient.patient_id)
    if order_dir == "desc":
        query = query.order_by(order_column.desc())
    else:
        query = query.order_by(order_column.asc())

    rows = query.offset(start).limit(length).all()

    data = []
    for patient, test_count, result_count in rows:
        data.append({
            "id": patient.id,
            "patient_id": patient.patient_id,
            "patient_name": patient.patient_name,
            "sex": patient.sex or "",
            "age": _calc_age(patient.birth_date),
            "birth_date": patient.birth_date or "",
            "birth_date_iso": _format_date_iso(patient.birth_date),
            "test_count": test_count,
            "result_count": result_count,
            "created_at": patient.created_at.strftime("%d/%m/%Y %H:%M") if patient.created_at else "-",
        })

    return jsonify({
        "draw": draw,
        "recordsTotal": total,
        "recordsFiltered": filtered,
        "data": data,
    })


# ---------------------------------------------------------------------------
# Single patient JSON (for edit modal)
# ---------------------------------------------------------------------------
@patients_bp.route("/api/patient/<int:patient_id>")
@login_required
@roles_required("admin", "it_admin", "nurse")
def get_patient(patient_id):
    p = Patient.query.get_or_404(patient_id)
    return jsonify({
        "id": p.id,
        "patient_id": p.patient_id,
        "patient_name": p.patient_name,
        "sex": p.sex or "M",
        "birth_date": _format_date_iso(p.birth_date),
    })


# ---------------------------------------------------------------------------
# Create patient
# ---------------------------------------------------------------------------
@patients_bp.route("/create", methods=["POST"])
@login_required
@roles_required("admin", "it_admin", "nurse")
def create():
    hn = request.form.get("patient_id", "").strip()
    name = request.form.get("patient_name", "").strip()
    sex = request.form.get("sex", "M").strip()
    birth_date = request.form.get("birth_date", "").replace("-", "").strip()

    if not hn or not name:
        return jsonify({"success": False, "error": "HN and Name are required | กรุณากรอก HN และชื่อ"}), 400

    if Patient.query.filter_by(patient_id=hn).first():
        return jsonify({"success": False, "error": "HN already exists | HN นี้มีอยู่แล้ว"}), 409

    patient = Patient(patient_id=hn, patient_name=name, sex=sex, birth_date=birth_date)
    db.session.add(patient)
    db.session.commit()

    return jsonify({"success": True, "message": f"Patient {hn} created | สร้างผู้ป่วย {hn} สำเร็จ"})


# ---------------------------------------------------------------------------
# Edit patient
# ---------------------------------------------------------------------------
@patients_bp.route("/<int:patient_id>/edit", methods=["POST"])
@login_required
@roles_required("admin", "it_admin", "nurse")
def edit(patient_id):
    patient = Patient.query.get_or_404(patient_id)
    name = request.form.get("patient_name", "").strip()
    sex = request.form.get("sex", "").strip()
    birth_date = request.form.get("birth_date", "").replace("-", "").strip()

    if not name:
        return jsonify({"success": False, "error": "Name is required | กรุณากรอกชื่อ"}), 400

    patient.patient_name = name
    if sex:
        patient.sex = sex
    if birth_date:
        patient.birth_date = birth_date

    db.session.commit()
    return jsonify({"success": True, "message": f"Patient {patient.patient_id} updated | อัปเดตผู้ป่วย {patient.patient_id} สำเร็จ"})


# ---------------------------------------------------------------------------
# Delete patient (with FK check)
# ---------------------------------------------------------------------------
@patients_bp.route("/<int:patient_id>/delete", methods=["POST"])
@login_required
@roles_required("admin", "it_admin", "nurse")
def delete(patient_id):
    patient = Patient.query.get_or_404(patient_id)

    # Block if patient has ECG results
    has_results = ECGResult.query.filter(ECGResult.patient_db_id == patient_id, ECGResult.is_deleted == False).first()
    if has_results:
        return jsonify({
            "success": False,
            "error": "Cannot delete: patient has ECG results | ไม่สามารถลบได้: ผู้ป่วยมีผลตรวจ ECG",
        }), 400

    # Delete worklist items first (if any)
    WorklistItem.query.filter_by(patient_id=patient_id).delete()
    db.session.delete(patient)
    db.session.commit()

    return jsonify({"success": True, "message": f"Patient {patient.patient_id} deleted | ลบผู้ป่วย {patient.patient_id} สำเร็จ"})


# ---------------------------------------------------------------------------
# Bulk delete unused patients
# ---------------------------------------------------------------------------
@patients_bp.route("/bulk-delete", methods=["POST"])
@login_required
@roles_required("admin", "it_admin", "nurse")
def bulk_delete():
    # Find patients with 0 worklist items AND 0 ECG results
    patients_with_tests = db.session.query(WorklistItem.patient_id).distinct()
    patients_with_results = db.session.query(ECGResult.patient_db_id).filter(ECGResult.patient_db_id.isnot(None), ECGResult.is_deleted == False).distinct()

    unused = (
        Patient.query
        .filter(~Patient.id.in_(patients_with_tests))
        .filter(~Patient.id.in_(patients_with_results))
        .all()
    )

    count = len(unused)
    for p in unused:
        db.session.delete(p)
    db.session.commit()

    return jsonify({
        "success": True,
        "count": count,
        "message": f"Deleted {count} unused patients | ลบผู้ป่วยที่ไม่ได้ใช้ {count} ราย",
    })


# ---------------------------------------------------------------------------
# Helpers (reuse pattern from ecg_tests.py)
# ---------------------------------------------------------------------------
def _calc_age(birth_date_str):
    if not birth_date_str or len(birth_date_str) != 8:
        return ""
    try:
        bd = date(int(birth_date_str[:4]), int(birth_date_str[4:6]), int(birth_date_str[6:8]))
        today = date.today()
        age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        return str(age)
    except (ValueError, TypeError):
        return ""


def _format_date_iso(d):
    if d and len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return ""
