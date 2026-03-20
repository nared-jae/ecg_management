from datetime import datetime, date

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), default="user")  # admin, user
    is_active_user = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.is_active_user


class Patient(db.Model):
    __tablename__ = "patients"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.String(20), unique=True, nullable=False)  # HN
    patient_name = db.Column(db.String(120), nullable=False)  # DICOM format: LAST^FIRST
    sex = db.Column(db.String(2))  # M, F, O
    birth_date = db.Column(db.String(8))  # YYYYMMDD
    created_at = db.Column(db.DateTime, default=datetime.now)

    worklist_items = db.relationship("WorklistItem", backref="patient", lazy=True)
    ecg_results = db.relationship("ECGResult", backref="patient", lazy=True)


class WorklistItem(db.Model):
    __tablename__ = "worklist_items"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=False)

    accession_number = db.Column(db.String(50), unique=True, nullable=False)
    requested_procedure_id = db.Column(db.String(50))
    requested_procedure_desc = db.Column(db.String(200))

    admission_id = db.Column(db.String(50))
    requested_procedure_priority = db.Column(db.String(20), default="ROUTINE")  # ROUTINE, URGENT

    scheduled_station_ae = db.Column(db.String(16), default="CP150")
    scheduled_station_name = db.Column(db.String(50), default="ECG-ROOM1")
    modality = db.Column(db.String(16), default="ECG")
    sps_id = db.Column(db.String(50))
    sps_desc = db.Column(db.String(200))
    scheduled_date = db.Column(db.String(8))  # YYYYMMDD
    scheduled_time = db.Column(db.String(6))  # HHMMSS

    study_instance_uid = db.Column(db.String(64))
    status = db.Column(db.String(20), default="SCHEDULED")  # SCHEDULED, IN_PROGRESS, COMPLETED, CANCELLED

    # Extended fields for ECG Test Management
    ordering_department = db.Column(db.String(100))  # แผนกที่สั่ง
    ordering_physician = db.Column(db.String(120))   # แพทย์ผู้สั่ง
    performing_physician = db.Column(db.String(120)) # แพทย์/เจ้าหน้าที่ผู้ตรวจ
    patient_source = db.Column(db.String(20), default="Outpatient")  # Outpatient/Inpatient/Emergency/Health Check
    bed_number = db.Column(db.String(20))            # เตียง/ห้อง
    phone = db.Column(db.String(20))                 # เบอร์โทร
    clinical_info = db.Column(db.String(500))        # ข้อมูลทางคลินิก

    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    ecg_results = db.relationship("ECGResult", backref="worklist_item", lazy=True)


class ECGResult(db.Model):
    __tablename__ = "ecg_results"

    id = db.Column(db.Integer, primary_key=True)
    worklist_id = db.Column(db.Integer, db.ForeignKey("worklist_items.id"), nullable=True)
    patient_db_id = db.Column(db.Integer, db.ForeignKey("patients.id"), nullable=True)

    accession_number = db.Column(db.String(50))
    study_instance_uid = db.Column(db.String(64))
    sop_instance_uid = db.Column(db.String(64))

    file_path = db.Column(db.String(500))
    received_at = db.Column(db.DateTime, default=datetime.now)
    status = db.Column(db.String(20), default="RECEIVED")  # RECEIVED, REVIEWED, APPROVED
    notes = db.Column(db.Text)

    # Diagnosis fields
    diagnosis = db.Column(db.String(500))        # ผลวินิจฉัย
    diagnosed_by = db.Column(db.String(120))     # แพทย์ผู้วินิจฉัย
    diagnosed_at = db.Column(db.DateTime)        # วันเวลาวินิจฉัย
