from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from models import db, ECGResult, User, AssignmentLog, get_setting
from routes.notifications import push_notification, push_case_unassigned
from utils.decorators import nurse_required, doctor_required

assignment_bp = Blueprint("assignment", __name__, url_prefix="/api/assignment")


@assignment_bp.route("/doctors")
@login_required
@nurse_required
def list_doctors():
    """Return active assignable users for the nurse's assign dropdown."""
    doctors = (
        User.query
        .filter(User.is_active_user == True, User.can_be_assigned == True)
        .order_by(User.display_name)
        .all()
    )
    return jsonify([{"id": d.id, "display_name": d.display_name} for d in doctors])


@assignment_bp.route("/<int:result_id>/assign", methods=["POST"])
@login_required
@nurse_required
def assign(result_id):
    """Nurse assigns a case to a specific doctor."""
    result = ECGResult.query.get_or_404(result_id)
    data = request.get_json(silent=True) or {}
    doctor_id = data.get("doctor_id")

    if not doctor_id:
        return jsonify({"success": False, "error": "doctor_id required"}), 400

    doctor = User.query.get(doctor_id)
    if not doctor or doctor.role not in ("doctor", "admin"):
        return jsonify({"success": False, "error": "Invalid doctor"}), 400

    if result.assigned_to_id and result.assigned_to_id != doctor_id:
        return jsonify({"success": False, "error": "Case already assigned to another doctor"}), 409

    timeout = int(get_setting("assignment_timeout_minutes", 30))
    now = datetime.now()
    result.assigned_to_id        = doctor_id
    result.assigned_at           = now
    result.assignment_expires_at = now + timedelta(minutes=timeout)

    db.session.add(AssignmentLog(
        ecg_result_id=result_id,
        action="assigned",
        actor_id=current_user.id,
        target_id=doctor_id,
    ))
    db.session.commit()

    push_notification(
        user_id=doctor_id,
        message=f"Case {result.accession_number} has been assigned to you by {current_user.display_name}.",
        message_th=f"เคส {result.accession_number} ถูกมอบหมายให้คุณวินิจฉัย โดย {current_user.display_name}",
        notif_type="assignment",
        result_id=result_id,
    )

    return jsonify({
        "success": True,
        "assigned_to": doctor.display_name,
        "expires_at": result.assignment_expires_at.strftime("%d/%m/%Y %H:%M"),
    })


@assignment_bp.route("/<int:result_id>/reassign", methods=["POST"])
@login_required
@nurse_required
def reassign(result_id):
    """Nurse reassigns a case directly to a different doctor (single-step)."""
    result = ECGResult.query.get_or_404(result_id)
    data = request.get_json(silent=True) or {}
    new_doctor_id = data.get("doctor_id")

    if not new_doctor_id:
        return jsonify({"success": False, "error": "doctor_id required"}), 400

    new_doctor = User.query.get(new_doctor_id)
    if not new_doctor or new_doctor.role not in ("doctor", "admin"):
        return jsonify({"success": False, "error": "Invalid doctor"}), 400

    old_doctor_id = result.assigned_to_id

    timeout = int(get_setting("assignment_timeout_minutes", 30))
    now = datetime.now()
    result.assigned_to_id        = new_doctor_id
    result.assigned_at           = now
    result.assignment_expires_at = now + timedelta(minutes=timeout)
    result.locked_by_id          = None
    result.locked_at             = None
    if result.status == "IN_REVIEW":
        result.status = "RECEIVED"

    if old_doctor_id:
        db.session.add(AssignmentLog(
            ecg_result_id=result_id,
            action="unassigned",
            actor_id=current_user.id,
            target_id=old_doctor_id,
        ))
    db.session.add(AssignmentLog(
        ecg_result_id=result_id,
        action="assigned",
        actor_id=current_user.id,
        target_id=new_doctor_id,
    ))
    db.session.commit()

    if old_doctor_id and old_doctor_id != new_doctor_id:
        push_notification(
            user_id=old_doctor_id,
            message=f"Case {result.accession_number} has been reassigned to another doctor by {current_user.display_name}.",
            message_th=f"เคส {result.accession_number} ถูกมอบหมายให้แพทย์ท่านอื่นโดย {current_user.display_name}",
            notif_type="unassigned",
            result_id=result_id,
        )
        # Force-redirect old doctor's detail page if still open
        push_case_unassigned(
            user_id=old_doctor_id,
            result_id=result_id,
            message=f"Case {result.accession_number} has been reassigned to another doctor by {current_user.display_name}.",
            message_th=f"เคส {result.accession_number} ถูกมอบหมายให้แพทย์ท่านอื่นโดย {current_user.display_name}",
        )

    push_notification(
        user_id=new_doctor_id,
        message=f"Case {result.accession_number} has been assigned to you by {current_user.display_name}.",
        message_th=f"เคส {result.accession_number} ถูกมอบหมายให้คุณวินิจฉัย โดย {current_user.display_name}",
        notif_type="assignment",
        result_id=result_id,
    )

    return jsonify({
        "success": True,
        "assigned_to": new_doctor.display_name,
        "expires_at": result.assignment_expires_at.strftime("%d/%m/%Y %H:%M"),
    })


@assignment_bp.route("/<int:result_id>/unassign", methods=["POST"])
@login_required
@nurse_required
def unassign(result_id):
    """Nurse manually returns a case to the central pool."""
    result = ECGResult.query.get_or_404(result_id)
    prev_doctor_id = result.assigned_to_id

    result.assigned_to_id        = None
    result.assigned_at           = None
    result.assignment_expires_at = None
    result.locked_by_id          = None
    result.locked_at             = None
    if result.status == "IN_REVIEW":
        result.status = "RECEIVED"

    db.session.add(AssignmentLog(
        ecg_result_id=result_id,
        action="unassigned",
        actor_id=current_user.id,
        target_id=prev_doctor_id,
    ))
    db.session.commit()

    if prev_doctor_id:
        push_notification(
            user_id=prev_doctor_id,
            message=f"Case {result.accession_number} has been unassigned by {current_user.display_name}.",
            message_th=f"เคส {result.accession_number} ถูกยกเลิกการมอบหมายโดย {current_user.display_name}",
            notif_type="unassigned",
            result_id=result_id,
        )
        # Force-redirect doctor's detail page if still open
        push_case_unassigned(
            user_id=prev_doctor_id,
            result_id=result_id,
            message=f"Case {result.accession_number} has been unassigned by {current_user.display_name}.",
            message_th=f"เคส {result.accession_number} ถูกยกเลิกการมอบหมายโดย {current_user.display_name}",
        )

    return jsonify({"success": True})


@assignment_bp.route("/<int:result_id>/lock", methods=["POST"])
@login_required
@doctor_required
def lock(result_id):
    """Doctor acquires the concurrency lock when entering the detail view."""
    result = ECGResult.query.get_or_404(result_id)

    # Visibility guard
    if result.assigned_to_id and result.assigned_to_id != current_user.id:
        return jsonify({"success": False, "error": "Case assigned to another doctor"}), 403

    # Already locked by someone else?
    if result.locked_by_id and result.locked_by_id != current_user.id:
        locker = User.query.get(result.locked_by_id)
        return jsonify({
            "success": False,
            "locked_by": locker.display_name if locker else "another user",
            "error": "Case is currently being edited",
        }), 409

    now = datetime.now()
    result.locked_by_id = current_user.id
    result.locked_at    = now

    # Extend assignment expiry whenever doctor actively opens the case
    if result.assigned_to_id == current_user.id:
        timeout = int(get_setting("assignment_timeout_minutes", 30))
        result.assignment_expires_at = now + timedelta(minutes=timeout)

    db.session.add(AssignmentLog(
        ecg_result_id=result_id,
        action="locked",
        actor_id=current_user.id,
    ))
    db.session.commit()

    return jsonify({"success": True, "locked_at": now.strftime("%d/%m/%Y %H:%M")})


@assignment_bp.route("/<int:result_id>/accept", methods=["POST"])
@login_required
@doctor_required
def accept(result_id):
    """Doctor confirms they will handle the case (extends expiry + notifies nurse)."""
    result = ECGResult.query.get_or_404(result_id)

    if result.assigned_to_id != current_user.id:
        return jsonify({"success": False, "error": "Not assigned to you"}), 403

    # Already IN_REVIEW by this doctor — no-op to prevent timer reset
    if result.status == "IN_REVIEW" and result.locked_by_id == current_user.id:
        return jsonify({
            "success": True,
            "already_accepted": True,
            "expires_at": result.assignment_expires_at.strftime("%d/%m/%Y %H:%M") if result.assignment_expires_at else "",
            "expires_at_iso": result.assignment_expires_at.isoformat() if result.assignment_expires_at else "",
        })

    timeout = int(get_setting("assignment_timeout_minutes", 30))
    now = datetime.now()
    result.assignment_expires_at = now + timedelta(minutes=timeout)

    db.session.add(AssignmentLog(
        ecg_result_id=result_id,
        action="accepted",
        actor_id=current_user.id,
    ))
    db.session.commit()

    # Notify the nurse who assigned this case
    last_log = (
        AssignmentLog.query
        .filter_by(ecg_result_id=result_id, action="assigned")
        .order_by(AssignmentLog.timestamp.desc())
        .first()
    )
    if last_log and last_log.actor_id:
        push_notification(
            user_id=last_log.actor_id,
            message=f"{current_user.display_name} has accepted case {result.accession_number}.",
            message_th=f"{current_user.display_name} รับเคส {result.accession_number} แล้ว",
            notif_type="accepted",
            result_id=result_id,
        )

    return jsonify({
        "success": True,
        "expires_at": result.assignment_expires_at.strftime("%d/%m/%Y %H:%M"),
        "expires_at_iso": result.assignment_expires_at.isoformat(),
    })


@assignment_bp.route("/<int:result_id>/reject", methods=["POST"])
@login_required
@doctor_required
def reject(result_id):
    """Doctor declines the case and returns it to the unassigned pool."""
    result = ECGResult.query.get_or_404(result_id)

    if result.assigned_to_id != current_user.id:
        return jsonify({"success": False, "error": "Not assigned to you"}), 403

    # Find the nurse who assigned this case
    last_log = (
        AssignmentLog.query
        .filter_by(ecg_result_id=result_id, action="assigned")
        .order_by(AssignmentLog.timestamp.desc())
        .first()
    )
    assigner_id = last_log.actor_id if last_log else None

    result.assigned_to_id        = None
    result.assigned_at           = None
    result.assignment_expires_at = None
    result.locked_by_id          = None
    result.locked_at             = None
    if result.status == "IN_REVIEW":
        result.status = "RECEIVED"

    db.session.add(AssignmentLog(
        ecg_result_id=result_id,
        action="rejected",
        actor_id=current_user.id,
    ))
    db.session.commit()

    if assigner_id:
        push_notification(
            user_id=assigner_id,
            message=f"{current_user.display_name} rejected case {result.accession_number}. Please reassign.",
            message_th=f"{current_user.display_name} ปฏิเสธเคส {result.accession_number} กรุณามอบหมายใหม่",
            notif_type="rejected",
            result_id=result_id,
        )

    return jsonify({"success": True})


@assignment_bp.route("/<int:result_id>/unlock", methods=["POST"])
@login_required
def unlock(result_id):
    """Release the concurrency lock (called on page leave via sendBeacon)."""
    result = ECGResult.query.get_or_404(result_id)

    if result.locked_by_id != current_user.id:
        return jsonify({"success": False, "error": "Not your lock"}), 403

    result.locked_by_id = None
    result.locked_at    = None

    db.session.add(AssignmentLog(
        ecg_result_id=result_id,
        action="unlocked",
        actor_id=current_user.id,
    ))
    db.session.commit()

    return jsonify({"success": True})
