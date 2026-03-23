from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, jsonify
from flask_login import login_required, current_user

from models import db, WorklistItem, ECGResult, Patient, AssignmentLog

dashboard_bp = Blueprint("dashboard", __name__)


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
        "total_results": ECGResult.query.count(),
        "pending_review": ECGResult.query.filter_by(status="RECEIVED").count(),
    }

    recent_worklist = (
        WorklistItem.query
        .order_by(WorklistItem.created_at.desc())
        .limit(10)
        .all()
    )

    recent_results = (
        ECGResult.query
        .order_by(ECGResult.received_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "dashboard.html",
        stats=stats,
        recent_worklist=recent_worklist,
        recent_results=recent_results,
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
        "total_results": ECGResult.query.count(),
        "pending_review": ECGResult.query.filter_by(status="RECEIVED").count(),
    }

    recent_worklist = (
        WorklistItem.query
        .order_by(WorklistItem.created_at.desc())
        .limit(10)
        .all()
    )

    recent_results = (
        ECGResult.query
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
            "accession_number": r.accession_number or "-",
            "patient_name": r.patient.patient_name if r.patient else "-",
            "received_at": r.received_at.strftime("%d/%m/%Y %H:%M") if r.received_at else "-",
            "status": r.status or "",
        } for r in recent_results],
    })


def _doctor_dashboard():
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    _DONE = ("APPROVED", "FINALIZED", "COMPLETED")

    pending = (
        ECGResult.query
        .filter(ECGResult.assigned_to_id == current_user.id,
                ECGResult.status.notin_(_DONE))
        .count()
    )

    completed_today = (
        AssignmentLog.query
        .filter(AssignmentLog.actor_id == current_user.id,
                AssignmentLog.action == "diagnosed",
                AssignmentLog.timestamp >= today_start)
        .count()
    )

    this_month = (
        AssignmentLog.query
        .filter(AssignmentLog.actor_id == current_user.id,
                AssignmentLog.action == "diagnosed",
                AssignmentLog.timestamp >= month_start)
        .count()
    )

    expiring_soon = (
        ECGResult.query
        .filter(ECGResult.assigned_to_id == current_user.id,
                ECGResult.assignment_expires_at.isnot(None),
                ECGResult.assignment_expires_at > now,
                ECGResult.assignment_expires_at <= now + timedelta(minutes=5))
        .count()
    )

    recent_pending = (
        ECGResult.query
        .filter(ECGResult.assigned_to_id == current_user.id,
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
        recent_pending=recent_pending,
        now=now,
    )
