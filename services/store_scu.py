"""
Store SCU - Send DICOM files to external PACS servers via C-STORE.
Callable functions (not a daemon thread) — invoked from routes.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Tuple

import pydicom
from pydicom.uid import (
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
)
from pynetdicom import AE
from pynetdicom.sop_class import (
    TwelveLeadECGWaveformStorage,
    GeneralECGWaveformStorage,
    SecondaryCaptureImageStorage,
    Verification,
)

logger = logging.getLogger(__name__)

# Map SOP Class UID strings to pynetdicom abstract syntax names
_SOP_CLASS_MAP = {
    TwelveLeadECGWaveformStorage: TwelveLeadECGWaveformStorage,
    GeneralECGWaveformStorage: GeneralECGWaveformStorage,
    SecondaryCaptureImageStorage: SecondaryCaptureImageStorage,
}


def send_to_pacs(
    file_path_or_buffer,
    host: str,
    port: int,
    remote_ae: str,
    local_ae: str,
) -> Tuple[bool, str]:
    """Send a DICOM file/buffer to PACS via C-STORE.

    Args:
        file_path_or_buffer: Path to .dcm file or BytesIO buffer.
        host: PACS hostname or IP.
        port: PACS port.
        remote_ae: PACS AE Title.
        local_ae: Local SCU AE Title.

    Returns:
        (success, message)
    """
    if not host:
        return False, "PACS host not configured"

    # Read DICOM dataset
    try:
        if isinstance(file_path_or_buffer, str):
            ds = pydicom.dcmread(file_path_or_buffer, force=True)
        else:
            file_path_or_buffer.seek(0)
            ds = pydicom.dcmread(file_path_or_buffer, force=True)
    except Exception as e:
        return False, f"Failed to read DICOM file: {e}"

    # Determine SOP Class
    sop_class_uid = str(getattr(ds, "SOPClassUID", ""))

    ae = AE(ae_title=local_ae)

    # Add all supported contexts
    transfer_syntaxes = [ExplicitVRLittleEndian, ImplicitVRLittleEndian]
    ae.add_requested_context(TwelveLeadECGWaveformStorage, transfer_syntaxes)
    ae.add_requested_context(GeneralECGWaveformStorage, transfer_syntaxes)
    ae.add_requested_context(SecondaryCaptureImageStorage, transfer_syntaxes)

    # If file has a specific SOP class not in our list, try to add it too
    if sop_class_uid and sop_class_uid not in (
        TwelveLeadECGWaveformStorage,
        GeneralECGWaveformStorage,
        SecondaryCaptureImageStorage,
    ):
        try:
            ae.add_requested_context(sop_class_uid, transfer_syntaxes)
        except Exception:
            pass

    try:
        assoc = ae.associate(host, port, ae_title=remote_ae)
    except Exception as e:
        return False, f"Connection failed: {e}"

    if not assoc.is_established:
        return False, f"Association rejected by {remote_ae}@{host}:{port}"

    try:
        status = assoc.send_c_store(ds)
        if status and status.Status == 0x0000:
            assoc.release()
            sop_uid = str(getattr(ds, "SOPInstanceUID", ""))
            logger.info(f"[Store SCU] Successfully sent to {remote_ae}@{host}:{port} SOP={sop_uid}")
            return True, "Successfully sent to PACS"
        else:
            status_val = status.Status if status else "unknown"
            msg = f"C-STORE failed with status 0x{status_val:04X}" if isinstance(status_val, int) else f"C-STORE failed: {status_val}"
            assoc.release()
            return False, msg
    except Exception as e:
        try:
            assoc.release()
        except Exception:
            pass
        return False, f"C-STORE error: {e}"


def test_pacs_connection(host: str, port: int, remote_ae: str, local_ae: str) -> Tuple[bool, str]:
    """Test PACS connectivity using C-ECHO (Verification SOP Class).

    Returns:
        (success, message)
    """
    if not host:
        return False, "Host not configured"

    ae = AE(ae_title=local_ae)
    ae.add_requested_context(Verification)

    try:
        assoc = ae.associate(host, port, ae_title=remote_ae)
    except Exception as e:
        return False, f"Connection failed: {e}"

    if not assoc.is_established:
        return False, f"Association rejected by {remote_ae}@{host}:{port}"

    try:
        status = assoc.send_c_echo()
        assoc.release()
        if status and status.Status == 0x0000:
            return True, f"C-ECHO successful to {remote_ae}@{host}:{port}"
        else:
            return False, "C-ECHO failed"
    except Exception as e:
        try:
            assoc.release()
        except Exception:
            pass
        return False, f"C-ECHO error: {e}"


def send_result_to_pacs(result_id: int, flask_app) -> Tuple[bool, str]:
    """High-level: look up ECG result, embed diagnosis, send to PACS, update DB.

    Args:
        result_id: ECGResult ID.
        flask_app: Flask application instance.

    Returns:
        (success, message)
    """
    with flask_app.app_context():
        from models import db, ECGResult, get_setting

        result = ECGResult.query.get(result_id)
        if not result:
            return False, "Result not found"

        if not result.file_path or not os.path.exists(result.file_path):
            return False, "DICOM file not found"

        # Read PACS config
        host = get_setting("pacs_host", "")
        port = int(get_setting("pacs_port", "104"))
        remote_ae = get_setting("pacs_ae", "PACS")
        local_ae = get_setting("pacs_local_ae", "ECG_SCU")

        if not host:
            return False, "PACS server not configured"

        # If diagnosis exists, embed it into a copy before sending
        file_or_buffer = result.file_path
        if result.diagnosis:
            from services.ecg_parser import embed_diagnosis_in_dicom
            try:
                buf = embed_diagnosis_in_dicom(
                    result.file_path, result.diagnosis, result.diagnosed_by
                )
                file_or_buffer = buf
            except Exception as e:
                logger.error(f"[Store SCU] Failed to embed diagnosis: {e}")
                # Continue with original file

        # Send to PACS
        success, message = send_to_pacs(file_or_buffer, host, port, remote_ae, local_ae)

        # Update DB
        if success:
            result.pacs_send_status = "SENT"
            result.pacs_sent_at = datetime.now()
        else:
            result.pacs_send_status = "FAILED"

        db.session.commit()
        return success, message
