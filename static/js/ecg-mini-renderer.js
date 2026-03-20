/**
 * ECG Mini Renderer - Compact waveform renderer for Report Comparison cards.
 * Renders a simplified 6x2 layout ECG on a small canvas.
 */

var MINI_LEADS_ORDER = ['I','II','III','aVR','aVL','aVF','V1','V2','V3','V4','V5','V6'];

/**
 * Render a compact 6x2 ECG waveform on a canvas.
 * @param {string} canvasId - Canvas element ID
 * @param {object} waveformData - {samplingFrequency, leads: {I:[...], II:[...], ...}}
 * @param {object} options - Optional overrides
 */
function renderMiniECG(canvasId, waveformData, options) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || !waveformData) return;

    var ctx = canvas.getContext('2d');
    var opts = options || {};

    // Canvas dimensions
    var W = canvas.width;
    var H = canvas.height;

    // Layout: 6 rows x 2 columns
    var rows = 6;
    var cols = 2;
    var labelW = 28; // Left margin for lead labels
    var topPad = 4;
    var bottomPad = 4;
    var colGap = 6;

    var colW = (W - labelW - colGap) / cols;
    var rowH = (H - topPad - bottomPad) / rows;

    var fs = waveformData.samplingFrequency || 500;
    var leads = waveformData.leads || {};

    // Speed: 25mm/s, Gain: 10mm/mV
    var pxPerMm = opts.pxPerMm || 1.5;
    var speed = 25; // mm/s
    var gain = 10;  // mm/mV
    var pxPerSec = speed * pxPerMm;
    var pxPerMv = gain * pxPerMm;

    // Duration shown per column
    var durPerCol = colW / pxPerSec;
    var samplesPerCol = Math.floor(durPerCol * fs);

    // Clear canvas
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, W, H);

    // Draw grid (major only for compact view)
    var bigBoxPx = 5 * pxPerMm; // 5mm
    ctx.strokeStyle = '#f0c8c8';
    ctx.lineWidth = 0.5;
    ctx.beginPath();
    for (var gx = labelW; gx <= W; gx += bigBoxPx) {
        ctx.moveTo(gx, 0);
        ctx.lineTo(gx, H);
    }
    for (var gy = topPad; gy <= H; gy += bigBoxPx) {
        ctx.moveTo(labelW, gy);
        ctx.lineTo(W, gy);
    }
    ctx.stroke();

    // Lead arrangement: 6x2
    // Col 0: I, II, III, aVR, aVL, aVF
    // Col 1: V1, V2, V3, V4, V5, V6
    var leadLayout = [
        ['I','V1'], ['II','V2'], ['III','V3'],
        ['aVR','V4'], ['aVL','V5'], ['aVF','V6']
    ];

    for (var r = 0; r < rows; r++) {
        for (var c = 0; c < cols; c++) {
            var leadName = leadLayout[r][c];
            var data = leads[leadName];
            if (!data) continue;

            var x0 = labelW + c * (colW + colGap);
            var y0 = topPad + r * rowH;
            var baseline = y0 + rowH / 2;

            // Draw lead label
            if (c === 0) {
                ctx.fillStyle = '#666';
                ctx.font = '9px Kanit, sans-serif';
                ctx.textAlign = 'right';
                ctx.textBaseline = 'middle';
                ctx.fillText(leadName, labelW - 3, baseline);
            } else {
                // Label for right column on left side of that column
                ctx.fillStyle = '#666';
                ctx.font = '9px Kanit, sans-serif';
                ctx.textAlign = 'left';
                ctx.textBaseline = 'middle';
                ctx.fillText(leadName, x0 + 2, y0 + 10);
            }

            // Draw waveform
            ctx.save();
            ctx.beginPath();
            ctx.rect(x0, y0, colW, rowH);
            ctx.clip();

            ctx.strokeStyle = '#1d1d1f';
            ctx.lineWidth = 0.8;
            ctx.beginPath();

            var nSamples = Math.min(data.length, samplesPerCol);
            var step = Math.max(1, Math.floor(nSamples / colW));

            for (var i = 0; i < nSamples; i += step) {
                var px = x0 + (i / fs) * pxPerSec;
                var py = baseline - data[i] * pxPerMv;

                // Clamp Y
                py = Math.max(y0 + 1, Math.min(y0 + rowH - 1, py));

                if (i === 0) {
                    ctx.moveTo(px, py);
                } else {
                    ctx.lineTo(px, py);
                }
            }
            ctx.stroke();
            ctx.restore();
        }
    }

    // Draw separator line between columns
    ctx.strokeStyle = '#d2d2d7';
    ctx.lineWidth = 0.5;
    var sepX = labelW + colW + colGap / 2;
    ctx.beginPath();
    ctx.moveTo(sepX, topPad);
    ctx.lineTo(sepX, H - bottomPad);
    ctx.stroke();
}

/**
 * Render all mini ECG canvases that have data attributes set.
 * Called after AJAX content is loaded into the comparison tab.
 */
function renderAllMiniECGs() {
    var canvases = document.querySelectorAll('.compare-ecg-canvas');
    canvases.forEach(function(canvas) {
        var resultId = canvas.dataset.resultId;
        var dataEl = document.getElementById('ecg-data-' + resultId);
        if (dataEl) {
            try {
                var wfData = JSON.parse(dataEl.textContent);
                if (wfData && wfData.waveforms && wfData.waveforms.length > 0) {
                    renderMiniECG(canvas.id, wfData.waveforms[0]);
                }
            } catch(e) {
                // Skip rendering on parse error
            }
        }
    });
}
