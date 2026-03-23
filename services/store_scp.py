"""
DICOM Store SCP - Receives ECG results from Mindray R700.
Runs as a daemon thread alongside the Flask application.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Optional

from pydicom.uid import (
    ImplicitVRLittleEndian,
    ExplicitVRLittleEndian,
    ExplicitVRBigEndian,
)
from pynetdicom import AE, evt
from pynetdicom.sop_class import (
    TwelveLeadECGWaveformStorage,
    GeneralECGWaveformStorage,
)


class StoreSCP:
    """DICOM Store SCP for receiving ECG results."""

    def __init__(self, flask_app, ae_title: str = "ECG_STORE", port: int = 6702,
                 storage_dir: str = "dicom_storage"):
        self.flask_app = flask_app
        self.ae_title = ae_title
        self.port = port
        self.storage_dir = storage_dir
        self._thread: Optional[threading.Thread] = None

        os.makedirs(self.storage_dir, exist_ok=True)

    def _handle_store(self, event):
        """C-STORE handler - saves received DICOM files and creates DB records."""
        ds = event.dataset
        ds.file_meta = event.file_meta

        # Extract metadata
        patient_id = str(getattr(ds, "PatientID", "UNKNOWN"))
        accession = str(getattr(ds, "AccessionNumber", ""))
        study_uid = str(getattr(ds, "StudyInstanceUID", ""))
        sop_uid = str(getattr(ds, "SOPInstanceUID", ""))

        # Create directory structure: dicom_storage/YYYYMMDD/PatientID/
        today = datetime.now().strftime("%Y%m%d")
        patient_dir = os.path.join(self.storage_dir, today, patient_id)
        os.makedirs(patient_dir, exist_ok=True)

        # Save file
        filename = f"{sop_uid}.dcm"
        filepath = os.path.join(patient_dir, filename)
        ds.save_as(filepath)

        print(f"[Store SCP] Received: Patient={patient_id} Accession={accession}")
        print(f"[Store SCP] Saved to: {filepath}")

        # Extract patient demographics from DICOM tags
        patient_name = str(getattr(ds, "PatientName", "") or "").strip()
        sex         = str(getattr(ds, "PatientSex", "") or "").strip().upper()[:1]
        birth_date  = str(getattr(ds, "PatientBirthDate", "") or "").strip()

        # Normalise sex to M/F/O or empty
        if sex not in ("M", "F", "O"):
            sex = ""

        # Extract study date/time from DICOM tags
        study_dt = None
        acq_dt_str = str(getattr(ds, "AcquisitionDateTime", "") or "").strip()
        study_date_str = str(getattr(ds, "StudyDate", "") or "").strip()
        study_time_str = str(getattr(ds, "StudyTime", "") or "").strip()
        try:
            if acq_dt_str and len(acq_dt_str) >= 14:
                study_dt = datetime.strptime(acq_dt_str[:14], "%Y%m%d%H%M%S")
            elif study_date_str and len(study_date_str) == 8:
                if study_time_str and len(study_time_str) >= 6:
                    study_dt = datetime.strptime(study_date_str + study_time_str[:6], "%Y%m%d%H%M%S")
                else:
                    study_dt = datetime.strptime(study_date_str, "%Y%m%d")
        except (ValueError, TypeError):
            study_dt = None

        # Create database record
        with self.flask_app.app_context():
            from models import db, ECGResult, Patient, WorklistItem

            # Find or auto-create patient from DICOM tags
            patient = Patient.query.filter_by(patient_id=patient_id).first()
            if patient is None and patient_id and patient_id != "UNKNOWN":
                patient = Patient(
                    patient_id=patient_id,
                    patient_name=patient_name or patient_id,
                    sex=sex,
                    birth_date=birth_date if len(birth_date) == 8 else None,
                )
                db.session.add(patient)
                db.session.flush()   # get patient.id before commit
                print(f"[Store SCP] Auto-created Patient: HN={patient_id} Name={patient_name}")

            # Find worklist item by accession number
            worklist = None
            if accession:
                worklist = WorklistItem.query.filter_by(accession_number=accession).first()
                if worklist:
                    worklist.status = "COMPLETED"

            result = ECGResult(
                worklist_id=worklist.id if worklist else None,
                patient_db_id=patient.id if patient else None,
                accession_number=accession,
                study_instance_uid=study_uid,
                sop_instance_uid=sop_uid,
                file_path=filepath,
                received_at=datetime.now(),
                study_datetime=study_dt,
                status="RECEIVED",
            )
            db.session.add(result)
            db.session.commit()
            print(f"[Store SCP] DB record created: ECGResult #{result.id}")

            # Notify nurses/admins that a new ECG result has arrived
            try:
                from routes.notifications import push_broadcast_to_roles
                push_broadcast_to_roles(
                    roles=["nurse", "admin"],
                    message=f"New ECG received: {patient_name or patient_id} (Acc: {accession or 'N/A'})",
                    message_th=f"ผล ECG ใหม่: {patient_name or patient_id} (Acc: {accession or 'N/A'})",
                    notif_type="new_result",
                    result_id=result.id,
                )
            except Exception as e:
                print(f"[Store SCP] Notification failed: {e}")

        return 0x0000  # Success

    def start(self):
        """Start Store SCP in a daemon thread."""
        ae = AE(ae_title=self.ae_title)

        transfer_syntaxes = [ImplicitVRLittleEndian, ExplicitVRLittleEndian, ExplicitVRBigEndian]

        # Support ECG waveform storage SOP classes
        ae.add_supported_context(TwelveLeadECGWaveformStorage, transfer_syntaxes)
        ae.add_supported_context(GeneralECGWaveformStorage, transfer_syntaxes)

        # Also support generic secondary capture (some devices use this)
        from pynetdicom.sop_class import SecondaryCaptureImageStorage
        ae.add_supported_context(SecondaryCaptureImageStorage, transfer_syntaxes)

        handlers = [(evt.EVT_C_STORE, self._handle_store)]

        self._thread = threading.Thread(
            target=ae.start_server,
            args=(("0.0.0.0", self.port),),
            kwargs={"block": True, "evt_handlers": handlers},
            daemon=True,
        )
        self._thread.start()
        print(f"[Store SCP] Started: AE={self.ae_title} Port={self.port}")
