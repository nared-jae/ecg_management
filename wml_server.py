from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
from datetime import date

from pydicom.dataset import Dataset
from pydicom.sequence import Sequence

from pynetdicom import AE, evt
from pynetdicom.sop_class import ModalityWorklistInformationFind
from pydicom.uid import (
    ImplicitVRLittleEndian,
    ExplicitVRLittleEndian,
    ExplicitVRBigEndian,
)

import hashlib
from pydicom.uid import UID


# สร้าง UID แบบคงที่จาก accession_number (หรือผสม HN+date ก็ได้)
def stable_uid_from_text(text: str, root: str = "2.25.") -> str:
    """
    สร้าง StudyInstanceUID แบบคงที่จากข้อความ
    - text เดิม -> UID เดิมเสมอ
    - ใช้มาตรฐาน 2.25.x (decimal UUID)
    """
    h = hashlib.sha1(text.encode("utf-8")).digest()
    as_int = int.from_bytes(h[:16], byteorder="big", signed=False)
    return str(UID(root + str(as_int)))


# ----------------------------
# Sample Worklist Data
# ----------------------------
@dataclass
class WorklistItem:
    patient_id: str
    patient_name: str
    sex: str
    birth_date: str
    accession_number: str
    requested_procedure_id: str
    requested_procedure_desc: str

    admission_id: str
    requested_procedure_priority: str

    scheduled_station_ae: str
    scheduled_station_name: str
    modality: str
    sps_id: str
    sps_desc: str
    sps_start_date: str
    sps_start_time: str

    study_instance_uid: str

    def to_full_mwl_dataset(self) -> Dataset:
        ds = Dataset()

        # NOTE: CP150 บางล็อตไม่ชอบ UTF-8 (ISO_IR 192)
        # เลยให้ตัว builder เลือก charset ตาม query เป็นหลัก
        ds.SpecificCharacterSet = "ISO_IR 192"

        ds.PatientID = self.patient_id
        ds.PatientName = self.patient_name
        ds.PatientSex = self.sex
        ds.PatientBirthDate = self.birth_date
        ds.StudyInstanceUID = stable_uid_from_text(self.accession_number)

        ds.AccessionNumber = self.accession_number
        ds.RequestedProcedureID = self.requested_procedure_id
        ds.RequestedProcedureDescription = self.requested_procedure_desc

        ds.AdmissionID = self.admission_id
        ds.RequestedProcedurePriority = self.requested_procedure_priority

        ds.RequestingPhysician = ""

        sps = Dataset()
        sps.ScheduledStationAETitle = self.scheduled_station_ae
        sps.ScheduledStationName = self.scheduled_station_name
        sps.Modality = self.modality
        sps.ScheduledProcedureStepID = self.sps_id
        sps.ScheduledProcedureStepDescription = self.sps_desc
        sps.ScheduledProcedureStepStartDate = self.sps_start_date
        sps.ScheduledProcedureStepStartTime = self.sps_start_time
        sps.ScheduledPerformingPhysicianName = ""

        ds.ScheduledProcedureStepSequence = [sps]
        return ds


SAMPLE_WORKLIST: List[WorklistItem] = [
    WorklistItem(
        study_instance_uid=stable_uid_from_text("ACC20260004"),
        patient_id="HN000004",
        patient_name="SAELI^KOMSAN",
        sex="M",
        birth_date="19680512",
        accession_number="ACC20260004",
        requested_procedure_id="RP1003",
        requested_procedure_desc="ECG Pre-op",
        admission_id="ADM20260004",
        requested_procedure_priority="ROUTINE",
        scheduled_station_ae="CP150",
        scheduled_station_name="ECG-ROOM1",
        modality="ECG",
        sps_id="SPS1003",
        sps_desc="ECG Pre-operation",
        sps_start_date=date.today().strftime("%Y%m%d"),
        sps_start_time="110000",
    ),
    WorklistItem(
        study_instance_uid=stable_uid_from_text("ACC20260005"),
        patient_id="HN000005",
        patient_name="NARIN^SAENGCHAN",
        sex="M",
        birth_date="19790322",
        accession_number="ACC20260005",
        requested_procedure_id="RP1004",
        requested_procedure_desc="ECG Annual Checkup",
        admission_id="ADM20260005",
        requested_procedure_priority="ROUTINE",
        scheduled_station_ae="CP150",
        scheduled_station_name="ECG-ROOM1",
        modality="ECG",
        sps_id="SPS1004",
        sps_desc="ECG Checkup",
        sps_start_date=date.today().strftime("%Y%m%d"),
        sps_start_time="113000",
    ),
    WorklistItem(
        study_instance_uid=stable_uid_from_text("ACC20260006"),
        patient_id="HN000006",
        patient_name="SUPAN^WONGSA",
        sex="F",
        birth_date="19840514",
        accession_number="ACC20260006",
        requested_procedure_id="RP1005",
        requested_procedure_desc="ECG Chest Pain",
        admission_id="ADM20260006",
        requested_procedure_priority="URGENT",
        scheduled_station_ae="CP150",
        scheduled_station_name="ECG-ROOM2",
        modality="ECG",
        sps_id="SPS1005",
        sps_desc="ECG Chest Pain",
        sps_start_date=date.today().strftime("%Y%m%d"),
        sps_start_time="130000",
    ),
    WorklistItem(
        study_instance_uid=stable_uid_from_text("ACC20260007"),
        patient_id="HN000007",
        patient_name="KANYA^PHROMDEE",
        sex="F",
        birth_date="19951109",
        accession_number="ACC20260007",
        requested_procedure_id="RP1006",
        requested_procedure_desc="ECG Pregnancy Screening",
        admission_id="ADM20260007",
        requested_procedure_priority="ROUTINE",
        scheduled_station_ae="CP150",
        scheduled_station_name="ECG-ROOM2",
        modality="ECG",
        sps_id="SPS1006",
        sps_desc="ECG Screening",
        sps_start_date=date.today().strftime("%Y%m%d"),
        sps_start_time="143000",
    ),
    WorklistItem(
        study_instance_uid=stable_uid_from_text("ACC20260008"),
        patient_id="HN000008",
        patient_name="WICHIT^BOONMA",
        sex="M",
        birth_date="19560218",
        accession_number="ACC20260008",
        requested_procedure_id="RP1007",
        requested_procedure_desc="ECG Post MI Follow-up",
        admission_id="ADM20260008",
        requested_procedure_priority="URGENT",
        scheduled_station_ae="CP150",
        scheduled_station_name="ECG-ROOM1",
        modality="ECG",
        sps_id="SPS1007",
        sps_desc="ECG Post MI",
        sps_start_date=date.today().strftime("%Y%m%d"),
        sps_start_time="153000",
    ),
    WorklistItem(
        study_instance_uid=stable_uid_from_text("ACC20260009"),
        patient_id="HN000009",
        patient_name="SOMCHAI^KAEWDEE",
        sex="M",
        birth_date="19691201",
        accession_number="ACC20260009",
        requested_procedure_id="RP1008",
        requested_procedure_desc="ECG Hypertension Follow-up",
        admission_id="ADM20260009",
        requested_procedure_priority="ROUTINE",
        scheduled_station_ae="CP150",
        scheduled_station_name="ECG-ROOM3",
        modality="ECG",
        sps_id="SPS1008",
        sps_desc="ECG HT Follow-up",
        sps_start_date=date.today().strftime("%Y%m%d"),
        sps_start_time="160000",
    ),
]


# ----------------------------
# Matching helpers
# ----------------------------
def _get_first(ds: Dataset, name: str) -> Optional[str]:
    return str(getattr(ds, name)) if hasattr(ds, name) else None


def _get_sps_first(query_ds: Dataset, name: str) -> Optional[str]:
    if not hasattr(query_ds, "ScheduledProcedureStepSequence"):
        return None
    seq = query_ds.ScheduledProcedureStepSequence
    if not seq or len(seq) == 0:
        return None
    sps = seq[0]
    return str(getattr(sps, name)) if hasattr(sps, name) else None


def _wildcard_match(value: str, pattern: str) -> bool:
    """
    รองรับ * และ ? แบบง่าย ๆ (CP150 บางทีส่งมาแนวนี้)
    """
    value = value.strip()
    pattern = pattern.strip()

    # exact
    if "*" not in pattern and "?" not in pattern:
        return value == pattern

    # แปลง wildcard -> regex แบบง่าย
    import re
    rx = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return re.match(rx, value) is not None


def _match_text(value: str, pattern: Optional[str]) -> bool:
    if pattern is None or pattern == "":
        return True
    value = str(value).strip()
    pattern = str(pattern).strip()
    return _wildcard_match(value, pattern)


def _match_da(value_yyyymmdd: str, pattern: Optional[str]) -> bool:
    if not pattern:
        return True

    value_yyyymmdd = str(value_yyyymmdd).strip()

    for p in str(pattern).split("\\"):
        p = p.strip()
        if not p:
            continue

        if "-" in p:
            start, end = p.split("-", 1)
            start = start.strip()
            end = end.strip()

            if start and value_yyyymmdd < start:
                continue
            if end and value_yyyymmdd > end:
                continue
            return True

        if value_yyyymmdd == p:
            return True

    return False


def _match_item(item: WorklistItem, query_ds: Dataset) -> bool:
    q_pid = _get_first(query_ds, "PatientID")
    q_pname = _get_first(query_ds, "PatientName")

    q_date = _get_sps_first(query_ds, "ScheduledProcedureStepStartDate")
    q_modality = _get_sps_first(query_ds, "Modality")
    q_station = _get_sps_first(query_ds, "ScheduledStationAETitle")

    if not _match_text(item.patient_id, q_pid):
        return False
    if not _match_text(item.patient_name, q_pname):
        return False
    if not _match_da(item.sps_start_date, q_date):
        return False
    if not _match_text(item.modality, q_modality):
        return False
    if not _match_text(item.scheduled_station_ae, q_station):
        return False

    return True


# ----------------------------
# VR-aware empty value
# ----------------------------
def _empty_value_for_vr(vr: str):
    """
    ใส่ค่าว่างให้ถูก VR (บาง modality ไม่ชอบ "" กับ DA/TM/US ฯลฯ)
    """
    if vr in ("DA", "TM", "DT", "UI", "PN", "LO", "SH", "CS", "ST", "LT", "UT", "AE"):
        return ""
    if vr in ("US", "SS", "UL", "SL", "FL", "FD"):
        return None  # ปล่อยว่างแบบ Null
    if vr in ("IS", "DS"):
        return ""
    if vr == "SQ":
        return Sequence([])
    return ""


def _choose_charset(query: Dataset, full_item: Dataset) -> str:
    """
    CP150 บาง FW ไม่รองรับ ISO_IR 192
    ถ้า query ส่ง SpecificCharacterSet มา ให้ใช้ตามนั้นก่อน
    """
    if hasattr(query, "SpecificCharacterSet") and str(query.SpecificCharacterSet).strip():
        return str(query.SpecificCharacterSet)
    if hasattr(full_item, "SpecificCharacterSet") and str(full_item.SpecificCharacterSet).strip():
        return str(full_item.SpecificCharacterSet)
    # default ปลอดภัยกว่าสำหรับเครื่องเก่า
    return "ISO_IR 100"


# ----------------------------
# Return Keys builder
# ----------------------------
def build_return_dataset(query: Dataset, full_item: Dataset) -> Dataset:
    rsp = Dataset()
    rsp.SpecificCharacterSet = _choose_charset(query, full_item)

    for elem in query:
        keyword = elem.keyword
        if not keyword:
            continue

        if elem.VR == "SQ":
            q_seq = getattr(query, keyword, None)

            # บาง modality ส่ง SQ เปล่า ๆ (0 items) แต่คาดหวังให้เราคืน 1 item ที่มี keys
            if not q_seq or len(q_seq) == 0:
                # ถ้ามี full seq ก็ยกมา item แรก แล้ว filter ด้วย empty query-item
                full_seq = getattr(full_item, keyword, None)
                full_item_sq = full_seq[0] if full_seq and len(full_seq) > 0 else Dataset()

                out_item = build_return_dataset(Dataset(), full_item_sq)
                rsp.__setattr__(keyword, Sequence([out_item]))
                continue

            out_seq_items = []
            full_seq = getattr(full_item, keyword, None)

            for i, q_item in enumerate(q_seq):
                full_item_sq = full_seq[i] if full_seq and len(full_seq) > i else Dataset()
                out_seq_items.append(build_return_dataset(q_item, full_item_sq))

            rsp.__setattr__(keyword, Sequence(out_seq_items))
            continue

        if hasattr(full_item, keyword):
            rsp.__setattr__(keyword, getattr(full_item, keyword))
        else:
            rsp.__setattr__(keyword, _empty_value_for_vr(elem.VR))

    return rsp


# ----------------------------
# C-FIND handler
# ----------------------------
def handle_find(event):
    query = event.identifier
    assoc = event.assoc

    print("\n--- C-FIND MWL Request ---")
    try:
        print(f"From AE={assoc.requestor.ae_title!r} IP={assoc.requestor.address}:{assoc.requestor.port}")
        print(f"To   AE={assoc.acceptor.ae_title!r}")
    except Exception:
        pass
    print(query)

    matches = [it for it in SAMPLE_WORKLIST if _match_item(it, query)]
    print(f"Matched items: {len(matches)}")

    for it in matches:
        full = it.to_full_mwl_dataset()
        rsp = build_return_dataset(query, full)
        yield 0xFF00, rsp

    yield 0x0000, None


def main():
    # ต้องตรงกับค่าใน CP150 (Calling/Called AE) ที่ตั้งไว้ในเครื่อง
    AE_TITLE = "MWL"
    PORT = 6701

    ae = AE(ae_title=AE_TITLE)

    # CP150 บางรุ่น picky เรื่อง transfer syntax: ใส่ไว้ให้ครบ ๆ
    ae.add_supported_context(
        ModalityWorklistInformationFind,
        [ImplicitVRLittleEndian, ExplicitVRLittleEndian, ExplicitVRBigEndian],
    )

    handlers = [(evt.EVT_C_FIND, handle_find)]

    print(f"Starting MWL Server: AE={AE_TITLE} on port {PORT}")
    print("Sample data loaded:", len(SAMPLE_WORKLIST), "items")
    print("Stop with Ctrl+C\n")

    ae.start_server(("0.0.0.0", PORT), block=True, evt_handlers=handlers)


if __name__ == "__main__":
    main()
