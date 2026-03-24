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
    display_name_en = db.Column(db.String(120), nullable=True)  # English name for DICOM/MWL
    role = db.Column(db.String(20), default="user")  # admin, doctor, nurse, it_admin, viewer, user
    is_active_user = db.Column(db.Boolean, default=True)
    can_be_assigned = db.Column(db.Boolean, default=False)  # Show in Assign Case dropdown
    created_at = db.Column(db.DateTime, default=datetime.now)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.is_active_user

    @property
    def can_assign(self):
        """Nurses and admins can assign cases to doctors."""
        return self.role in ("nurse", "admin")

    @property
    def can_diagnose(self):
        """Doctors and admins can submit diagnoses."""
        return self.role in ("doctor", "admin")


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

    source = db.Column(db.String(20), default="MANUAL")  # MANUAL | EXTERNAL

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
    study_datetime = db.Column(db.DateTime, nullable=True)  # Actual exam date/time from DICOM tags
    status = db.Column(db.String(20), default="RECEIVED")  # RECEIVED, REVIEWED, APPROVED
    notes = db.Column(db.Text)

    # Diagnosis fields
    diagnosis = db.Column(db.String(500))        # ผลวินิจฉัย
    diagnosed_by = db.Column(db.String(120))     # แพทย์ผู้วินิจฉัย
    diagnosed_at = db.Column(db.DateTime)        # วันเวลาวินิจฉัย

    # Assignment fields
    assigned_to_id        = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    assigned_at           = db.Column(db.DateTime, nullable=True)
    assignment_expires_at = db.Column(db.DateTime, nullable=True)

    # Concurrency lock fields
    locked_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    locked_at    = db.Column(db.DateTime, nullable=True)

    # PACS send fields
    pacs_send_status = db.Column(db.String(20), nullable=True)  # None | SENT | FAILED
    pacs_sent_at = db.Column(db.DateTime, nullable=True)

    # Export to folder fields
    pdf_export_status = db.Column(db.String(20), nullable=True)   # None | SENT | FAILED
    hl7_export_status = db.Column(db.String(20), nullable=True)   # None | SENT | FAILED

    # Soft delete
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    original_file_path = db.Column(db.String(500), nullable=True)  # path before archive

    assigned_to = db.relationship("User", foreign_keys=[assigned_to_id], backref="assigned_results")
    locked_by   = db.relationship("User", foreign_keys=[locked_by_id],   backref="locked_results")
    deleted_by  = db.relationship("User", foreign_keys=[deleted_by_id])


class AssignmentLog(db.Model):
    """Audit trail for every assign/unassign/timeout/lock/unlock/diagnosed action."""
    __tablename__ = "assignment_logs"

    id            = db.Column(db.Integer, primary_key=True)
    ecg_result_id = db.Column(db.Integer, db.ForeignKey("ecg_results.id"), nullable=False)
    action        = db.Column(db.String(20), nullable=False)
    # assigned | unassigned | timeout | locked | unlocked | diagnosed
    actor_id      = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    target_id     = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    timestamp     = db.Column(db.DateTime, default=datetime.now)
    notes         = db.Column(db.String(200))

    ecg_result = db.relationship("ECGResult", backref="assignment_logs")
    actor      = db.relationship("User", foreign_keys=[actor_id])
    target     = db.relationship("User", foreign_keys=[target_id])


class Notification(db.Model):
    """Persistent notifications for real-time + offline delivery."""
    __tablename__ = "notifications"

    id                = db.Column(db.Integer, primary_key=True)
    user_id           = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    message           = db.Column(db.String(300), nullable=False)
    message_th        = db.Column(db.String(300))
    type              = db.Column(db.String(30))   # assignment | timeout | unassigned
    related_result_id = db.Column(db.Integer, db.ForeignKey("ecg_results.id"), nullable=True)
    is_read           = db.Column(db.Boolean, default=False)
    created_at        = db.Column(db.DateTime, default=datetime.now)

    user           = db.relationship("User",      backref="notifications")
    related_result = db.relationship("ECGResult", backref="notifications")


class SystemSetting(db.Model):
    """Key-value store for runtime system configuration."""
    __tablename__ = "system_settings"

    id            = db.Column(db.Integer, primary_key=True)
    key           = db.Column(db.String(80), unique=True, nullable=False)
    value         = db.Column(db.String(200), nullable=False)
    label         = db.Column(db.String(200))
    description   = db.Column(db.String(500))
    updated_at    = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    updated_by = db.relationship("User", foreign_keys=[updated_by_id])


class AuditLog(db.Model):
    """General audit trail for destructive or sensitive actions (no FK to deleted records)."""
    __tablename__ = "audit_logs"

    id         = db.Column(db.Integer, primary_key=True)
    action     = db.Column(db.String(50), nullable=False)   # e.g. delete_result, reset_status
    actor_id   = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    detail     = db.Column(db.Text)                          # JSON or free-text description
    created_at = db.Column(db.DateTime, default=datetime.now)

    actor = db.relationship("User", foreign_keys=[actor_id])


def get_setting(key: str, default=None):
    """Read a system setting from DB; return default if not found."""
    s = SystemSetting.query.filter_by(key=key).first()
    return s.value if s else default
