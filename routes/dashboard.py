from datetime import datetime

from flask import Blueprint, render_template
from flask_login import login_required

from models import db, WorklistItem, ECGResult, Patient

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    today = datetime.now().strftime("%Y%m%d")

    stats = {
        "total_patients": Patient.query.count(),
        "today_worklist": WorklistItem.query.filter_by(scheduled_date=today).count(),
        "pending_worklist": WorklistItem.query.filter_by(status="SCHEDULED").count(),
        "completed_today": WorklistItem.query.filter_by(status="COMPLETED").count(),
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
