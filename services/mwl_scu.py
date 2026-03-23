"""
MWL SCU - Query external Modality Worklist servers via C-FIND.
Callable functions (not a daemon thread) — invoked from routes or APScheduler.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import List, Optional, Tuple

from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian
from pynetdicom import AE
from pynetdicom.sop_class import ModalityWorklistInformationFind

logger = logging.getLogger(__name__)


def build_cfind_query(scheduled_date: str = "", modality: str = "") -> Dataset:
    """Build a C-FIND MWL query dataset.

    Args:
        scheduled_date: YYYYMMDD string. Empty string = no date filter.
        modality: Modality filter. Empty string = all modalities.

    Returns:
        Dataset ready for C-FIND.
    """
    ds = Dataset()
    ds.PatientName = ""
    ds.PatientID = ""
    ds.PatientBirthDate = ""
    ds.PatientSex = ""

    ds.AccessionNumber = ""
    ds.StudyInstanceUID = ""
    ds.RequestedProcedureID = ""
    ds.RequestedProcedureDescription = ""
    ds.AdmissionID = ""
    ds.RequestedProcedurePriority = ""
    ds.ReferringPhysicianName = ""

    # Scheduled Procedure Step Sequence
    sps = Dataset()
    sps.ScheduledStationAETitle = ""
    sps.ScheduledStationName = ""
    sps.Modality = modality  # empty = return all modalities
    sps.ScheduledProcedureStepID = ""
    sps.ScheduledProcedureStepDescription = ""
    sps.ScheduledProcedureStepStartDate = scheduled_date
    sps.ScheduledProcedureStepStartTime = ""
    sps.ScheduledPerformingPhysicianName = ""
    ds.ScheduledProcedureStepSequence = [sps]

    logger.info(f"[MWL SCU] C-FIND query: date={scheduled_date!r}, modality={modality!r}")
    return ds


def query_mwl(
    host: str,
    port: int,
    remote_ae: str,
    local_ae: str,
    query_ds: Optional[Dataset] = None,
    scheduled_date: str = "",
) -> Tuple[List[Dataset], Optional[str]]:
    """Send C-FIND to an external MWL SCP.

    Args:
        host: Remote host/IP.
        port: Remote port.
        remote_ae: Remote AE Title.
        local_ae: Local AE Title.
        query_ds: Pre-built query dataset (optional).
        scheduled_date: YYYYMMDD date filter (used if query_ds is None).

    Returns:
        (list_of_datasets, error_message_or_None)
    """
    if not host:
        return [], "MWL host not configured"

    if query_ds is None:
        query_ds = build_cfind_query(scheduled_date=scheduled_date)

    ae = AE(ae_title=local_ae)
    ae.add_requested_context(ModalityWorklistInformationFind)

    results: List[Dataset] = []

    try:
        assoc = ae.associate(host, port, ae_title=remote_ae)
    except Exception as e:
        logger.error(f"[MWL SCU] Association failed: {e}")
        return [], f"Connection failed: {e}"

    if not assoc.is_established:
        return [], f"Association rejected by {remote_ae}@{host}:{port}"

    try:
        logger.info(f"[MWL SCU] Sending C-FIND to {remote_ae}@{host}:{port}")
        responses = assoc.send_c_find(query_ds, ModalityWorklistInformationFind)
        for status, identifier in responses:
            if status and status.Status in (0xFF00, 0xFF01):
                if identifier:
                    results.append(identifier)
                    logger.debug(f"[MWL SCU] Got item: PatientID={getattr(identifier, 'PatientID', '?')}, "
                                 f"AccessionNumber={getattr(identifier, 'AccessionNumber', '?')}")
            elif status and status.Status == 0x0000:
                logger.info(f"[MWL SCU] C-FIND completed successfully")
                break  # Success (no more results)
            else:
                status_val = status.Status if status else "unknown"
                logger.warning(f"[MWL SCU] C-FIND status: 0x{status_val:04X}" if isinstance(status_val, int) else f"[MWL SCU] C-FIND status: {status_val}")
    except Exception as e:
        logger.error(f"[MWL SCU] C-FIND error: {e}")
        assoc.release()
        return results, f"C-FIND error: {e}"

    assoc.release()
    logger.info(f"[MWL SCU] Received {len(results)} worklist items from {remote_ae}@{host}:{port}")
    return results, None


def test_mwl_connection(host: str, port: int, remote_ae: str, local_ae: str) -> Tuple[bool, str]:
    """Test MWL server connectivity by attempting a C-FIND association.

    Returns:
        (success, message)
    """
    if not host:
        return False, "Host not configured"

    ae = AE(ae_title=local_ae)
    ae.add_requested_context(ModalityWorklistInformationFind)

    try:
        assoc = ae.associate(host, port, ae_title=remote_ae)
    except Exception as e:
        return False, f"Connection failed: {e}"

    if not assoc.is_established:
        return False, f"Association rejected by {remote_ae}@{host}:{port}"

    assoc.release()
    return True, f"Successfully connected to {remote_ae}@{host}:{port}"


def _parse_mwl_response(ds: Dataset) -> dict:
    """Parse a MWL C-FIND response dataset into a flat dict.

    Returns dict with keys matching WorklistItem/Patient fields.
    """
    result = {
        "patient_id": str(getattr(ds, "PatientID", "") or "").strip(),
        "patient_name": str(getattr(ds, "PatientName", "") or "").strip(),
        "patient_sex": str(getattr(ds, "PatientSex", "") or "").strip().upper()[:1],
        "patient_birth_date": str(getattr(ds, "PatientBirthDate", "") or "").strip(),
        "accession_number": str(getattr(ds, "AccessionNumber", "") or "").strip(),
        "study_instance_uid": str(getattr(ds, "StudyInstanceUID", "") or "").strip(),
        "requested_procedure_id": str(getattr(ds, "RequestedProcedureID", "") or "").strip(),
        "requested_procedure_desc": str(getattr(ds, "RequestedProcedureDescription", "") or "").strip(),
        "admission_id": str(getattr(ds, "AdmissionID", "") or "").strip(),
        "priority": str(getattr(ds, "RequestedProcedurePriority", "") or "ROUTINE").strip(),
        "ordering_physician": str(getattr(ds, "ReferringPhysicianName", "") or "").strip(),
    }

    # Normalise sex
    if result["patient_sex"] not in ("M", "F", "O"):
        result["patient_sex"] = ""

    # Normalise priority
    if result["priority"] not in ("ROUTINE", "URGENT", "STAT"):
        result["priority"] = "ROUTINE"

    # Parse Scheduled Procedure Step Sequence
    sps_seq = getattr(ds, "ScheduledProcedureStepSequence", None)
    if sps_seq and len(sps_seq) > 0:
        sps = sps_seq[0]
        result["scheduled_station_ae"] = str(getattr(sps, "ScheduledStationAETitle", "") or "").strip()
        result["scheduled_station_name"] = str(getattr(sps, "ScheduledStationName", "") or "").strip()
        result["modality"] = str(getattr(sps, "Modality", "ECG") or "ECG").strip()
        result["sps_id"] = str(getattr(sps, "ScheduledProcedureStepID", "") or "").strip()
        result["sps_desc"] = str(getattr(sps, "ScheduledProcedureStepDescription", "") or "").strip()
        result["scheduled_date"] = str(getattr(sps, "ScheduledProcedureStepStartDate", "") or "").strip()
        result["scheduled_time"] = str(getattr(sps, "ScheduledProcedureStepStartTime", "") or "").strip()
        result["performing_physician"] = str(getattr(sps, "ScheduledPerformingPhysicianName", "") or "").strip()
    else:
        result["scheduled_station_ae"] = ""
        result["scheduled_station_name"] = ""
        result["modality"] = "ECG"
        result["sps_id"] = ""
        result["sps_desc"] = ""
        result["scheduled_date"] = ""
        result["scheduled_time"] = ""
        result["performing_physician"] = ""

    return result


def upsert_worklist_item(ds: Dataset, flask_app) -> str:
    """Parse MWL response and upsert Patient + WorklistItem.

    Returns:
        "created" | "updated" | "skipped"
    """
    parsed = _parse_mwl_response(ds)

    if not parsed["accession_number"]:
        logger.warning("[MWL SCU] Skipping item with no accession number")
        return "skipped"

    if not parsed["patient_id"]:
        logger.warning(f"[MWL SCU] Skipping item {parsed['accession_number']} with no Patient ID")
        return "skipped"

    with flask_app.app_context():
        from models import db, Patient, WorklistItem
        from services.dicom_helpers import stable_uid_from_text

        # Find or create patient
        patient = Patient.query.filter_by(patient_id=parsed["patient_id"]).first()
        if not patient:
            patient = Patient(
                patient_id=parsed["patient_id"],
                patient_name=parsed["patient_name"] or parsed["patient_id"],
                sex=parsed["patient_sex"],
                birth_date=parsed["patient_birth_date"] if len(parsed["patient_birth_date"]) == 8 else None,
            )
            db.session.add(patient)
            db.session.flush()
        else:
            # Update patient info if new data is richer
            if parsed["patient_name"] and not patient.patient_name:
                patient.patient_name = parsed["patient_name"]
            if parsed["patient_sex"] and not patient.sex:
                patient.sex = parsed["patient_sex"]
            if parsed["patient_birth_date"] and len(parsed["patient_birth_date"]) == 8 and not patient.birth_date:
                patient.birth_date = parsed["patient_birth_date"]

        # Upsert worklist item by accession_number
        existing = WorklistItem.query.filter_by(accession_number=parsed["accession_number"]).first()

        if existing:
            # Update existing item
            existing.patient_id = patient.id
            existing.requested_procedure_id = parsed["requested_procedure_id"] or existing.requested_procedure_id
            existing.requested_procedure_desc = parsed["requested_procedure_desc"] or existing.requested_procedure_desc
            existing.admission_id = parsed["admission_id"] or existing.admission_id
            existing.requested_procedure_priority = parsed["priority"]
            existing.scheduled_station_ae = parsed["scheduled_station_ae"] or existing.scheduled_station_ae
            existing.scheduled_station_name = parsed["scheduled_station_name"] or existing.scheduled_station_name
            existing.modality = parsed["modality"]
            existing.sps_id = parsed["sps_id"] or existing.sps_id
            existing.sps_desc = parsed["sps_desc"] or existing.sps_desc
            existing.scheduled_date = parsed["scheduled_date"] or existing.scheduled_date
            existing.scheduled_time = parsed["scheduled_time"] or existing.scheduled_time
            existing.ordering_physician = parsed["ordering_physician"] or existing.ordering_physician
            existing.performing_physician = parsed["performing_physician"] or existing.performing_physician
            if parsed["study_instance_uid"]:
                existing.study_instance_uid = parsed["study_instance_uid"]
            existing.source = "EXTERNAL"
            action = "updated"
        else:
            # Create new item
            study_uid = parsed["study_instance_uid"] or stable_uid_from_text(parsed["accession_number"])
            wl = WorklistItem(
                patient_id=patient.id,
                accession_number=parsed["accession_number"],
                requested_procedure_id=parsed["requested_procedure_id"],
                requested_procedure_desc=parsed["requested_procedure_desc"] or "ECG",
                admission_id=parsed["admission_id"],
                requested_procedure_priority=parsed["priority"],
                scheduled_station_ae=parsed["scheduled_station_ae"] or "CP150",
                scheduled_station_name=parsed["scheduled_station_name"] or "ECG-ROOM1",
                modality=parsed["modality"],
                sps_id=parsed["sps_id"],
                sps_desc=parsed["sps_desc"],
                scheduled_date=parsed["scheduled_date"],
                scheduled_time=parsed["scheduled_time"],
                study_instance_uid=study_uid,
                status="SCHEDULED",
                ordering_physician=parsed["ordering_physician"],
                performing_physician=parsed["performing_physician"],
                source="EXTERNAL",
            )
            db.session.add(wl)
            action = "created"

        db.session.commit()
        return action


def sync_from_external_mwl(
    flask_app,
    scheduled_date: str = "",
) -> dict:
    """Full pipeline: read config -> C-FIND -> upsert all items.

    Args:
        flask_app: Flask application instance.
        scheduled_date: YYYYMMDD filter. Empty = today.

    Returns:
        dict with keys: success, created, updated, skipped, total, error
    """
    with flask_app.app_context():
        from models import get_setting, SystemSetting, db

        host = get_setting("ext_mwl_host", "")
        port = int(get_setting("ext_mwl_port", "104"))
        remote_ae = get_setting("ext_mwl_ae", "MWL")
        local_ae = get_setting("ext_mwl_local_ae", "ECG_SCU")

    if not host:
        return {"success": False, "error": "MWL server host not configured", "created": 0, "updated": 0, "skipped": 0, "total": 0}

    if not scheduled_date:
        scheduled_date = date.today().strftime("%Y%m%d")

    logger.info(f"[MWL SCU] Syncing from {remote_ae}@{host}:{port} (local AE: {local_ae}), date={scheduled_date}")

    # Query MWL
    datasets, error = query_mwl(host, port, remote_ae, local_ae, scheduled_date=scheduled_date)

    if error and not datasets:
        return {"success": False, "error": error, "created": 0, "updated": 0, "skipped": 0, "total": 0}

    # Upsert each item
    created = 0
    updated = 0
    skipped = 0

    for ds in datasets:
        try:
            action = upsert_worklist_item(ds, flask_app)
            if action == "created":
                created += 1
            elif action == "updated":
                updated += 1
            else:
                skipped += 1
        except Exception as e:
            logger.error(f"[MWL SCU] Upsert error: {e}")
            skipped += 1

    # Update last sync timestamp
    with flask_app.app_context():
        from models import SystemSetting, db
        s = SystemSetting.query.filter_by(key="ext_mwl_last_sync_at").first()
        if s:
            s.value = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.session.commit()

    logger.info(f"[MWL SCU] Sync complete: {created} created, {updated} updated, {skipped} skipped")

    return {
        "success": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "total": len(datasets),
        "error": error,
    }
