import os
from datetime import datetime, timedelta

from flask import Blueprint, render_template, request, jsonify, send_file, current_app, flash, redirect, url_for, abort
from flask_login import login_required, current_user

from models import db, ECGResult, Patient, WorklistItem, AssignmentLog
from services.ecg_parser import parse_dicom_ecg, ecg_data_to_json, extract_dicom_tags
from utils.decorators import doctor_required

results_bp = Blueprint("results", __name__, url_prefix="/results")


def _calc_stats(role, user_id):
    """Shared stat calculation for index() and api_stats()."""
    from datetime import date as date_cls
    _DONE = ("APPROVED", "FINALIZED", "COMPLETED")
    base_q = ECGResult.query
    if role == "doctor":
        base_q = base_q.filter(ECGResult.assigned_to_id == user_id)

    if role == "doctor":
        # Doctor: pending = assigned to me, not yet done
        pending_count = base_q.filter(ECGResult.status.notin_(_DONE)).count()
    else:
        # Nurse/Admin: pending = currently assigned (active), not yet done
        pending_count = ECGResult.query.filter(
            ECGResult.assigned_to_id.isnot(None),
            ECGResult.status.notin_(_DONE)
        ).count()

    return {
        "unassigned": ECGResult.query.filter(
            ECGResult.assigned_to_id.is_(None),
            ECGResult.status.notin_(_DONE)
        ).count(),
        "pending": pending_count,
        "completed": base_q.filter(ECGResult.status.in_(_DONE)).count(),
        "today": base_q.filter(
            db.func.date(ECGResult.received_at) == date_cls.today()
        ).count(),
    }


@results_bp.route("/")
@login_required
def index():
    default_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dicom")
    return render_template("results/index.html",
                           stats=_calc_stats(current_user.role, current_user.id),
                           import_default_path=default_path)


@results_bp.route("/api/stats")
@login_required
def api_stats():
    """Return live stat counts for the ECG Results header pills."""
    return jsonify(_calc_stats(current_user.role, current_user.id))


@results_bp.route("/api/data")
@login_required
def api_data():
    """Server-side DataTables JSON API."""
    draw = request.args.get("draw", 1, type=int)
    start = request.args.get("start", 0, type=int)
    length = request.args.get("length", 25, type=int)
    search_value = request.args.get("search[value]", "").strip()

    query = ECGResult.query.outerjoin(Patient, ECGResult.patient_db_id == Patient.id).outerjoin(WorklistItem, ECGResult.worklist_id == WorklistItem.id)

    # Role-based visibility
    _DONE_STATUSES = ("APPROVED", "FINALIZED", "COMPLETED")
    view_filter = request.args.get("view", "all")
    if current_user.role == "doctor":
        if view_filter == "mine":
            # My Cases: pending cases assigned to me
            query = query.filter(
                ECGResult.assigned_to_id == current_user.id,
                ECGResult.status.notin_(_DONE_STATUSES),
            )
        elif view_filter == "unassigned":
            # Unassigned pool: pending cases not yet assigned to anyone
            query = query.filter(
                ECGResult.assigned_to_id.is_(None),
                ECGResult.status.notin_(_DONE_STATUSES),
            )
        else:
            # All: full history of cases assigned to me
            query = query.filter(ECGResult.assigned_to_id == current_user.id)
    elif view_filter == "mine":
        query = query.filter(ECGResult.assigned_to_id == current_user.id)
    elif view_filter == "unassigned":
        query = query.filter(
            ECGResult.assigned_to_id.is_(None),
            ECGResult.status.notin_(_DONE_STATUSES),
        )

    # Sidebar filters
    date_from     = request.args.get("date_from", "").strip()
    date_to       = request.args.get("date_to", "").strip()
    status_filter = request.args.get("status", "").strip()
    source_filter = request.args.get("source", "").strip()
    assign_filter = request.args.get("assignment", "").strip()

    if date_from:
        try:
            query = query.filter(ECGResult.received_at >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import timedelta
            query = query.filter(ECGResult.received_at < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            pass
    if status_filter:
        statuses = [s.strip().upper() for s in status_filter.split(",") if s.strip()]
        if statuses:
            query = query.filter(ECGResult.status.in_(statuses))
    if source_filter:
        sources = [s.strip() for s in source_filter.split(",") if s.strip()]
        if sources:
            query = query.filter(WorklistItem.patient_source.in_(sources))
    if assign_filter:
        now = datetime.now()
        assignments = [a.strip() for a in assign_filter.split(",") if a.strip()]
        conds = []
        for a in assignments:
            if a == "unassigned":
                conds.append(ECGResult.assigned_to_id.is_(None))
            elif a == "assigned":
                conds.append(db.and_(
                    ECGResult.assigned_to_id.isnot(None),
                    db.or_(ECGResult.assignment_expires_at.is_(None), ECGResult.assignment_expires_at > now)
                ))
            elif a == "expired":
                conds.append(db.and_(
                    ECGResult.assigned_to_id.isnot(None),
                    ECGResult.assignment_expires_at.isnot(None),
                    ECGResult.assignment_expires_at <= now
                ))
        if conds:
            query = query.filter(db.or_(*conds))

    # recordsTotal = count after role/view filter but before search filter
    total = query.count()

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

    filtered = query.count()

    order_col  = request.args.get("order[0][column]", "5")
    order_dir  = request.args.get("order[0][dir]", "desc")

    # Logical status sort order (workflow sequence, not alphabetical)
    from sqlalchemy import case as sa_case
    status_order = sa_case(
        (ECGResult.status == "RECEIVED",  1),
        (ECGResult.status == "IN_REVIEW", 2),
        (ECGResult.status == "REVIEWED",  3),
        (ECGResult.status == "APPROVED",  4),
        (ECGResult.status == "FINALIZED", 5),
        (ECGResult.status == "COMPLETED", 6),
        else_=0
    )

    sort_map = {
        "0": Patient.patient_id,
        "1": Patient.patient_name,
        "5": ECGResult.received_at,
        "7": status_order,
        "10": ECGResult.accession_number,
    }
    sort_col = sort_map.get(order_col, ECGResult.received_at)

    if order_dir == "desc":
        query = query.order_by(sort_col.desc())
    else:
        query = query.order_by(sort_col.asc())

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
            # Assignment & lock info
            "assigned_to": r.assigned_to.display_name if r.assigned_to else None,
            "assigned_to_id": r.assigned_to_id,
            "assignment_expires_at": r.assignment_expires_at.strftime("%d/%m/%Y %H:%M") if r.assignment_expires_at else None,
            "assignment_expires_at_iso": r.assignment_expires_at.isoformat() if r.assignment_expires_at else None,
            "is_locked": r.locked_by_id is not None,
            "locked_by": r.locked_by.display_name if r.locked_by else None,
        })

    return jsonify({
        "draw": draw,
        "recordsTotal": total,
        "recordsFiltered": filtered,
        "data": data,
    })


@results_bp.route("/my-worklist")
@login_required
@doctor_required
def my_worklist():
    """Redirect to ECG Results with My Cases filter."""
    return redirect(url_for("results.index", view="mine"))


@results_bp.route("/<int:result_id>")
@login_required
def detail(result_id):
    result = ECGResult.query.get_or_404(result_id)

    # Visibility guard: doctors can only open cases assigned specifically to them
    if current_user.role == "doctor":
        if result.assigned_to_id != current_user.id:
            flash("This case is not assigned to you, or has expired and returned to the central queue. | เคสนี้ไม่ได้ถูกมอบหมายให้คุณ หรือหมดเวลาและถูกคืนกลับสู่คิวส่วนกลางแล้ว", "warning")
            return redirect(url_for("results.my_worklist"))

    # Concurrency lock: try to acquire when doctor opens the case
    lock_status = {"success": True, "locked_by": None}
    if current_user.can_diagnose:
        if result.locked_by_id and result.locked_by_id != current_user.id:
            locker = result.locked_by
            lock_status = {
                "success": False,
                "locked_by": locker.display_name if locker else "another user",
            }
        else:
            now = datetime.now()
            result.locked_by_id = current_user.id
            result.locked_at    = now
            # Advance status to IN_REVIEW when doctor first opens the case;
            # clear the timer — IN_REVIEW cases have no timeout
            status_changed = result.status == "RECEIVED"
            if status_changed:
                result.status = "IN_REVIEW"
                result.assignment_expires_at = None
            db.session.add(AssignmentLog(
                ecg_result_id=result_id, action="locked", actor_id=current_user.id
            ))
            db.session.commit()
            # Notify the nurse who assigned this case that doctor has started reviewing
            if status_changed:
                from routes.notifications import push_notification
                last_log = (
                    AssignmentLog.query
                    .filter_by(ecg_result_id=result_id, action="assigned")
                    .order_by(AssignmentLog.timestamp.desc())
                    .first()
                )
                if last_log and last_log.actor_id:
                    push_notification(
                        user_id=last_log.actor_id,
                        message=f"{current_user.display_name} has started reviewing case {result.accession_number}.",
                        message_th=f"{current_user.display_name} กำลังวินิจฉัยเคส {result.accession_number}",
                        notif_type="in_review",
                        result_id=result_id,
                    )

    # Parse ECG waveform if file exists
    ecg_json = None
    if result.file_path and os.path.exists(result.file_path):
        ecg_data = parse_dicom_ecg(result.file_path)
        if ecg_data:
            ecg_json = ecg_data_to_json(ecg_data)

    return render_template("results/detail.html", result=result, ecg_json=ecg_json,
                           lock_status=lock_status)


@results_bp.route("/download/<int:result_id>")
@login_required
def download(result_id):
    result = ECGResult.query.get_or_404(result_id)
    if not result.file_path or not os.path.exists(result.file_path):
        return "ไม่พบไฟล์", 404

    # If diagnosis exists, embed it into a DICOM copy before download
    if result.diagnosis:
        from services.ecg_parser import embed_diagnosis_in_dicom
        buf = embed_diagnosis_in_dicom(
            result.file_path, result.diagnosis, result.diagnosed_by)
        filename = os.path.basename(result.file_path)
        return send_file(buf, as_attachment=True, download_name=filename,
                         mimetype='application/dicom')

    return send_file(result.file_path, as_attachment=True)


@results_bp.route("/pdf/<int:result_id>")
@login_required
def export_pdf(result_id):
    """Generate and download ECG PDF report."""
    result = ECGResult.query.get_or_404(result_id)

    if not result.file_path or not os.path.exists(result.file_path):
        return "ไม่พบไฟล์ DICOM", 404

    ecg_data = parse_dicom_ecg(result.file_path)
    if not ecg_data:
        return "ไม่สามารถอ่านข้อมูล ECG ได้", 500

    from services.ecg_pdf import generate_ecg_pdf
    pdf_buffer = generate_ecg_pdf(ecg_data, db_result=result)

    patient_id = result.patient.patient_id if result.patient else "unknown"
    date_str = result.received_at.strftime("%Y%m%d") if result.received_at else "undated"
    filename = f"ECG_{patient_id}_{date_str}.pdf"

    return send_file(pdf_buffer, mimetype="application/pdf",
                     as_attachment=True, download_name=filename)


@results_bp.route("/hl7/<int:result_id>")
@login_required
def export_hl7(result_id):
    """Generate and download HL7 v3 aECG (FDA XML) file."""
    result = ECGResult.query.get_or_404(result_id)

    if not result.file_path or not os.path.exists(result.file_path):
        return "ไม่พบไฟล์ DICOM", 404

    ecg_data = parse_dicom_ecg(result.file_path)
    if not ecg_data:
        return "ไม่สามารถอ่านข้อมูล ECG ได้", 500

    from services.ecg_hl7 import generate_ecg_hl7
    xml_str = generate_ecg_hl7(ecg_data, db_result=result)

    patient_id = result.patient.patient_id if result.patient else "unknown"
    date_str = result.received_at.strftime("%Y%m%d%H%M%S") if result.received_at else "undated"
    filename = f"{patient_id}_{date_str}.xml"

    from io import BytesIO
    buf = BytesIO(xml_str.encode('utf-8'))
    return send_file(buf, mimetype="application/xml",
                     as_attachment=True, download_name=filename)


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

    # Only doctors/admins can diagnose
    if not current_user.can_diagnose:
        return jsonify({"success": False, "error": "Insufficient permissions"}), 403

    # Concurrency guard: doctor must still hold the lock.
    # If nurse unassigned/reassigned while doctor had the page open,
    # locked_by_id will be None → this check blocks the save.
    if result.locked_by_id != current_user.id:
        return jsonify({
            "success": False,
            "error": "Your session has expired. This case was unassigned or reassigned by the nurse.",
            "error_th": "เซสชันหมดอายุ เคสนี้ถูกยกเลิกหรือมอบหมายใหม่โดยพยาบาลแล้ว",
            "force_redirect": True,
        }), 409

    data = request.get_json()
    diagnosis = data.get("diagnosis", "").strip()
    diagnosed_by = data.get("diagnosed_by", "").strip()
    action = data.get("action", "save")  # save, submit, submit_next

    result.diagnosis = diagnosis
    result.diagnosed_by = diagnosed_by

    if action in ("submit", "submit_next"):
        result.status = "APPROVED"
        result.diagnosed_at = datetime.now()
        # Keep assigned_to_id for history (stats + visibility);
        # only release lock and timer
        result.assignment_expires_at = None
        result.locked_by_id          = None
        result.locked_at             = None
        db.session.add(AssignmentLog(
            ecg_result_id=result_id, action="diagnosed", actor_id=current_user.id
        ))
    elif action == "save":
        if result.status in ("RECEIVED", "IN_REVIEW"):
            result.status = "REVIEWED"

    db.session.commit()

    # Notify the nurse who assigned this case
    from routes.notifications import push_notification
    last_assign_log = (
        AssignmentLog.query
        .filter_by(ecg_result_id=result_id, action="assigned")
        .order_by(AssignmentLog.timestamp.desc())
        .first()
    )
    nurse_id = last_assign_log.actor_id if last_assign_log else None

    if action == "save" and nurse_id:
        push_notification(
            user_id=nurse_id,
            message=f"{current_user.display_name} saved a draft for case {result.accession_number}.",
            message_th=f"{current_user.display_name} บันทึกฉบับร่างเคส {result.accession_number}",
            notif_type="draft_saved",
            result_id=result_id,
        )

    if action in ("submit", "submit_next") and nurse_id:
        push_notification(
            user_id=nurse_id,
            message=f"{current_user.display_name} has completed diagnosis for case {result.accession_number}.",
            message_th=f"{current_user.display_name} วินิจฉัยเคส {result.accession_number} เสร็จแล้ว",
            notif_type="diagnosed",
            result_id=result_id,
        )

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


@results_bp.route("/api/browse")
@login_required
def api_browse():
    """Return directory listing for the folder browser."""
    req_path = request.args.get("path", "").strip()

    # Default to the dicom folder
    if not req_path:
        req_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dicom")

    # Normalise
    req_path = os.path.abspath(req_path)

    if not os.path.isdir(req_path):
        return jsonify({"error": "Not a valid directory", "error_th": "ไม่ใช่โฟลเดอร์ที่ถูกต้อง"}), 400

    folders = []
    dcm_count = 0
    try:
        for entry in sorted(os.scandir(req_path), key=lambda e: e.name.lower()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir(follow_symlinks=False):
                folders.append(entry.name)
            elif entry.name.lower().endswith(".dcm"):
                dcm_count += 1
    except PermissionError:
        return jsonify({"error": "Permission denied", "error_th": "ไม่มีสิทธิ์เข้าถึง"}), 403

    parent = os.path.dirname(req_path)

    return jsonify({
        "current": req_path,
        "parent": parent if parent != req_path else None,
        "folders": folders,
        "dcm_count": dcm_count,
    })


@results_bp.route("/import", methods=["POST"])
@login_required
def import_dicom():
    """Import DICOM files from a directory (AJAX)."""
    data = request.get_json(silent=True) or {}
    import_path = data.get("import_path", "").strip()

    if not import_path or not os.path.exists(import_path):
        return jsonify({"success": False, "error": "Folder not found.", "error_th": "ไม่พบโฟลเดอร์ที่ระบุ"}), 400

    imported = _import_dicom_files(import_path)
    return jsonify({"success": True, "count": imported})


def _import_dicom_files(directory: str) -> int:
    """Scan directory for .dcm files, copy into dicom_storage, and import."""
    import shutil
    import pydicom
    count = 0

    storage_root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dicom_storage")

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

            patient_id_val = str(getattr(ds, "PatientID", "UNKNOWN"))
            patient_name_val = str(getattr(ds, "PatientName", ""))
            accession = str(getattr(ds, "AccessionNumber", ""))
            study_uid = str(getattr(ds, "StudyInstanceUID", ""))

            # Copy file into dicom_storage/YYYYMMDD/PatientID/
            today = datetime.now().strftime("%Y%m%d")
            patient_dir = os.path.join(storage_root, today, patient_id_val)
            os.makedirs(patient_dir, exist_ok=True)

            dest_filename = f"{sop_uid}.dcm" if sop_uid else fname
            dest_path = os.path.join(patient_dir, dest_filename)
            shutil.copy2(filepath, dest_path)

            # Find or create patient
            patient = Patient.query.filter_by(patient_id=patient_id_val).first() if patient_id_val else None
            if not patient and patient_id_val:
                sex = str(getattr(ds, "PatientSex", "") or "").strip().upper()[:1]
                if sex not in ("M", "F", "O"):
                    sex = ""
                birth_date = str(getattr(ds, "PatientBirthDate", "") or "").strip()
                patient = Patient(
                    patient_id=patient_id_val,
                    patient_name=patient_name_val or patient_id_val,
                    sex=sex,
                    birth_date=birth_date if len(birth_date) == 8 else None,
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
                file_path=os.path.abspath(dest_path),
                received_at=datetime.now(),
                status="RECEIVED",
            )
            db.session.add(result)
            count += 1

    db.session.commit()
    return count
