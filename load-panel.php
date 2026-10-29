<?php
require_once __DIR__ . '/viewer-config.php';
require_once __DIR__ . '/data-paths.php';
$filename = panel_filename($_GET['filename'] ?? '');
// Check every frame ancestor, so another website cannot load a panel to trigger graph requests.
header("Content-Security-Policy: frame-ancestors 'self'");
header('X-Frame-Options: SAMEORIGIN');
$read_only = viewer_read_only();
$waveforms = viewer_config()['waveforms'];
$generation = '';
if (!$read_only) {
    require_once __DIR__ . '/graph-generation.php';
    try {
        $generation = graph_generation_token();
    } catch (Throwable $error) {
        error_log($error->getMessage());
        http_response_code(503);
        exit('Graph generation controls are unavailable. Check the server error log.');
    }
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate" />
  <meta http-equiv="Pragma" content="no-cache" />
  <meta http-equiv="Expires" content="0" />
  <meta charset="UTF-8">
  <title><?php echo htmlspecialchars($filename, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); ?></title>

  <!-- jQuery -->
  <script src="https://code.jquery.com/jquery-3.7.1.min.js" integrity="sha384-1H217gwSVyLSIfaLxHbE7dRb3v4mYCKbpQvzx0cegeju1MVsGrX5xXxAvs/HgeFs" crossorigin="anonymous"></script>

  <!-- Bootstrap3 -->
  <link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/3.4.1/css/bootstrap.min.css" integrity="sha384-HSMxcRTRxnN+Bdg0JdbxYKrThecOKuH5zCYotlSAcp1+c8xmyTe9GYg1l9a69psu" crossorigin="anonymous">
  <link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/3.4.1/css/bootstrap-theme.min.css" integrity="sha384-6pzBo3FDv/PJ8r2KRkGHifhEocL+1X2rVCTTkUfGk7/0pbek5mMa1upzvWbrUbOZ" crossorigin="anonymous">
  <script src="https://maxcdn.bootstrapcdn.com/bootstrap/3.4.1/js/bootstrap.min.js" integrity="sha384-aJ21OjlMXNL5UyIl/XNwTMqvzeRMZH2w8c5cRVpzpU8Y5bApTppSuUkhZXN0VxHd" crossorigin="anonymous"></script>

  <?php if (!$read_only): ?>
  <!-- Flatpickr -->
  <script src="https://cdnjs.cloudflare.com/ajax/libs/flatpickr/4.5.1/flatpickr.min.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/flatpickr/4.5.1/flatpickr.min.css" />

  <!-- JSON Editor -->
  <script src="https://cdn.jsdelivr.net/npm/@json-editor/json-editor@1.3.5/dist/jsoneditor.min.js"></script>
  <?php endif; ?>

  <!-- custom css -->
  <link rel="stylesheet" href="custom.css">

  <!-- Dygraphs -->
  <script src="https://cdnjs.cloudflare.com/ajax/libs/dygraph/2.1.0/dygraph.min.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/dygraph/2.1.0/dygraph.min.css" />
  <!-- Pin the synchronizer to the same release as Dygraphs above. -->
  <script src="https://cdn.jsdelivr.net/gh/danvk/dygraphs@v2.1.0/src/extras/synchronizer.js" integrity="sha384-S9ej3yzlnbAY5ot0GP52Y79mD/4z6KBeE6JMeTmjkX0XZSnmLD14sfQMCBElUxVG" crossorigin="anonymous"></script>

  <?php if (!$read_only): ?>
  <!-- dockPanel -->
  <script src='dockPanel/dockPanel-1.0.0.js'></script>
  <?php endif; ?>

  <!-- Custom CSS -->
  <link rel="stylesheet" href="style.css?v=<?php echo filemtime(__DIR__ . '/style.css'); ?>">

  <!-- Linkify Script -->
  <script src="linkify.js?v=<?php echo filemtime(__DIR__ . '/linkify.js'); ?>"></script>
</head>

<body>
  <?php if (!$read_only): ?>
  <a href="javascript:;" onclick="showPanel()">show editor</a> |
  <a href="javascript:;" onclick="hidePanel()">hide editor</a> |
  <a href="javascript:void(0);" onclick="location.reload(true);">refresh</a>
  <div id="save-error" class="text-danger" role="alert"></div>

  <br><br>

  <input type="text" id="snapshot-name" placeholder="snapshot label" style="margin-right: 5px;">
  <a href="javascript:;" onclick="createSnapshot()">create snapshot</a>

  <h4>Available Snapshots</h4>
  <div id="snapshot-list" style="margin-bottom: 20px;"></div>
  <?php endif; ?>

  <div style="margin-bottom: 10px;">
    <label>
      <input type="checkbox" id="sync-toggle"> synchronize time axis zoom
    </label>
  </div>

  <div id="header"></div>
  <div id="notes"></div>

  <div id="panels" class="container-fluid">
    <div id="graphs" class="panel"></div>
    <?php if (!$read_only): ?>
    <div id="panelRight" style="display:none;width:930px;background:#FFFFFF;opacity:1.0">
      <div id="editor" class="panel"></div>
    </div>
    <?php endif; ?>
  </div>
</body>

<script>
  var $_GET = <?php echo json_encode(['filename' => $filename], JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT | JSON_INVALID_UTF8_SUBSTITUTE); ?>;

  <?php if (!$read_only): ?>
  function hidePanel() {
    $('#panelRight').dockPanel('hide', () => console.log('panel collapsed'));
  }

  function showPanel() {
    $('#panelRight').dockPanel('show', () => console.log('panel shown'));
  }

  $(function () {
    $('#panelRight').dockPanel({ dock: 'right' });
  });

  function createSnapshot() {
    const label = document.getElementById("snapshot-name").value.trim();
    if (!label) {
      alert("Please enter a snapshot label.");
      return;
    }

    fetch("create-snapshot.php", {
      method: "POST",
      body: new URLSearchParams({
        filename: $_GET.filename,
        label
      })
    })
      .then(async resp => {
        const message = await resp.text();
        if (!resp.ok) throw new Error(message || 'Request failed (HTTP ' + resp.status + ').');
        return message;
      })
      .then(result => {
        alert("Snapshot created:\n" + result);
        loadSnapshotList();
      })
      .catch(err => alert("Error creating snapshot:\n" + err));
  }

  function loadSnapshotList() {
    fetch("list-snapshots.php?filename=" + encodeURIComponent($_GET.filename))
      .then(resp => resp.json())
      .then(data => {
        const $list = $("#snapshot-list");
        $list.empty();

        if (!data.snapshots.length) {
          $list.append("<p>No snapshots found.</p>");
          return;
        }

        data.snapshots.forEach(snapshot => {
          const $section = $("<details>").append($("<summary>").text(snapshot.folder));
          const $ul = $("<ul>");

          snapshot.graphs.forEach(q => {
            let rowInfo = "";
            if (q.row_count !== null) rowInfo += ` — rows: ${q.row_count}`;
            if (q.csv_size_mb !== null && q.csv_size_mb !== undefined) {
              rowInfo += ` — size: ${q.csv_size_mb.toFixed(2)} MB`;
            }

            const $li = $("<li>").text(`${q.file}${rowInfo}`);
            const $ulQueries = $("<ul>");

            (q.queries || []).forEach(queryStr => {
              $ulQueries.append($("<li style='font-family:monospace; font-size:smaller;'>").text(queryStr));
            });

            $li.append($ulQueries);
            $ul.append($li);
          });

          $section.append($ul);
          $list.append($section);          
        });

        if (data.snapshots.length > 0) {
          const $deleteAll = $(`
            <div style="margin-top:10px;">
              <a href="javascript:;" id="delete-all-snapshots" class="text-danger">delete all snapshots</a>
            </div>
          `);
          $list.append($deleteAll);

          $("#delete-all-snapshots").on("click", () => {
            if (!confirm("Delete ALL snapshot folders? This cannot be undone.")) return;

            const fd = new FormData();
            fd.append("filename", $_GET.filename);

            fetch("delete-snapshots.php", { method: "POST", body: fd })
              .then(r => r.json())
              .then(res => {
                if (!res.ok) throw new Error(res.error || "Delete failed");
                alert("All snapshots deleted.");
                loadSnapshotList();  // refresh UI
              })
              .catch(err => alert("Error deleting snapshots: " + err.message));
          });
        }            
      });  
  }

  // Load snapshot list on initial page load
  loadSnapshotList();
  <?php endif; ?>
</script>

<script>
  // Waveforms app config
  const WAVEFORMS_BASE  = <?php echo json_encode($waveforms['url'], JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT); ?>;
  const WAVEFORMS_TABLE = <?php echo json_encode($waveforms['index_table'], JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT); ?>;
  const WAVEFORMS_DEBUG = false; // true => append &debug=1

  // Per-graph, per-series meta (aligned with labels.slice(1))
  // bandMetaByGraph[graphno] = [{device, filter}, ...]
  const bandMetaByGraph = {};

  // Format Date -> "YYYY-MM-DDTHH:MM:SS" (local time)
  function toLocalISO(dt) {
    const p = n => String(n).padStart(2, '0');
    return `${dt.getFullYear()}-${p(dt.getMonth()+1)}-${p(dt.getDate())}T${p(dt.getHours())}:${p(dt.getMinutes())}:${p(dt.getSeconds())}`;
  }

  // Build waveforms URL from dygraph window + meta (device/filter).
  // Channel is NOT sent: the backend infers it from the device (e.g., caen8ch_ch7).
  // Build waveforms URL; if windowOverride is given, use that start/end (ISO strings).
  function buildWaveformsURL(g, device, filter, windowOverride=null) {
    if (!WAVEFORMS_BASE) return '';
    let startISO, endISO;

    if (windowOverride && windowOverride.start && windowOverride.end) {
      startISO = windowOverride.start;
      endISO   = windowOverride.end;
    } else {
      const [xmin, xmax] = g.xAxisRange();
      startISO = toLocalISO(new Date(xmin));
      endISO   = toLocalISO(new Date(xmax));
    }

    const url = new URL(WAVEFORMS_BASE);
    const qs = url.searchParams;
    qs.set("table", WAVEFORMS_TABLE);
    qs.set("start", startISO);
    qs.set("end", endISO);
    if (device) qs.set("device", device);
    if (filter) qs.set("filter", filter);
    if (WAVEFORMS_DEBUG) qs.set("debug", "1");
    return url.toString();
  }

  // --------- Meta extraction (robust to naming differences) ----------

  function resolveArray(meta, names) {
    for (const n of names) {
      if (Array.isArray(meta?.[n])) return meta[n];
    }
    return null;
  }

  function extractDeviceFilterArraysFromMeta(meta) {
    let devices = resolveArray(meta, ["devices_per_band", "device_per_band", "devices"]);
    let filters = resolveArray(meta, ["filters_per_band", "filter_per_band", "filters"]);

    if ((!devices || !filters) && Array.isArray(meta?.bands)) {
      const pick = (obj, keys) => keys.reduce((v, k) => v ?? obj?.[k], null);
      const dn = ["device","Device","DEV","dev","DeviceName"];
      const fn = ["filter","Filter","FILTER","filters","Filters"];
      devices = devices || meta.bands.map(b => pick(b, dn));
      filters = filters || meta.bands.map(b => pick(b, fn));
    }
    return { devices: devices || [], filters: filters || [] };
  }

  // From the **graph config** (the JSON you edit in the UI)
  // Pulls device, filters, and per-band time window (start/end).
  function extractBandMetaFromGraphConfig(graph) {
    const bands = Array.isArray(graph?.bands) ? graph.bands : [];
    return bands.map(b => ({
      device: b.device || null,
      filter: b.filters || null,
      tStartISO: isoOrNull(b.start || null), // HH:MM etc. -> YYYY-MM-DDTHH:MM:SS
      tEndISO:   isoOrNull(b.end   || null),
      gran:      b.granularity != null ? Number(b.granularity) : null
    }));
  }

</script>

<script type="application/javascript">
  const charts = {};
  const chartConfigs = {};
  let jsoneditor, value, schema;
  let syncObj = null;

  const changeVisibility = (graphno, seriesno, changeto) => {
    charts[graphno].setVisibility(seriesno, changeto);
  };

  const changeAxisscale = (graphno, changeto) => {
    // Keep scale choices made while the CSV request is still loading.
    chartConfigs[graphno].logscale = changeto;
    charts[graphno]?.updateOptions({ logscale: changeto });
  };

  <?php if (!$read_only): ?>
  const editorElement = document.getElementById('editor');
  let textSaveTimer;
  editorElement.addEventListener('input', event => {
    const input = event.target;
    if (!input.matches('input[name], textarea[name]')) return;
    clearTimeout(textSaveTimer);
    textSaveTimer = setTimeout(() => {
      // JSON Editor reads text values on change; commit them after a typing pause.
      if (editorElement.contains(input)) input.dispatchEvent(new Event('change', { bubbles: true }));
    }, 500);
  });
  // Leaving a field still saves immediately; cancel its pending delayed change.
  editorElement.addEventListener('change', () => clearTimeout(textSaveTimer), true);

  let saveInProgress = false;
  let pendingSave = null;
  const savePanelChanges = () => {
    if (saveInProgress || pendingSave === null) return;
    saveInProgress = true;
    const fd = new FormData();
    fd.append("json", pendingSave);
    fd.append("filename", $_GET.filename);
    pendingSave = null;
    fetch("write-to-json.php", { method: "POST", body: fd })
      .then(resp => {
        if (!resp.ok) throw new Error('Request failed (HTTP ' + resp.status + ').');
        $('#save-error').text('');
      })
      .catch(err => {
        console.error('Panel save failed:', err);
        $('#save-error').text('Panel changes could not be saved. Keep this page open to retain your edits.');
      })
      .finally(() => {
        saveInProgress = false;
        savePanelChanges();
      });
  };

  const reloadEditor = () => {
    value = jsoneditor ? jsoneditor.getValue() : value;
    if (jsoneditor) jsoneditor.destroy();
    jsoneditor = new JSONEditor(document.getElementById('editor'), {
      theme: 'bootstrap3',
      iconlib: 'bootstrap3',
      schema,
      startval: value,
      no_additional_properties: true,
      show_opt_in: false
    });

    jsoneditor.on('change', () => {
      value = jsoneditor.getValue();
      // Each save contains the whole panel, so keep only the latest waiting version.
      pendingSave = JSON.stringify(value);
      savePanelChanges();
    });
  };
  <?php endif; ?>

  const random = Math.floor(Math.random() * 100000);

  // Encode each path segment while preserving the panel's nested folder structure.
  const encodePath = path => path.split('/').map(encodeURIComponent).join('/');

// ---------- filter helpers ----------
function isCpsCpmFilter(s) {
  return !!s && /\bcp[sm]\s*\(/i.test(String(s));
}
function isSpectrumFilter(s) {
  return !!s && /^spectrum\s*\(/i.test(String(s));
}
// Treat spectrum(...) the same as cps(...) for the backend (keep inner args)
function normalizeFilterForWaveforms(rawFilter) {
  if (!rawFilter) return rawFilter;
  return String(rawFilter).replace(/^spectrum\s*\(/i, "cps(");
}

// ---------- time helpers ----------
function isoOrNull(v) {
  if (!v) return null;
  try {
    // accept either ISO or Date parsable string; return canonical "YYYY-MM-DDTHH:MM:SS"
    const d = new Date(v);
    if (isNaN(d)) return null;
    const pad = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}` +
           `T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  } catch { return null; }
}

const buildGraph = ($element, graph, graphno, globalStart, globalEnd) => {

  if (graph.header) $element.append($('<h3>').text(`Graph ${graphno}: ${graph.header}`));

  // Seed band meta from the graph config (editor JSON)
  bandMetaByGraph[graphno] = extractBandMetaFromGraphConfig(graph);

  // File and directory info
  const panelName = $_GET.filename.split("/").pop().replace(".json", "");
  const panelDir = ["csv-files", ...$_GET.filename.split("/").slice(0, -1), panelName].join("/");

  const processedCsv = encodePath(`${panelDir}/${panelName}-${graphno}.csv`);
  const fullresCsv   = encodePath(`${panelDir}/${panelName}-${graphno}-fullres.csv`);
  // The page's token also identifies graph requests still waiting in the browser.
  const generation = <?php echo json_encode($generation); ?>;
  const dataUrl = `call-pyscript.php?filename=${encodeURIComponent($_GET.filename)}&graphno=${graphno}`
    + (generation ? '&generation=' + encodeURIComponent(generation) : '');

  // Flexbox layout container
  const $graphContainer = $(`
    <div class="graph-container" style="display: flex; flex-direction: row; align-items: flex-start; margin-bottom: 25px;">
      <div class="graph-left" style="flex: 1; position: relative;">
        <div class="graph-status text-muted" id="graph-status${graphno}" role="status">
          <span class="graph-spinner" aria-hidden="true"></span>
          <span>Loading graph data…</span>
        </div>
        <div class="dygraph" id="dygraph-${graphno}" aria-busy="true"></div>
      </div>
      <div class="graph-right" style="flex: 0 0 420px; margin-left: 15px;">
        <div class="all-legends" id="legend${graphno}"></div>
        <div class="controls" id="control${graphno}" style="margin-top: 10px;"></div>
        <div class="graph-metrics text-muted" id="metrics${graphno}" style="margin-top: 10px; font-size: small;"></div>
      </div>
    </div>
  `);
  $element.append($graphContainer);
  const graphElement = document.getElementById(`dygraph-${graphno}`);
  const graphStatus = document.getElementById(`graph-status${graphno}`);

  // Set control buttons
  const controlsHtml = `
    <a onClick="changeAxisscale(${graphno},1)">log scale</a> |
    <a onClick="changeAxisscale(${graphno},0)">lin scale</a><br>
    <a href="${escapeHtml(processedCsv)}" download>db data processed (gran.)</a> |
    <a href="${escapeHtml(fullresCsv)}" download>db data fullres</a> |
    <a href="${escapeHtml(dataUrl)}&amp;debug=1" target="_blank" rel="noopener">query</a>
  `;
  document.getElementById(`control${graphno}`).innerHTML = controlsHtml;

  // Dygraph legend formatter
  const legendFormatter = function (data) {
    const g = this;
    const labels = g.getLabels();
    if (!labels) return "";

    let html = data.xHTML ? `${escapeHtml(labels[0])}: ${data.xHTML}` : `${escapeHtml(labels[0])}: not selected`;

    const metaArr = bandMetaByGraph[graphno] || [];
    data.series.forEach((series, idx) => {
      const checked = series.isVisible ? "checked" : "";
      // Dygraphs already escapes labelHTML; keep its colour swatches and value formatting.
      const labelHTML = (series.yHTML == null)
        ? series.labelHTML
        : `${series.labelHTML}: ${series.yHTML}`;
      const labelFormatted = series.isHighlighted ? `<b>${labelHTML}</b>` : labelHTML;

      const m = metaArr[idx] || {};
      const device = m.device || null;
      const filter = m.filter || null;

      let wfLink = "";

      // An empty waveform URL disables links for every band.
      // Spectrum needs both band times; CPS/CPM uses the current zoom window.
      if (WAVEFORMS_BASE && isSpectrumFilter(filter)) {
        if (m.tStartISO && m.tEndISO) {
          const norm = normalizeFilterForWaveforms(filter);
          const href = buildWaveformsURL(g, device, norm, { start: m.tStartISO, end: m.tEndISO });
          wfLink = ` &nbsp;<a class="wf-link" data-series-idx="${idx}" data-fixed="1" href="${escapeHtml(href)}" target="_blank" rel="noopener" title="Open waveforms">waveforms (band time range)</a>`;
        }
      } else if (WAVEFORMS_BASE && isCpsCpmFilter(filter)) {
        const href = buildWaveformsURL(g, device, filter);
        wfLink = ` &nbsp;<a class="wf-link" data-series-idx="${idx}" data-fixed="0" href="${escapeHtml(href)}" target="_blank" rel="noopener" title="Open waveforms">waveforms (viewport time range)</a>`;
      }

      html += `<br><input type="checkbox" onClick="changeVisibility(${graphno},${idx},${!series.isVisible})" ${checked}> ${series.dashHTML} ${labelFormatted}${wfLink}`;
    });
    return html;
  };

  // Dygraph config
  const config = chartConfigs[graphno] = {
    labelsDiv: document.getElementById(`legend${graphno}`),
    legend: 'always',
    logscale: false,
    labelsSeparateLines: true,
    legendFormatter,
    axes: { y: { valueFormatter: v => v.toFixed(3) } },

    drawCallback: function (g) {
      const metaArr = bandMetaByGraph[graphno] || [];
      $(`#legend${graphno} a.wf-link`).each(function () {
        const idx = Number(this.getAttribute('data-series-idx'));
        const fixed = this.getAttribute('data-fixed') === '1'; // spectrum with band window
        const m = metaArr[idx];
        if (!m) return;

        const device = m.device || null;
        const filter = normalizeFilterForWaveforms(m.filter || null);

        if (fixed) {
          // Keep the band-specific window
          if (m.tStartISO && m.tEndISO) {
            this.href = buildWaveformsURL(g, device, filter, { start: m.tStartISO, end: m.tEndISO });
          }
        } else {
          // cps/cpm: follow the current dygraph window
          this.href = buildWaveformsURL(g, device, filter);
        }
      });
    }
  };

  const graphReady = () => {
    graphStatus.hidden = true;
    graphElement.setAttribute('aria-busy', 'false');
    const labels = charts[graphno].getLabels();
    if (!labels?.length) return;

    const xAxisLabel = labels[0].toLowerCase();

    if (xAxisLabel === "time" && globalStart) {
      charts[graphno].updateOptions({ dateWindow: [globalStart.getTime(), globalEnd?.getTime() || charts[graphno].xAxisRange()[1]] });
    }

    const visibilityArray = labels.slice(1).map(l => !l.trim().startsWith("#"));
    charts[graphno].updateOptions({ visibility: visibilityArray });

    const metaUrl = encodePath(`${panelDir}/${panelName}-${graphno}.meta.json`) + `?random=${random}`;

    fetch(metaUrl)
      .then(resp => resp.ok ? resp.json() : Promise.reject("No metadata"))
      .then(meta => {
        // Merge config-derived meta (cfgArr) with any meta.json hints, but KEEP band windows
        const cfgArr = bandMetaByGraph[graphno] || [];
        const { devices, filters } = extractDeviceFilterArraysFromMeta(meta);
        const maxLen = Math.max(cfgArr.length, devices.length, filters.length);

        const merged = [];
        for (let i = 0; i < maxLen; i++) {
          const fromCfg = cfgArr[i] || {};
          merged[i] = {
            device: (devices?.[i] ?? fromCfg.device ?? null),
            filter: (filters?.[i] ?? fromCfg.filter ?? null),

            // preserve band-specific window (needed for spectrum links)
            tStartISO: fromCfg.tStartISO ?? null,
            tEndISO:   fromCfg.tEndISO   ?? null,

            // keep gran too (if you ever use it)
            gran: fromCfg.gran ?? null
          };
        }
        bandMetaByGraph[graphno] = merged;

        // Nudge redraw so legendFormatter re-runs with preserved windows
        const g = charts[graphno];
        const [xmin, xmax] = g.xAxisRange();
        g.updateOptions({ dateWindow: [xmin, xmax] });

        // Keep our formatting as HTML, but display metadata values as plain text.
        let snapshotInfo;
        if (!meta.snapshot_used) {
          snapshotInfo = "No snapshot with matching query found; loading from DB";
        } else if (meta.snapshot_type === "partial") {
          snapshotInfo = `Partial snapshot used: ${escapeHtml(meta.snapshot_used)}<br>Snapshot end time: ${escapeHtml(meta.snapshot_end_time_used)}`;
        } else {
          snapshotInfo = `Full snapshot used: ${escapeHtml(meta.snapshot_used)}`;
        }

        const sqlTimingsLine = (!meta.snapshot_used || meta.snapshot_type === "partial")
          ? `SQL queries: ${(meta.timings_queries_per_band || [])
              .map(b => b.sql !== undefined ? `${b.sql.toFixed(2)} s` : "n/a")
              .join(", ")}`
          : null;

        const filterPostprocLine = (meta.timings_filterpostprocessing_per_band || []).length > 0
          ? `Filter postproc: ${meta.timings_filterpostprocessing_per_band
              .map(b => b.filter !== undefined ? `${b.filter.toFixed(2)} s` : "n/a")
              .join(", ")}`
          : null;

        const granularityPostprocLine = (meta.timings_granularitypostprocessing_per_band || []).length > 0
          ? `Granularity postproc: ${meta.timings_granularitypostprocessing_per_band
              .map(b => b.postproc !== undefined ? `${b.postproc.toFixed(2)} s` : "n/a")
              .join(", ")}`
          : null;

        const granularityLine = (meta.granularities || []).length > 0
          ? `Granularities: ${escapeHtml(meta.granularities.join(", "))}`
          : null;

        const mergingLines = (!meta.snapshot_used || meta.snapshot_type === "partial")
          ? `Band merging processed file: ${meta.merging_time_processed?.toFixed(2) ?? 'n/a'} s |
             Band merging fullres file: ${meta.merging_time_fullres?.toFixed(2) ?? 'n/a'} s<br>`
          : "";

        const timingsLines = [
          sqlTimingsLine,
          filterPostprocLine,
          granularityPostprocLine,
          granularityLine
        ].filter(Boolean).join("<br>");

        const queryLine = `
          Total time: ${meta.total_time?.toFixed(2) ?? 'n/a'} s<br>
          ${timingsLines}<br>
          ${mergingLines}
          Rows processed file: ${escapeHtml(meta.row_count_processed?.toLocaleString() ?? 'n/a')} |
          Rows fullres file: ${escapeHtml(meta.row_count_fullres?.toLocaleString() ?? 'n/a')}<br>
          CSV size processed file: ${meta.csv_size_mb?.toFixed(2) ?? 'n/a'} MB |
          CSV size fullres file: ${meta.fullres_csv_size_mb?.toFixed(2) ?? 'n/a'} MB
        `;

        document.getElementById(`metrics${graphno}`).innerHTML = `<em>${snapshotInfo}</em><br>${queryLine}`;
      })
      .catch(err => console.warn(`No metadata for graph ${graphno}:`, err));
  };

  // Only an active generator holds its request open. Retry busy responses in
  // the browser, keeping web workers and connections available for other work.
  const fetchGraphData = async () => {
    while (graphElement.isConnected) {
      const resp = await fetch(`${dataUrl}&random=${random}`);
      if (resp.status === 202 && resp.headers.get('X-Graph-Generation') === 'waiting') {
        await resp.text(); // Consume the small response before waiting, releasing the connection.
        const seconds = Math.max(1, Math.min(10, Number(resp.headers.get('Retry-After')) || 2));
        // Stagger retries from different graphs and tabs.
        await new Promise(resolve => setTimeout(resolve, seconds * 1000 + Math.random() * 500));
        continue;
      }
      if (resp.status === 409 && resp.headers.get('X-Graph-Generation') === 'cancelled') {
        const error = new Error('Graph generation cancelled. Reload the panel to try again.');
        error.name = 'GraphGenerationCancelled';
        throw error;
      }
      if (!resp.ok) throw new Error('Request failed (HTTP ' + resp.status + ').');
      return resp.text();
    }
    return null;
  };
  fetchGraphData()
    .then(csv => {
      if (csv === null || !graphElement.isConnected) return;
      // Dygraphs treats a string without a newline as another URL to fetch.
      if (!csv.trim() || !/[\r\n]/.test(csv)) throw new Error('No CSV data was returned.');
      charts[graphno] = new Dygraph(graphElement, csv, config);
      charts[graphno].ready(graphReady);
    })
    .catch(err => {
      if (!graphElement.isConnected) return;
      console.error('Graph ' + graphno + ' data load failed:', err);
      graphElement.setAttribute('aria-busy', 'false');
      graphStatus.className = 'graph-status text-danger';
      graphStatus.setAttribute('role', 'alert');
      graphStatus.textContent = err.name === 'GraphGenerationCancelled' ? err.message
        : 'Graph data could not be loaded. ' + err.message + ' Click “query” beside this graph for more information.';
      graphStatus.hidden = false;
    });
};

  const reloadGraphs = () => {
    const $graphs = $('#graphs').empty();
    const globalStart = value.start ? new Date(value.start) : null;
    const globalEnd = value.end ? new Date(value.end) : new Date();
    value.graphs.forEach((item, idx) => {
      const $graph = $('<div class="graph"></div>');
      $graphs.append($graph);
      buildGraph($graph, item, idx + 1, globalStart, globalEnd);
    });
  };

  $('#sync-toggle').on('change', function () {
    if (syncObj) {
      syncObj.detach();
      syncObj = null;
    }

    if (this.checked) {
      const timeCharts = Object.values(charts).filter(g => g.getLabels()?.[0]?.toLowerCase() === "time");
      if (timeCharts.length > 1) {
        syncObj = Dygraph.synchronize(timeCharts, {
          zoom: true,
          selection: false,
          range: false
        });
      }
    }
  });

  // Both modes load panel data; only editing needs the schema first.
  const loadPanel = () => {
    $.getJSON('json-files/' + encodePath($_GET.filename) + '?random=' + Math.random(), json => {
      value = json;
      $("#header").append($("<h2>").text(json.header));
      $("#notes").append(linkify(json.notes));
      reloadGraphs();
      <?php if (!$read_only): ?>
      reloadEditor();
      if (value.graphs.length === 0) $(showPanel);
      <?php endif; ?>
    });
  };

  <?php if ($read_only): ?>
  loadPanel();
  <?php else: ?>
  $.getJSON('panel-schema.json?nocache=' + Math.random(), loadedSchema => {
    schema = loadedSchema;
    loadPanel();
  });
  <?php endif; ?>
</script>

</html>
