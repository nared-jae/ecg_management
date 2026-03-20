from datetime import date

from flask import Flask
from flask_login import LoginManager

from config import Config
from models import db, User, Patient, WorklistItem
from services.dicom_helpers import stable_uid_from_text


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize extensions
    db.init_app(app)

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

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(worklist_bp)
    app.register_blueprint(results_bp)
    app.register_blueprint(ecg_tests_bp)

    # Create tables and seed data
    with app.app_context():
        db.create_all()
        _seed_default_data()

    return app


def _seed_default_data():
    """Create default admin user and sample worklist data if DB is empty."""
    # Default admin user
    if not User.query.filter_by(username="admin").first():
        admin = User(
            username="admin",
            display_name="ผู้ดูแลระบบ",
            role="admin",
        )
        admin.set_password("admin123")
        db.session.add(admin)
        db.session.commit()
        print("[Seed] Created default admin user (admin/admin123)")

    # Sample patients and worklist items (from original wml_server.py)
    if Patient.query.count() == 0:
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

    print("\n" + "=" * 50)
    print("  ECG Management System")
    print("  Web:   http://localhost:5000")
    print(f"  MWL:   Port {app.config['MWL_PORT']} (AE: {app.config['MWL_AE_TITLE']})")
    print(f"  Store: Port {app.config['STORE_PORT']} (AE: {app.config['STORE_AE_TITLE']})")
    print("  Login: admin / admin123")
    print("=" * 50 + "\n")

    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
