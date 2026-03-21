"""
ECG Waveform Parser - Extract 12-lead ECG data from DICOM files.
Parses WaveformSequence and returns structured data for visualization.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pydicom
from pydicom.dataset import Dataset


# Standard 12-lead order
STANDARD_LEADS = ["I", "II", "III", "aVR", "aVL", "aVF", "V1", "V2", "V3", "V4", "V5", "V6"]

LEAD_NAME_MAP = {
    "Lead I": "I", "Lead II": "II", "Lead III": "III",
    "Lead aVR": "aVR", "Lead aVL": "aVL", "Lead aVF": "aVF",
    "Lead V1": "V1", "Lead V2": "V2", "Lead V3": "V3",
    "Lead V4": "V4", "Lead V5": "V5", "Lead V6": "V6",
    "I": "I", "II": "II", "III": "III",
    "aVR": "aVR", "aVL": "aVL", "aVF": "aVF",
    "V1": "V1", "V2": "V2", "V3": "V3",
    "V4": "V4", "V5": "V5", "V6": "V6",
}


@dataclass
class ECGChannel:
    name: str  # e.g., "I", "II", "V1"
    data: List[float]  # waveform samples in millivolts
    sensitivity: float = 1.0
    unit: str = "mV"


@dataclass
class ECGWaveform:
    channels: List[ECGChannel] = field(default_factory=list)
    sampling_frequency: float = 500.0
    num_samples: int = 0
    duration_seconds: float = 0.0
    bits_allocated: int = 16


@dataclass
class ECGData:
    # Patient info
    patient_id: str = ""
    patient_name: str = ""
    patient_sex: str = ""
    patient_age: str = ""
    patient_birth_date: str = ""

    # Study info
    study_date: str = ""
    study_time: str = ""
    acquisition_datetime: str = ""
    accession_number: str = ""
    study_description: str = ""
    study_instance_uid: str = ""

    # Device info
    manufacturer: str = ""
    model_name: str = ""
    device_serial: str = ""
    software_version: str = ""
    institution: str = ""

    # Waveforms
    waveforms: List[ECGWaveform] = field(default_factory=list)

    # Annotations/Measurements
    annotations: List[dict] = field(default_factory=list)
    interpretation_texts: List[str] = field(default_factory=list)
    measurements: dict = field(default_factory=dict)


def parse_dicom_ecg(filepath: str) -> Optional[ECGData]:
    """Parse a DICOM ECG file and extract waveform data."""
    if not os.path.exists(filepath):
        return None

    ds = pydicom.dcmread(filepath, force=True)
    ecg = ECGData()

    # Patient info
    ecg.patient_id = str(getattr(ds, "PatientID", ""))
    ecg.patient_name = str(getattr(ds, "PatientName", ""))
    ecg.patient_sex = str(getattr(ds, "PatientSex", ""))
    ecg.patient_age = str(getattr(ds, "PatientAge", ""))
    ecg.patient_birth_date = str(getattr(ds, "PatientBirthDate", ""))

    # Study info
    ecg.study_date = str(getattr(ds, "StudyDate", ""))
    ecg.study_time = str(getattr(ds, "StudyTime", ""))
    ecg.acquisition_datetime = str(getattr(ds, "AcquisitionDateTime", ""))
    ecg.accession_number = str(getattr(ds, "AccessionNumber", ""))
    ecg.study_description = str(getattr(ds, "StudyDescription", ""))
    ecg.study_instance_uid = str(getattr(ds, "StudyInstanceUID", ""))

    # Device info
    ecg.manufacturer = str(getattr(ds, "Manufacturer", ""))
    ecg.model_name = str(getattr(ds, "ManufacturerModelName", ""))
    ecg.device_serial = str(getattr(ds, "DeviceSerialNumber", ""))
    ecg.software_version = str(getattr(ds, "SoftwareVersions", ""))
    ecg.institution = str(getattr(ds, "InstitutionName", ""))

    # Parse waveform sequences
    if hasattr(ds, "WaveformSequence"):
        for wf_seq in ds.WaveformSequence:
            waveform = _parse_waveform_sequence(wf_seq)
            if waveform:
                ecg.waveforms.append(waveform)

    # Parse annotations
    if hasattr(ds, "WaveformAnnotationSequence"):
        ecg.annotations, ecg.interpretation_texts = _parse_annotations(ds.WaveformAnnotationSequence)

    return ecg


def _parse_waveform_sequence(wf: Dataset) -> Optional[ECGWaveform]:
    """Parse a single WaveformSequence item."""
    num_channels = int(getattr(wf, "NumberOfWaveformChannels", 0))
    num_samples = int(getattr(wf, "NumberOfWaveformSamples", 0))
    sampling_freq = float(getattr(wf, "SamplingFrequency", 500))
    bits_allocated = int(getattr(wf, "WaveformBitsAllocated", 16))

    if num_channels == 0 or num_samples == 0:
        return None

    # Get raw waveform data
    waveform_data = getattr(wf, "WaveformData", None)
    if waveform_data is None:
        return None

    # Decode waveform data
    if bits_allocated == 16:
        dtype = np.int16
    elif bits_allocated == 32:
        dtype = np.int32
    else:
        dtype = np.int16

    raw = np.frombuffer(waveform_data, dtype=dtype)

    # Reshape: data is interleaved [ch0_s0, ch1_s0, ..., ch0_s1, ch1_s1, ...]
    if len(raw) != num_channels * num_samples:
        return None

    raw = raw.reshape(num_samples, num_channels)

    # Parse channel definitions
    channels = []
    channel_defs = getattr(wf, "ChannelDefinitionSequence", [])

    for ch_idx in range(num_channels):
        ch_def = channel_defs[ch_idx] if ch_idx < len(channel_defs) else None

        # Get channel name
        name = f"Ch{ch_idx}"
        if ch_def:
            src_seq = getattr(ch_def, "ChannelSourceSequence", None)
            if src_seq and len(src_seq) > 0:
                code_meaning = str(getattr(src_seq[0], "CodeMeaning", ""))
                name = LEAD_NAME_MAP.get(code_meaning, code_meaning)

        # Get sensitivity and correction
        sensitivity = float(getattr(ch_def, "ChannelSensitivity", 1.0)) if ch_def else 1.0
        correction = float(getattr(ch_def, "ChannelSensitivityCorrectionFactor", 1.0)) if ch_def else 1.0
        baseline = float(getattr(ch_def, "ChannelBaseline", 0.0)) if ch_def else 0.0

        # Convert to physical units (millivolts)
        # Formula: physical = (raw + baseline) * sensitivity * correction / 1000
        ch_raw = raw[:, ch_idx].astype(np.float64)
        ch_physical = (ch_raw + baseline) * sensitivity * correction / 1000.0  # to mV

        channels.append(ECGChannel(
            name=name,
            data=ch_physical.tolist(),
            sensitivity=sensitivity,
            unit="mV",
        ))

    waveform = ECGWaveform(
        channels=channels,
        sampling_frequency=sampling_freq,
        num_samples=num_samples,
        duration_seconds=num_samples / sampling_freq,
        bits_allocated=bits_allocated,
    )

    return waveform


def _parse_annotations(ann_seq) -> tuple:
    """Parse WaveformAnnotationSequence for diagnostic info.

    Returns (annotations, interpretation_texts) where:
    - annotations: list of dicts with numeric measurements (concept/value/unit)
    - interpretation_texts: list of strings from tag (0070,0006) UnformattedTextValue
    """
    annotations = []
    interpretation_texts = []

    for ann in ann_seq:
        entry = {}

        has_text = hasattr(ann, "UnformattedTextValue")
        has_numeric = hasattr(ann, "NumericValue")

        # Tag (0070,0006) UnformattedTextValue - device interpretation text
        if has_text:
            text = str(ann.UnformattedTextValue).strip()
            if text:
                entry["text"] = text
                # Collect ALL UnformattedTextValue as interpretation text
                interpretation_texts.append(text)

        # Concept name
        if hasattr(ann, "ConceptNameCodeSequence") and len(ann.ConceptNameCodeSequence) > 0:
            concept = ann.ConceptNameCodeSequence[0]
            entry["concept"] = str(getattr(concept, "CodeMeaning", ""))

        # Numeric value
        if has_numeric:
            entry["value"] = str(ann.NumericValue)

        # Measurement units
        if hasattr(ann, "MeasurementUnitsCodeSequence") and len(ann.MeasurementUnitsCodeSequence) > 0:
            unit = ann.MeasurementUnitsCodeSequence[0]
            entry["unit"] = str(getattr(unit, "CodeMeaning", ""))

        # Referenced waveform channels
        if hasattr(ann, "ReferencedWaveformChannels"):
            entry["channels"] = list(ann.ReferencedWaveformChannels)

        if entry:
            annotations.append(entry)

    return annotations, interpretation_texts


def _generate_demo_ecg(fs: float = 500, duration: float = 10.0) -> ECGWaveform:
    """Generate a realistic demo 12-lead ECG waveform for display purposes."""
    num_samples = int(fs * duration)
    t = np.arange(num_samples) / fs

    # Heart rate ~72 bpm (period ~0.833s)
    hr = 72
    period = 60.0 / hr

    channels = []
    # Amplitude factors for each lead (relative to Lead II)
    lead_factors = {
        "I": 0.6, "II": 1.0, "III": 0.4,
        "aVR": -0.5, "aVL": 0.3, "aVF": 0.7,
        "V1": -0.3, "V2": 0.8, "V3": 1.5,
        "V4": 1.8, "V5": 1.2, "V6": 0.7,
    }

    for lead_name in STANDARD_LEADS:
        factor = lead_factors.get(lead_name, 1.0)
        signal = np.zeros(num_samples)

        for beat_start in np.arange(0, duration, period):
            beat_t = t - beat_start

            # P wave (duration ~0.1s, centered at 0.05s)
            p_mask = (beat_t >= 0) & (beat_t < 0.1)
            signal[p_mask] += factor * 0.15 * np.sin(np.pi * (beat_t[p_mask]) / 0.1)

            # QRS complex
            # Q wave (small negative, ~0.02s)
            q_mask = (beat_t >= 0.14) & (beat_t < 0.16)
            signal[q_mask] += factor * (-0.1) * np.sin(np.pi * (beat_t[q_mask] - 0.14) / 0.02)

            # R wave (tall positive, ~0.04s)
            r_mask = (beat_t >= 0.16) & (beat_t < 0.20)
            signal[r_mask] += factor * 1.2 * np.sin(np.pi * (beat_t[r_mask] - 0.16) / 0.04)

            # S wave (small negative, ~0.02s)
            s_mask = (beat_t >= 0.20) & (beat_t < 0.22)
            signal[s_mask] += factor * (-0.25) * np.sin(np.pi * (beat_t[s_mask] - 0.20) / 0.02)

            # T wave (duration ~0.16s, centered at 0.36s)
            t_mask = (beat_t >= 0.30) & (beat_t < 0.46)
            signal[t_mask] += factor * 0.3 * np.sin(np.pi * (beat_t[t_mask] - 0.30) / 0.16)

        # Add subtle noise
        signal += np.random.normal(0, 0.005, num_samples)

        channels.append(ECGChannel(
            name=lead_name,
            data=signal.tolist(),
            sensitivity=1.0,
            unit="mV",
        ))

    return ECGWaveform(
        channels=channels,
        sampling_frequency=fs,
        num_samples=num_samples,
        duration_seconds=duration,
        bits_allocated=16,
    )


def ecg_data_to_json(ecg: ECGData, waveform_index: int = 0) -> dict:
    """Convert ECGData to JSON-serializable dict for the viewer."""
    result = {
        "patient": {
            "id": ecg.patient_id,
            "name": ecg.patient_name,
            "sex": ecg.patient_sex,
            "age": ecg.patient_age,
            "birthDate": ecg.patient_birth_date,
        },
        "study": {
            "date": ecg.study_date,
            "time": ecg.study_time,
            "accession": ecg.accession_number,
            "description": ecg.study_description,
        },
        "device": {
            "manufacturer": ecg.manufacturer,
            "model": ecg.model_name,
            "serial": ecg.device_serial,
            "software": ecg.software_version,
        },
        "annotations": ecg.annotations,
        "interpretation_texts": ecg.interpretation_texts,
        "waveforms": [],
    }

    wf = None

    if waveform_index < len(ecg.waveforms):
        wf = ecg.waveforms[waveform_index]
    elif len(ecg.waveforms) > 0:
        wf = ecg.waveforms[0]

    if wf:
        leads = {}
        for ch in wf.channels:
            leads[ch.name] = ch.data

        result["waveforms"] = [{
            "samplingFrequency": wf.sampling_frequency,
            "numSamples": wf.num_samples,
            "durationSeconds": wf.duration_seconds,
            "leads": leads,
        }]

    return result


def extract_dicom_tags(filepath: str) -> List[dict]:
    """Extract all DICOM tags from a file for display.

    Returns list of dicts: {tag, vr, name, value}
    Sequences are flattened with indentation prefix.
    """
    if not os.path.exists(filepath):
        return []

    ds = pydicom.dcmread(filepath, force=True)
    tags = []
    _walk_dataset(ds, tags, depth=0)
    return tags


def _walk_dataset(ds: Dataset, tags: list, depth: int):
    """Recursively walk a DICOM dataset and collect tag info."""
    prefix = "  " * depth
    for elem in ds:
        tag_str = f"({elem.tag.group:04X},{elem.tag.element:04X})"
        vr = str(elem.VR) if hasattr(elem, "VR") else "??"
        name = elem.keyword or elem.description() or "Unknown"

        if elem.VR == "SQ":
            # Sequence: add header then recurse into each item
            val = f"({len(elem.value)} item{'s' if len(elem.value) != 1 else ''})" if elem.value else "(empty)"
            tags.append({"tag": tag_str, "vr": vr, "name": prefix + name, "value": val, "depth": depth})
            if elem.value:
                for i, item in enumerate(elem.value):
                    tags.append({"tag": "", "vr": "", "name": f"{prefix}  > Item #{i+1}", "value": "", "depth": depth + 1})
                    _walk_dataset(item, tags, depth + 2)
        elif elem.VR in ("OB", "OW", "OF", "OD", "UN"):
            # Binary data - show length only
            length = len(elem.value) if elem.value else 0
            tags.append({"tag": tag_str, "vr": vr, "name": prefix + name, "value": f"[binary data, {length} bytes]", "depth": depth})
        elif elem.tag == (0x7FE0, 0x0010):
            # Pixel/Waveform data - too large to display
            length = len(elem.value) if elem.value else 0
            tags.append({"tag": tag_str, "vr": vr, "name": prefix + name, "value": f"[waveform data, {length} bytes]", "depth": depth})
        else:
            # Regular value
            val = str(elem.value)
            if len(val) > 150:
                val = val[:150] + "..."
            tags.append({"tag": tag_str, "vr": vr, "name": prefix + name, "value": val, "depth": depth})


def embed_diagnosis_in_dicom(filepath: str, diagnosis: str,
                              diagnosed_by: str = None):
    """Create a DICOM copy with doctor's diagnosis replacing device interpretation.

    Removes existing text-only annotations (UnformattedTextValue without
    NumericValue — these are the device interpretation lines) and replaces
    them with the doctor's diagnosis text. Measurement annotations (those
    with NumericValue/ConceptNameCodeSequence) are preserved.

    The original file is NOT modified.

    Returns:
        BytesIO buffer containing the modified DICOM file.
    """
    from io import BytesIO

    ds = pydicom.dcmread(filepath, force=True)

    if hasattr(ds, 'WaveformAnnotationSequence'):
        # Keep only measurement annotations (those with NumericValue or
        # ConceptNameCodeSequence), remove text-only interpretation items
        kept = []
        for ann in ds.WaveformAnnotationSequence:
            has_text_only = (hasattr(ann, 'UnformattedTextValue')
                             and not hasattr(ann, 'NumericValue')
                             and not hasattr(ann, 'ConceptNameCodeSequence'))
            if not has_text_only:
                kept.append(ann)

        # Add doctor's diagnosis as replacement interpretation lines
        for line in diagnosis.split('\n'):
            stripped = line.strip()
            if stripped:
                ann_item = Dataset()
                ann_item.UnformattedTextValue = stripped  # (0070,0006)
                kept.append(ann_item)

        ds.WaveformAnnotationSequence = pydicom.Sequence(kept)
    else:
        # No existing annotations — just add diagnosis
        items = []
        for line in diagnosis.split('\n'):
            stripped = line.strip()
            if stripped:
                ann_item = Dataset()
                ann_item.UnformattedTextValue = stripped
                items.append(ann_item)
        ds.WaveformAnnotationSequence = pydicom.Sequence(items)

    # Save to buffer (original file untouched)
    buf = BytesIO()
    ds.save_as(buf)
    buf.seek(0)
    return buf
