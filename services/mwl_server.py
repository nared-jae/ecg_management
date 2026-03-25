"""
DICOM Modality Worklist (MWL) Server - Database-backed.
Runs as a daemon thread alongside the Flask application.
"""
from __future__ import annotations

import threading
from typing import Optional

from pydicom.dataset import Dataset
from pydicom.sequence import Sequence
from pydicom.uid import (
    ImplicitVRLittleEndian,
    ExplicitVRLittleEndian,
    ExplicitVRBigEndian,
)
from pynetdicom import AE, evt
from pynetdicom.sop_class import ModalityWorklistInformationFind

from services.dicom_helpers import (
    stable_uid_from_text,
    get_first,
    get_sps_first,
    match_text,
    match_da,
    build_return_dataset,
)


def _worklist_item_to_dataset(item) -> Dataset:
    """Convert a WorklistItem DB model to a DICOM Dataset."""
    ds = Dataset()
    ds.SpecificCharacterSet = "ISO_IR 192"

    patient = item.patient
    ds.PatientID = patient.patient_id
    ds.PatientName = patient.patient_name
    ds.PatientSex = patient.sex or ""
    ds.PatientBirthDate = patient.birth_date or ""
    ds.StudyInstanceUID = item.study_instance_uid or stable_uid_from_text(item.accession_number)

    ds.AccessionNumber = item.accession_number
    ds.RequestedProcedureID = item.requested_procedure_id or ""
    ds.RequestedProcedureDescription = item.requested_procedure_desc or ""

    ds.AdmissionID = item.admission_id or ""
    ds.RequestedProcedurePriority = item.requested_procedure_priority or "ROUTINE"
    ds.RequestingPhysician = ""

    sps = Dataset()
    sps.ScheduledStationAETitle = item.scheduled_station_ae or ""
    sps.ScheduledStationName = item.scheduled_station_name or ""
    sps.Modality = item.modality or "ECG"
    sps.ScheduledProcedureStepID = item.sps_id or ""
    sps.ScheduledProcedureStepDescription = item.sps_desc or ""
    sps.ScheduledProcedureStepStartDate = item.scheduled_date or ""
    sps.ScheduledProcedureStepStartTime = item.scheduled_time or ""
    sps.ScheduledPerformingPhysicianName = ""

    ds.ScheduledProcedureStepSequence = [sps]
    return ds


def _match_item(item, query_ds: Dataset) -> bool:
    """Check if a WorklistItem matches a C-FIND query."""
    q_pid = get_first(query_ds, "PatientID")
    q_pname = get_first(query_ds, "PatientName")
    q_date = get_sps_first(query_ds, "ScheduledProcedureStepStartDate")
    q_modality = get_sps_first(query_ds, "Modality")
    q_station = get_sps_first(query_ds, "ScheduledStationAETitle")

    patient = item.patient
    if not match_text(patient.patient_id, q_pid):
        return False
    if not match_text(patient.patient_name, q_pname):
        return False
    if not match_da(item.scheduled_date or "", q_date):
        return False
    if not match_text(item.modality or "", q_modality):
        return False
    if not match_text(item.scheduled_station_ae or "", q_station):
        return False

    return True


class MWLServer:
    """Database-backed DICOM MWL Server running as a daemon thread."""

    def __init__(self, flask_app, ae_title: str = "MWL", port: int = 6701):
        self.flask_app = flask_app
        self.ae_title = ae_title
        self.port = port
        self._thread: Optional[threading.Thread] = None

    def _handle_find(self, event):
        """C-FIND handler - queries database for matching worklist items."""
        query = event.identifier
        assoc = event.assoc

        print("\n--- C-FIND MWL Request ---")
        try:
            print(f"From AE={assoc.requestor.ae_title!r} IP={assoc.requestor.address}:{assoc.requestor.port}")
            print(f"To   AE={assoc.acceptor.ae_title!r}")
        except Exception:
            pass
        print(query)

        with self.flask_app.app_context():
            from models import db, WorklistItem as WLModel

            # Only return SCHEDULED / IN_PROGRESS items
            items = WLModel.query.filter(
                WLModel.status.in_(["SCHEDULED", "IN_PROGRESS"])
            ).all()

            matches = [it for it in items if _match_item(it, query)]
            print(f"Matched items: {len(matches)}")

            # Auto IN_PROGRESS: when ECG machine queries a specific patient
            # (not a wildcard/broad query), mark matched SCHEDULED items as IN_PROGRESS
            q_pid = get_first(query, "PatientID")
            is_specific_query = bool(q_pid and "*" not in q_pid)
            if is_specific_query and matches:
                for it in matches:
                    if it.status == "SCHEDULED":
                        it.status = "IN_PROGRESS"
                        print(f"[MWL] Auto IN_PROGRESS: {it.accession_number} (patient={q_pid})")
                try:
                    db.session.commit()
                except Exception as e:
                    print(f"[MWL] Error updating status: {e}")
                    db.session.rollback()

            for it in matches:
                full = _worklist_item_to_dataset(it)
                rsp = build_return_dataset(query, full)
                yield 0xFF00, rsp

        yield 0x0000, None

    def start(self):
        """Start MWL server in a daemon thread."""
        ae = AE(ae_title=self.ae_title)
        ae.add_supported_context(
            ModalityWorklistInformationFind,
            [ImplicitVRLittleEndian, ExplicitVRLittleEndian, ExplicitVRBigEndian],
        )

        handlers = [(evt.EVT_C_FIND, self._handle_find)]

        self._thread = threading.Thread(
            target=ae.start_server,
            args=(("0.0.0.0", self.port),),
            kwargs={"block": True, "evt_handlers": handlers},
            daemon=True,
        )
        self._thread.start()
        print(f"[MWL Server] Started: AE={self.ae_title} Port={self.port}")
