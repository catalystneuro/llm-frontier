/* LLM Frontier extras: the download buttons, the embed snippet, and the
   chart-only embed mode. Kept apart from the dashboard script, which this
   file never reaches into: it reads the same data file and the page's own
   markup, so either script can change without the other. */
(function () {
  'use strict';
  var BASE = location.pathname.replace(/[^/]*$/, '');
  // The same root the dashboard script computes: metric pages live one
  // level below the site root and say so in data-root.
  var ROOT = (BASE + (document.body.getAttribute('data-root') || '')).replace(/[^/]+\/\.\.\//, '');
  var DATA_URL = ROOT + 'data/llm-frontier.json';
  var SITE = 'https://llm-frontier.catalystneuro.com/';

  // ---- embed mode ----
  // ?embed=1 strips the page to the frontier chart, for an <iframe> on
  // someone else's page. The class does the hiding in style.css; the hash
  // still picks the tab's view, the highlighted model, and the slider dates,
  // because the dashboard script reads it as usual.
  var EMBED = new URLSearchParams(location.search).get('embed') === '1';
  function pageUrl(embed) {
    return location.origin + location.pathname + (embed ? '?embed=1' : '') + location.hash;
  }
  if (EMBED) {
    document.documentElement.classList.add('pfc-embed');
    var fig = document.querySelector('.pfc-cols .pfc-figure');
    if (fig) {
      var credit = document.createElement('p');
      credit.className = 'pfc-embed-credit';
      var a = document.createElement('a');
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = 'LLM Frontier · llm-frontier.catalystneuro.com';
      a.href = pageUrl(false);
      // The dashboard rewrites the hash as the view changes; the link
      // follows it, so the full page opens on the view the reader was on.
      a.addEventListener('click', function () { a.href = pageUrl(false); });
      credit.append(a);
      fig.after(credit);
    }
    // The dashboard re-renders on resize; this makes it measure the chart
    // again if it drew before the page was stripped down.
    window.dispatchEvent(new Event('resize'));
    return;
  }

  // ---- downloads ----
  var dataPromise = null;
  function loadData() {
    if (!dataPromise) {
      dataPromise = fetch(DATA_URL).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
      dataPromise.catch(function () { dataPromise = null; });
    }
    return dataPromise;
  }
  // Text cells are quoted, and one that begins with a character a
  // spreadsheet would read as the start of a formula gets a leading
  // apostrophe, so a model name can never run as one. Numbers stay bare.
  function csvCell(v) {
    if (v == null) return '';
    if (typeof v === 'number') return isFinite(v) ? String(v) : '';
    var s = String(v);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return '"' + s.replace(/"/g, '""') + '"';
  }
  function toCsv(rows) {
    return rows.map(function (r) { return r.map(csvCell).join(','); }).join('\r\n') + '\r\n';
  }
  function yesNo(v) { return v == null ? '' : v ? 'yes' : 'no'; }
  // The index version a row's scores were measured under, from the era list:
  // era 0 is before the first boundary, and each later era starts at one.
  function versionOf(era, eras) {
    if (!eras || !eras.length) return '';
    if (era == null || era >= eras.length) return eras[eras.length - 1][2] || '';
    return era === 0 ? (eras[0][3] || '') : (eras[era - 1][2] || '');
  }
  function mainTable(d) {
    var caps = (d.capabilities || []).filter(function (c) { return !c.own; });
    var head = ['model', 'lab', 'release_date', 'intelligence_index', 'index_version', 'cost_per_task_usd',
                'time_per_task_s', 'open_weights', 'retired'];
    caps.forEach(function (c) { head.push(c.label + ' (' + c.metric + (c.percent ? ', %' : '') + ')'); });
    var rows = [head];
    d.models.forEach(function (m) {
      var row = [m[0], m[1], m[2], m[3], versionOf(m[9], d.eras), m[4], m[10], yesNo(m[6]), yesNo(m[5])];
      caps.forEach(function (c, i) { row.push(m[8] ? m[8][i] : null); });
      rows.push(row);
    });
    return rows;
  }
  function decisionTable(d) {
    var rows = [['model', 'lab', 'kind', 'first_measured', 'decision_index', 'index_version',
                 'cost_per_1k_tasks_usd', 'time_per_task_s', 'open_weights', 'retired']];
    (d.decision_models || []).forEach(function (m) {
      rows.push([m[0], m[1], m[13] === 'decision' ? 'decision model' : 'language model', m[2], m[3],
                 versionOf(m[9], d.decision_eras), m[4], m[10], yesNo(m[6]), yesNo(m[5])]);
    });
    return rows;
  }
  // Which models the page is showing: the Decision Index tab has its own
  // contestants. The selected tab says so; the page's own metric is the
  // fallback before the tabs have rendered.
  function onDecisionTab(d) {
    var own = (d.capabilities || []).filter(function (c) { return c.own; })[0];
    if (!own || !(d.decision_models || []).length) return false;
    var sel = document.querySelector('#pfc-cap-tabs [aria-selected="true"]');
    if (sel) return sel.textContent.trim() === own.label;
    return document.body.getAttribute('data-cap') === own.key;
  }
  function save(name, text) {
    var url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
    var a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  var csvBtn = document.getElementById('pfc-dl-csv');
  if (csvBtn) {
    csvBtn.addEventListener('click', function () {
      csvBtn.disabled = true;
      loadData().then(function (d) {
        var decision = onDecisionTab(d);
        save('llm-frontier-' + (decision ? 'decision-models-' : 'models-') + d.updated + '.csv',
             toCsv(decision ? decisionTable(d) : mainTable(d)));
      }).catch(function (err) {
        csvBtn.textContent = 'Download failed (' + err.message + ')';
      }).then(function () { csvBtn.disabled = false; });
    });
  }

  // ---- the embed snippet ----
  var embedBtn = document.getElementById('pfc-embed-btn');
  var embedBox = document.getElementById('pfc-embed-box');
  var embedCode = document.getElementById('pfc-embed-code');
  var copyBtn = document.getElementById('pfc-embed-copy');
  function snippet() {
    var src = pageUrl(true).replace(/&/g, '&amp;').replace(/"/g, '&quot;');
    return '<iframe src="' + src + '" title="LLM Frontier: the cheapest model at each level of capability" ' +
      'width="100%" height="660" style="border:0" loading="lazy"></iframe>';
  }
  if (embedBtn && embedBox && embedCode) {
    var refresh = function () { embedCode.value = snippet(); };
    embedBtn.addEventListener('click', function () {
      var open = embedBox.hidden;
      embedBox.hidden = !open;
      embedBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) { refresh(); embedCode.focus(); embedCode.select(); }
    });
    // The view can change while the box is open (a slider move rewrites the
    // hash), so the snippet is rebuilt whenever it is about to be used.
    embedCode.addEventListener('focus', function () { refresh(); embedCode.select(); });
    if (copyBtn) {
      copyBtn.addEventListener('click', function () {
        refresh();
        var done = function () {
          copyBtn.textContent = 'Copied';
          setTimeout(function () { copyBtn.textContent = 'Copy'; }, 1500);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(embedCode.value).then(done, function () { embedCode.select(); });
        } else {
          embedCode.select();
          try { if (document.execCommand('copy')) done(); } catch (e) { /* the text stays selected for a manual copy */ }
        }
      });
    }
  }
})();
