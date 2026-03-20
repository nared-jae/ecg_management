"""
DICOM helper utilities extracted from wml_server.py.
Includes: UID generation, wildcard matching, date matching,
VR handling, charset selection, and return-key building.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

from pydicom.dataset import Dataset
from pydicom.sequence import Sequence
from pydicom.uid import UID


def stable_uid_from_text(text: str, root: str = "2.25.") -> str:
    """Generate a stable DICOM UID from text using SHA-1."""
    h = hashlib.sha1(text.encode("utf-8")).digest()
    as_int = int.from_bytes(h[:16], byteorder="big", signed=False)
    return str(UID(root + str(as_int)))


# ----------------------------
# Matching helpers
# ----------------------------
def get_first(ds: Dataset, name: str) -> Optional[str]:
    return str(getattr(ds, name)) if hasattr(ds, name) else None


def get_sps_first(query_ds: Dataset, name: str) -> Optional[str]:
    if not hasattr(query_ds, "ScheduledProcedureStepSequence"):
        return None
    seq = query_ds.ScheduledProcedureStepSequence
    if not seq or len(seq) == 0:
        return None
    sps = seq[0]
    return str(getattr(sps, name)) if hasattr(sps, name) else None


def wildcard_match(value: str, pattern: str) -> bool:
    """Support * and ? wildcards (as sent by some ECG devices)."""
    value = value.strip()
    pattern = pattern.strip()

    if "*" not in pattern and "?" not in pattern:
        return value == pattern

    rx = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return re.match(rx, value) is not None


def match_text(value: str, pattern: Optional[str]) -> bool:
    if pattern is None or pattern == "":
        return True
    return wildcard_match(str(value).strip(), str(pattern).strip())


def match_da(value_yyyymmdd: str, pattern: Optional[str]) -> bool:
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


# ----------------------------
# VR-aware empty value
# ----------------------------
def empty_value_for_vr(vr: str):
    """Return appropriate empty value for a given DICOM VR."""
    if vr in ("DA", "TM", "DT", "UI", "PN", "LO", "SH", "CS", "ST", "LT", "UT", "AE"):
        return ""
    if vr in ("US", "SS", "UL", "SL", "FL", "FD"):
        return None
    if vr in ("IS", "DS"):
        return ""
    if vr == "SQ":
        return Sequence([])
    return ""


def choose_charset(query: Dataset, full_item: Dataset) -> str:
    """Choose character set based on query preference (for device compatibility)."""
    if hasattr(query, "SpecificCharacterSet") and str(query.SpecificCharacterSet).strip():
        return str(query.SpecificCharacterSet)
    if hasattr(full_item, "SpecificCharacterSet") and str(full_item.SpecificCharacterSet).strip():
        return str(full_item.SpecificCharacterSet)
    return "ISO_IR 100"


# ----------------------------
# Return Keys builder
# ----------------------------
def build_return_dataset(query: Dataset, full_item: Dataset) -> Dataset:
    """Build a response dataset based on query return keys."""
    rsp = Dataset()
    rsp.SpecificCharacterSet = choose_charset(query, full_item)

    for elem in query:
        keyword = elem.keyword
        if not keyword:
            continue

        if elem.VR == "SQ":
            q_seq = getattr(query, keyword, None)

            if not q_seq or len(q_seq) == 0:
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
            rsp.__setattr__(keyword, empty_value_for_vr(elem.VR))

    return rsp
