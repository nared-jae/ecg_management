"""
HL7 v3 Annotated ECG (aECG / FDA XML) Generator.

Converts parsed DICOM ECG data into HL7 v3 aECG XML format,
matching the Mindray R700 "XML FDA" export structure
(schema: PORT_MT020001.xsd, namespace: urn:hl7-org:v3).
"""
from __future__ import annotations

from typing import List, Optional

# ===== Constants =====

NS = 'urn:hl7-org:v3'
NS_VOC = 'urn:hl7-org:v3/voc'
NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'

CS_MDC = '2.16.840.1.113883.6.24'
CS_CPT4 = '2.16.840.1.113883.6.12'
CS_ACTCODE = '2.16.840.1.113883.5.4'
CS_GENDER = '2.16.840.1.113883.5.1'
CS_RACE = '2.16.840.1.113883.5.104'

STANDARD_LEADS = ['I', 'II', 'III', 'aVR', 'aVL', 'aVF',
                  'V1', 'V2', 'V3', 'V4', 'V5', 'V6']

LEAD_MDC_CODES = {
    'I': 'MDC_ECG_LEAD_I', 'II': 'MDC_ECG_LEAD_II',
    'III': 'MDC_ECG_LEAD_III',
    'aVR': 'MDC_ECG_LEAD_AVR', 'aVL': 'MDC_ECG_LEAD_AVL',
    'aVF': 'MDC_ECG_LEAD_AVF',
    'V1': 'MDC_ECG_LEAD_V1', 'V2': 'MDC_ECG_LEAD_V2',
    'V3': 'MDC_ECG_LEAD_V3', 'V4': 'MDC_ECG_LEAD_V4',
    'V5': 'MDC_ECG_LEAD_V5', 'V6': 'MDC_ECG_LEAD_V6',
}

# DICOM concept name → (MDC code, unit)
CONCEPT_TO_MDC = {
    'Heart Rate': ('MDC_ECG_HEART_RATE', 'bpm'),
    'Ventricular Heart Rate': ('MDC_ECG_HEART_RATE', 'bpm'),
    'HR': ('MDC_ECG_HEART_RATE', 'bpm'),    # Lepu Medical uses abbreviated name
    'PR Interval': ('MDC_ECG_TIME_PD_PR', 'ms'),
    'QRS Duration': ('MDC_ECG_TIME_PD_QRS', 'ms'),
    'QT Interval': ('MDC_ECG_TIME_PD_QT', 'ms'),
    'QTc Interval': ('MDC_ECG_TIME_PD_QTc', 'ms'),
    'P Axis': ('MINDRAY_ECG_P_AXIS', 'deg'),
    'QRS Axis': ('MINDRAY_ECG_QRS_AXIS', 'deg'),
    'T Axis': ('MINDRAY_ECG_T_AXIS', 'deg'),
    'RR Interval': ('MDC_ECG_TIME_PD_RR', 'ms'),
}

# Per-lead Mindray measurement codes in standard order
MINDRAY_LEAD_MEASUREMENTS = [
    ('MINDRAY_P_ONSET', 'ms'), ('MINDRAY_P_DUR', 'ms'),
    ('MINDRAY_QRS_ONSET', 'ms'), ('MINDRAY_QRS_DURATION', 'ms'),
    ('MINDRAY_Q_DUR', 'ms'), ('MINDRAY_R_DUR', 'ms'),
    ('MINDRAY_S_DUR', 'ms'), ('MINDRAY_R_PRIME_DUR', 'ms'),
    ('MINDRAY_S_PRIME_DUR', 'ms'), ('MINDRAY_P_POS_DUR', 'ms'),
    ('MINDRAY_QRS_DEF', 'ms'),
    ('MINDRAY_P_POS_AMP', 'uV'), ('MINDRAY_P_NEG_AMP', 'uV'),
    ('MINDRAY_QRS_P2P', 'uV'),
    ('MINDRAY_Q_AMP', 'uV'), ('MINDRAY_R_AMP', 'uV'),
    ('MINDRAY_S_AMP', 'uV'), ('MINDRAY_R_PRIME_AMP', 'uV'),
    ('MINDRAY_S_PRIME_AMP', 'uV'), ('MINDRAY_ST_AMP', 'uV'),
    ('MINDRAY_2_8THS_ST_T', 'uV'), ('MINDRAY_3_8THS_ST_T', 'uV'),
    ('MINDRAY_T_POS_AMP', 'uV'), ('MINDRAY_T_NEG_AMP', 'uV'),
    ('MINDRAY_QRS_AREA', 'uV*ms'), ('MINDRAY_R_NOTCH_CNT', 'N/A'),
    ('MINDRAY_DW_CONF', '%'), ('MINDRAY_ST_SLOPE', 'deg'),
    ('MINDRAY_T_ONSET', 'ms'), ('MINDRAY_T_DUR', 'ms'),
    ('MINDRAY_T_POS_DUR', 'ms'), ('MINDRAY_QT_INTERVAL', 'ms'),
]

MISSING_VALUE = '-32768'

# Normalize DICOM unit names to abbreviations used in HL7 aECG
UNIT_NORMALIZE = {
    'heart beats per minute': 'bpm',
    'beats per minute': 'bpm',
    'millisecond': 'ms',
    'milliseconds': 'ms',
    'degree': 'deg',
    'degrees': 'deg',
    'millivolt': 'mV',
    'millivolts': 'mV',
    'microvolt': 'uV',
    'microvolts': 'uV',
}

# Mindray model name mapping (DICOM stores short names)
MINDRAY_MODEL_NAMES = {
    'R700': 'BeneHeart R700',
    'R300': 'BeneHeart R300',
}

# ST lead concept name → lead name mapping
ST_LEAD_MAP = {
    'ST I': 'I', 'ST II': 'II', 'ST III': 'III',
    'ST AVR': 'aVR', 'ST AVL': 'aVL', 'ST AVF': 'aVF',
    'ST V1': 'V1', 'ST V2': 'V2', 'ST V3': 'V3',
    'ST V4': 'V4', 'ST V5': 'V5', 'ST V6': 'V6',
}


def _normalize_unit(unit: str) -> str:
    """Normalize DICOM unit string to standard abbreviation."""
    return UNIT_NORMALIZE.get(unit.lower().strip(), unit)


def _esc(text: str) -> str:
    """XML-escape a string."""
    return (text.replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace("'", '&apos;')
            .replace('"', '&quot;'))


def _make_timestamp(date_str: str, time_str: str) -> str:
    """Combine DICOM date (YYYYMMDD) and time (HHMMSS.fff) into YYYYMMDDhhmmss."""
    d = date_str or ''
    t = (time_str or '').split('.')[0]  # remove fractional
    if len(d) == 8 and len(t) >= 6:
        return d + t[:6]
    elif len(d) == 8 and len(t) >= 4:
        return d + t[:4] + '00'
    elif len(d) == 8:
        return d + '000000'
    return ''


def _get_start_timestamp(ecg_data) -> str:
    """Get start timestamp, preferring AcquisitionDateTime over StudyDate/StudyTime."""
    acq = (ecg_data.acquisition_datetime or '').split('.')[0]  # remove fractional
    if len(acq) >= 14:
        return acq[:14]
    return _make_timestamp(ecg_data.study_date, ecg_data.study_time)


# DICOM age unit → Mindray HL7 age unit mapping
_AGE_UNIT_MAP = {'Y': 'years', 'M': 'months', 'W': 'weeks', 'D': 'days'}


def _format_age_hl7(dicom_age: str) -> str:
    """Convert DICOM age string (e.g. '57Y') to Mindray HL7 format ('P57years')."""
    age = (dicom_age or '').strip()
    if not age:
        return ''
    if age[-1] in _AGE_UNIT_MAP:
        num = age[:-1].lstrip('0') or '0'
        return f'P{num}{_AGE_UNIT_MAP[age[-1]]}'
    return age


def _make_end_timestamp(start_ts: str, duration: float) -> str:
    """Calculate end timestamp from start + duration seconds."""
    if len(start_ts) < 14:
        return start_ts
    try:
        from datetime import datetime, timedelta
        dt = datetime.strptime(start_ts, '%Y%m%d%H%M%S')
        dt_end = dt + timedelta(seconds=int(duration))
        return dt_end.strftime('%Y%m%d%H%M%S')
    except (ValueError, TypeError):
        return start_ts


def _generate_oid(ecg_data) -> str:
    """Generate an OID-like identifier matching Mindray format.

    Format: 755.{serial_digits}.{YYYY}{M}{DD}.{H}{M}{SS}
    where month/day/hour/minute are NOT zero-padded.
    """
    serial = ecg_data.device_serial or '0'
    serial_digits = ''.join(c for c in serial if c.isdigit()) or '0'
    ts = _get_start_timestamp(ecg_data)
    if len(ts) >= 14:
        year = ts[:4]
        month = str(int(ts[4:6]))    # no zero-pad
        day = str(int(ts[6:8]))      # no zero-pad
        hour = str(int(ts[8:10]))    # no zero-pad
        minute = str(int(ts[10:12])) # no zero-pad
        second = ts[12:14]
        date_seg = f"{year}{month}{day}"
        time_seg = f"{hour}{minute}{second}"
        return f"755.{serial_digits}.{date_seg}.{time_seg}"
    return f"755.{serial_digits}.0.0"


def _extract_global_annotations(ecg_data) -> dict:
    """Extract global measurements from DICOM annotations.

    Returns dict mapping MDC/MINDRAY code → (value, unit).
    """
    result = {}
    for ann in (ecg_data.annotations or []):
        concept = ann.get('concept', '')
        value = ann.get('value', '')
        unit = ann.get('unit', '')

        if concept in CONCEPT_TO_MDC and value:
            mdc_code, default_unit = CONCEPT_TO_MDC[concept]
            result[mdc_code] = (value, _normalize_unit(unit) if unit else default_unit)

        # RV5, SV1, RV5+SV1 — exact concept match to avoid cross-contamination
        if concept == 'RV5' and value:
            result['MINDRAY_ECG_RV5'] = (value, 'mV')
        elif concept == 'SV1' and value:
            result['MINDRAY_ECG_SV1'] = (value, 'mV')
        elif concept == 'RV5+SV1' and value:
            result['MINDRAY_ECG_RV5_PLUS_SV1'] = (value, 'mV')

    # Always include P_AXIS (use -32768 if not available from DICOM)
    if 'MINDRAY_ECG_P_AXIS' not in result:
        result['MINDRAY_ECG_P_AXIS'] = (MISSING_VALUE, 'deg')

    # QTc with Hodges sub-annotation
    if 'MDC_ECG_TIME_PD_QTc' in result:
        result['MINDRAY_ECG_TIME_PD_QTcH'] = result['MDC_ECG_TIME_PD_QTc']

    return result


def _extract_per_lead_annotations(ecg_data) -> dict:
    """Extract per-lead ST measurements from DICOM annotations.

    DICOM stores per-lead ST values as global annotations with concept
    names like "ST I", "ST II", etc. (not via ReferencedWaveformChannels).

    Returns dict mapping lead_name → {'ST_AMP': value_in_uV}.
    """
    per_lead = {}
    for ann in (ecg_data.annotations or []):
        concept = ann.get('concept', '')
        value = ann.get('value', '')
        if concept in ST_LEAD_MAP and value:
            lead = ST_LEAD_MAP[concept]
            try:
                # ST values from DICOM are in mV, convert to uV (int)
                st_uv = int(round(float(value) * 1000))
                per_lead[lead] = {'ST_AMP': str(st_uv)}
            except (ValueError, TypeError):
                pass
    return per_lead


def _waveform_to_uv_digits(channel_data: List[float]) -> str:
    """Convert mV float waveform to space-separated µV integer string."""
    return ' '.join(str(int(round(v * 1000))) for v in channel_data)


# ===== XML Section Builders =====

def _build_xml_header() -> str:
    return "<?xml version=\"1.0\" encoding='utf-8' ?>\n"


def _build_root_open() -> str:
    return (
        '<AnnotatedECG xmlns="urn:hl7-org:v3"'
        ' xmlns:voc="urn:hl7-org:v3/voc"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        ' xsi:schemaLocation="urn:hl7-org:v3 ../schema/PORT_MT020001.xsd"'
        ' type="Observation">\n'
    )


def _build_document_header(oid: str, start_ts: str, end_ts: str) -> str:
    return (
        f'  <id root="{oid}" extension="annotatedEcg"/>\n'
        f'  <code code="93000" codeSystem="{CS_CPT4}" codeSystemName="CPT-4"/>\n'
        f'  <effectiveTime>\n'
        f'    <low value="{start_ts}"/>\n'
        f'    <high value="{end_ts}"/>\n'
        f'  </effectiveTime>\n'
    )


def _build_patient_section(ecg_data, oid: str) -> str:
    """Build componentOf section with patient demographics."""
    name = _esc(ecg_data.patient_name.replace('^', ' ').strip())
    pid = _esc(ecg_data.patient_id or '')
    gender = ecg_data.patient_sex or ''
    dob = ecg_data.patient_birth_date or ''
    age = _format_age_hl7(ecg_data.patient_age)

    lines = []
    lines.append('  <componentOf>')
    lines.append('    <timepointEvent>')
    lines.append('      <componentOf>')
    lines.append('        <subjectAssignment>')
    lines.append('          <subject>')
    lines.append('            <trialSubject>')
    lines.append(f'              <id root="{oid}" extension="trialSubject"/>')
    lines.append('              <subjectDemographicPerson>')
    lines.append(f'                <name>{name}</name>')
    lines.append(f'                <PatientID>{pid}</PatientID>')
    lines.append('                <SecondPatientID/>')
    lines.append(f'                <Age>{_esc(age)}</Age>')
    lines.append(f'                <administrativeGenderCode code="{gender}"'
                 f' codeSystem="{CS_GENDER}"/>')
    lines.append(f'                <birthTime value="{dob}"/>'
                 if dob else '                <birthTime/>')
    lines.append(f'                <raceCode code="2131-1" codeSystem="{CS_RACE}"'
                 f' codeSystemName="Race" displayName=""/>')
    lines.append('                <V3Placement>Standard</V3Placement>')
    lines.append('                <Paced>false</Paced>')
    lines.append('                <Medications>')
    lines.append('                  <Medication/>')
    lines.append('                  <Medication/>')
    lines.append('                </Medications>')
    lines.append('                <ClinicalClassifications>')
    lines.append('                  <ClinicalClassification/>')
    lines.append('                  <ClinicalClassification/>')
    lines.append('                </ClinicalClassifications>')
    lines.append('                <Bed/>')
    lines.append('                <Room/>')
    lines.append('                <PointOfCare/>')
    lines.append('                <Weight/>')
    lines.append('                <NibpOfSys/>')
    lines.append('                <NibpOfDia/>')
    lines.append('              </subjectDemographicPerson>')
    lines.append('            </trialSubject>')
    lines.append('          </subject>')
    # clinicalTrial section
    lines.append('          <componentOf>')
    lines.append('            <clinicalTrial>')
    lines.append(f'              <id root="{oid}" extension="clinicalTrial"/>')
    lines.append('              <location>')
    lines.append('                <trialSite>')
    lines.append(f'                  <id root="{oid}" extension="trialSite"/>')
    lines.append('                  <location>')
    lines.append(f'                    <name>{_esc(ecg_data.institution or "")}</name>')
    lines.append('                  </location>')
    lines.append('                  <responsibleParty>')
    lines.append('                    <trialInvestigator>')
    lines.append(f'                      <id root="{oid}" extension="trialInvestigator"/>')
    lines.append('                      <investigatorPerson>')
    lines.append('                        <name/>')
    lines.append('                      </investigatorPerson>')
    lines.append('                    </trialInvestigator>')
    lines.append('                  </responsibleParty>')
    lines.append('                </trialSite>')
    lines.append('              </location>')
    lines.append('            </clinicalTrial>')
    lines.append('          </componentOf>')
    lines.append('        </subjectAssignment>')
    lines.append('      </componentOf>')
    lines.append('    </timepointEvent>')
    lines.append('  </componentOf>')
    return '\n'.join(lines) + '\n'


def _build_device_section(ecg_data, oid: str) -> str:
    """Build author/seriesAuthor block."""
    raw_model = ecg_data.model_name or ''
    model = _esc(MINDRAY_MODEL_NAMES.get(raw_model, raw_model))
    serial = _esc(ecg_data.device_serial or '')
    # Strip build number from software version (e.g. "01.07.00.01 485980" → "01.07.00.01")
    sw = _esc((ecg_data.software_version or '').split(' ')[0])
    raw_mfr = ecg_data.manufacturer or ''
    if 'mindray' in raw_mfr.lower():
        mfr = _esc('(C) Shenzhen Mindray Bio-Medical Electronics Co., Ltd. All rights reserved.')
    else:
        mfr = _esc(raw_mfr)

    return (
        '      <author>\n'
        '        <seriesAuthor>\n'
        '          <manufacturedSeriesDevice>\n'
        f'            <manufacturerModelName>{model}</manufacturerModelName>\n'
        f'            <SerialNumber>{serial}</SerialNumber>\n'
        f'            <softwareName>{sw}</softwareName>\n'
        '          </manufacturedSeriesDevice>\n'
        '          <manufacturerOrganization>\n'
        f'            <name>{mfr}</name>\n'
        '          </manufacturerOrganization>\n'
        '        </seriesAuthor>\n'
        '      </author>\n'
    )


def _build_secondary_performer() -> str:
    return (
        '      <secondaryPerformer>\n'
        '        <functionCode code="ELECTROCARDIOGRAPH_TECH" codeSystem=""/>\n'
        '        <seriesPerformer>\n'
        '          <assignedPerson>\n'
        '            <name/>\n'
        '          </assignedPerson>\n'
        '        </seriesPerformer>\n'
        '      </secondaryPerformer>\n'
    )


def _build_filters() -> str:
    """Build controlVariable filter definitions."""
    def _filter(code, display, cutoff_code, cutoff_display, value, unit):
        return (
            '      <controlVariable>\n'
            '        <controlVariable>\n'
            f'          <code code="{code}" codeSystem="{CS_MDC}"'
            f' codeSystemName="MDC" displayName="{display}"/>\n'
            '          <component>\n'
            '            <controlVariable>\n'
            f'              <code code="{cutoff_code}" codeSystem="{CS_MDC}"'
            f' codeSystemName="MDC" displayName="{cutoff_display}"/>\n'
            f'              <value xsi:type="PQ" value="{value}" unit="{unit}"/>\n'
            '            </controlVariable>\n'
            '          </component>\n'
            '        </controlVariable>\n'
            '      </controlVariable>\n'
        )

    return (
        _filter('MDC_ECG_CTL_VBL_ATTR_FILTER_LOW_PASS', 'Low Pass Filter',
                'MDC_ECG_CTL_VBL_ATTR_FILTER_CUTOFF_FREQ', 'Cutoff Frequency',
                '35', 'Hz')
        + _filter('MDC_ECG_CTL_VBL_ATTR_FILTER_HIGH_PASS', 'High Pass Filter',
                  'MDC_ECG_CTL_VBL_ATTR_FILTER_CUTOFF_FREQ', 'Cutoff Frequency',
                  '0.56', 'Hz')
        + _filter('MDC_ECG_CTL_VBL_ATTR_FILTER_NOTCH', 'Notch Filter',
                  'MDC_ECG_CTL_VBL_ATTR_FILTER_NOTCH_FREQ', 'Notch filter frequency',
                  '50', 'Hz')
    )


def _build_rhythm_waveforms(ecg_data, start_ts: str) -> str:
    """Build sequenceSet with TIME_ABSOLUTE + 12 lead waveforms."""
    if not ecg_data.waveforms:
        return ''

    wf = ecg_data.waveforms[0]
    fs = wf.sampling_frequency
    increment = f"{1.0 / fs:.6f}".rstrip('0').rstrip('.')
    channels = {ch.name: ch for ch in wf.channels}

    lines = []
    lines.append('      <component>')
    lines.append('        <sequenceSet>')

    # TIME_ABSOLUTE
    lines.append('          <component>')
    lines.append('            <sequence>')
    lines.append(f'              <code code="TIME_ABSOLUTE" codeSystem="{CS_ACTCODE}"'
                 f' codeSystemName="ActCode" displayName="Aboslute Time"/>')
    lines.append('              <value xsi:type="GLIST_TS">')
    lines.append(f'                <head value="{start_ts}" unit="s"/>')
    lines.append(f'                <increment value="{increment}" unit="s"/>')
    lines.append('              </value>')
    lines.append('            </sequence>')
    lines.append('          </component>')

    # 12 leads
    for lead_name in STANDARD_LEADS:
        ch = channels.get(lead_name)
        if ch:
            digits = _waveform_to_uv_digits(ch.data)
        else:
            digits = ''
        mdc_code = LEAD_MDC_CODES.get(lead_name, f'MDC_ECG_LEAD_{lead_name}')

        lines.append('          <component>')
        lines.append('            <sequence>')
        lines.append(f'              <code code="{mdc_code}" codeSystem="{CS_MDC}"'
                     f' codeSystemName="MDC"/>')
        lines.append('              <value xsi:type="SLIST_PQ">')
        lines.append('                <origin value="0" unit="uV"/>')
        lines.append('                <scale value="1" unit="uV"/>')
        lines.append(f'                <digits>{digits}</digits>')
        lines.append('              </value>')
        lines.append('            </sequence>')
        lines.append('          </component>')

    lines.append('        </sequenceSet>')
    lines.append('      </component>')
    return '\n'.join(lines) + '\n'


def _build_representative_beat(ecg_data, oid: str, start_ts: str, end_ts: str) -> str:
    """Build derivedSeries for representative beat if second waveform exists."""
    if len(ecg_data.waveforms) < 2:
        return ''

    wf = ecg_data.waveforms[1]
    fs = wf.sampling_frequency
    increment = f"{1.0 / fs:.6f}".rstrip('0').rstrip('.')
    channels = {ch.name: ch for ch in wf.channels}

    lines = []
    lines.append('      <derivation>')
    lines.append('        <derivedSeries>')
    lines.append(f'          <id root="{oid}" extension="derivedSeries"/>')
    lines.append(f'          <code code="REPRESENTATIVE_BEAT" codeSystem="{CS_ACTCODE}"'
                 f' codeSystemName="ActCode" displayName="Representative Beat Waveforms"/>')
    lines.append('          <effectiveTime>')
    lines.append(f'            <low value="{start_ts}" inclusive="true"/>')
    lines.append(f'            <high value="{end_ts}" inclusive="false"/>')
    lines.append('          </effectiveTime>')
    lines.append('          <component>')
    lines.append('            <sequenceSet>')

    # TIME_RELATIVE
    lines.append('              <component>')
    lines.append('                <sequence>')
    lines.append(f'                  <code code="TIME_RELATIVE" codeSystem="{CS_ACTCODE}"'
                 f' codeSystemName="ActCode" displayName="Relative Time"/>')
    lines.append('                  <value xsi:type="GLIST_PQ">')
    lines.append(f'                    <head value="0.000" unit="s"/>')
    lines.append(f'                    <increment value="{increment}" unit="s"/>')
    lines.append('                  </value>')
    lines.append('                </sequence>')
    lines.append('              </component>')

    # 12 leads
    for lead_name in STANDARD_LEADS:
        ch = channels.get(lead_name)
        if ch:
            digits = _waveform_to_uv_digits(ch.data)
        else:
            digits = ''
        mdc_code = LEAD_MDC_CODES.get(lead_name)

        lines.append('              <component>')
        lines.append('                <sequence>')
        lines.append(f'                  <code code="{mdc_code}" codeSystem="{CS_MDC}"'
                     f' codeSystemName="MDC"/>')
        lines.append('                  <value xsi:type="SLIST_PQ">')
        lines.append('                    <origin value="0" unit="uV"/>')
        lines.append('                    <scale value="1" unit="uV"/>')
        lines.append(f'                    <digits>{digits}</digits>')
        lines.append('                  </value>')
        lines.append('                </sequence>')
        lines.append('              </component>')

    lines.append('            </sequenceSet>')
    lines.append('          </component>')
    lines.append('        </derivedSeries>')
    lines.append('      </derivation>')
    return '\n'.join(lines) + '\n'


def _build_global_annotations(global_ann: dict, end_ts: str) -> str:
    """Build annotationSet with global measurement annotations."""
    lines = []
    lines.append('      <subjectOf>')
    lines.append('        <annotationSet>')
    lines.append(f'          <activityTime value="{end_ts}"/>')

    # Standard MDC measurements
    standard_order = [
        'MDC_ECG_HEART_RATE', 'MDC_ECG_TIME_PD_PR',
        'MDC_ECG_TIME_PD_QRS', 'MDC_ECG_TIME_PD_QT',
    ]
    for code in standard_order:
        if code in global_ann:
            val, unit = global_ann[code]
            cs = CS_MDC
            csn = 'MDC'
        else:
            val, unit = MISSING_VALUE, 'ms' if 'TIME' in code else 'bpm'
            cs = CS_MDC
            csn = 'MDC'
        lines.append('          <component>')
        lines.append('            <annotation>')
        lines.append(f'              <code code="{code}" codeSystem="{cs}"'
                     f' codeSystemName="{csn}"/>')
        lines.append(f'              <value xsi:type="PQ" value="{val}" unit="{unit}"/>')
        lines.append('            </annotation>')
        lines.append('          </component>')

    # QTc with Hodges sub-annotation
    if 'MINDRAY_ECG_TIME_PD_QTcH' in global_ann:
        val, unit = global_ann['MINDRAY_ECG_TIME_PD_QTcH']
        lines.append('          <component>')
        lines.append('            <annotation>')
        lines.append(f'              <code code="MDC_ECG_TIME_PD_QTc" codeSystem="{CS_MDC}"'
                     f' codeSystemName="MDC"/>')
        lines.append('              <component>')
        lines.append('                <annotation>')
        lines.append('                  <code code="MINDRAY_ECG_TIME_PD_QTcH"'
                     ' codeSystem="" codeSystemName="MINDRAY"/>')
        lines.append(f'                  <value xsi:type="PQ" value="{val}" unit="{unit}"/>')
        lines.append('                </annotation>')
        lines.append('              </component>')
        lines.append('            </annotation>')
        lines.append('          </component>')

    # Axes and RV5/SV1 (MINDRAY codes)
    mindray_globals = [
        'MINDRAY_ECG_P_AXIS', 'MINDRAY_ECG_QRS_AXIS', 'MINDRAY_ECG_T_AXIS',
        'MINDRAY_ECG_RV5', 'MINDRAY_ECG_SV1', 'MINDRAY_ECG_RV5_PLUS_SV1',
    ]
    for code in mindray_globals:
        if code in global_ann:
            val, unit = global_ann[code]
            lines.append('          <component>')
            lines.append('            <annotation>')
            lines.append(f'              <code code="{code}" codeSystem=""'
                         f' codeSystemName="MINDRAY"/>')
            lines.append(f'              <value xsi:type="PQ" value="{val}" unit="{unit}"/>')
            lines.append('            </annotation>')
            lines.append('          </component>')

    return '\n'.join(lines) + '\n'


def _build_per_lead_measurements(per_lead: dict) -> str:
    """Build MINDRAY_MEASUREMENT_MATRIX blocks for all 12 leads.

    Generates a block for every lead. Most values default to -32768 (missing)
    since DICOM doesn't store per-lead measurement matrices. ST_AMP is
    populated from DICOM ST annotations where available.
    """
    lines = []
    for lead_name in STANDARD_LEADS:
        lead_data = per_lead.get(lead_name, {})
        mdc_code = LEAD_MDC_CODES.get(lead_name)

        lines.append('          <component>')
        lines.append('            <annotation>')
        lines.append('              <code code="MINDRAY_MEASUREMENT_MATRIX"'
                     ' codeSystem="" codeSystemName="MINDRAY"/>')
        lines.append('              <support>')
        lines.append('                <supportingROI classCode="ROIBND">')
        lines.append(f'                  <code code="ROIPS" codeSystem="{CS_ACTCODE}"'
                     f' codeSystemName="HL7V3"/>')
        lines.append('                  <component>')
        lines.append('                    <boundary>')
        lines.append(f'                      <code code="{mdc_code}" codeSystem="{CS_MDC}"'
                     f' codeSystemName="MDC"/>')
        lines.append('                    </boundary>')
        lines.append('                  </component>')
        lines.append('                </supportingROI>')
        lines.append('              </support>')

        for m_code, m_unit in MINDRAY_LEAD_MEASUREMENTS:
            # Use ST_AMP from DICOM if available, otherwise -32768
            val = MISSING_VALUE
            if m_code == 'MINDRAY_ST_AMP' and 'ST_AMP' in lead_data:
                val = lead_data['ST_AMP']
            lines.append('              <component>')
            lines.append('                <annotation>')
            lines.append(f'                  <code code="{m_code}" codeSystem=""'
                         f' codeSystemName="MINDRAY"/>')
            lines.append(f'                  <value xsi:type="PQ" value="{val}" unit="{m_unit}"/>')
            lines.append('                </annotation>')
            lines.append('              </component>')

        lines.append('            </annotation>')
        lines.append('          </component>')

    return '\n'.join(lines) + '\n'


def _build_interpretation(ecg_data, db_result=None) -> str:
    """Build MDC_ECG_INTERPRETATION annotation block.

    If the doctor has submitted a diagnosis, it replaces the device
    interpretation as STATEMENT/SUMMARY lines. COMMENT contains
    physician attribution.  If no diagnosis, uses device interpretation
    with "Unconfirmed Diagnosis" as COMMENT.
    """
    has_diagnosis = (db_result and hasattr(db_result, 'diagnosis')
                     and db_result.diagnosis)

    # Determine interpretation source
    if has_diagnosis:
        # Use doctor's diagnosis as interpretation text
        source_texts = [db_result.diagnosis]
    else:
        source_texts = ecg_data.interpretation_texts or []

    if not source_texts:
        return ''

    lines = []
    lines.append('          <component>')
    lines.append('            <annotation>')
    lines.append(f'              <code code="MDC_ECG_INTERPRETATION"'
                 f' codeSystem="{CS_MDC}"/>')

    # Separate interpretation texts into STATEMENT and SUMMARY.
    # The LAST non-empty line is the SUMMARY (overall conclusion),
    # all preceding lines are STATEMENT entries.
    all_lines = []
    for text in source_texts:
        for line in text.split('\n'):
            stripped = line.strip()
            if not stripped:
                continue
            all_lines.append(stripped)

    # Last line = SUMMARY, rest = STATEMENT
    statements = all_lines[:-1] if len(all_lines) > 1 else []
    summary = all_lines[-1] if all_lines else ''

    # STATEMENT entries
    for stmt in statements:
        lines.append('              <component>')
        lines.append('                <annotation>')
        lines.append(f'                  <code code="MDC_ECG_INTERPRETATION_STATEMENT"'
                     f' codeSystem="{CS_MDC}"/>')
        lines.append(f'                  <value xsi:type="ST">{_esc(stmt)}</value>')
        lines.append('                </annotation>')
        lines.append('              </component>')

    # SUMMARY (overall conclusion)
    if summary:
        lines.append('              <component>')
        lines.append('                <annotation>')
        lines.append(f'                  <code code="MDC_ECG_INTERPRETATION_SUMMARY"'
                     f' codeSystem="{CS_MDC}"/>')
        lines.append(f'                  <value xsi:type="ST">{_esc(summary)}</value>')
        lines.append('                </annotation>')
        lines.append('              </component>')

    # COMMENT — physician attribution or "Unconfirmed Diagnosis"
    if has_diagnosis:
        diagnosed_by = getattr(db_result, 'diagnosed_by', '') or ''
        comment = f"Diagnosed by: {diagnosed_by}" if diagnosed_by else 'Confirmed Diagnosis'
    else:
        comment = 'Unconfirmed Diagnosis'
    lines.append('              <component>')
    lines.append('                <annotation>')
    lines.append(f'                  <code code="MDC_ECG_INTERPRETATION_COMMENT"'
                 f' codeSystem="{CS_MDC}"/>')
    lines.append(f'                  <value xsi:type="ST">{_esc(comment)}</value>')
    lines.append('                </annotation>')
    lines.append('              </component>')

    # <author> element — standard HL7 v3 aECG way to identify
    # the physician who interpreted the study
    if has_diagnosis:
        diagnosed_by = getattr(db_result, 'diagnosed_by', '') or ''
        diagnosed_at = getattr(db_result, 'diagnosed_at', None)
        author_time = diagnosed_at.strftime('%Y%m%d%H%M%S') if diagnosed_at else ''
        if diagnosed_by:
            lines.append('              <author>')
            lines.append('                <assignedEntity>')
            lines.append('                  <assignedAuthorType>')
            lines.append('                    <assignedPerson>')
            lines.append(f'                      <name>{_esc(diagnosed_by)}</name>')
            lines.append('                    </assignedPerson>')
            lines.append('                  </assignedAuthorType>')
            lines.append('                </assignedEntity>')
            if author_time:
                lines.append(f'                <time value="{author_time}"/>')
            lines.append('              </author>')

    lines.append('            </annotation>')
    lines.append('          </component>')
    return '\n'.join(lines) + '\n'


# ===== Main Entry Point =====

def generate_ecg_hl7(ecg_data, db_result=None) -> str:
    """Generate HL7 v3 aECG (FDA XML) string from parsed DICOM ECG data.

    Args:
        ecg_data: ECGData from parse_dicom_ecg()
        db_result: Optional ECGResult for additional metadata

    Returns:
        Complete XML string
    """
    oid = _generate_oid(ecg_data)

    # Timestamps — prefer AcquisitionDateTime over StudyDate/StudyTime
    start_ts = _get_start_timestamp(ecg_data)
    # End time = start + last sample time. Last sample is at (N-1)/fs seconds,
    # not N/fs, so subtract one sample interval from duration.
    wf0 = ecg_data.waveforms[0] if ecg_data.waveforms else None
    if wf0:
        last_sample_sec = wf0.duration_seconds - 1.0 / wf0.sampling_frequency
    else:
        last_sample_sec = 9.0
    end_ts = _make_end_timestamp(start_ts, last_sample_sec)

    # Extract annotations
    global_ann = _extract_global_annotations(ecg_data)
    per_lead = _extract_per_lead_annotations(ecg_data)

    # Build XML
    xml_parts = []
    xml_parts.append(_build_xml_header())
    xml_parts.append(_build_root_open())
    xml_parts.append(_build_document_header(oid, start_ts, end_ts))
    xml_parts.append(_build_patient_section(ecg_data, oid))

    # Series
    xml_parts.append('  <component>\n')
    xml_parts.append('    <series>\n')
    xml_parts.append(f'      <id root="{oid}" extension="series"/>\n')
    xml_parts.append(f'      <code code="RHYTHM" codeSystem="{CS_ACTCODE}"'
                     f' codeSystemName="ActCode" displayName="Rhythm Waveforms"/>\n')
    xml_parts.append('      <effectiveTime>\n')
    xml_parts.append(f'        <low value="{start_ts}" inclusive="true"/>\n')
    xml_parts.append(f'        <high value="{end_ts}" inclusive="false"/>\n')
    xml_parts.append('      </effectiveTime>\n')

    xml_parts.append(_build_device_section(ecg_data, oid))
    xml_parts.append(_build_secondary_performer())
    xml_parts.append(_build_filters())
    xml_parts.append(_build_rhythm_waveforms(ecg_data, start_ts))
    xml_parts.append(_build_representative_beat(ecg_data, oid, start_ts, end_ts))

    # Annotations
    xml_parts.append(_build_global_annotations(global_ann, end_ts))
    xml_parts.append(_build_per_lead_measurements(per_lead))
    xml_parts.append(_build_interpretation(ecg_data, db_result))

    # Close annotationSet, subjectOf, series, component
    xml_parts.append('        </annotationSet>\n')
    xml_parts.append('      </subjectOf>\n')
    xml_parts.append('    </series>\n')
    xml_parts.append('  </component>\n')
    xml_parts.append('</AnnotatedECG>\n')

    return ''.join(xml_parts)
