/**
 * ECG Compare Renderer - Side-by-side lead-by-lead comparison.
 * Self-contained renderer for the Report Comparison tab.
 */

// ===== ECG Paper Constants =====
var CMP_PX_PER_MM = 4;
var CMP_SMALL_BOX = CMP_PX_PER_MM;          // 1mm
var CMP_BIG_BOX   = CMP_PX_PER_MM * 5;      // 5mm
var CMP_SPEED     = 25;                       // mm/s
var CMP_GAIN      = 10;                       // mm/mV
var CMP_PX_PER_SEC = CMP_SPEED * CMP_PX_PER_MM;  // 75 px/s
var CMP_PX_PER_MV  = CMP_GAIN  * CMP_PX_PER_MM;  // 30 px/mV

// Colors
var CMP_BG_COLOR         = '#fff5f5';
var CMP_GRID_MINOR_COLOR = '#f0c8c8';
var CMP_GRID_MAJOR_COLOR = '#d4a0a0';
var CMP_WAVE_COLOR       = '#1d1d1f';
var CMP_LABEL_COLOR      = '#86868b';
var CMP_SEPARATOR_COLOR  = '#a08080';

// Standard 12-lead order
var CMP_ALL_LEADS = ['I','II','III','aVR','aVL','aVF','V1','V2','V3','V4','V5','V6'];

/**
 * Render ECG waveforms for comparison on a canvas.
 * @param {string} canvasId - Canvas element ID
 * @param {object} waveformData - {samplingFrequency, leads: {I:[...], II:[...], ...}}
 * @param {string[]} selectedLeads - Array of lead names to render (e.g. ['I','II','V1'])
 */
function renderCompareECG(canvasId, waveformData, selectedLeads) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;

    var ctx = canvas.getContext('2d');
    var containerWidth = canvas.parentElement.clientWidth - 2;
    // Fallback if container hasn't laid out yet
    if (containerWidth < 100) containerWidth = 600;

    if (!waveformData || !waveformData.leads) {
        // No data - render empty state
        canvas.width = containerWidth;
        canvas.height = 200;
        ctx.fillStyle = '#f8f9fa';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = CMP_LABEL_COLOR;
        ctx.font = '14px Kanit, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('No waveform data', canvas.width / 2, canvas.height / 2);
        ctx.textAlign = 'left';
        return;
    }

    var leads = selectedLeads || CMP_ALL_LEADS;
    var numLeads = leads.length;
    if (numLeads === 0) {
        canvas.width = containerWidth;
        canvas.height = 100;
        ctx.fillStyle = '#f8f9fa';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        return;
    }

    var fs = waveformData.samplingFrequency || 500;
    var leadData = waveformData.leads || {};

    // Layout
    var marginLeft = 50;
    var marginTop = 8;
    var marginBottom = 8;
    var rowBigBoxes = 4;  // 4 big boxes (20mm) per lead row
    var rowHeight = rowBigBoxes * CMP_BIG_BOX;

    // Calculate duration from data
    var maxSamples = 0;
    for (var i = 0; i < leads.length; i++) {
        var d = leadData[leads[i]];
        if (d && d.length > maxSamples) maxSamples = d.length;
    }
    var duration = maxSamples / fs;

    var gridW = duration * CMP_PX_PER_SEC;   // full duration — scrollbar handles overflow
    var gridH = rowHeight * numLeads;

    var marginTimeTop = 18;  // space for time markers above grid
    canvas.width = marginLeft + gridW + 10;
    canvas.height = marginTimeTop + marginTop + gridH + marginBottom;

    var gridY = marginTimeTop + marginTop;  // actual top of grid area

    // 1. Background
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // 2. Grid area background
    ctx.fillStyle = CMP_BG_COLOR;
    ctx.fillRect(marginLeft, gridY, gridW, gridH);

    // 3. Draw grid
    _cmpDrawGrid(ctx, marginLeft, gridY, gridW, gridH);

    // 4. Time markers (0s, 1s, 2s, ...)
    ctx.fillStyle = CMP_LABEL_COLOR;
    ctx.font = '10px Kanit, sans-serif';
    ctx.textAlign = 'center';
    for (var sec = 0; sec <= duration; sec++) {
        var tx = marginLeft + sec * CMP_PX_PER_SEC;
        if (tx > marginLeft + gridW + 1) break;
        ctx.fillText(sec + 's', tx, gridY - 5);
        ctx.strokeStyle = CMP_LABEL_COLOR;
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.moveTo(tx, gridY - 2);
        ctx.lineTo(tx, gridY);
        ctx.stroke();
    }
    ctx.textAlign = 'left';

    // 5. Row separators
    ctx.strokeStyle = CMP_SEPARATOR_COLOR;
    ctx.lineWidth = 1;
    for (var r = 0; r <= numLeads; r++) {
        var sy = gridY + r * rowHeight;
        ctx.beginPath();
        ctx.moveTo(marginLeft, sy);
        ctx.lineTo(marginLeft + gridW, sy);
        ctx.stroke();
    }

    // 6. Draw each lead
    for (var row = 0; row < numLeads; row++) {
        var leadName = leads[row];
        var data = leadData[leadName];
        var baselineY = gridY + row * rowHeight + rowHeight / 2;

        // Lead label
        ctx.fillStyle = CMP_WAVE_COLOR;
        ctx.font = 'bold 11px Kanit, sans-serif';
        ctx.textAlign = 'right';
        ctx.fillText(leadName, marginLeft - 6, baselineY + 4);
        ctx.textAlign = 'left';

        // Calibration mark (1mV pulse)
        _cmpDrawCalibration(ctx, marginLeft - 42, baselineY);

        // Waveform
        if (data && data.length > 0) {
            _cmpDrawWaveform(ctx, data, fs, 0, data.length, marginLeft, baselineY, gridW);
        }
    }

    // 7. Speed/gain info
    ctx.fillStyle = CMP_LABEL_COLOR;
    ctx.font = '9px Kanit, sans-serif';
    ctx.fillText(CMP_SPEED + 'mm/s  ' + CMP_GAIN + 'mm/mV', marginLeft, canvas.height - 1);
}


function _cmpDrawGrid(ctx, x0, y0, w, h) {
    // Minor grid
    ctx.strokeStyle = CMP_GRID_MINOR_COLOR;
    ctx.lineWidth = 0.3;
    ctx.beginPath();
    for (var x = x0; x <= x0 + w + 0.5; x += CMP_SMALL_BOX) {
        ctx.moveTo(Math.round(x) + 0.5, y0);
        ctx.lineTo(Math.round(x) + 0.5, y0 + h);
    }
    for (var y = y0; y <= y0 + h + 0.5; y += CMP_SMALL_BOX) {
        ctx.moveTo(x0, Math.round(y) + 0.5);
        ctx.lineTo(x0 + w, Math.round(y) + 0.5);
    }
    ctx.stroke();

    // Major grid
    ctx.strokeStyle = CMP_GRID_MAJOR_COLOR;
    ctx.lineWidth = 0.6;
    ctx.beginPath();
    for (var x = x0; x <= x0 + w + 0.5; x += CMP_BIG_BOX) {
        ctx.moveTo(Math.round(x) + 0.5, y0);
        ctx.lineTo(Math.round(x) + 0.5, y0 + h);
    }
    for (var y = y0; y <= y0 + h + 0.5; y += CMP_BIG_BOX) {
        ctx.moveTo(x0, Math.round(y) + 0.5);
        ctx.lineTo(x0 + w, Math.round(y) + 0.5);
    }
    ctx.stroke();
}


function _cmpDrawCalibration(ctx, x, baselineY) {
    var pulseH = CMP_PX_PER_MV;
    var pulseW = CMP_BIG_BOX;

    ctx.strokeStyle = CMP_WAVE_COLOR;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, baselineY);
    ctx.lineTo(x, baselineY - pulseH);
    ctx.lineTo(x + pulseW, baselineY - pulseH);
    ctx.lineTo(x + pulseW, baselineY);
    ctx.stroke();
}


function _cmpDrawWaveform(ctx, data, fs, startSample, endSample, x0, baselineY, maxWidth) {
    var actualStart = Math.max(0, Math.floor(startSample));
    var actualEnd = Math.min(data.length, Math.ceil(endSample));

    ctx.strokeStyle = CMP_WAVE_COLOR;
    ctx.lineWidth = 1.0;
    ctx.beginPath();

    var firstPoint = true;
    var totalPoints = actualEnd - actualStart;
    var step = Math.max(1, Math.floor(totalPoints / 3000));

    if (step <= 1) {
        // No downsampling — draw every point
        for (var i = actualStart; i < actualEnd; i++) {
            var t = (i - startSample) / fs;
            var px = x0 + t * CMP_PX_PER_SEC;
            if (px > x0 + maxWidth) break;
            var py = baselineY - data[i] * CMP_PX_PER_MV;
            if (firstPoint) { ctx.moveTo(px, py); firstPoint = false; }
            else { ctx.lineTo(px, py); }
        }
    } else {
        // Min-max downsampling: preserve spikes by drawing both min and max per bucket
        for (var b = actualStart; b < actualEnd; b += step) {
            var bEnd = Math.min(b + step, actualEnd);
            var minVal = data[b], maxVal = data[b], minIdx = b, maxIdx = b;
            for (var j = b + 1; j < bEnd; j++) {
                if (data[j] < minVal) { minVal = data[j]; minIdx = j; }
                if (data[j] > maxVal) { maxVal = data[j]; maxIdx = j; }
            }
            var pts = minIdx < maxIdx ? [[minIdx, minVal], [maxIdx, maxVal]] : [[maxIdx, maxVal], [minIdx, minVal]];
            for (var p = 0; p < pts.length; p++) {
                var t = (pts[p][0] - startSample) / fs;
                var px = x0 + t * CMP_PX_PER_SEC;
                if (px > x0 + maxWidth) break;
                var py = baselineY - pts[p][1] * CMP_PX_PER_MV;
                if (firstPoint) { ctx.moveTo(px, py); firstPoint = false; }
                else { ctx.lineTo(px, py); }
            }
        }
    }
    ctx.stroke();
}


// ===== Compare View Controller =====
// Called from loadComparison() after the fragment HTML is inserted via innerHTML.

var _cmpSelectedLeads = CMP_ALL_LEADS.slice();
var _cmpCompareRecordId = null;
var _cmpCurrentId = null;

var _CMP_MEAS_LABELS = [
    {key: 'Ventricular Heart Rate', label: 'HR', unit: 'bpm'},
    {key: 'PR Interval', label: 'PR', unit: 'ms'},
    {key: 'QRS Duration', label: 'QRS', unit: 'ms'},
    {key: 'QT Interval', label: 'QT', unit: 'ms'},
    {key: 'QTc Interval', label: 'QTc', unit: 'ms'},
    {key: 'P Axis', label: 'P Axis', unit: '\u00B0'},
    {key: 'QRS Axis', label: 'QRS Axis', unit: '\u00B0'},
    {key: 'T Axis', label: 'T Axis', unit: '\u00B0'}
];

function _cmpGetEcgJson(id) {
    var el = document.getElementById('ecg-data-' + id);
    if (!el) return null;
    try {
        var raw = el.textContent;
        var parsed = JSON.parse(raw);
        // tojson on a dict produces JSON; tojson on a string double-encodes
        if (typeof parsed === 'string') {
            parsed = JSON.parse(parsed);
        }
        return parsed;
    } catch(e) {
        return null;
    }
}

function _cmpGetWaveformData(id) {
    var ecg = _cmpGetEcgJson(id);
    if (ecg && ecg.waveforms && ecg.waveforms.length > 0) {
        return ecg.waveforms[0];
    }
    return null;
}

function _cmpGetAnnotations(id) {
    var ecg = _cmpGetEcgJson(id);
    return (ecg && ecg.annotations) ? ecg.annotations : [];
}

function _cmpGetMeta(id) {
    var el = document.querySelector('.ecg-meta-block[data-id="' + id + '"]');
    if (!el) return {};
    return {
        status: el.dataset.status || '',
        diagnosis: el.dataset.diagnosis || '',
        diagnosed_by: el.dataset.diagnosedBy || '',
        diagnosed_at: el.dataset.diagnosedAt || '',
        ecg_interpretation: el.dataset.ecgInterpretation || '',
        received_date: el.dataset.receivedDate || '-',
        received_time: el.dataset.receivedTime || ''
    };
}

function _cmpFillMeasurements(containerId, annotations) {
    var el = document.getElementById(containerId);
    if (!el) return;
    var measMap = {};
    for (var i = 0; i < annotations.length; i++) {
        if (annotations[i].value && annotations[i].concept) {
            measMap[annotations[i].concept] = {value: annotations[i].value, unit: annotations[i].unit || ''};
        }
    }
    var html = '';
    for (var m = 0; m < _CMP_MEAS_LABELS.length; m++) {
        var ml = _CMP_MEAS_LABELS[m];
        var val = measMap[ml.key] ? measMap[ml.key].value : '-';
        html += '<div class="cmp-meas-item">';
        html += '<span class="cmp-meas-label">' + ml.label + '</span>';
        html += '<span class="cmp-meas-value">' + val + '</span>';
        html += '<span class="cmp-meas-unit">' + ml.unit + '</span>';
        html += '</div>';
    }
    el.innerHTML = html;
}

function _cmpFillDiagnosis(containerId, meta) {
    var el = document.getElementById(containerId);
    if (!el) return;
    var html = '';
    if (meta.diagnosis) {
        // Doctor has diagnosed — show only doctor's diagnosis (replaces device interpretation)
        html += '<div class="cmp-diagnosis-text">' + meta.diagnosis + '</div>';
    } else if (meta.ecg_interpretation) {
        // No doctor diagnosis — show device interpretation
        html += '<div class="cmp-diagnosis-text interpretation">' + meta.ecg_interpretation + '</div>';
    } else {
        html += '<div class="cmp-diagnosis-text" style="color:var(--apple-text-secondary);">-</div>';
    }
    if (meta.diagnosed_by) {
        html += '<div class="cmp-diagnosis-by"><i class="bi bi-person me-1"></i>' + meta.diagnosed_by;
        if (meta.diagnosed_at) html += ' | ' + meta.diagnosed_at;
        html += '</div>';
    }
    el.innerHTML = html;
}

function _cmpRedraw() {
    // Left: current record
    var leftWf = _cmpGetWaveformData(_cmpCurrentId);
    var leftMeta = _cmpGetMeta(_cmpCurrentId);
    var leftAnns = _cmpGetAnnotations(_cmpCurrentId);

    var leftTitle = document.getElementById('cmpLeftTitle');
    if (leftTitle) {
        leftTitle.textContent = (leftMeta.received_date || '-') + '  ' + (leftMeta.received_time || '');
    }

    renderCompareECG('cmpCanvasLeft', leftWf, _cmpSelectedLeads);
    _cmpFillMeasurements('cmpMeasLeft', leftAnns);
    _cmpFillDiagnosis('cmpDiagLeft', leftMeta);

    // Right: compare record
    if (_cmpCompareRecordId) {
        var rightWf = _cmpGetWaveformData(_cmpCompareRecordId);
        var rightMeta = _cmpGetMeta(_cmpCompareRecordId);
        var rightAnns = _cmpGetAnnotations(_cmpCompareRecordId);

        var rightTitle = document.getElementById('cmpRightTitle');
        if (rightTitle) {
            rightTitle.textContent = (rightMeta.received_date || '-') + '  ' + (rightMeta.received_time || '');
        }

        var link = document.getElementById('cmpRightLink');
        if (link) link.href = '/results/' + _cmpCompareRecordId;

        renderCompareECG('cmpCanvasRight', rightWf, _cmpSelectedLeads);
        _cmpFillMeasurements('cmpMeasRight', rightAnns);
        _cmpFillDiagnosis('cmpDiagRight', rightMeta);
    }
}

// ---- Global functions called from onclick in fragment HTML ----

function selectCompareRecord(id) {
    _cmpCompareRecordId = id;
    document.querySelectorAll('.cmp-record-pill:not(.current)').forEach(function(pill) {
        pill.classList.toggle('active', parseInt(pill.dataset.id) === id);
    });
    _cmpRedraw();
}

function toggleLead(name) {
    var btn = document.querySelector('.cmp-lead-btn[data-lead="' + name + '"]');
    var idx = _cmpSelectedLeads.indexOf(name);
    if (idx >= 0) {
        _cmpSelectedLeads.splice(idx, 1);
        if (btn) btn.classList.remove('active');
    } else {
        _cmpSelectedLeads.push(name);
        _cmpSelectedLeads.sort(function(a, b) {
            return CMP_ALL_LEADS.indexOf(a) - CMP_ALL_LEADS.indexOf(b);
        });
        if (btn) btn.classList.add('active');
    }
    var allBtn = document.querySelector('.cmp-lead-btn.all-btn');
    if (allBtn) {
        allBtn.classList.toggle('active', _cmpSelectedLeads.length === 12);
    }
    _cmpRedraw();
}

function toggleAllLeads() {
    var allBtn = document.querySelector('.cmp-lead-btn.all-btn');
    if (_cmpSelectedLeads.length === 12) {
        _cmpSelectedLeads = [];
        document.querySelectorAll('.cmp-lead-btn[data-lead]').forEach(function(b) { b.classList.remove('active'); });
        if (allBtn) allBtn.classList.remove('active');
    } else {
        _cmpSelectedLeads = CMP_ALL_LEADS.slice();
        document.querySelectorAll('.cmp-lead-btn[data-lead]').forEach(function(b) { b.classList.add('active'); });
        if (allBtn) allBtn.classList.add('active');
    }
    _cmpRedraw();
}

/**
 * Initialize the Compare View after fragment HTML is inserted.
 * Called from loadComparison() in detail.html.
 * @param {number} currentId - The current ECG result ID
 */
function initCompareView(currentId) {
    _cmpCurrentId = currentId;
    _cmpSelectedLeads = CMP_ALL_LEADS.slice();
    _cmpCompareRecordId = null;

    // Find first non-current record as default comparison
    var recordPills = document.querySelectorAll('.cmp-record-pill:not(.current)');
    if (recordPills.length > 0) {
        _cmpCompareRecordId = parseInt(recordPills[0].dataset.id);
    }

    // Reset all lead buttons to active
    document.querySelectorAll('.cmp-lead-btn[data-lead]').forEach(function(b) { b.classList.add('active'); });
    var allBtn = document.querySelector('.cmp-lead-btn.all-btn');
    if (allBtn) allBtn.classList.add('active');

    // Wait for layout, then draw
    function tryDraw() {
        var canvas = document.getElementById('cmpCanvasLeft');
        var parentW = canvas ? canvas.parentElement.clientWidth : 0;
        if (parentW < 50) {
            requestAnimationFrame(tryDraw);
            return;
        }
        _cmpRedraw();
    }
    setTimeout(function() {
        requestAnimationFrame(tryDraw);
    }, 100);
}
