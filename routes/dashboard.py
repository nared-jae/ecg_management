import socket
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, jsonify, current_app
from flask_login import login_required, current_user

from models import db, WorklistItem, ECGResult, Patient, AssignmentLog

dashboard_bp = Blueprint("dashboard", __name__)


def _check_port(port):
    """Quick check if a TCP port is listening on localhost."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(("127.0.0.1", int(port)))
            return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


def _get_dicom_status():
    """Return dict with MWL and Store SCP online status."""
    return {
        "mwl": _check_port(current_app.config.get("MWL_PORT", 6701)),
        "store": _check_port(current_app.config.get("STORE_PORT", 6702)),
    }


@dashboard_bp.route("/")
@login_required
def index():
    if current_user.role == "doctor":
        return _doctor_dashboard()

    today = datetime.now().strftime("%Y%m%d")

    stats = {
        "total_patients": Patient.query.count(),
        "today_worklist": WorklistItem.query.filter_by(scheduled_date=today).count(),
        "pending_worklist": WorklistItem.query.filter_by(status="SCHEDULED").count(),
        "completed_today": WorklistItem.query.filter_by(status="COMPLETED", scheduled_date=today).count(),
        "total_results": ECGResult.query.filter(ECGResult.is_deleted == False).count(),
        "pending_review": ECGResult.query.filter(ECGResult.is_deleted == False, ECGResult.status == "RECEIVED").count(),
    }

    recent_worklist = (
        WorklistItem.query
        .order_by(WorklistItem.created_at.desc())
        .limit(10)
        .all()
    )

    recent_results = (
        ECGResult.query
        .filter(ECGResult.is_deleted == False)
        .order_by(ECGResult.received_at.desc())
        .limit(10)
        .all()
    )

    dicom_status = _get_dicom_status()

    return render_template(
        "dashboard.html",
        stats=stats,
        recent_worklist=recent_worklist,
        recent_results=recent_results,
        dicom_status=dicom_status,
    )


@dashboard_bp.route("/api/data")
@login_required
def api_data():
    """JSON API for dashboard auto-refresh."""
    today = datetime.now().strftime("%Y%m%d")

    stats = {
        "total_patients": Patient.query.count(),
        "today_worklist": WorklistItem.query.filter_by(scheduled_date=today).count(),
        "pending_worklist": WorklistItem.query.filter_by(status="SCHEDULED").count(),
        "completed_today": WorklistItem.query.filter_by(status="COMPLETED", scheduled_date=today).count(),
        "total_results": ECGResult.query.filter(ECGResult.is_deleted == False).count(),
        "pending_review": ECGResult.query.filter(ECGResult.is_deleted == False, ECGResult.status == "RECEIVED").count(),
    }

    recent_worklist = (
        WorklistItem.query
        .order_by(WorklistItem.created_at.desc())
        .limit(10)
        .all()
    )

    recent_results = (
        ECGResult.query
        .filter(ECGResult.is_deleted == False)
        .order_by(ECGResult.received_at.desc())
        .limit(10)
        .all()
    )

    return jsonify({
        "stats": stats,
        "recent_worklist": [{
            "patient_id": w.patient.patient_id if w.patient else "",
            "patient_name": w.patient.patient_name if w.patient else "",
            "procedure": w.requested_procedure_desc or "",
            "status": w.status or "",
        } for w in recent_worklist],
        "recent_results": [{
            "patient_id": r.patient.patient_id if r.patient else "-",
            "patient_name": r.patient.patient_name if r.patient else "-",
            "received_at": r.received_at.strftime("%d/%m/%Y %H:%M") if r.received_at else "-",
            "status": r.status or "",
        } for r in recent_results],
        "dicom_status": _get_dicom_status(),
    })


def _doctor_dashboard():
    from models import get_setting
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    _DONE = ("APPROVED", "FINALIZED", "COMPLETED")

    timeout_min = int(get_setting("assignment_timeout_minutes", "30"))

    pending = (
        ECGResult.query
        .filter(ECGResult.is_deleted == False,
                ECGResult.assigned_to_id == current_user.id,
                ECGResult.status.notin_(_DONE))
        .count()
    )

    completed_today = (
        db.session.query(db.func.count(db.distinct(AssignmentLog.ecg_result_id)))
        .filter(AssignmentLog.actor_id == current_user.id,
                AssignmentLog.action == "diagnosed",
                AssignmentLog.timestamp >= today_start)
        .scalar()
    )

    this_month = (
        db.session.query(db.func.count(db.distinct(AssignmentLog.ecg_result_id)))
        .filter(AssignmentLog.actor_id == current_user.id,
                AssignmentLog.action == "diagnosed",
                AssignmentLog.timestamp >= month_start)
        .scalar()
    )

    expiring_soon = (
        ECGResult.query
        .filter(ECGResult.is_deleted == False,
                ECGResult.assigned_to_id == current_user.id,
                ECGResult.status.notin_(_DONE),
                ECGResult.assignment_expires_at.isnot(None),
                ECGResult.assignment_expires_at > now,
                ECGResult.assignment_expires_at <= now + timedelta(minutes=timeout_min))
        .count()
    )

    recent_pending = (
        ECGResult.query
        .filter(ECGResult.is_deleted == False,
                ECGResult.assigned_to_id == current_user.id,
                ECGResult.status.notin_(_DONE))
        .order_by(ECGResult.assignment_expires_at.asc())
        .limit(8)
        .all()
    )

    return render_template(
        "dashboard_doctor.html",
        pending=pending,
        completed_today=completed_today,
        this_month=this_month,
        expiring_soon=expiring_soon,
        expiring_minutes=timeout_min,
        recent_pending=recent_pending,
        now=now,
    )
