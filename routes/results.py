import os
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, send_file, current_app, flash, redirect, url_for
from flask_login import login_required

from models import db, ECGResult, Patient, WorklistItem
from services.ecg_parser import parse_dicom_ecg, ecg_data_to_json, extract_dicom_tags

results_bp = Blueprint("results", __name__, url_prefix="/results")


@results_bp.route("/")
@login_required
def index():
    return render_template("results/index.html")


@results_bp.route("/api/data")
@login_required
def api_data():
    """Server-side DataTables JSON API."""
    draw = request.args.get("draw", 1, type=int)
    start = request.args.get("start", 0, type=int)
    length = request.args.get("length", 25, type=int)
    search_value = request.args.get("search[value]", "").strip()

    query = ECGResult.query.outerjoin(Patient, ECGResult.patient_db_id == Patient.id).outerjoin(WorklistItem, ECGResult.worklist_id == WorklistItem.id)

    if search_value:
        like = f"%{search_value}%"
        query = query.filter(
            db.or_(
                ECGResult.accession_number.ilike(like),
                ECGResult.status.ilike(like),
                Patient.patient_id.ilike(like),
                Patient.patient_name.ilike(like),
            )
        )

    total = ECGResult.query.count()
    filtered = query.count()

    order_dir = request.args.get("order[0][dir]", "desc")
    if order_dir == "desc":
        query = query.order_by(ECGResult.received_at.desc())
    else:
        query = query.order_by(ECGResult.received_at.asc())

    items = query.offset(start).limit(length).all()

    data = []
    for r in items:
        physician = ""
        procedure = ""
        sex = ""
        age = ""
        if r.worklist_item:
            physician = r.worklist_item.performing_physician or r.worklist_item.ordering_physician or ""
            procedure = r.worklist_item.requested_procedure_desc or ""
        if r.patient:
            sex = r.patient.sex or ""
            if r.patient.birth_date and len(r.patient.birth_date) == 8:
                try:
                    from datetime import date as date_cls
                    bd = date_cls(int(r.patient.birth_date[:4]), int(r.patient.birth_date[4:6]), int(r.patient.birth_date[6:8]))
                    age = str((date_cls.today() - bd).days // 365)
                except Exception:
                    age = ""
        data.append({
            "id": r.id,
            "accession_number": r.accession_number or "-",
            "patient_id": r.patient.patient_id if r.patient else "-",
            "patient_name": r.patient.patient_name if r.patient else "-",
            "sex": sex,
            "age": age,
            "procedure": procedure,
            "received_at": r.received_at.strftime("%d/%m/%Y %H:%M") if r.received_at else "-",
            "status": r.status,
            "physician": physician,
            "diagnosis": r.diagnosis or "",
        })

    return jsonify({
        "draw": draw,
        "recordsTotal": total,
        "recordsFiltered": filtered,
        "data": data,
    })


@results_bp.route("/<int:result_id>")
@login_required
def detail(result_id):
    result = ECGResult.query.get_or_404(result_id)

    # Parse ECG waveform if file exists
    ecg_json = None
    if result.file_path and os.path.exists(result.file_path):
        ecg_data = parse_dicom_ecg(result.file_path)
        if ecg_data:
            ecg_json = ecg_data_to_json(ecg_data)

    return render_template("results/detail.html", result=result, ecg_json=ecg_json)


@results_bp.route("/download/<int:result_id>")
@login_required
def download(result_id):
    result = ECGResult.query.get_or_404(result_id)
    if result.file_path and os.path.exists(result.file_path):
        return send_file(result.file_path, as_attachment=True)
    return "ไม่พบไฟล์", 404


@results_bp.route("/waveform/<int:result_id>")
@login_required
def waveform_data(result_id):
    """API endpoint returning ECG waveform JSON for the viewer."""
    result = ECGResult.query.get_or_404(result_id)

    if not result.file_path or not os.path.exists(result.file_path):
        return jsonify({"error": "ไม่พบไฟล์ DICOM"}), 404

    ecg_data = parse_dicom_ecg(result.file_path)
    if not ecg_data:
        return jsonify({"error": "ไม่สามารถอ่านข้อมูล ECG ได้"}), 400

    return jsonify(ecg_data_to_json(ecg_data))


@results_bp.route("/dicom-tags/<int:result_id>")
@login_required
def dicom_tags(result_id):
    """API endpoint returning all DICOM tags for inspection."""
    result = ECGResult.query.get_or_404(result_id)

    if not result.file_path or not os.path.exists(result.file_path):
        return jsonify({"error": "ไม่พบไฟล์ DICOM"}), 404

    tags = extract_dicom_tags(result.file_path)
    return jsonify({"tags": tags})


@results_bp.route("/<int:result_id>/compare")
@login_required
def compare(result_id):
    """AJAX endpoint returning HTML fragment for Report Comparison."""
    result = ECGResult.query.get_or_404(result_id)

    # Get all results for this patient
    patient = result.patient
    results_list = []

    if result.patient_db_id:
        all_results = (
            ECGResult.query
            .filter_by(patient_db_id=result.patient_db_id)
            .order_by(ECGResult.received_at.desc())
            .limit(10)
            .all()
        )

        for r in all_results:
            item = r
            # Parse ECG data for each result
            item.ecg_json = None
            item.ecg_interpretation = None
            if r.file_path and os.path.exists(r.file_path):
                ecg_data = parse_dicom_ecg(r.file_path)
                if ecg_data:
                    item.ecg_json = ecg_data_to_json(ecg_data)
                    # Get interpretation text
                    if ecg_data.interpretation_texts:
                        item.ecg_interpretation = " | ".join(ecg_data.interpretation_texts)
            results_list.append(item)

    return render_template(
        "results/compare_fragment.html",
        results=results_list,
        current_id=result_id,
        patient=patient,
    )


@results_bp.route("/<int:result_id>/diagnosis", methods=["POST"])
@login_required
def save_diagnosis(result_id):
    """Save or submit diagnosis for an ECG result (AJAX)."""
    result = ECGResult.query.get_or_404(result_id)
    data = request.get_json()

    diagnosis = data.get("diagnosis", "").strip()
    diagnosed_by = data.get("diagnosed_by", "").strip()
    action = data.get("action", "save")  # save, submit, submit_next

    result.diagnosis = diagnosis
    result.diagnosed_by = diagnosed_by

    if action in ("submit", "submit_next"):
        result.status = "APPROVED"
        result.diagnosed_at = datetime.now()
    elif action == "save":
        if result.status == "RECEIVED":
            result.status = "REVIEWED"

    db.session.commit()

    response = {"success": True, "status": result.status}

    # For submit_next, find the next undiagnosed result
    if action == "submit_next":
        next_result = (
            ECGResult.query
            .filter(ECGResult.id != result_id)
            .filter(ECGResult.status.in_(["RECEIVED", "REVIEWED"]))
            .order_by(ECGResult.received_at.asc())
            .first()
        )
        if next_result:
            response["next_id"] = next_result.id

    return jsonify(response)


@results_bp.route("/import", methods=["GET", "POST"])
@login_required
def import_dicom():
    """Import DICOM files from a directory."""
    if request.method == "POST":
        import_path = request.form.get("import_path", "").strip()
        if not import_path or not os.path.exists(import_path):
            flash("ไม่พบโฟลเดอร์ที่ระบุ", "error")
            return redirect(url_for("results.import_dicom"))

        imported = _import_dicom_files(import_path)
        flash(f"นำเข้าไฟล์ DICOM สำเร็จ {imported} ไฟล์", "success")
        return redirect(url_for("results.index"))

    # Default path
    default_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dicom")
    return render_template("results/import.html", default_path=default_path)


def _import_dicom_files(directory: str) -> int:
    """Scan directory for .dcm files and import them."""
    import pydicom
    count = 0

    for root, dirs, files in os.walk(directory):
        for fname in files:
            if not fname.lower().endswith(".dcm"):
                continue

            filepath = os.path.join(root, fname)

            # Check if already imported (by SOP Instance UID)
            try:
                ds = pydicom.dcmread(filepath, force=True)
            except Exception:
                continue

            sop_uid = str(getattr(ds, "SOPInstanceUID", ""))
            if sop_uid and ECGResult.query.filter_by(sop_instance_uid=sop_uid).first():
                continue  # Already imported

            patient_id_val = str(getattr(ds, "PatientID", ""))
            patient_name_val = str(getattr(ds, "PatientName", ""))
            accession = str(getattr(ds, "AccessionNumber", ""))
            study_uid = str(getattr(ds, "StudyInstanceUID", ""))

            # Find or create patient
            patient = Patient.query.filter_by(patient_id=patient_id_val).first() if patient_id_val else None
            if not patient and patient_id_val:
                patient = Patient(
                    patient_id=patient_id_val,
                    patient_name=patient_name_val,
                    sex=str(getattr(ds, "PatientSex", "")),
                    birth_date=str(getattr(ds, "PatientBirthDate", "")),
                )
                db.session.add(patient)
                db.session.flush()

            # Find matching worklist
            worklist = None
            if accession:
                worklist = WorklistItem.query.filter_by(accession_number=accession).first()

            result = ECGResult(
                worklist_id=worklist.id if worklist else None,
                patient_db_id=patient.id if patient else None,
                accession_number=accession,
                study_instance_uid=study_uid,
                sop_instance_uid=sop_uid,
                file_path=os.path.abspath(filepath),
                received_at=datetime.now(),
                status="RECEIVED",
            )
            db.session.add(result)
            count += 1

    db.session.commit()
    return count
