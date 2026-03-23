from datetime import date, datetime, timedelta

from flask import Flask
from flask_login import LoginManager

from config import Config
from extensions import socketio, scheduler
from models import db, User, Patient, WorklistItem


def _check_assignment_timeouts(flask_app):
    """
    Background job: runs every 60 seconds.
    Finds expired assignments and auto-unlocks them back to the central pool.
    """
    from models import ECGResult, AssignmentLog, get_setting

    with flask_app.app_context():
        now = datetime.now()
        timeout_minutes = int(get_setting("assignment_timeout_minutes", 30))
        expired = (
            ECGResult.query
            .filter(ECGResult.assigned_to_id.isnot(None))
            .filter(ECGResult.status == "RECEIVED")
            .filter(ECGResult.assignment_expires_at <= now)
            .all()
        )

        for result in expired:
            doctor_id = result.assigned_to_id

            # If doctor is actively on the case (has lock), skip — they are mid-diagnosis
            if result.locked_by_id is not None:
                continue

            # Find the nurse who originally assigned this case
            last_log = (
                AssignmentLog.query
                .filter_by(ecg_result_id=result.id, action="assigned")
                .order_by(AssignmentLog.timestamp.desc())
                .first()
            )
            assigner_id = last_log.actor_id if last_log else None

            # Log the timeout
            db.session.add(AssignmentLog(
                ecg_result_id=result.id,
                action="timeout",
                target_id=doctor_id,
                notes=f"Auto-expired at {now.strftime('%Y-%m-%d %H:%M:%S')}",
            ))

            # Clear assignment and lock; revert IN_REVIEW → RECEIVED
            result.assigned_to_id        = None
            result.assigned_at           = None
            result.assignment_expires_at = None
            result.locked_by_id          = None
            result.locked_at             = None
            if result.status == "IN_REVIEW":
                result.status = "RECEIVED"
            db.session.commit()

            # Notify doctor
            from routes.notifications import push_notification
            push_notification(
                user_id=doctor_id,
                message=f"Case {result.accession_number} has been returned to the unassigned pool ({timeout_minutes}-minute timeout).",
                message_th=f"เคส {result.accession_number} ถูกส่งคืนคิวกลาง (หมดเวลา {timeout_minutes} นาที)",
                notif_type="timeout",
                result_id=result.id,
            )

            # Notify nurse who assigned
            if assigner_id:
                push_notification(
                    user_id=assigner_id,
                    message=f"Case {result.accession_number} was not diagnosed within {timeout_minutes} minutes and has been returned to the unassigned pool.",
                    message_th=f"เคส {result.accession_number} ยังไม่ถูกวินิจฉัยภายใน {timeout_minutes} นาที จึงถูกส่งคืนคิวกลาง",
                    notif_type="timeout",
                    result_id=result.id,
                )


def create_app():
    from services.dicom_helpers import stable_uid_from_text

    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize extensions
    db.init_app(app)

    socketio.init_app(
        app,
        async_mode="threading",
        cors_allowed_origins="*",
        logger=False,
        engineio_logger=False,
        transports=["polling"],
    )

    app.config["SCHEDULER_API_ENABLED"] = False
    scheduler.init_app(app)
    scheduler.start()
    scheduler.add_job(
        id="check_assignment_timeouts",
        func=_check_assignment_timeouts,
        args=[app],
        trigger="interval",
        seconds=60,
        replace_existing=True,
    )

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "กรุณาเข้าสู่ระบบก่อนใช้งาน"
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Register blueprints
    from routes.auth import auth_bp
    from routes.dashboard import dashboard_bp
    from routes.worklist import worklist_bp
    from routes.results import results_bp
    from routes.ecg_tests import ecg_tests_bp
    from routes.notifications import notifications_bp
    from routes.assignment import assignment_bp
    from routes.settings import settings_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(worklist_bp)
    app.register_blueprint(results_bp)
    app.register_blueprint(ecg_tests_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(assignment_bp)
    app.register_blueprint(settings_bp)

    # Create tables and seed data
    with app.app_context():
        db.create_all()
        _seed_default_data(stable_uid_from_text)

    return app


def _seed_default_data(stable_uid_from_text=None):
    """Create default users and sample worklist data if DB is empty."""
    # Default admin user
    if not User.query.filter_by(username="admin").first():
        admin = User(username="admin", display_name="ผู้ดูแลระบบ", role="admin")
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()
        print("[Seed] Created default admin user (admin/admin123)")

    # Demo role users
    role_seeds = [
        {"username": "nurse01",   "display_name": "พยาบาล สมใจ",    "role": "nurse",    "password": "nurse123"},
        {"username": "doctor01",  "display_name": "นพ. วิชัย ใจดี", "role": "doctor",   "password": "doctor123"},
        {"username": "doctor02",  "display_name": "นพ. สมชาย เก่ง", "role": "doctor",   "password": "doctor123"},
        {"username": "viewer01",  "display_name": "Viewer User",     "role": "viewer",   "password": "viewer123"},
        {"username": "itadmin01", "display_name": "IT Admin",        "role": "it_admin", "password": "itadmin123"},
    ]
    for seed in role_seeds:
        if not User.query.filter_by(username=seed["username"]).first():
            u = User(username=seed["username"], display_name=seed["display_name"], role=seed["role"])
            u.set_password(seed["password"])
            db.session.add(u)
    db.session.commit()

    # Sample patients and worklist items
    if Patient.query.count() == 0 and stable_uid_from_text:
        samples = [
            {
                "patient": {"patient_id": "HN000004", "patient_name": "SAELI^KOMSAN", "sex": "M", "birth_date": "19680512"},
                "worklist": {
                    "accession_number": "ACC20260004", "requested_procedure_id": "RP1003",
                    "requested_procedure_desc": "ECG Pre-op", "admission_id": "ADM20260004",
                    "requested_procedure_priority": "ROUTINE",
                    "scheduled_station_ae": "CP150", "scheduled_station_name": "ECG-ROOM1",
                    "modality": "ECG", "sps_id": "SPS1003", "sps_desc": "ECG Pre-operation",
                    "scheduled_time": "110000",
                    "ordering_department": "Surgery", "ordering_physician": "Dr. Anupong",
                    "patient_source": "Inpatient", "bed_number": "5A-12",
                },
            },
            {
                "patient": {"patient_id": "HN000005", "patient_name": "NARIN^SAENGCHAN", "sex": "M", "birth_date": "19790322"},
                "worklist": {
                    "accession_number": "ACC20260005", "requested_procedure_id": "RP1004",
                    "requested_procedure_desc": "ECG Annual Checkup", "admission_id": "ADM20260005",
                    "requested_procedure_priority": "ROUTINE",
                    "scheduled_station_ae": "CP150", "scheduled_station_name": "ECG-ROOM1",
                    "modality": "ECG", "sps_id": "SPS1004", "sps_desc": "ECG Checkup",
                    "scheduled_time": "113000",
                    "ordering_department": "Health Check", "ordering_physician": "Dr. Siriporn",
                    "patient_source": "Health Check",
                },
            },
            {
                "patient": {"patient_id": "HN000006", "patient_name": "SUPAN^WONGSA", "sex": "F", "birth_date": "19840514"},
                "worklist": {
                    "accession_number": "ACC20260006", "requested_procedure_id": "RP1005",
                    "requested_procedure_desc": "ECG Chest Pain", "admission_id": "ADM20260006",
                    "requested_procedure_priority": "URGENT",
                    "scheduled_station_ae": "CP150", "scheduled_station_name": "ECG-ROOM2",
                    "modality": "ECG", "sps_id": "SPS1005", "sps_desc": "ECG Chest Pain",
                    "scheduled_time": "130000",
                    "ordering_department": "ER", "ordering_physician": "Dr. Pattanapong",
                    "patient_source": "Emergency", "clinical_info": "Chest pain, SOB",
                },
            },
            {
                "patient": {"patient_id": "HN000007", "patient_name": "KANYA^PHROMDEE", "sex": "F", "birth_date": "19951109"},
                "worklist": {
                    "accession_number": "ACC20260007", "requested_procedure_id": "RP1006",
                    "requested_procedure_desc": "ECG Pregnancy Screening", "admission_id": "ADM20260007",
                    "requested_procedure_priority": "ROUTINE",
                    "scheduled_station_ae": "CP150", "scheduled_station_name": "ECG-ROOM2",
                    "modality": "ECG", "sps_id": "SPS1006", "sps_desc": "ECG Screening",
                    "scheduled_time": "143000",
                    "ordering_department": "OPD", "ordering_physician": "Dr. Kamolwan",
                    "patient_source": "Outpatient",
                },
            },
            {
                "patient": {"patient_id": "HN000008", "patient_name": "WICHIT^BOONMA", "sex": "M", "birth_date": "19560218"},
                "worklist": {
                    "accession_number": "ACC20260008", "requested_procedure_id": "RP1007",
                    "requested_procedure_desc": "ECG Post MI Follow-up", "admission_id": "ADM20260008",
                    "requested_procedure_priority": "URGENT",
                    "scheduled_station_ae": "CP150", "scheduled_station_name": "ECG-ROOM1",
                    "modality": "ECG", "sps_id": "SPS1007", "sps_desc": "ECG Post MI",
                    "scheduled_time": "153000",
                    "ordering_department": "Cardiology", "ordering_physician": "Dr. Somkiat",
                    "patient_source": "Inpatient", "bed_number": "CCU-3",
                    "clinical_info": "Post MI day 5, follow-up",
                },
            },
            {
                "patient": {"patient_id": "HN000009", "patient_name": "SOMCHAI^KAEWDEE", "sex": "M", "birth_date": "19691201"},
                "worklist": {
                    "accession_number": "ACC20260009", "requested_procedure_id": "RP1008",
                    "requested_procedure_desc": "ECG Hypertension Follow-up", "admission_id": "ADM20260009",
                    "requested_procedure_priority": "ROUTINE",
                    "scheduled_station_ae": "CP150", "scheduled_station_name": "ECG-ROOM3",
                    "modality": "ECG", "sps_id": "SPS1008", "sps_desc": "ECG HT Follow-up",
                    "scheduled_time": "160000",
                    "ordering_department": "Internal Medicine", "ordering_physician": "Dr. Prasit",
                    "patient_source": "Outpatient",
                },
            },
        ]

        today = date.today().strftime("%Y%m%d")
        for s in samples:
            p = Patient(**s["patient"])
            db.session.add(p)
            db.session.flush()
            wl_data = s["worklist"]
            wl = WorklistItem(
                patient_id=p.id,
                scheduled_date=today,
                study_instance_uid=stable_uid_from_text(wl_data["accession_number"]),
                status="SCHEDULED",
                **wl_data,
            )
            db.session.add(wl)

        db.session.commit()
        print(f"[Seed] Created {len(samples)} sample patients and worklist items")

    # Default system settings
    from models import SystemSetting
    defaults = [
        {
            "key": "assignment_timeout_minutes",
            "value": "30",
            "label": "Assignment Timeout (minutes)",
            "description": "เวลาที่แพทย์มีในการวินิจฉัยเคสก่อนที่ระบบจะคืนกลับสู่คิวส่วนกลาง",
        },
    ]
    for d in defaults:
        if not SystemSetting.query.filter_by(key=d["key"]).first():
            db.session.add(SystemSetting(**d))
    db.session.commit()


def start_dicom_servers(app):
    """Start DICOM MWL and Store SCP servers as daemon threads."""
    from services.mwl_server import MWLServer
    from services.store_scp import StoreSCP

    mwl = MWLServer(
        flask_app=app,
        ae_title=app.config["MWL_AE_TITLE"],
        port=app.config["MWL_PORT"],
    )
    mwl.start()

    store = StoreSCP(
        flask_app=app,
        ae_title=app.config["STORE_AE_TITLE"],
        port=app.config["STORE_PORT"],
        storage_dir=app.config["DICOM_STORAGE_DIR"],
    )
    store.start()


if __name__ == "__main__":
    app = create_app()
    start_dicom_servers(app)

    print("\n" + "=" * 60)
    print("  ECG Management System")
    print("  Web:   http://localhost:5000")
    print(f"  MWL:   Port {app.config['MWL_PORT']} (AE: {app.config['MWL_AE_TITLE']})")
    print(f"  Store: Port {app.config['STORE_PORT']} (AE: {app.config['STORE_AE_TITLE']})")
    print("  Login: admin/admin123  |  nurse01/nurse123")
    print("         doctor01/doctor123  |  doctor02/doctor123")
    print("=" * 60 + "\n")

    socketio.run(app, host="0.0.0.0", port=5000, debug=True, use_reloader=False, allow_unsafe_werkzeug=True)
