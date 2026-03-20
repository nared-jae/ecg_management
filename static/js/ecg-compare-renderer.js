/**
 * ECG Compare Renderer - Side-by-side lead-by-lead comparison.
 * Self-contained renderer for the Report Comparison tab.
 */

// ===== ECG Paper Constants =====
var CMP_PX_PER_MM = 3;
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

    var gridW = Math.min(duration * CMP_PX_PER_SEC, containerWidth - marginLeft - 10);
    var gridH = rowHeight * numLeads;

    canvas.width = marginLeft + gridW + 10;
    canvas.height = marginTop + gridH + marginBottom;

    // 1. Background
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // 2. Grid area background
    ctx.fillStyle = CMP_BG_COLOR;
    ctx.fillRect(marginLeft, marginTop, gridW, gridH);

    // 3. Draw grid
    _cmpDrawGrid(ctx, marginLeft, marginTop, gridW, gridH);

    // 4. Row separators
    ctx.strokeStyle = CMP_SEPARATOR_COLOR;
    ctx.lineWidth = 1;
    for (var r = 0; r <= numLeads; r++) {
        var sy = marginTop + r * rowHeight;
        ctx.beginPath();
        ctx.moveTo(marginLeft, sy);
        ctx.lineTo(marginLeft + gridW, sy);
        ctx.stroke();
    }

    // 5. Draw each lead
    for (var row = 0; row < numLeads; row++) {
        var leadName = leads[row];
        var data = leadData[leadName];
        var baselineY = marginTop + row * rowHeight + rowHeight / 2;

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

    // 6. Speed/gain info
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
    ctx.lineWidth = 0.8;
    ctx.beginPath();

    var firstPoint = true;
    var totalPoints = actualEnd - actualStart;
    var step = Math.max(1, Math.floor(totalPoints / 2000));

    for (var i = actualStart; i < actualEnd; i += step) {
        var t = (i - startSample) / fs;
        var px = x0 + t * CMP_PX_PER_SEC;
        if (px > x0 + maxWidth) break;
        var py = baselineY - data[i] * CMP_PX_PER_MV;

        if (firstPoint) {
            ctx.moveTo(px, py);
            firstPoint = false;
        } else {
            ctx.lineTo(px, py);
        }
    }
    ctx.stroke();
}
