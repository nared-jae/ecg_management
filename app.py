import os
import uuid
from datetime import date, datetime, timedelta

# PyInstaller hidden imports for SocketIO async driver
import engineio.async_drivers.threading  # noqa: F401

from flask import Flask
from flask_login import LoginManager

from config import Config
from extensions import socketio, scheduler
from models import db, User, Patient, WorklistItem, Station


def _auto_sync_mwl(flask_app):
    """
    Background job: runs every 60 seconds.
    Checks if auto-sync is enabled and if the configured interval has elapsed,
    then triggers MWL synchronization.
    """
    import logging
    logger = logging.getLogger("mwl_sync")

    from models import get_setting, SystemSetting, db

    with flask_app.app_context():
        if get_setting("ext_mwl_auto_sync", "false") != "true":
            return
        if not get_setting("ext_mwl_host", ""):
            return

        interval = int(get_setting("ext_mwl_sync_interval", "30"))
        last_sync_str = get_setting("ext_mwl_last_sync_at", "")

        if last_sync_str:
            try:
                last_sync = datetime.strptime(last_sync_str, "%Y-%m-%d %H:%M:%S")
                elapsed = (datetime.now() - last_sync).total_seconds() / 60
                if elapsed < interval:
                    logger.debug("AUTO-SYNC SKIP | elapsed=%.1f min < interval=%d min | last_sync=%s",
                                 elapsed, interval, last_sync_str)
                    return  # Not yet time to sync
            except ValueError:
                logger.warning("AUTO-SYNC | invalid last_sync_at=%r, proceeding with sync", last_sync_str)

        logger.info("AUTO-SYNC TRIGGERED | interval=%d min | last_sync=%s", interval, last_sync_str or "(never)")

    from services.mwl_scu import sync_from_external_mwl
    result = sync_from_external_mwl(flask_app)
    if result.get("success"):
        logger.info("AUTO-SYNC DONE | created=%d, updated=%d, skipped=%d",
                     result["created"], result["updated"], result.get("skipped", 0))
    elif result.get("error"):
        logger.error("AUTO-SYNC ERROR | %s", result["error"])


def _check_assignment_timeouts(flask_app):
    """
    Background job: runs every 60 seconds.
    Finds expired assignments and auto-unlocks them back to the central pool.
    """
    from models import ECGResult, AssignmentLog, get_setting

    with flask_app.app_context():
        # Skip if expiry is disabled
        if get_setting("assignment_expiry_enabled", "true") != "true":
            return

        now = datetime.now()
        timeout_minutes = int(get_setting("assignment_timeout_minutes", 30))
        expired = (
            ECGResult.query
            .filter(ECGResult.is_deleted == False)
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
    scheduler.add_job(
        id="auto_sync_mwl",
        func=_auto_sync_mwl,
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
    from routes.patients import patients_bp
    from routes.api import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(worklist_bp)
    app.register_blueprint(results_bp)
    app.register_blueprint(ecg_tests_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(assignment_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(patients_bp)
    app.register_blueprint(api_bp)

    # Create tables and seed data
    with app.app_context():
        db.create_all()
        _auto_migrate(db)
        _seed_default_data(stable_uid_from_text)

    return app


def _auto_migrate(db):
    """Add missing columns to existing tables (SQLite ALTER TABLE)."""
    import sqlalchemy
    inspector = sqlalchemy.inspect(db.engine)
    ecg_cols = [c["name"] for c in inspector.get_columns("ecg_results")]
    if "study_datetime" not in ecg_cols:
        db.session.execute(sqlalchemy.text("ALTER TABLE ecg_results ADD COLUMN study_datetime DATETIME"))
        db.session.commit()
        print("[Migrate] Added study_datetime column to ecg_results")

        # Backfill from DICOM files
        _backfill_study_datetime(db)

    # Users: add display_name_en column
    user_cols = [c["name"] for c in inspector.get_columns("users")]
    if "display_name_en" not in user_cols:
        db.session.execute(sqlalchemy.text("ALTER TABLE users ADD COLUMN display_name_en VARCHAR(120)"))
        db.session.commit()
        print("[Migrate] Added display_name_en column to users")
    if "can_be_assigned" not in user_cols:
        db.session.execute(sqlalchemy.text("ALTER TABLE users ADD COLUMN can_be_assigned BOOLEAN DEFAULT 0"))
        # Auto-enable for existing doctors
        db.session.execute(sqlalchemy.text("UPDATE users SET can_be_assigned = 1 WHERE role = 'doctor'"))
        db.session.commit()
        print("[Migrate] Added can_be_assigned column to users")

    # ECG Results: add export status columns
    result_cols = [c["name"] for c in inspector.get_columns("ecg_results")]
    if "pdf_export_status" not in result_cols:
        db.session.execute(sqlalchemy.text("ALTER TABLE ecg_results ADD COLUMN pdf_export_status VARCHAR(20)"))
        db.session.execute(sqlalchemy.text("ALTER TABLE ecg_results ADD COLUMN hl7_export_status VARCHAR(20)"))
        db.session.commit()
        print("[Migrate] Added pdf_export_status, hl7_export_status columns to ecg_results")

    # WorklistItem: add completed_manually column
    wl_cols = [c["name"] for c in inspector.get_columns("worklist_items")]
    if "completed_manually" not in wl_cols:
        db.session.execute(sqlalchemy.text("ALTER TABLE worklist_items ADD COLUMN completed_manually BOOLEAN DEFAULT 0"))
        db.session.commit()
        print("[Migrate] Added completed_manually column to worklist_items")

    # ECG Results: add soft delete columns
    if "is_deleted" not in result_cols:
        db.session.execute(sqlalchemy.text("ALTER TABLE ecg_results ADD COLUMN is_deleted BOOLEAN DEFAULT 0"))
        db.session.execute(sqlalchemy.text("ALTER TABLE ecg_results ADD COLUMN deleted_at DATETIME"))
        db.session.execute(sqlalchemy.text("ALTER TABLE ecg_results ADD COLUMN deleted_by_id INTEGER REFERENCES users(id)"))
        db.session.execute(sqlalchemy.text("ALTER TABLE ecg_results ADD COLUMN original_file_path VARCHAR(500)"))
        db.session.commit()
        print("[Migrate] Added soft delete columns to ecg_results")

    # Station table (new)
    if "stations" not in inspector.get_table_names():
        from models import Station
        Station.__table__.create(db.engine)
        db.session.commit()
        print("[Migrate] Created stations table")
    else:
        st_cols = [c["name"] for c in inspector.get_columns("stations")]
        if "location" not in st_cols:
            db.session.execute(sqlalchemy.text("ALTER TABLE stations ADD COLUMN location VARCHAR(100)"))
            db.session.commit()
            print("[Migrate] Added location column to stations")


def _backfill_study_datetime(db):
    """Backfill study_datetime from DICOM files for existing records."""
    import os
    from datetime import datetime as dt
    from models import ECGResult
    try:
        import pydicom
    except ImportError:
        return

    results = ECGResult.query.filter(ECGResult.study_datetime.is_(None)).all()
    count = 0
    for r in results:
        if not r.file_path or not os.path.exists(r.file_path):
            continue
        try:
            ds = pydicom.dcmread(r.file_path, force=True)
            acq_dt_str = str(getattr(ds, "AcquisitionDateTime", "") or "").strip()
            sd_str = str(getattr(ds, "StudyDate", "") or "").strip()
            st_str = str(getattr(ds, "StudyTime", "") or "").strip()
            study_dt = None
            if acq_dt_str and len(acq_dt_str) >= 14:
                study_dt = dt.strptime(acq_dt_str[:14], "%Y%m%d%H%M%S")
            elif sd_str and len(sd_str) == 8:
                if st_str and len(st_str) >= 6:
                    study_dt = dt.strptime(sd_str + st_str[:6], "%Y%m%d%H%M%S")
                else:
                    study_dt = dt.strptime(sd_str, "%Y%m%d")
            if study_dt:
                r.study_datetime = study_dt
                count += 1
        except Exception:
            continue
    if count:
        db.session.commit()
        print(f"[Migrate] Backfilled study_datetime for {count} records")


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
        {"username": "nurse01",   "display_name": "พยาบาล สมใจ",       "role": "nurse",    "password": "nurse123"},
        {"username": "doctor01",  "display_name": "นพ. วิชัย ใจดี",    "role": "doctor",   "password": "doctor123"},
        {"username": "doctor02",  "display_name": "นพ. สมชาย เก่ง",    "role": "doctor",   "password": "doctor123"},
        {"username": "cardio01",  "display_name": "นพ. หัวใจ รักษ์ดี",  "role": "cardio",   "password": "cardio123"},
        {"username": "viewer01",  "display_name": "Viewer User",        "role": "viewer",   "password": "viewer123"},
        {"username": "itadmin01", "display_name": "IT Admin",           "role": "it_admin", "password": "itadmin123"},
    ]
    for seed in role_seeds:
        if not User.query.filter_by(username=seed["username"]).first():
            u = User(username=seed["username"], display_name=seed["display_name"], role=seed["role"])
            u.set_password(seed["password"])
            db.session.add(u)
    db.session.commit()

    # Display all user accounts on startup
    all_users = User.query.order_by(User.role, User.username).all()
    print("\n" + "=" * 70)
    print("  USER ACCOUNTS")
    print("=" * 70)
    print(f"  {'USERNAME':<15} {'ROLE':<15} {'DISPLAY NAME':<25} {'STATUS'}")
    print("-" * 70)
    for u in all_users:
        status = "Active" if u.is_active_user else "Disabled"
        print(f"  {u.username:<15} {u.role:<15} {u.display_name:<25} {status}")
    print("=" * 70 + "\n")

    # Default stations
    if Station.query.count() == 0:
        db.session.add(Station(ae_title="CP150", name="ECG-ROOM1", description="Default ECG Station"))
        db.session.commit()
        print("[Seed] Created default station (CP150 / ECG-ROOM1)")

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
        # External MWL Server settings
        {"key": "ext_mwl_host", "value": "", "label": "External MWL Server Host", "description": "Hostname or IP of the external MWL server"},
        {"key": "ext_mwl_port", "value": "104", "label": "External MWL Server Port", "description": "Port of the external MWL server"},
        {"key": "ext_mwl_ae", "value": "MWL", "label": "External MWL AE Title", "description": "AE Title of the external MWL server"},
        {"key": "ext_mwl_local_ae", "value": "ECG_SCU", "label": "Local SCU AE Title (MWL)", "description": "Local AE Title used when querying MWL"},
        {"key": "ext_mwl_auto_sync", "value": "false", "label": "MWL Auto-Sync", "description": "Enable automatic worklist synchronization"},
        {"key": "ext_mwl_sync_interval", "value": "30", "label": "MWL Sync Interval (minutes)", "description": "Interval in minutes between auto-sync runs"},
        {"key": "ext_mwl_last_sync_at", "value": "", "label": "Last MWL Sync", "description": "Timestamp of the last successful MWL sync"},
        # PACS Server settings
        {"key": "pacs_host", "value": "", "label": "PACS Server Host", "description": "Hostname or IP of the PACS server"},
        {"key": "pacs_port", "value": "104", "label": "PACS Server Port", "description": "Port of the PACS server"},
        {"key": "pacs_ae", "value": "PACS", "label": "PACS AE Title", "description": "AE Title of the PACS server"},
        {"key": "pacs_local_ae", "value": "ECG_SCU", "label": "Local SCU AE Title (PACS)", "description": "Local AE Title used when sending to PACS"},
        # Local SCP settings (require restart)
        {"key": "scp_mwl_ae_title", "value": "MWL", "label": "MWL SCP AE Title", "description": "AE Title for the local MWL SCP server (requires restart)"},
        {"key": "scp_mwl_port", "value": "6701", "label": "MWL SCP Port", "description": "Port for the local MWL SCP server (requires restart)"},
        {"key": "scp_store_ae_title", "value": "ECG_STORE", "label": "Store SCP AE Title", "description": "AE Title for the local Store SCP server (requires restart)"},
        {"key": "scp_store_port", "value": "6702", "label": "Store SCP Port", "description": "Port for the local Store SCP server (requires restart)"},
        {"key": "scp_storage_dir", "value": "", "label": "DICOM Storage Directory", "description": "Directory for storing received DICOM files (requires restart)"},
        # Assignment expiry toggle
        {"key": "assignment_expiry_enabled", "value": "true", "label": "Assignment Expiry", "description": "เปิด/ปิด countdown timer สำหรับเคสที่ถูก assign (ถ้าปิด เคสจะเปลี่ยนเป็น IN_REVIEW ทันทีเมื่อ assign)"},
        # Export to Folder
        {"key": "export_pdf_path", "value": "", "label": "Export PDF Path", "description": "โฟลเดอร์สำหรับส่งไฟล์ PDF (เช่น D:\\ecg_export\\pdf หรือ \\\\server\\share\\pdf)"},
        {"key": "export_hl7_path", "value": "", "label": "Export HL7 Path", "description": "โฟลเดอร์สำหรับส่งไฟล์ HL7 XML (เช่น D:\\ecg_export\\hl7 หรือ \\\\server\\share\\hl7)"},
        # API Integration
        {"key": "api_key", "value": str(uuid.uuid4()), "label": "API Key", "description": "API Key for external system integration (HIS)"},
    ]
    for d in defaults:
        if not SystemSetting.query.filter_by(key=d["key"]).first():
            db.session.add(SystemSetting(**d))
    db.session.commit()


def start_dicom_servers(app):
    """Start DICOM MWL and Store SCP servers as daemon threads.

    Reads SCP settings from SystemSetting DB (falls back to config.py defaults).
    Changes to these settings require a program restart to take effect.
    """
    from services.mwl_server import MWLServer
    from services.store_scp import StoreSCP
    from models import get_setting

    with app.app_context():
        mwl_ae = get_setting("scp_mwl_ae_title", app.config["MWL_AE_TITLE"])
        mwl_port = int(get_setting("scp_mwl_port", str(app.config["MWL_PORT"])))
        store_ae = get_setting("scp_store_ae_title", app.config["STORE_AE_TITLE"])
        store_port = int(get_setting("scp_store_port", str(app.config["STORE_PORT"])))
        storage_dir = get_setting("scp_storage_dir", "") or app.config["DICOM_STORAGE_DIR"]

    mwl = MWLServer(
        flask_app=app,
        ae_title=mwl_ae,
        port=mwl_port,
    )
    mwl.start()

    store = StoreSCP(
        flask_app=app,
        ae_title=store_ae,
        port=store_port,
        storage_dir=storage_dir,
    )
    store.start()

    # Update app.config so the running values are visible elsewhere
    app.config["MWL_AE_TITLE"] = mwl_ae
    app.config["MWL_PORT"] = mwl_port
    app.config["STORE_AE_TITLE"] = store_ae
    app.config["STORE_PORT"] = store_port
    app.config["DICOM_STORAGE_DIR"] = storage_dir


if __name__ == "__main__":
    app = create_app()
    start_dicom_servers(app)
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    print("\n" + "=" * 60)
    print("  ECG Management System")
    print("  Web:      http://localhost:5000")
    print(f"  MWL SCP:  Port {app.config['MWL_PORT']} (AE: {app.config['MWL_AE_TITLE']})")
    print(f"  Store SCP: Port {app.config['STORE_PORT']} (AE: {app.config['STORE_AE_TITLE']})")
    print(f"  Storage:  {app.config['DICOM_STORAGE_DIR']}")
    if debug_mode:
        print("  Login: admin/admin123  |  nurse01/nurse123")
        print("         doctor01/doctor123  |  doctor02/doctor123")
    print("=" * 60 + "\n")

    socketio.run(app, host="0.0.0.0", port=5000, debug=debug_mode, use_reloader=False, allow_unsafe_werkzeug=True)
