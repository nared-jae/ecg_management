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
    base_q = ECGResult.query.filter(ECGResult.is_deleted == False)
    if role == "doctor":
        # Include cases assigned to me OR cases I diagnosed
        my_diagnosed_ids = db.session.query(AssignmentLog.ecg_result_id).filter(
            AssignmentLog.actor_id == user_id,
            AssignmentLog.action == "diagnosed",
        )
        base_q = base_q.filter(
            db.or_(ECGResult.assigned_to_id == user_id, ECGResult.id.in_(my_diagnosed_ids))
        )

    if role == "doctor":
        # Doctor: pending = assigned to me, not yet done
        pending_count = ECGResult.query.filter(
            ECGResult.is_deleted == False,
            ECGResult.assigned_to_id == user_id,
            ECGResult.status.notin_(_DONE),
        ).count()
    else:
        # Nurse/Admin: pending = currently assigned (active), not yet done
        pending_count = ECGResult.query.filter(
            ECGResult.is_deleted == False,
            ECGResult.assigned_to_id.isnot(None),
            ECGResult.status.notin_(_DONE)
        ).count()

    return {
        "unassigned": ECGResult.query.filter(
            ECGResult.is_deleted == False,
            ECGResult.assigned_to_id.is_(None),
            ECGResult.status.notin_(_DONE)
        ).count(),
        "pending": pending_count,
        "completed": base_q.filter(ECGResult.status.in_(_DONE)).count(),
        "today": base_q.filter(
            db.func.date(db.func.coalesce(ECGResult.study_datetime, ECGResult.received_at)) == date_cls.today()
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

    query = ECGResult.query.filter(ECGResult.is_deleted == False).outerjoin(Patient, ECGResult.patient_db_id == Patient.id).outerjoin(WorklistItem, ECGResult.worklist_id == WorklistItem.id)

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
            # All: cases assigned to me OR cases I diagnosed
            my_diagnosed_ids = db.session.query(AssignmentLog.ecg_result_id).filter(
                AssignmentLog.actor_id == current_user.id,
                AssignmentLog.action == "diagnosed",
            )
            query = query.filter(
                db.or_(
                    ECGResult.assigned_to_id == current_user.id,
                    ECGResult.id.in_(my_diagnosed_ids),
                )
            )
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

    # Use study_datetime for date filtering, fallback to received_at
    _exam_dt = db.func.coalesce(ECGResult.study_datetime, ECGResult.received_at)
    if date_from:
        try:
            query = query.filter(_exam_dt >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            from datetime import timedelta
            query = query.filter(_exam_dt < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
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
        "5": _exam_dt,
        "7": status_order,
        "10": ECGResult.accession_number,
    }
    sort_col = sort_map.get(order_col, _exam_dt)

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
            "received_at": (r.study_datetime or r.received_at).strftime("%d/%m/%Y %H:%M") if (r.study_datetime or r.received_at) else "-",
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
            "pacs_send_status": r.pacs_send_status,
            "pacs_sent_at": r.pacs_sent_at.strftime("%d/%m/%Y %H:%M") if r.pacs_sent_at else None,
            "pdf_export_status": r.pdf_export_status,
            "hl7_export_status": r.hl7_export_status,
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

    # Concurrency lock: try to acquire when assigned doctor opens the case
    lock_status = {"success": True, "locked_by": None}
    is_assigned_doctor = (current_user.role == "doctor" and result.assigned_to_id == current_user.id)
    if is_assigned_doctor:
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
            # Advance status to IN_REVIEW when assigned doctor first opens the case;
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
    _dt = result.study_datetime or result.received_at
    date_str = _dt.strftime("%Y%m%d") if _dt else "undated"
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
    _dt = result.study_datetime or result.received_at
    date_str = _dt.strftime("%Y%m%d%H%M%S") if _dt else "undated"
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
            .filter(ECGResult.is_deleted == False)
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

    # Allow the assigned doctor to revise APPROVED cases (typo fix, etc.)
    # Block only FINALIZED/COMPLETED (sent to PACS or fully closed)
    if result.status in ("FINALIZED", "COMPLETED"):
        return jsonify({
            "success": False,
            "error": "Cannot modify diagnosis on finalized cases",
            "error_th": "ไม่สามารถแก้ไขผลวินิจฉัยที่ส่งออกแล้วได้",
        }), 400

    # For APPROVED cases, only the assigned doctor can revise
    if result.status == "APPROVED" and result.assigned_to_id != current_user.id:
        return jsonify({
            "success": False,
            "error": "Only the assigned doctor can revise this diagnosis",
            "error_th": "เฉพาะแพทย์เจ้าของเคสเท่านั้นที่สามารถแก้ไขได้",
        }), 403

    # Concurrency guard: doctor must still hold the lock (skip for APPROVED revision)
    if result.status != "APPROVED" and result.locked_by_id != current_user.id:
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
    is_revision = result.status == "APPROVED"

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
            ecg_result_id=result_id,
            action="revised" if is_revision else "diagnosed",
            actor_id=current_user.id,
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
    print(f"[Diagnosis] action={action}, is_revision={is_revision}, nurse_id={nurse_id}, result_id={result_id}")

    if action == "save" and nurse_id:
        push_notification(
            user_id=nurse_id,
            message=f"{current_user.display_name} saved a draft for case {result.accession_number}.",
            message_th=f"{current_user.display_name} บันทึกฉบับร่างเคส {result.accession_number}",
            notif_type="draft_saved",
            result_id=result_id,
        )

    if action in ("submit", "submit_next") and nurse_id:
        if is_revision:
            push_notification(
                user_id=nurse_id,
                message=f"{current_user.display_name} has revised diagnosis for case {result.accession_number}.",
                message_th=f"{current_user.display_name} แก้ไขคำวินิจฉัยเคส {result.accession_number}",
                notif_type="revised",
                result_id=result_id,
            )
        else:
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
            .filter(ECGResult.is_deleted == False)
            .filter(ECGResult.id != result_id)
            .filter(ECGResult.status.in_(["RECEIVED", "REVIEWED"]))
            .order_by(ECGResult.received_at.asc())
            .first()
        )
        if next_result:
            response["next_id"] = next_result.id

    return jsonify(response)


@results_bp.route("/<int:result_id>/send-to-pacs", methods=["POST"])
@login_required
def send_to_pacs(result_id):
    """Send an approved ECG result to PACS."""
    result = ECGResult.query.get_or_404(result_id)
    if result.status not in ("APPROVED", "FINALIZED", "COMPLETED"):
        return jsonify({"success": False, "error": "Only approved results can be sent to PACS"}), 400

    from services.store_scu import send_result_to_pacs
    success, message = send_result_to_pacs(result_id, current_app._get_current_object())
    return jsonify({"success": success, "message": message})


@results_bp.route("/<int:result_id>/send-pdf", methods=["POST"])
@login_required
def send_pdf(result_id):
    """Export PDF report to the configured folder."""
    from models import get_setting
    result = ECGResult.query.get_or_404(result_id)
    if result.status not in ("APPROVED", "FINALIZED", "COMPLETED"):
        return jsonify({"success": False, "error": "Only approved results can be exported"}), 400

    export_path = get_setting("export_pdf_path", "")
    if not export_path:
        return jsonify({"success": False, "error": "Export PDF path not configured. Go to Settings > General."}), 400

    if not os.path.isdir(export_path):
        try:
            os.makedirs(export_path, exist_ok=True)
        except Exception as e:
            result.pdf_export_status = "FAILED"
            db.session.commit()
            return jsonify({"success": False, "error": f"Cannot access folder: {e}"}), 500

    if not result.file_path or not os.path.exists(result.file_path):
        result.pdf_export_status = "FAILED"
        db.session.commit()
        return jsonify({"success": False, "error": "DICOM file not found"}), 404

    try:
        ecg_data = parse_dicom_ecg(result.file_path)
        if not ecg_data:
            raise ValueError("Cannot read ECG data")

        from services.ecg_pdf import generate_ecg_pdf
        pdf_buffer = generate_ecg_pdf(ecg_data, db_result=result)

        acc = result.accession_number or "NOACC"
        hn = result.patient.patient_id if result.patient else "unknown"
        name = result.patient.patient_name.replace("^", "_") if result.patient and result.patient.patient_name else "unknown"
        filename = f"{acc}_{hn}_{name}.pdf"
        # Sanitize filename
        filename = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)

        dest = os.path.join(export_path, filename)
        with open(dest, "wb") as f:
            f.write(pdf_buffer.read())

        result.pdf_export_status = "SENT"
        db.session.commit()
        return jsonify({"success": True, "message": f"PDF exported to {dest}"})
    except Exception as e:
        result.pdf_export_status = "FAILED"
        db.session.commit()
        return jsonify({"success": False, "error": str(e)}), 500


@results_bp.route("/<int:result_id>/send-hl7", methods=["POST"])
@login_required
def send_hl7(result_id):
    """Export HL7 XML file to the configured folder."""
    from models import get_setting
    result = ECGResult.query.get_or_404(result_id)
    if result.status not in ("APPROVED", "FINALIZED", "COMPLETED"):
        return jsonify({"success": False, "error": "Only approved results can be exported"}), 400

    export_path = get_setting("export_hl7_path", "")
    if not export_path:
        return jsonify({"success": False, "error": "Export HL7 path not configured. Go to Settings > General."}), 400

    if not os.path.isdir(export_path):
        try:
            os.makedirs(export_path, exist_ok=True)
        except Exception as e:
            result.hl7_export_status = "FAILED"
            db.session.commit()
            return jsonify({"success": False, "error": f"Cannot access folder: {e}"}), 500

    if not result.file_path or not os.path.exists(result.file_path):
        result.hl7_export_status = "FAILED"
        db.session.commit()
        return jsonify({"success": False, "error": "DICOM file not found"}), 404

    try:
        ecg_data = parse_dicom_ecg(result.file_path)
        if not ecg_data:
            raise ValueError("Cannot read ECG data")

        from services.ecg_hl7 import generate_ecg_hl7
        xml_str = generate_ecg_hl7(ecg_data, db_result=result)

        acc = result.accession_number or "NOACC"
        hn = result.patient.patient_id if result.patient else "unknown"
        name = result.patient.patient_name.replace("^", "_") if result.patient and result.patient.patient_name else "unknown"
        filename = f"{acc}_{hn}_{name}.xml"
        filename = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)

        dest = os.path.join(export_path, filename)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(xml_str)

        result.hl7_export_status = "SENT"
        db.session.commit()
        return jsonify({"success": True, "message": f"HL7 exported to {dest}"})
    except Exception as e:
        result.hl7_export_status = "FAILED"
        db.session.commit()
        return jsonify({"success": False, "error": str(e)}), 500


@results_bp.route("/<int:result_id>/delete", methods=["POST"])
@login_required
def delete_result(result_id):
    """Delete an ECG result. Admin/IT can delete any; nurse can only delete RECEIVED."""
    role = current_user.role
    if role not in ("admin", "it_admin", "nurse"):
        return jsonify({"success": False, "error": "Not authorised | ไม่มีสิทธิ์"}), 403

    result = ECGResult.query.get_or_404(result_id)

    # Nurse can only delete RECEIVED
    if role == "nurse" and result.status != "RECEIVED":
        return jsonify({
            "success": False,
            "error": "Nurses can only delete RECEIVED results | พยาบาลลบได้เฉพาะผลที่ยังไม่ถูกวินิจฉัย",
        }), 400

    accession = result.accession_number or "-"
    now = datetime.now()

    # Soft delete: mark as deleted, keep all records
    result.is_deleted = True
    result.deleted_at = now
    result.deleted_by_id = current_user.id

    # Archive DICOM file (move to archive/ subfolder)
    import shutil
    file_archived = False
    if result.file_path and os.path.exists(result.file_path):
        archive_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dicom_archive")
        os.makedirs(archive_dir, exist_ok=True)
        archive_path = os.path.join(archive_dir, os.path.basename(result.file_path))
        try:
            shutil.move(result.file_path, archive_path)
            result.original_file_path = result.file_path
            result.file_path = archive_path
            file_archived = True
        except OSError:
            pass  # archive is best-effort

    # Release assignment/lock so it doesn't affect stats
    result.locked_by_id = None
    result.locked_at = None
    result.assignment_expires_at = None

    # Reset linked worklist item back to SCHEDULED
    if result.worklist_id:
        wl = WorklistItem.query.get(result.worklist_id)
        if wl and wl.status == "COMPLETED":
            wl.status = "SCHEDULED"

    # Log the deletion in AssignmentLog (keep existing logs intact)
    db.session.add(AssignmentLog(
        ecg_result_id=result_id,
        action="deleted",
        actor_id=current_user.id,
        notes=f"Status was {result.status}" + (" (PACS sent)" if result.pacs_send_status == "SENT" else ""),
    ))

    db.session.commit()

    msg = f"Result {accession} deleted"
    if file_archived:
        msg += " (DICOM archived)"
    msg += f" | ลบผลตรวจ {accession} แล้ว"
    if file_archived:
        msg += " (ไฟล์ถูกเก็บถาวร)"

    return jsonify({"success": True, "message": msg})


@results_bp.route("/<int:result_id>/reset-status", methods=["POST"])
@login_required
def reset_status(result_id):
    """Reset a stuck IN_REVIEW case back to RECEIVED (nurse/admin only)."""
    if current_user.role not in ("admin", "it_admin", "nurse"):
        return jsonify({"success": False, "error": "Not authorised"}), 403

    result = ECGResult.query.get_or_404(result_id)
    if result.status != "IN_REVIEW":
        return jsonify({"success": False, "error": "Only IN_REVIEW cases can be reset"}), 400

    result.status = "RECEIVED"
    result.locked_by_id = None
    result.locked_at = None
    result.assigned_to_id = None
    result.assignment_expires_at = None
    db.session.add(AssignmentLog(
        ecg_result_id=result_id, action="reset", actor_id=current_user.id
    ))
    db.session.commit()

    return jsonify({"success": True, "message": "Case reset to RECEIVED | เคสถูกรีเซ็ตเป็น RECEIVED"})


@results_bp.route("/<int:result_id>/finalize", methods=["POST"])
@login_required
def finalize_result(result_id):
    """Finalize an APPROVED case — locks it permanently (nurse/admin only)."""
    if current_user.role not in ("admin", "it_admin", "nurse"):
        return jsonify({"success": False, "error": "Not authorised | ไม่มีสิทธิ์"}), 403

    result = ECGResult.query.get_or_404(result_id)
    if result.is_deleted:
        return jsonify({"success": False, "error": "Result is deleted"}), 400

    if result.status != "APPROVED":
        return jsonify({
            "success": False,
            "error": "Only APPROVED results can be finalized | Finalize ได้เฉพาะผลที่ Approved แล้ว",
        }), 400

    result.status = "FINALIZED"
    result.locked_by_id = None
    result.locked_at = None
    result.assignment_expires_at = None

    db.session.add(AssignmentLog(
        ecg_result_id=result_id,
        action="finalized",
        actor_id=current_user.id,
        notes=f"Finalized by {current_user.display_name}",
    ))
    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Result finalized | ผลตรวจ {result.accession_number} ถูก Finalize แล้ว",
    })


# ---------------------------------------------------------------------------
# Deleted Results (Trash) — Admin only
# ---------------------------------------------------------------------------
@results_bp.route("/api/deleted")
@login_required
def api_deleted():
    """Return deleted ECG results for admin trash view."""
    if current_user.role not in ("admin", "it_admin"):
        return jsonify([])

    deleted = (
        ECGResult.query
        .filter(ECGResult.is_deleted == True)
        .order_by(ECGResult.deleted_at.desc())
        .all()
    )

    data = []
    for r in deleted:
        data.append({
            "id": r.id,
            "accession_number": r.accession_number or "-",
            "patient_name": r.patient.patient_name if r.patient else "-",
            "patient_id": r.patient.patient_id if r.patient else "-",
            "status": r.status,
            "deleted_at": r.deleted_at.strftime("%d/%m/%Y %H:%M") if r.deleted_at else "-",
            "deleted_by": r.deleted_by.display_name if r.deleted_by else "-",
            "diagnosed_by": r.diagnosed_by or "-",
            "pacs_sent": r.pacs_send_status == "SENT",
        })

    return jsonify(data)


@results_bp.route("/<int:result_id>/restore", methods=["POST"])
@login_required
def restore_result(result_id):
    """Restore a soft-deleted ECG result (admin only)."""
    if current_user.role not in ("admin", "it_admin"):
        return jsonify({"success": False, "error": "Not authorised"}), 403

    result = ECGResult.query.get_or_404(result_id)
    if not result.is_deleted:
        return jsonify({"success": False, "error": "Result is not deleted"}), 400

    import shutil
    # Restore DICOM file from archive if applicable
    if result.original_file_path:
        if result.file_path and os.path.exists(result.file_path):
            os.makedirs(os.path.dirname(result.original_file_path), exist_ok=True)
            try:
                shutil.move(result.file_path, result.original_file_path)
                result.file_path = result.original_file_path
            except OSError:
                pass
        result.original_file_path = None

    result.is_deleted = False
    result.deleted_at = None
    result.deleted_by_id = None

    db.session.add(AssignmentLog(
        ecg_result_id=result_id,
        action="restored",
        actor_id=current_user.id,
    ))
    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Result {result.accession_number} restored | กู้คืนผลตรวจ {result.accession_number} แล้ว",
    })


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

    for fname in os.listdir(directory):
        if not fname.lower().endswith(".dcm"):
            continue

        filepath = os.path.join(directory, fname)
        if not os.path.isfile(filepath):
            continue

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

        # Extract study date/time from DICOM tags
        study_dt = None
        acq_dt_str = str(getattr(ds, "AcquisitionDateTime", "") or "").strip()
        sd_str = str(getattr(ds, "StudyDate", "") or "").strip()
        st_str = str(getattr(ds, "StudyTime", "") or "").strip()
        try:
            if acq_dt_str and len(acq_dt_str) >= 14:
                study_dt = datetime.strptime(acq_dt_str[:14], "%Y%m%d%H%M%S")
            elif sd_str and len(sd_str) == 8:
                if st_str and len(st_str) >= 6:
                    study_dt = datetime.strptime(sd_str + st_str[:6], "%Y%m%d%H%M%S")
                else:
                    study_dt = datetime.strptime(sd_str, "%Y%m%d")
        except (ValueError, TypeError):
            study_dt = None

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
            study_datetime=study_dt,
            status="RECEIVED",
        )
        db.session.add(result)
        count += 1

    db.session.commit()
    return count
