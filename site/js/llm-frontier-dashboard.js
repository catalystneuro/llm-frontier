/* LLM cost frontier dashboard. Same charts as the blog post, fed from
   /data/llm-frontier.json, which a scheduled workflow refreshes from
   Artificial Analysis. */
(function () {
  'use strict';
  var BASE = location.pathname.replace(/[^/]*$/, '');
  // Per-metric pages live one level below the site root and declare which
  // metric they are about; the root page declares neither.
  var PAGE_CAP = document.body.getAttribute('data-cap') || null;
  var ROOT = (BASE + (document.body.getAttribute('data-root') || '')).replace(/[^/]+\/\.\.\//, '');
  // PR previews live under /previews/pr-N/ on the production host and do
  // not carry the card images; SITE_ROOT points shared assets at the real
  // site, which serves them for every preview.
  var SITE_ROOT = ROOT.replace(/previews\/[^/]+\/$/, '');
  var DATA_URL = ROOT + 'data/llm-frontier.json';
  var DATA = null;

  var C = {
    surface: '#ffffff', grid: '#edf1f9', axis: '#d9e1f0',
    ink: '#101642', ink2: '#55607a', muted: '#5b6580', deemph: '#c2cbdc', retired: '#9aa4bb',
    mono: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    ord: ['#86b6ef', '#3987e5', '#1c5cab', '#0d366b']
  };
  var SNAPS = [];
  var TIERS = [];
  var CAPS = [];   // capability metric metadata from the data file
  var CAP = -1;    // active tab: -1 = overall Intelligence Index, else index into CAPS
  var ERAS = [];   // index era boundaries [date, note]; scores are only comparable within an era
  var ERA_VIEW = 0; // which era the Overall frontier chart shows; defaults to the current era
  var AXIS = 'cost'; // x-axis of the current view: measured cost, or measured time per task
  var SEL = null;      // name of the highlighted model or creator
  var SEL_KIND = 'model'; // 'model' rings one point; 'creator' lights up a lab's whole fleet

  var models = [], retiredByName = {}, openByName = {}, modelsByName = {}, creators = {};
  var lastDrawnView = null;
  function capMeta() { return CAP >= 0 ? CAPS[CAP] : null; }
  function curTiers() { return CAP < 0 ? TIERS : ((DATA.cap_tiers || {})[capMeta().key] || []); }
  function curTierCost() { return CAP < 0 ? DATA.tier_cost : ((DATA.cap_tier_cost || {})[capMeta().key] || {}); }
  function curTierSummary() { return CAP < 0 ? DATA.tier_summary : ((DATA.cap_tier_summary || {})[capMeta().key] || {}); }
  function score(m) { return CAP < 0 ? m.iq : (m.caps ? m.caps[CAP] : null); }
  function fmtScore(v) { var c = capMeta(); return v.toFixed(1) + (c && c.percent ? '%' : ''); }
  function metricName() { var c = capMeta(); return c ? c.metric : 'Index'; }
  function tierLabel(t) { var c = capMeta(); return '≥ ' + t + (c && c.percent ? '%' : ''); }
  // Cost in effect on a given date: the last recorded change at or before it.
  function costAt(m, date) {
    if (!m.hist) return m.mcost;
    var c = m.hist[0][1];
    for (var i = 0; i < m.hist.length; i++) { if (m.hist[i][0] <= date) c = m.hist[i][1]; else break; }
    return c;
  }
  // Index in effect on a given date, from the same change history.
  function iqAt(m, date) {
    if (!m.hist) return m.iq;
    var v = m.hist[0].length > 2 && m.hist[0][2] != null ? m.hist[0][2] : m.iq;
    for (var i = 0; i < m.hist.length; i++) {
      if (m.hist[i][0] > date) break;
      if (m.hist[i].length > 2 && m.hist[i][2] != null) v = m.hist[i][2];
    }
    return v;
  }
  function eraOfDate(date) {
    var n = 0;
    ERAS.forEach(function (e) { if (e[0] <= date) n++; });
    return n;
  }
  // Short display label for an era's index composition, from eras.json.
  function eraShortLabel(view) {
    if (!ERAS.length) return '';
    if (view >= ERAS.length) return ERAS[ERAS.length - 1][2] || '';
    return view === 0 ? (ERAS[0][3] || '') : (ERAS[view - 1][2] || '');
  }
  function dayBefore(iso) {
    var t = new Date(iso + 'T00:00:00Z');
    t.setUTCDate(t.getUTCDate() - 1);
    return t.toISOString().slice(0, 10);
  }
  // Cost on the current era's basis at any date. Within the current era it is
  // the cost measured then; before the recomposition it is the first settled
  // measurement scaled by the model's own price ratio from its dated history,
  // so price changes carry over, suite changes never leak in, and the series
  // is continuous at the settle point. Between the recomposition and the
  // settle point the basis is unsettled (or unmeasured, if the updater was
  // down), so those days hold the last pre-recomposition cost, projected.
  function basisCostAt(m, date) {
    if (!ERAS.length) return costAt(m, date);
    var start = ERAS[ERAS.length - 1][0];
    var settled = ERAS[ERAS.length - 1][4] || start;
    if (date >= settled) return costAt(m, date);
    var anchor = costAt(m, settled);
    var lastOld = costAt(m, dayBefore(start));
    return lastOld > 0 ? anchor * costAt(m, date < start ? date : dayBefore(start)) / lastOld : m.mcost;
  }
  // Whether a model had been measured under the era of snapDate by that date;
  // a model dropped at a boundary and re-measured later must not appear at
  // its old score in between.
  function measuredBy(m, snapDate) {
    var e = eraOfDate(snapDate);
    if (e === 0) return m.date <= snapDate;
    if (!m.hist) return m.date <= snapDate && eraOfDate(m.date) === e;
    for (var i = 0; i < m.hist.length; i++) if (m.hist[i][0] <= snapDate && eraOfDate(m.hist[i][0]) === e) return true;
    return false;
  }
  function isTimeAxis() { return AXIS === 'time'; }
  function fmtTime(s) {
    if (s >= 5400) return (s / 3600).toFixed(1) + 'h';
    if (s >= 90) return (s / 60).toFixed(1) + 'm';
    return (s >= 10 ? s.toFixed(0) : s.toFixed(1)) + 's';
  }
  function fmtVal(v) { return isTimeAxis() ? fmtTime(v) : fmt$(v); }
  // Time per task in effect on a date, within one era's view: dated
  // measurements from the change history where they exist, forward-filled
  // within the era. Before a model's first measurement, the current view
  // leans on its latest value and an archive view on its first era value;
  // times are never carried across an era boundary, since a recomposition
  // changes the task mix they are measured on.
  function timeAt(m, date, view) {
    var v = null, first = null;
    if (m.hist) for (var i = 0; i < m.hist.length; i++) {
      var h = m.hist[i];
      if (h.length > 3 && h[3] != null && eraOfDate(h[0]) === view) {
        if (first == null) first = h[3];
        if (h[0] <= date) v = h[3];
      }
    }
    if (v != null) return v;
    if (view === ERAS.length) return m.time != null ? m.time : first;
    return first;
  }
  function timeMeasuredBy(m, date, view) {
    if (!m || !m.hist) return false;
    for (var i = 0; i < m.hist.length; i++) {
      var h = m.hist[i];
      if (h[0] <= date && h.length > 3 && h[3] != null && eraOfDate(h[0]) === view) return true;
    }
    return false;
  }
  function eraHasTime(e) {
    var recs = (DATA.era_tier_time || [])[e];
    if (!recs) return false;
    for (var t in recs) if ((recs[t] || []).length) return true;
    return false;
  }
  // One quiet line of speed facts for a model's tooltip.
  function speedFacts(m) {
    if (m.tps == null && m.time == null && m.ttft == null) return null;
    var bits = [];
    if (m.tps != null) bits.push(m.tps + ' tok/s');
    if (m.ttft != null) bits.push('first answer token ' + fmtTime(m.ttft));
    if (m.time != null) bits.push(fmtTime(m.time) + '/task');
    return el('div', 'pfc-tt-speed', bits.join(' \u00b7 '));
  }
  function loadData(d) {
    DATA = d;
    SNAPS = d.snapshots.map(function (s) { return [s[0], s[1]]; });
    TIERS = d.tiers.slice();
    CAPS = d.capabilities || [];
    ERAS = d.eras || [];
    ERA_VIEW = ERAS.length;
    models = d.models.map(function (m) {
      return { name: m[0], creator: m[1], date: m[2], iq: m[3], mcost: m[4], retired: !!m[5], open: !!m[6], hist: m[7] || null, caps: m[8] || null,
               era: m[9] != null ? m[9] : (d.eras || []).length,
               time: m[10] != null ? m[10] : null, tps: m[11] != null ? m[11] : null, ttft: m[12] != null ? m[12] : null };
    });
    retiredByName = {}; openByName = {}; modelsByName = {}; creators = {};
    models.forEach(function (m) {
      retiredByName[m.name] = m.retired; openByName[m.name] = m.open; modelsByName[m.name] = m;
      creators[m.creator] = (creators[m.creator] || 0) + 1;
    });
  }

  function dot(svg, x, y, r, color, open) {
    if (open) svg.append(svgEl('circle', { cx: x, cy: y, r: r, fill: C.surface, stroke: color, 'stroke-width': 2 }));
    else svg.append(svgEl('circle', { cx: x, cy: y, r: r, fill: color, stroke: C.surface, 'stroke-width': 2 }));
  }

  function fmt$(c) { return '$' + (c >= 0.1 ? c.toFixed(2) : c >= 0.01 ? c.toFixed(3) : c.toFixed(4)); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function svgEl(tag, attrs) {
    var e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  var tt = el('div', 'pfc-tooltip');
  document.body.appendChild(tt);
  window.addEventListener('scroll', hideTip, { passive: true });  // the tooltip is fixed, so it would not follow its point

  function showTip(box, px, py, rows) {
    tt.replaceChildren.apply(tt, rows);
    tt.style.display = 'block';
    var r = box.getBoundingClientRect();
    var x = r.left + px + 14;
    var y = r.top + py - 10;
    tt.style.left = '0px'; tt.style.top = '0px';
    if (r.left + px + 14 + tt.offsetWidth > window.innerWidth - 8) x = r.left + px - tt.offsetWidth - 14;
    tt.style.left = x + 'px'; tt.style.top = y + 'px';
  }
  function hideTip() { tt.style.display = 'none'; }

  function attachHover(box, svg, points) {
    function nearest(ev) {
      var r = svg.getBoundingClientRect();
      var sx = svg.viewBox.baseVal.width / r.width;
      var mx = (ev.clientX - r.left) * sx, my = (ev.clientY - r.top) * sx;
      var best = null, bd = Infinity;
      var visible = points;
      visible.forEach(function (p) {
        var d = Math.hypot(p.x - mx, p.y - my);
        if (d < bd) { bd = d; best = p; }
      });
      return { best: best, dist: bd, visible: visible, sx: sx };
    }
    // Clicking a point highlights its model; clicking empty chart clears.
    svg.addEventListener('click', function (ev) {
      var n = nearest(ev);
      if (n.best && n.dist <= 40 && n.best.key && modelsByName[n.best.key]) {
        if (SEL !== n.best.key || SEL_KIND !== 'model') setSel('model', n.best.key);
      } else if (SEL) setModel(null);
    });
    svg.addEventListener('pointermove', function (ev) {
      var n = nearest(ev);
      var best = n.best, bd = n.dist, visible = n.visible, sx = n.sx;
      if (!best || bd > 40) { hideTip(); return; }
      var group = visible.filter(function (p) { return Math.hypot(p.x - best.x, p.y - best.y) < 3; });
      var rows = [], seen = {}, order = [];
      group.forEach(function (p) {
        var k = p.key || ('#' + p.x + ',' + p.y + Math.random());
        if (!seen[k]) { seen[k] = { pt: p, labels: [] }; order.push(k); }
        if (p.snapLabel) seen[k].labels.push(p.snapLabel);
        if (p.snapLabel && !seen[k].pt.snapLabel) seen[k].pt = p;
      });
      order.forEach(function (k) { rows = rows.concat(seen[k].pt.rows(seen[k].labels)); });
      showTip(box, best.x / sx, best.y / sx, rows);
    });
    svg.addEventListener('pointerleave', hideTip);
  }

  function frame(box, W, H, M, ariaLabel) {
    var svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': ariaLabel });
    svg.style.display = 'block'; svg.style.width = '100%'; svg.style.height = 'auto';
    return svg;
  }

  // ---- full window ----
  // Each figure gets a button that expands it to fill the browser window.
  // An expanded chart is drawn at the size of its box, not the page's
  // default size, so it uses the whole window; the view state, slider
  // window included, carries over. Escape closes it.
  function chartSize(box, defW, defH, below) {
    if (!box.closest('.pfc-expanded')) return { W: Math.max(320, Math.min(defW, box.clientWidth)), H: defH };
    return { W: Math.max(320, box.clientWidth), H: Math.max(defH * 0.6, box.clientHeight - below) };
  }
  function setExpanded(fig) {
    var open = document.querySelector('.pfc-expanded');
    if (open) open.classList.remove('pfc-expanded');
    if (fig && fig !== open) fig.classList.add('pfc-expanded');
    var now = document.querySelector('.pfc-expanded');
    document.documentElement.classList.toggle('pfc-noscroll', !!now);
    Array.prototype.forEach.call(document.querySelectorAll('.pfc-fs-btn'), function (b) {
      var on = now && now.contains(b);
      b.textContent = on ? 'Close full window' : 'Full window';
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    hideTip();
    renderAll();
  }
  function addExpandButtons() {
    Array.prototype.forEach.call(document.querySelectorAll('.pfc-figure'), function (fig) {
      var cap = fig.querySelector('figcaption');
      if (!cap || cap.querySelector('.pfc-fs-btn')) return;
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'pfc-fs-btn';
      b.textContent = 'Full window';
      b.setAttribute('aria-pressed', 'false');
      b.addEventListener('click', function () { setExpanded(fig); });
      cap.append(b);
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && document.querySelector('.pfc-expanded')) setExpanded(null);
    });
  }
  // ---- chart 1: intelligence vs cost, with a time slider ----
  // The labs with the most models on the chart each get a hue; every other
  // lab shares a neutral gray. Five hues plus gray is as many as stay
  // distinguishable with every pair on screen at once, as a scatter needs,
  // under color vision deficiency as well as normal vision.
  var PROVIDER_COLORS = [['OpenAI', '#1baf7a'], ['Anthropic', '#eb6834'], ['Google', '#2a78d6'], ['Alibaba', '#8a5a2b'], ['DeepSeek', '#4a3aa7']];
  var OTHER_COLOR = '#a3abbd';
  function providerColor(creator) {
    for (var i = 0; i < PROVIDER_COLORS.length; i++) if (PROVIDER_COLORS[i][0] === creator) return PROVIDER_COLORS[i][1];
    return OTHER_COLOR;
  }
  // Earlier frontiers are gray and lighten with age, newest first.
  var PAST_GRAYS = ['#a9b2c4', '#bcc4d3', '#cdd3df', '#dbe0e9'];
  // Reasoning variants of one model ('GPT-5.6 Luna (Xhigh)') share a base
  // name, split the same way as split_variant in update.py. Only a suffix
  // that names a reasoning mode groups variants, so dated versions like
  // "(Dec '24)" stay separate models. Order matters: 'xhigh' before 'high',
  // 'non-reasoning' before 'reasoning'.
  var EFFORTS = [['non-reasoning', 0], ['minimal', 1], ['xhigh', 5], ['max', 6], ['high', 4], ['medium', 3], ['low', 2], ['reasoning', 4]];
  function variantOf(name) {
    var mm = name.match(/^(.*?)\s*\(([^()]*)\)\s*$/);
    if (!mm) return null;
    var v = mm[2].toLowerCase();
    for (var i = 0; i < EFFORTS.length; i++) if (v.indexOf(EFFORTS[i][0]) >= 0) return { base: mm[1], rank: EFFORTS[i][1] };
    return null;
  }
  function dayNum(iso) { return Math.round(Date.parse(iso + 'T00:00:00Z') / 86400000); }
  function isoOfDay(n) { return new Date(n * 86400000).toISOString().slice(0, 10); }
  function fmtShortDate(iso) {
    var p = iso.split('-'), MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return MON[+p[1] - 1] + ' ' + (+p[2]) + ', ' + p[0];
  }
  // The provider key shared by both charts: one button per colored lab
  // among the given creators, which highlights that lab's models, and a gray
  // "Other" entry if any remaining lab is present.
  function appendProviderLegend(legend, creatorList) {
    legend.append(el('span', 'pfc-legend-title', 'Provider'));
    var hasOther = false;
    PROVIDER_COLORS.forEach(function (pc) {
      if (creatorList.indexOf(pc[0]) < 0) return;
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'pfc-lk pfc-lk-btn';
      b.setAttribute('aria-pressed', SEL_KIND === 'creator' && SEL === pc[0] ? 'true' : 'false');
      b.title = 'Highlight ' + pc[0] + ' models';
      var d = el('span', 'pfc-dot'); d.style.background = pc[1]; d.style.borderColor = pc[1];
      b.append(d, el('span', null, pc[0]));
      b.addEventListener('click', function () {
        setSel('creator', SEL_KIND === 'creator' && SEL === pc[0] ? null : pc[0]);
      });
      legend.append(b);
    });
    creatorList.forEach(function (c) { if (providerColor(c) === OTHER_COLOR) hasOther = true; });
    if (hasOther) {
      var o = el('span', 'pfc-lk'); var od = el('span', 'pfc-dot');
      od.style.background = OTHER_COLOR; od.style.borderColor = OTHER_COLOR;
      o.append(od, el('span', null, 'Other'));
      legend.append(o);
    }
  }

  // The release window the slider shows: models released from SLIDE_LO
  // through SLIDE, at prices in effect on SLIDE. null means the view's first
  // or last day.
  var SLIDE = null, SLIDE_LO = null;

  function renderFrontier() {
    var box = document.getElementById('pfc-frontier');
    if (!box) return;
    var prevCtl = box.querySelector('.pfc-time');  // the slider block's height, kept clear of the chart in a full window
    var below = prevCtl ? prevCtl.offsetHeight + 12 : 100;
    box.replaceChildren();
    // The chart shows one index era at a time on every tab; the era toggle
    // above the figure picks which. Scores are only comparable within an era
    // on the Overall tab, and measured costs are only comparable within an
    // era on every tab, since a recomposition changes the evaluation suite.
    // Every value in the selected view is the value in effect at the era's
    // last day, so an old era is a frozen picture of how it ended.
    var eraView = DATA.era_snapshots ? ERA_VIEW : ERAS.length;
    SNAPS = (DATA.era_snapshots ? DATA.era_snapshots[eraView] : DATA.snapshots).map(function (s) { return [s[0], s[1]]; });
    var viewEnd = SNAPS[SNAPS.length - 1][0];
    var timeAxis = isTimeAxis() && (eraView === ERAS.length || eraHasTime(eraView));
    function viewScore(m) { return CAP < 0 ? iqAt(m, viewEnd) : score(m); }
    function viewCost(m) { return timeAxis ? timeAt(m, viewEnd, eraView) : costAt(m, viewEnd); }
    var size = chartSize(box, 880, 440, below);
    var W = size.W, H = size.H;
    var M = { l: 56, r: 16, t: 12, b: 42 };
    var svg = frame(box, W, H, M, (CAP < 0 ? 'Intelligence Index' : metricName()) + ' versus ' + (timeAxis ? 'time' : 'cost') + ' per task, colored by provider, with the Pareto frontier at the date on the slider');
    // Models appear in an era's view only if they were measured in that era
    // (or later, for old views: their scores then are in the change history).
    var isCur = ERAS.length > 0 && eraView === ERAS.length;
    var ms = models.filter(function (m) {
      if (CAP >= 0 && score(m) == null) return false;
      if (timeAxis && timeAt(m, viewEnd, eraView) == null) return false;
      if (isCur) return m.era === ERAS.length;
      return m.era >= eraView && measuredBy(m, viewEnd);
    });
    if (!ms.length) {
      box.append(el('p', 'pfc-lead', 'No current-era measurements for this metric yet.'));
      renderModelChip(0);
      return;
    }
    var allS = ms.map(viewScore), allC = ms.map(viewCost);
    ms.forEach(function (m) {
      (m.hist || []).forEach(function (h) {
        if (h[0] > viewEnd) return;
        if (timeAxis) {
          var tv = timeAt(m, h[0], eraView);
          if (tv != null) allC.push(tv);
          if (!isCur && eraOfDate(h[0]) === eraView && CAP < 0 && h.length > 2 && h[2] != null) allS.push(h[2]);
          return;
        }
        if (isCur) { allC.push(basisCostAt(m, h[0])); return; }
        if (eraOfDate(h[0]) !== eraView) return;
        allC.push(h[1]);
        if (CAP < 0 && h.length > 2 && h[2] != null) allS.push(h[2]);
      });
    });
    var maxS = Math.max.apply(null, allS);
    var minS = Math.min.apply(null, allS);
    var maxCost = Math.max.apply(null, allC);
    var minCost = Math.min.apply(null, allC);
    var yTop = CAP < 0 ? Math.max(66, Math.ceil((maxS + 3) / 10) * 10) : Math.min(100, Math.ceil((maxS + 3) / 10) * 10);
    var yBot = Math.max(minS < 0 ? -100 : 0, Math.floor((minS - 3) / 10) * 10);
    var xd = [minCost * 0.66, maxCost * 1.5], yd = [yBot, yTop];
    function X(v) { return M.l + (Math.log10(v) - Math.log10(xd[0])) / (Math.log10(xd[1]) - Math.log10(xd[0])) * (W - M.l - M.r); }
    function Y(v) { return H - M.b - (v - yd[0]) / (yd[1] - yd[0]) * (H - M.t - M.b); }

    var cs = [];
    if (timeAxis) [1, 10, 60, 600, 3600, 36000].forEach(function (c) { if (c >= xd[0] && c <= xd[1]) cs.push(c); });
    else for (var c0 = 0.001; c0 <= xd[1]; c0 *= 10) if (c0 >= xd[0]) cs.push(c0);
    cs.forEach(function (c) {
      svg.append(svgEl('line', { x1: X(c), x2: X(c), y1: M.t, y2: H - M.b, stroke: C.grid, 'stroke-width': 1 }));
      var lb = svgEl('text', { x: X(c), y: H - M.b + 18, 'text-anchor': 'middle', 'font-size': 10.5, fill: C.muted, 'font-family': C.mono });
      lb.textContent = timeAxis ? (c >= 3600 ? (c / 3600) + 'h' : c >= 60 ? (c / 60) + 'm' : c + 's') : '$' + (c >= 1 ? c.toFixed(0) : c.toFixed(2));
      svg.append(lb);
    });
    var qs = []; for (var q0 = yd[0]; q0 <= yd[1] - 5; q0 += 10) qs.push(q0);
    qs.forEach(function (q) {
      svg.append(svgEl('line', { x1: M.l, x2: W - M.r, y1: Y(q), y2: Y(q), stroke: C.grid, 'stroke-width': 1 }));
      var lb = svgEl('text', { x: M.l - 8, y: Y(q) + 4, 'text-anchor': 'end', 'font-size': 10.5, fill: C.muted, 'font-family': C.mono });
      lb.textContent = q; svg.append(lb);
    });
    svg.append(svgEl('line', { x1: M.l, x2: W - M.r, y1: H - M.b, y2: H - M.b, stroke: C.axis, 'stroke-width': 1 }));
    svg.append(svgEl('line', { x1: M.l, x2: M.l, y1: M.t, y2: H - M.b, stroke: C.axis, 'stroke-width': 1 }));
    var xt = svgEl('text', { x: (M.l + W - M.r) / 2, y: H - 6, 'text-anchor': 'middle', 'font-size': 11.5, fill: C.ink2 });
    xt.textContent = (timeAxis ? 'Time' : 'Cost') + ' per task (log)'; svg.append(xt);
    var yt = svgEl('text', { x: 14, y: (M.t + H - M.b) / 2, 'font-size': 11.5, fill: C.ink2, transform: 'rotate(-90 14 ' + ((M.t + H - M.b) / 2) + ')', 'text-anchor': 'middle' });
    var verLbl = CAP < 0 ? eraShortLabel(eraView) : '';
    yt.textContent = CAP < 0 ? 'Artificial Analysis Intelligence Index' + (verLbl ? ' (' + verLbl + ')' : '') : metricName(); svg.append(yt);

    // Every value at a date is the one in effect then: a model appears on
    // its release (or, in an archive, its first measurement in that era) and
    // moves as its price changes. Models released before `from`, the start of
    // the slider's window, are left out.
    function stateAt(date, from) {
      var est = isCur && eraOfDate(date) < ERAS.length;
      return ms.filter(function (m) {
        if (from && m.date < from) return false;
        return isCur ? m.date <= date : measuredBy(m, date);
      }).map(function (m) {
        var c = timeAxis ? timeAt(m, date, eraView) : isCur ? basisCostAt(m, date) : costAt(m, date);
        var e = timeAxis ? (date < viewEnd && !timeMeasuredBy(m, date, eraView)) : est;
        return { m: m, name: m.name, iq: CAP < 0 ? (isCur ? viewScore(m) : iqAt(m, date)) : score(m), mcost: c, current: viewCost(m), est: e };
      }).filter(function (p) { return p.mcost != null && p.iq != null; });
    }
    function frontierOf(sub) {
      return sub.filter(function (p) {
        return !sub.some(function (o) { return o.iq >= p.iq && o.mcost <= p.mcost && (o.iq > p.iq || o.mcost < p.mcost); });
      }).sort(function (a, b) { return a.iq - b.iq; });
    }
    function stepPath(fr) {
      var d = 'M ' + X(fr[0].mcost) + ' ' + Y(fr[0].iq);
      fr.forEach(function (p, j) {
        if (j < fr.length - 1) d += ' H ' + X(fr[j + 1].mcost) + ' V ' + Y(fr[j + 1].iq);
      });
      return d + ' H ' + (W - M.r);
    }

    var startDay = Infinity;
    ms.forEach(function (m) { startDay = Math.min(startDay, dayNum(m.date)); });
    if (!isCur && eraView > 0) startDay = Math.max(startDay, dayNum(ERAS[eraView - 1][0]));
    var endDay = dayNum(viewEnd);
    startDay = Math.min(startDay, endDay);
    // Days on which something on the chart changes, for keyboard stepping.
    var stopSet = {};
    stopSet[startDay] = 1; stopSet[endDay] = 1;
    ms.forEach(function (m) {
      stopSet[dayNum(m.date)] = 1;
      (m.hist || []).forEach(function (h) { stopSet[dayNum(h[0])] = 1; });
    });
    var stops = Object.keys(stopSet).map(Number).filter(function (n) { return n >= startDay && n <= endDay; }).sort(function (a, b) { return a - b; });
    var t = SLIDE && dayNum(SLIDE) >= startDay && dayNum(SLIDE) < endDay ? SLIDE : viewEnd;
    var lo = SLIDE_LO && dayNum(SLIDE_LO) > startDay && SLIDE_LO <= t ? SLIDE_LO : null;
    SLIDE = t === viewEnd ? null : t; SLIDE_LO = lo;  // a date from the URL outside this view's range is dropped

    var past = SNAPS.slice(0, -1).map(function (s) {
      return { date: s[0], label: s[1].replace(/ 1, /, ' ') };
    }).filter(function (e) { return dayNum(e.date) >= startDay; });

    var pastLayer = svgEl('g', {}), layer = svgEl('g', {});
    svg.append(pastLayer, layer);
    var pts = [];
    var selCount = SEL == null ? 0 : ms.filter(selMatches).length;
    var selShown = selCount > 0;

    function stageText() {
      var end = t === viewEnd ? SNAPS[SNAPS.length - 1][1] : fmtShortDate(t);
      return lo ? 'Pareto frontier of models released ' + fmtShortDate(lo) + ' to ' + end : 'Pareto frontier as of ' + end;
    }
    function draw() {
      pastLayer.replaceChildren(); layer.replaceChildren(); pts.length = 0;
      // Earlier frontiers inside the window, from the models released in it
      // by then, labeled at their right end.
      var shown = past.filter(function (e) { return e.date < t && (!lo || e.date > lo); }).map(function (e) {
        return { label: e.label, fr: frontierOf(stateAt(e.date, lo)) };
      }).filter(function (e) { return e.fr.length; });
      var sub = stateAt(t, lo);
      var fr = frontierOf(sub);
      // A label is skipped where it would sit on the live frontier or on a
      // newer label.
      var labelYs = fr.length ? [Y(fr[fr.length - 1].iq) - 4] : [];
      shown.slice().reverse().forEach(function (e, age) {
        var gray = PAST_GRAYS[Math.min(age, PAST_GRAYS.length - 1)];
        pastLayer.append(svgEl('path', { d: stepPath(e.fr), fill: 'none', stroke: gray, 'stroke-width': 1.25, 'stroke-linejoin': 'round', 'class': 'pfc-past' }));
        var ly = Y(e.fr[e.fr.length - 1].iq) - 4;
        if (ly < M.t + 10 || labelYs.some(function (y) { return Math.abs(y - ly) < 11; })) return;
        labelYs.push(ly);
        var lb = svgEl('text', { x: W - M.r - 2, y: ly, 'text-anchor': 'end', 'font-size': 10, fill: age === 0 ? '#8a94aa' : '#a3abbd', 'class': 'pfc-past-label' });
        lb.textContent = e.label;
        pastLayer.append(lb);
      });

      var onFr = {};
      fr.forEach(function (p) { onFr[p.name] = 1; });
      sub.forEach(function (p) { p.x = X(p.mcost); p.y = Y(p.iq); });

      // Thin lines join the reasoning levels of one model, lowest effort first.
      var bases = {};
      sub.forEach(function (p) {
        var v = variantOf(p.name);
        if (!v) return;
        var k = p.m.creator + '|' + v.base;
        (bases[k] = bases[k] || []).push({ p: p, rank: v.rank });
      });
      Object.keys(bases).forEach(function (k) {
        var vs = bases[k];
        if (vs.length < 2) return;
        vs.sort(function (a, b) { return a.rank - b.rank || a.p.mcost - b.p.mcost; });
        var dim = selShown && !selMatches(vs[0].p.m);
        layer.append(svgEl('polyline', {
          points: vs.map(function (v) { return v.p.x + ',' + v.p.y; }).join(' '),
          fill: 'none', stroke: providerColor(vs[0].p.m.creator), 'stroke-width': 1.25, 'stroke-linejoin': 'round',
          opacity: dim ? 0.12 : 0.5, 'class': 'pfc-variants'
        }));
      });

      function tipRows(p) {
        var m = p.m;
        return function () {
          var d1 = el('div', 'pfc-tt-name'); d1.textContent = m.name;
          var d2 = el('div'); var s = el('span', 'pfc-tt-val'); s.textContent = (p.est ? '≈' : '') + (timeAxis ? fmtTime(p.mcost) : fmt$(p.mcost));
          d2.append(s, ' per task at ' + (CAP < 0 ? 'Index ' + p.iq.toFixed(1) : metricName() + ' ' + fmtScore(p.iq)) +
            (Math.abs(p.current - p.mcost) > 1e-9 ? (timeAxis ? ' (speed then; ' + fmtTime(p.current) + ' now)' : ' (price then; ' + fmt$(p.current) + (viewEnd === DATA.updated ? ' now)' : ' at era end)')) : '') +
            (p.est ? (timeAxis ? ' · at its nearest measured speed' : ' · estimated from its price history') : ''));
          var d3 = el('div', 'pfc-tt-row'); var kd = el('span', 'pfc-tt-key'); kd.style.borderTopColor = providerColor(m.creator);
          d3.append(kd, m.creator + ' · ' + (m.open ? 'open weights' : 'proprietary') + ' · released ' + m.date + (m.retired ? ' · retired' : ''));
          var rows = [d1, d2, d3];
          if (onFr[m.name]) rows.push(el('div', null, 'On the ' + stageText()));
          var sp = isCur && speedFacts(m); if (sp) rows.push(sp);
          return rows;
        };
      }
      function selMark(g, p) {
        if (SEL_KIND === 'model') {
          g.append(svgEl('circle', { cx: p.x, cy: p.y, r: 9.5, fill: 'none', stroke: C.ink, 'stroke-width': 1.5, 'class': 'pfc-sel-ring' }));
          var anchor = p.x < M.l + 70 ? 'start' : p.x > W - M.r - 70 ? 'end' : 'middle';
          var lx = anchor === 'start' ? p.x + 13 : anchor === 'end' ? p.x - 13 : p.x;
          var ly = p.y < M.t + 30 ? p.y + 24 : p.y - 15;
          var nl = svgEl('text', { x: lx, y: ly, 'text-anchor': anchor, 'font-size': 11, 'font-weight': 600, fill: C.ink, 'class': 'pfc-sel-label' });
          nl.textContent = p.name;
          g.append(nl);
        } else {
          g.append(svgEl('circle', { cx: p.x, cy: p.y, r: 8, fill: 'none', stroke: C.ink, 'stroke-width': 1.2, 'class': 'pfc-sel-ring' }));
        }
      }
      sub.forEach(function (p) {
        if (onFr[p.name]) return;
        var isSel = selMatches(p.m);
        var g = svgEl('g', { opacity: isSel ? 1 : selShown ? 0.15 : 0.75 });
        dot(g, p.x, p.y, isSel && SEL_KIND === 'model' ? 5 : 4, providerColor(p.m.creator), p.m.open);
        if (isSel) selMark(g, p);
        layer.append(g);
        pts.push({ x: p.x, y: p.y, key: p.name, rows: tipRows(p) });
      });

      // The frontier at the slider date: the one dark line on the chart.
      if (fr.length) {
        var fg = svgEl('g', {});
        var pline = svgEl('path', { d: stepPath(fr), fill: 'none', stroke: C.ink, 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round', 'data-current': '1' });
        fg.append(pline);
        fr.forEach(function (p) {
          dot(fg, p.x, p.y, 5, providerColor(p.m.creator), p.m.open);
          if (selMatches(p.m)) selMark(fg, p);
          pts.push({ x: p.x, y: p.y, key: p.name, rows: tipRows(p) });
        });
        layer.append(fg);
      }
      var cap = document.getElementById('pfc-frontier-stage');
      if (cap) cap.textContent = stageText();
    }
    draw();
    box.append(svg);
    // The page's one authored motion: when the view changes, the current
    // frontier draws itself in.
    var viewKey = CAP + ':' + eraView + ':' + AXIS;
    if (viewKey !== lastDrawnView && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      lastDrawnView = viewKey;
      var cur = svg.querySelector('path[data-current="1"]');
      if (cur) {
        var plen = cur.getTotalLength();
        cur.style.strokeDasharray = plen;
        cur.style.setProperty('--pfc-len', plen);
        cur.classList.add('pfc-draw-in');
      }
    }
    renderModelChip(selCount);
    attachHover(box, svg, pts);

    // The time slider has two handles that set a release window. Models
    // released outside it are hidden, and every frontier is drawn from the
    // models inside it, at the prices in effect on the right handle's date.
    // Ticks mark the dates of the gray frontiers and move the right handle;
    // arrow keys step either handle from one change to the next.
    var ctl = el('div', 'pfc-time');
    var stageLabel = el('span', 'pfc-stage'); stageLabel.id = 'pfc-frontier-stage';
    stageLabel.textContent = stageText();
    var range = el('div', 'pfc-range');
    var rail = el('div', 'pfc-rail'), fill = el('div', 'pfc-rail-fill');
    function handle(id, label, value) {
      var h = document.createElement('input');
      h.type = 'range'; h.id = id; h.className = 'pfc-slider';
      h.min = startDay; h.max = endDay; h.step = 1; h.value = value;
      h.setAttribute('aria-label', label);
      h.disabled = startDay === endDay;
      return h;
    }
    var loH = handle('pfc-frontier-slider-start', 'Earliest release date shown on the frontier chart', lo ? dayNum(lo) : startDay);
    var hiH = handle('pfc-frontier-slider', 'Date shown on the frontier chart', dayNum(t));
    function frac(n) { return (n - startDay) / Math.max(1, endDay - startDay); }
    function sync() {
      var a = frac(+loH.value), b = frac(+hiH.value);
      fill.style.left = 'calc(8px + ' + (a * 100) + '% - ' + (a * 16) + 'px)';
      fill.style.width = 'calc(' + ((b - a) * 100) + '% - ' + ((b - a) * 16) + 'px)';
      // When the handles meet, the one that can still move goes on top.
      loH.style.zIndex = +loH.value >= +hiH.value && +hiH.value > (startDay + endDay) / 2 ? 3 : 1;
      loH.setAttribute('aria-valuetext', fmtShortDate(isoOfDay(+loH.value)));
      hiH.setAttribute('aria-valuetext', fmtShortDate(t));
      stageLabel.textContent = stageText();
    }
    function setLo(n) {
      n = Math.max(startDay, Math.min(+hiH.value, n));
      loH.value = n;
      lo = n === startDay ? null : isoOfDay(n);
      SLIDE_LO = lo;
      syncHashSoon();
      hideTip(); sync(); draw();
    }
    function setHi(n) {
      n = Math.max(+loH.value, Math.min(endDay, n));
      hiH.value = n;
      t = n === endDay ? viewEnd : isoOfDay(n);
      SLIDE = t === viewEnd ? null : t;
      syncHashSoon();
      hideTip(); sync(); draw();
    }
    function stepper(h, set) {
      h.addEventListener('input', function () { set(+h.value); });
      h.addEventListener('keydown', function (ev) {
        var cur = +h.value, next = null;
        if (ev.key === 'ArrowRight' || ev.key === 'ArrowUp') next = stops.filter(function (n) { return n > cur; })[0];
        else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowDown') next = stops.filter(function (n) { return n < cur; }).pop();
        else return;
        ev.preventDefault();
        if (next != null) set(next);
      });
    }
    stepper(loH, setLo);
    stepper(hiH, setHi);
    range.append(rail, fill, loH, hiH);
    var ticks = el('div', 'pfc-ticks');
    past.forEach(function (e) {
      var f = frac(dayNum(e.date));
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'pfc-tick';
      b.style.left = 'calc(8px + ' + (f * 100) + '% - ' + (f * 16) + 'px)';
      b.title = e.label;
      b.setAttribute('aria-label', 'Show the chart as of ' + e.label);
      b.addEventListener('click', function () { setHi(dayNum(e.date)); });
      ticks.append(b);
    });
    var track = el('div', 'pfc-slider-track');
    var ends = el('div', 'pfc-slider-ends');
    ends.append(el('span', null, fmtShortDate(isoOfDay(startDay))), el('span', null, SNAPS[SNAPS.length - 1][1] === 'today' ? 'today' : fmtShortDate(viewEnd)));
    track.append(range, ticks);
    ctl.append(stageLabel, track, ends);
    box.append(ctl);
    sync();

    var legend = document.getElementById('pfc-frontier-legend');
    if (legend) {
      legend.replaceChildren();
      appendProviderLegend(legend, ms.map(function (m) { return m.creator; }));
      var live = el('span', 'pfc-lk pfc-lk-gap');
      var d1 = el('span', 'pfc-dot');
      d1.style.background = C.deemph; d1.style.borderColor = C.deemph;
      live.append(d1, el('span', null, 'proprietary'));
      var ret = el('span', 'pfc-lk');
      var d2 = el('span', 'pfc-dot');
      d2.style.background = C.surface; d2.style.borderColor = C.deemph;
      ret.append(d2, el('span', null, 'open weights'));
      legend.append(live, ret);
      [['Pareto frontier', C.ink, 2.5], ['earlier frontiers', PAST_GRAYS[0], 1.25], ['reasoning levels', C.ink2, 1.5, 0.6]].forEach(function (k, i) {
        var item = el('span', 'pfc-lk' + (i === 0 ? ' pfc-lk-gap' : ''));
        var sw = el('span', 'pfc-swatch');
        sw.style.borderTopColor = k[1]; sw.style.borderTopWidth = k[2] + 'px';
        if (k[3]) sw.style.opacity = k[3];
        item.append(sw, el('span', null, k[0]));
        legend.append(item);
      });
      if (verLbl) legend.append(el('span', 'pfc-legend-ver', 'AA Intelligence Index ' + verLbl));
    }
  }

  // ---- shareable view state in the URL hash ----
  // The slider window rides at the end as @FROM..TO, either side left
  // empty when its handle is at the end of the range: #index@2026-01-25..
  // shows models released since that date, as of today.
  function hashFor() {
    var key = CAP >= 0 ? CAPS[CAP].key : 'index';
    var win = SLIDE || SLIDE_LO ? '@' + (SLIDE_LO || '') + '..' + (SLIDE || '') : '';
    var sel = (SEL ? '~' + slugify(SEL) : '') + win;
    if (ERAS.length && ERA_VIEW < ERAS.length && DATA.era_snapshots) {
      var snaps = DATA.era_snapshots[ERA_VIEW];
      return '#' + key + '-through-' + snaps[snaps.length - 1][0] + (isTimeAxis() ? '-time' : '') + sel;
    }
    if (isTimeAxis()) return '#' + key + '-time' + sel;
    if (CAP >= 0) return key === PAGE_CAP && !sel ? '' : '#' + key + sel;
    return sel ? '#index' + sel : '';
  }
  // Dragging a slider handle fires many input events, and Safari limits how
  // often a page may call replaceState, so slider moves write the hash once
  // the handle settles.
  var hashTimer = null;
  function syncHashSoon() { clearTimeout(hashTimer); hashTimer = setTimeout(syncHash, 250); }
  function syncHash() {
    clearTimeout(hashTimer);
    var h = hashFor();
    if ((location.hash || '') === h) return;
    history.replaceState(null, '', h || location.pathname + location.search);
  }
  function applyHash() {
    var h = (location.hash || '').replace(/^#/, '');
    var win = h.match(/@(\d{4}-\d{2}-\d{2})?\.\.(\d{4}-\d{2}-\d{2})?$/);
    if (win) {
      // renderFrontier drops a date outside the view's range.
      SLIDE_LO = win[1] || null; SLIDE = win[2] || null;
      h = h.slice(0, win.index);
    }
    var tilde = h.indexOf('~');
    if (tilde >= 0) {
      var slug = h.slice(tilde + 1);
      h = h.slice(0, tilde);
      for (var cn in creators) if (slugify(cn) === slug) { SEL = cn; SEL_KIND = 'creator'; break; }
      if (!SEL) for (var nm in modelsByName) if (slugify(nm) === slug) { SEL = nm; SEL_KIND = 'model'; break; }
    }
    if (!h || h === 'advances' || h === 'index') return;
    if (/-time$/.test(h)) {
      var base = h.replace(/-time$/, '');
      if (base === 'index') { AXIS = 'time'; return; }
      for (var j = 0; j < CAPS.length; j++) if (CAPS[j].key === base) { CAP = j; AXIS = 'time'; return; }
    }
    for (var i = 0; i < CAPS.length; i++) if (CAPS[i].key === h) { CAP = i; return; }
    var m = h.match(/^([a-z]+)-through-(\d{4}-\d{2}-\d{2})(-time)?$/);
    if (m && DATA.era_snapshots) {
      var cap = -1;
      if (m[1] !== 'index') {
        for (var k = 0; k < CAPS.length; k++) if (CAPS[k].key === m[1]) cap = k;
        if (cap === -1) return;
      }
      for (var e = 0; e < ERAS.length; e++) {
        var snaps = DATA.era_snapshots[e];
        if (snaps[snaps.length - 1][0] === m[2]) { CAP = cap; ERA_VIEW = e; if (m[3]) AXIS = 'time'; return; }
      }
    }
  }

  // ---- the era archive link on the frontier chart ----
  // Past eras are an archive, not a peer view: every tab gets a quiet link to
  // each frozen era instead of a toggle. On Overall the score scale changed
  // there; on capability tabs the scores carry over but the cost basis does
  // not, so the earlier frontier history is an archive too.
  function switchEra(i) {
    ERA_VIEW = i;
    if (i < ERAS.length && !eraHasTime(i)) AXIS = 'cost';  // this archive has no time measurements
    advPage = 1;
    hideTip();
    SLIDE = null; SLIDE_LO = null;  // a new view opens on its full range
    syncHash();
    renderEraNav(); renderAxisNav(); renderFrontier(); renderCapTable(); renderRecords(); renderTable(); renderAdvances();
  }
  // A dropdown, since every recomposition adds an archive: the current index
  // first, then the archives newest first.
  function renderEraNav() {
    var nav = document.getElementById('pfc-era-nav');
    if (!nav) return;
    var show = ERAS.length > 0 && DATA.era_snapshots;
    nav.hidden = !show;
    if (!show) return;
    var sel = document.createElement('select');
    sel.id = 'pfc-era-select';
    sel.setAttribute('aria-label', 'Intelligence Index version');
    for (var i = ERAS.length; i >= 0; i--) {
      var o = document.createElement('option');
      var end = DATA.era_snapshots[i][DATA.era_snapshots[i].length - 1][0];
      var lbl = eraShortLabel(i);
      o.value = String(i);
      o.textContent = i === ERAS.length
        ? 'Index ' + (lbl || 'current') + (lbl ? ' (current)' : '')
        : 'Index ' + (lbl ? lbl + ' ' : '') + '(through ' + fmtDate(end) + ')';
      o.selected = ERA_VIEW === i;
      sel.append(o);
    }
    sel.addEventListener('change', function () {
      var i = +sel.value;
      if (ERA_VIEW !== i) switchEra(i);
    });
    nav.replaceChildren(sel);
  }

  // ---- the cost/time axis toggle ----
  // Speed is a second x-axis, not a second chart: the toggle swaps measured
  // cost per task for Artificial Analysis's measured end-to-end time per
  // task. Times are only measured going forward, so the toggle exists only
  // on the current view; the archives are cost-only.
  function renderAxisNav() {
    var nav = document.getElementById('pfc-axis-nav');
    if (!nav) return;
    var isCurView = !ERAS.length || ERA_VIEW === ERAS.length;
    var show = isCurView ? models.some(function (m) { return m.time != null; }) : eraHasTime(ERA_VIEW);
    nav.hidden = !show;
    if (!show) { AXIS = 'cost'; return; }
    nav.replaceChildren();
    [['Cost', 'cost'], ['Time per task', 'time']].forEach(function (t) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = t[0];
      b.setAttribute('aria-pressed', AXIS === t[1] ? 'true' : 'false');
      b.addEventListener('click', function () {
        if (AXIS === t[1]) return;
        AXIS = t[1];
        advPage = 1;
        hideTip();
        SLIDE = null; SLIDE_LO = null;
        syncHash();
        renderTabs(); renderAxisNav(); renderFrontier(); renderCapTable(); renderRecords(); renderTable(); renderAdvances();
      });
      nav.append(b);
    });
  }

  // ---- finding one model on the charts ----
  // One model can be highlighted at a time: its points get a ring and a
  // label while the rest of the scatter recedes, a chip above the figures
  // names it, and the selection survives tab, era, and axis switches so a
  // model can be followed across every view. Set from the search box or by
  // clicking a model name anywhere on the page; shareable as #view~slug.
  function setSel(kind, name) {
    var known = kind === 'creator' ? creators[name] : modelsByName[name];
    SEL = name && known ? name : null;
    SEL_KIND = SEL ? kind : 'model';
    hideTip();
    syncHash();
    renderTabs(); renderFrontier(); renderRecords();
  }
  function setModel(name) { setSel('model', name); }
  function selMatches(m) {
    return SEL != null && (SEL_KIND === 'model' ? SEL === m.name : SEL === m.creator);
  }
  function selMatchesName(name) {
    if (SEL == null) return false;
    if (SEL_KIND === 'model') return SEL === name;
    var m = modelsByName[name];
    return !!m && m.creator === SEL;
  }
  function nameLink(name, cls, target) {
    var sp = el('span', cls || null, name);
    var t = target || name;
    if (modelsByName[t]) {
      sp.classList.add('pfc-name-link');
      sp.setAttribute('role', 'button');
      sp.setAttribute('tabindex', '0');
      sp.title = 'Highlight ' + t + ' on the charts';
      var go = function () { setModel(t); document.getElementById('pfc-frontier').scrollIntoView({ behavior: 'smooth', block: 'nearest' }); };
      sp.addEventListener('click', go);
      sp.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); go(); } });
    }
    return sp;
  }
  function renderSearch() {
    var input = document.getElementById('pfc-model-search');
    var list = document.getElementById('pfc-model-list');
    if (!input || !list || input.dataset.wired) return;
    input.dataset.wired = '1';
    Object.keys(creators).sort().forEach(function (c) {
      var o = document.createElement('option');
      o.value = c;
      o.label = 'all ' + creators[c] + ' models';
      list.append(o);
    });
    models.slice().sort(function (a, b) { return a.name < b.name ? -1 : 1; }).forEach(function (m) {
      var o = document.createElement('option');
      o.value = m.name;
      o.label = m.creator;
      list.append(o);
    });
    var apply = function () {
      var v = input.value.trim();
      if (creators[v]) { if (v !== SEL || SEL_KIND !== 'creator') setSel('creator', v); }
      else if (modelsByName[v]) { if (v !== SEL || SEL_KIND !== 'model') setModel(v); }
      else if (!v && SEL) setModel(null);
    };
    input.addEventListener('change', apply);
    input.addEventListener('input', function () { var v = input.value.trim(); if (modelsByName[v] || creators[v]) apply(); });
    input.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { input.value = ''; setModel(null); } });
  }
  // The chip names the selection and, when the current view cannot contain
  // the model, says why and offers the view that can.
  function renderModelChip(shown) {
    var chip = document.getElementById('pfc-model-chip');
    if (!chip) return;
    var input = document.getElementById('pfc-model-search');
    if (input && input.value.trim() !== (SEL || '')) input.value = SEL || '';
    chip.hidden = !SEL;
    if (!SEL) return;
    chip.replaceChildren();
    var facts = null, action = null;
    var showCostAxis = ['Show on the cost axis', function () { AXIS = 'cost'; advPage = 1; syncHash(); renderAxisNav(); renderFrontier(); renderCapTable(); renderRecords(); renderTable(); renderAdvances(); }];
    if (SEL_KIND === 'creator') {
      chip.append(el('span', 'pfc-chip-name', SEL));
      var fleet = models.filter(function (mm) { return mm.creator === SEL; });
      if (shown) {
        facts = shown + ' of ' + fleet.length + ' models in this view';
      } else if (CAP >= 0 && !fleet.some(function (mm) { return score(mm) != null; })) {
        facts = 'no models measured on ' + metricName();
      } else if (isTimeAxis() && !fleet.some(function (mm) { return timeAt(mm, DATA.updated, ERA_VIEW) != null; })) {
        facts = 'no measured speeds';
        action = showCostAxis;
      } else if (ERAS.length && ERA_VIEW === ERAS.length) {
        var last = Math.max.apply(null, fleet.map(function (mm) { return mm.era; }));
        facts = 'no models measured under the current index';
        if (last < ERAS.length) action = ['View the ' + (eraShortLabel(last) || 'archive') + ' archive', function () { switchEra(last); }];
      } else {
        facts = 'no models measured under index ' + (eraShortLabel(ERA_VIEW) || 'this era');
        action = ['View the current index', function () { switchEra(ERAS.length); }];
      }
      chip.append(el('span', 'pfc-chip-facts', facts));
      if (action) {
        var cb = document.createElement('button');
        cb.type = 'button'; cb.textContent = action[0];
        cb.addEventListener('click', action[1]);
        chip.append(cb);
      }
      var cx = document.createElement('button');
      cx.type = 'button'; cx.className = 'pfc-chip-clear'; cx.textContent = '\u00d7';
      cx.setAttribute('aria-label', 'Clear the highlight');
      cx.addEventListener('click', function () { setModel(null); });
      chip.append(cx);
      return;
    }
    var m = modelsByName[SEL];
    var eraEnd = ERAS.length && ERA_VIEW < ERAS.length && DATA.era_snapshots
      ? DATA.era_snapshots[ERA_VIEW][DATA.era_snapshots[ERA_VIEW].length - 1][0] : DATA.updated;
    chip.append(el('span', 'pfc-chip-name', m.name));
    if (CAP >= 0 && score(m) == null) {
      facts = 'not measured on ' + metricName();
    } else if (isTimeAxis() && timeAt(m, eraEnd, ERA_VIEW) == null) {
      facts = ERAS.length && ERA_VIEW < ERAS.length ? 'no measured speed in this era' : 'no measured speed';
      action = showCostAxis;
    } else if (!shown && ERAS.length && ERA_VIEW === ERAS.length && m.era < ERAS.length) {
      facts = 'not measured under the current index';
      action = ['View the ' + (eraShortLabel(m.era) || 'archive') + ' archive', function () { switchEra(m.era); }];
    } else if (!shown && ERAS.length && ERA_VIEW < ERAS.length) {
      facts = 'not measured under index ' + (eraShortLabel(ERA_VIEW) || 'this era');
      action = ['View the current index', function () { switchEra(ERAS.length); }];
    } else {
      var v = isTimeAxis() ? timeAt(m, eraEnd, ERA_VIEW) : m.mcost;
      facts = m.creator + ' \u00b7 ' + (CAP < 0 ? 'Index ' + m.iq.toFixed(1) : metricName() + ' ' + fmtScore(score(m))) + ' at ' + fmtVal(v) + (m.retired ? ' \u00b7 retired' : '');
    }
    chip.append(el('span', 'pfc-chip-facts', facts));
    if (action) {
      var b = document.createElement('button');
      b.type = 'button'; b.textContent = action[0];
      b.addEventListener('click', action[1]);
      chip.append(b);
    }
    var x = document.createElement('button');
    x.type = 'button'; x.className = 'pfc-chip-clear'; x.textContent = '\u00d7';
    x.setAttribute('aria-label', 'Clear the model highlight');
    x.addEventListener('click', function () { setModel(null); });
    chip.append(x);
  }

  // ---- capability tabs ----
  var leadDefault = null;
  // Each metric is a real page; the tabs are links between them, carrying
  // the axis and any highlighted model along so a view survives the hop.
  function renderTabs() {
    var bar = document.getElementById('pfc-cap-tabs');
    if (!bar || !CAPS.length) return;
    bar.replaceChildren();
    var tabs = [['Overall', -1]].concat(CAPS.map(function (c, i) { return [c.label, i]; }));
    tabs.forEach(function (t) {
      var cur = CAP === t[1];
      var b = document.createElement(cur ? 'span' : 'a');
      b.className = 'pfc-tab';
      b.textContent = t[0];
      b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', cur ? 'true' : 'false');
      if (!cur) {
        var key = t[1] < 0 ? 'index' : CAPS[t[1]].key;
        var frag = '';
        if (isTimeAxis() && (!ERAS.length || ERA_VIEW === ERAS.length)) frag = '#' + key + '-time';
        if (SEL) frag = (frag || '#' + key) + '~' + slugify(SEL);
        b.href = (t[1] < 0 ? ROOT : ROOT + CAPS[t[1]].key + '/') + frag;
      }
      bar.append(b);
    });
  }
  function renderLead() {
    var p = document.getElementById('pfc-frontier-lead');
    if (!p) return;
    if (leadDefault === null) leadDefault = p.innerHTML;
    var c = capMeta();
    function evalLink(url, label) {
      var a = document.createElement('a');
      a.href = url; a.textContent = label;
      return a;
    }
    if (!c) { p.innerHTML = leadDefault; return; }
    var n = models.filter(function (m) { return score(m) != null; }).length;
    p.replaceChildren();
    // Link the blurb's first mention of the metric to its evaluation page.
    var txt = c.blurb;
    var cands = [c.metric, c.metric.replace(/^AA[ -]/, ''), c.metric.replace(/-AA$/, '')];
    var hit = null, at = -1;
    for (var i = 0; i < cands.length && at < 0; i++) { at = txt.indexOf(cands[i]); if (at >= 0) hit = cands[i]; }
    if (c.url && at >= 0) p.append(txt.slice(0, at), evalLink(c.url, hit), txt.slice(at + hit.length));
    else p.append(txt);
    p.append(' Measured for ' + n + ' of ' + models.length + ' tracked models.');
  }
  function renderCapTable() {
    var wrap = document.getElementById('pfc-cap-table-wrap');
    if (!wrap) return;
    var c = capMeta();
    if (!c) { wrap.hidden = true; return; }
    wrap.hidden = false;
    var th = document.getElementById('pfc-cap-metric-name');
    if (th) th.textContent = c.metric;
    var tb = document.getElementById('pfc-cap-table');
    if (!tb) return;
    var timeAxis = isTimeAxis() && ERA_VIEW === ERAS.length;
    var ch = document.getElementById('pfc-cap-cost-head');
    if (ch) ch.textContent = timeAxis ? 'Time per task' : 'Cost per task';
    var bh = document.getElementById('pfc-cap-best-head');
    if (bh) bh.textContent = (timeAxis ? 'Fastest' : 'Cheapest') + ' model at or above';
    tb.replaceChildren();
    function axisVal(m) { return timeAxis ? m.time : m.mcost; }
    var live = models.filter(function (m) { return !m.retired && score(m) != null && m.era === ERAS.length && axisVal(m) != null; });
    if (!live.length) return;
    var maxS = Math.max.apply(null, live.map(score));
    var hi = Math.floor(maxS / 10) * 10;
    function cell(node, cls) { var td = document.createElement('td'); if (cls) td.className = cls; td.append(node); return td; }
    [hi, hi - 10, hi - 20, hi - 30].forEach(function (t) {
      if (t <= 0) return;
      var cands = live.filter(function (m) { return score(m) >= t; });
      if (!cands.length) return;
      var best = cands.reduce(function (a, b) { return axisVal(b) < axisVal(a) ? b : a; });
      var tr = document.createElement('tr');
      tr.append(cell('≥ ' + t + (c.percent ? '%' : ''), 'pfc-td-tier'));
      var w = el('div', 'pfc-ev');
      var a1 = el('div', 'pfc-ev-top'); a1.append(nameLink(best.name));
      var a2 = el('div', 'pfc-ev-model'); a2.textContent = best.creator + ' · ' + (best.open ? 'open weights' : 'proprietary');
      w.append(a1, a2);
      tr.append(cell(w));
      tr.append(cell(fmtVal(axisVal(best)), 'pfc-td-num'));
      tr.append(cell(fmtScore(score(best)), 'pfc-td-num'));
      tb.append(tr);
    });
  }

  // ---- chart 2: cost records by tier ----
  function renderRecords() {
    var box = document.getElementById('pfc-records');
    if (!box) return;
    box.replaceChildren();
    var size = chartSize(box, 880, 370, 4);
    var W = size.W, H = size.H;
    var M = { l: 56, r: 60, t: 12, b: 40 };
    // The era toggle controls this chart too. The current view is one
    // continuous series rebased onto the current index: current scores, with
    // pre-recomposition costs from each model's own price ratios. An archive
    // view shows only that era's actual measured records.
    var isCur = ERAS.length > 0 && ERA_VIEW === ERAS.length;
    var isArch = ERAS.length > 0 && ERA_VIEW < ERAS.length;
    var timeAxis = isTimeAxis() && (!isArch || eraHasTime(ERA_VIEW));
    var title = document.getElementById('pfc-records-title');
    if (title) title.textContent = (timeAxis ? 'Speed' : 'Cost') + ' Records by Capability Tier';
    var lead = document.getElementById('pfc-records-lead');
    if (lead) {
      lead.textContent = timeAxis
        ? 'The fastest measured time per task achieved by any released model at or above each ' + (CAP < 0 ? 'Intelligence Index' : metricName()) + ' tier, by release date. Each step is a model that set a new speed record for its tier.'
        : 'The cheapest cost per task achieved by any released model at or above each ' + (CAP < 0 ? 'Intelligence Index' : metricName()) + ' tier, by release date. Each step is a model that set a new low for its tier.';
    }
    var svg = frame(box, W, H, M, 'Running minimum ' + (timeAxis ? 'time' : 'cost') + ' per task by capability tier');
    var tiers = curTiers();
    var tierCost = curTierCost();
    if (timeAxis) {
      tierCost = isArch
        ? (CAP < 0 ? ((DATA.era_tier_time || [])[ERA_VIEW] || {}) : (((DATA.era_cap_tier_time || {})[capMeta().key] || [])[ERA_VIEW] || {}))
        : (CAP < 0 ? (DATA.tier_time || {}) : ((DATA.cap_tier_time || {})[capMeta().key] || {}));
    } else if (isCur && DATA.tier_cost_rebased) {
      tierCost = CAP < 0 ? DATA.tier_cost_rebased : ((DATA.cap_tier_cost_rebased || {})[capMeta().key] || tierCost);
    }
    if (isArch && !timeAxis) {
      var clipped = {};
      tiers.forEach(function (t) {
        clipped[t] = (tierCost[t] || []).filter(function (r) { return eraOfDate(r[0]) === ERA_VIEW; });
      });
      tierCost = clipped;
    }
    var endDate = isArch ? DATA.era_snapshots[ERA_VIEW][DATA.era_snapshots[ERA_VIEW].length - 1][0] : DATA.updated;
    var allRecs = []; tiers.forEach(function (t) { (tierCost[t] || []).forEach(function (r) { allRecs.push(r); }); });
    if (!allRecs.length) { box.append(el('p', 'pfc-lead', 'No records in this era.')); return; }
    // Fixed time origin so the axis reads the same on every tab; records set
    // before it enter from the left edge at the value in effect on that date.
    var X0DATE = '2025-08-01';
    var x0d = new Date(X0DATE + 'T00:00:00Z');
    var x1d = new Date(endDate + 'T00:00:00Z');
    var x0 = x0d.getTime(), x1 = x1d.getTime();
    // An archived era is frozen: pad the axis past its end and cap the lines
    // there, so the series visibly stops instead of running to the edge.
    if (isArch) x1 = x1 + (x1 - x0) * 0.06;
    var maxRec = Math.max.apply(null, allRecs.map(function (r) { return r[1]; }));
    var minRec = Math.min.apply(null, allRecs.map(function (r) { return r[1]; }));
    var yd = timeAxis
      ? [Math.pow(10, Math.floor(Math.log10(minRec))), Math.pow(10, Math.ceil(Math.log10(maxRec)))]
      : [0.005, Math.max(5, Math.pow(10, Math.ceil(Math.log10(maxRec))))];
    var ticks = []; var td = new Date(x0d.getTime());
    var monthsSpan = (x1d.getUTCFullYear() - x0d.getUTCFullYear()) * 12 + x1d.getUTCMonth() - x0d.getUTCMonth();
    // Tick spacing follows the pixels available, not just the span: a month
    // label needs about 52px, so narrow charts get fewer, wider-stepped ticks.
    var maxTicks = Math.max(2, Math.floor((W - M.l - M.r) / 52));
    var stepM = Math.ceil(monthsSpan / maxTicks);
    stepM = stepM <= 2 ? 2 : stepM <= 3 ? 3 : stepM <= 4 ? 4 : stepM <= 6 ? 6 : 12;
    var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    while (td.getTime() <= x1) { ticks.push([td.toISOString().slice(0, 10), MON[td.getUTCMonth()] + " '" + String(td.getUTCFullYear()).slice(2)]); td.setUTCMonth(td.getUTCMonth() + stepM); }
    function X(dstr) { return M.l + (Date.parse(dstr + 'T00:00:00Z') - x0) / (x1 - x0) * (W - M.l - M.r); }
    function Y(v) { return H - M.b - (Math.log10(v) - Math.log10(yd[0])) / (Math.log10(yd[1]) - Math.log10(yd[0])) * (H - M.t - M.b); }

    ticks.forEach(function (t) {
      svg.append(svgEl('line', { x1: X(t[0]), x2: X(t[0]), y1: M.t, y2: H - M.b, stroke: C.grid, 'stroke-width': 1 }));
      var lb = svgEl('text', { x: X(t[0]), y: H - M.b + 18, 'text-anchor': 'middle', 'font-size': 10.5, fill: C.muted, 'font-family': C.mono });
      lb.textContent = t[1]; svg.append(lb);
    });
    var vs = [];
    if (timeAxis) [1, 10, 60, 600, 3600, 36000].forEach(function (v) { if (v >= yd[0] && v <= yd[1]) vs.push(v); });
    else for (var v0 = 0.01; v0 <= yd[1] / 2; v0 *= 10) vs.push(v0);
    vs.forEach(function (v) {
      svg.append(svgEl('line', { x1: M.l, x2: W - M.r, y1: Y(v), y2: Y(v), stroke: C.grid, 'stroke-width': 1 }));
      var lb = svgEl('text', { x: M.l - 8, y: Y(v) + 4, 'text-anchor': 'end', 'font-size': 10.5, fill: C.muted, 'font-family': C.mono });
      lb.textContent = timeAxis ? (v >= 3600 ? (v / 3600) + 'h' : v >= 60 ? (v / 60) + 'm' : v + 's') : '$' + (v >= 1 ? v.toFixed(0) : v.toFixed(2));
      svg.append(lb);
    });
    svg.append(svgEl('line', { x1: M.l, x2: W - M.r, y1: H - M.b, y2: H - M.b, stroke: C.axis, 'stroke-width': 1 }));
    svg.append(svgEl('line', { x1: M.l, x2: M.l, y1: M.t, y2: H - M.b, stroke: C.axis, 'stroke-width': 1 }));

    var pts = [];
    var endLabels = [];
    // Every view of this chart is on a single index basis, named at the top:
    // the rebased current basis, or the archived era's actual one.
    var endX = isArch ? X(endDate) : W - M.r;
    if (isArch) {
      svg.append(svgEl('line', { x1: endX, x2: endX, y1: M.t, y2: H - M.b, stroke: C.ink2, 'stroke-width': 1, 'stroke-dasharray': '4 3' }));
      var endLb = svgEl('text', { x: endX - 5, y: M.t + 24, 'text-anchor': 'end', 'font-size': 10, fill: C.ink2 });
      endLb.textContent = 'measurements ended ' + fmtDate(endDate); svg.append(endLb);
    }
    if (ERAS.length) {
      var lblv = eraShortLabel(ERA_VIEW);
      if (lblv) {
        var lb = svgEl('text', { x: (M.l + W - M.r) / 2, y: M.t + 11, 'text-anchor': 'middle', 'font-size': 10, fill: C.ink2 });
        lb.textContent = timeAxis ? 'time per task \u00b7 index ' + lblv : 'index ' + lblv + (isCur ? ' basis' : '');
        svg.append(lb);
      }
    }
    var recCreators = [];
    tiers.forEach(function (tier, i) {
      var all = tierCost[tier];
      if (!all || !all.length) return;
      var carry = null;
      var recs = all.filter(function (r) { if (r[0] < X0DATE) { carry = r; return false; } return true; });
      if (!carry && !recs.length) return;
      var color = C.ord[i];
      var d = '';
      if (carry) {
        d = 'M ' + M.l + ' ' + Y(carry[1]) + ' H ' + (recs.length ? X(recs[0][0]) : endX);
      }
      recs.forEach(function (r, j) {
        var x = X(r[0]), y = Y(r[1]);
        d += (d ? ' V ' + y : ' M ' + x + ' ' + y);
        var next = j < recs.length - 1 ? recs[j + 1] : null;
        d += ' H ' + (next ? X(next[0]) : endX);
      });
      svg.append(svgEl('path', { d: d, fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
      recs.forEach(function (r) {
        var x = X(r[0]), y = Y(r[1]);
        var rm = modelsByName[r[2]];
        if (rm) recCreators.push(rm.creator);
        dot(svg, x, y, 4, rm ? providerColor(rm.creator) : color, !!openByName[r[2]]);
        if (selMatchesName(r[2])) svg.append(svgEl('circle', { cx: x, cy: y, r: 9.5, fill: 'none', stroke: C.ink, 'stroke-width': 1.5, 'class': 'pfc-sel-ring' }));
        pts.push({ x: x, y: y, key: r[2], rows: function () {
          var d1 = el('div', 'pfc-tt-name'); d1.textContent = r[2];
          var d2 = el('div', 'pfc-tt-row');
          var kd = el('span', 'pfc-tt-key'); kd.style.borderTopColor = color;
          var est = timeAxis ? (!isArch && !timeMeasuredBy(modelsByName[r[2]], r[0], ERAS.length) && r[0] < DATA.updated)
                             : isCur && ERAS.length && r[0] < ERAS[ERAS.length - 1][0];
          var s = el('span', 'pfc-tt-val'); s.textContent = (est ? '≈' : '') + (timeAxis ? fmtTime(r[1]) : fmt$(r[1]));
          d2.append(kd, s, ' new record, ' + (CAP < 0 ? 'Index' : metricName()) + ' ' + tierLabel(tier) + (est ? (timeAxis ? ' · at its nearest measured speed' : ' · estimated from price history') : ''));
          var d3 = el('div', null, (r[4] ? r[4] + ' \u00b7 ' : 'released ' + r[0] + ' \u00b7 ') + (CAP < 0 ? 'Index ' + r[3].toFixed(1) : metricName() + ' ' + fmtScore(r[3])) + (rm ? ' \u00b7 ' + rm.creator : '') + (openByName[r[2]] ? ' \u00b7 open weights' : ' \u00b7 proprietary') + (retiredByName[r[2]] ? ' \u00b7 retired' : ''));
          return [d1, d2, d3];
        }});
      });
      var endY = Y((recs.length ? recs[recs.length - 1] : carry)[1]);
      if (!endLabels.some(function (yy) { return Math.abs(yy - endY) < 14; })) {
        var lb = svgEl('text', { x: endX + 6, y: endY + 4, 'font-size': 10.5, 'font-weight': 500, fill: C.ink2 });
        lb.textContent = tierLabel(tier); svg.append(lb);
        endLabels.push(endY);
      }
    });
    box.append(svg);
    attachHover(box, svg, pts);

    var legend = document.getElementById('pfc-records-legend');
    if (legend) {
      legend.replaceChildren();
      legend.append(el('span', 'pfc-legend-title', CAP < 0 ? 'Intelligence Index' : metricName()));
      tiers.slice().reverse().forEach(function (tier, ri) {
        var i = tiers.length - 1 - ri;
        var item = el('span', 'pfc-lk');
        var sw = el('span', 'pfc-swatch');
        sw.style.borderTopColor = C.ord[i];
        item.append(sw, el('span', null, tierLabel(tier)));
        legend.append(item);
      });
      appendProviderLegend(legend, recCreators);
    }
  }

  function fmtDate(d) { var p = d.split('-'); var MON = ['January','February','March','April','May','June','July','August','September','October','November','December']; return MON[+p[1] - 1] + ' ' + (+p[2]) + ', ' + p[0]; }
  function renderTable() {
    var tb = document.getElementById('pfc-tier-table');
    if (!tb) return;
    tb.replaceChildren();
    function cell(html, cls) { var td = document.createElement('td'); if (cls) td.className = cls; td.append(html); return td; }
    function event(date, model, cost) {
      var w = el('div', 'pfc-ev');
      var a = el('div', 'pfc-ev-top'); var c = el('span', 'pfc-ev-cost'); c.textContent = fmtVal(cost); a.append(c, ' \u00b7 ' + fmtDate(date));
      var b = el('div', 'pfc-ev-model'); b.append(nameLink(model));
      w.append(a, b); return w;
    }
    var isCur = ERAS.length > 0 && ERA_VIEW === ERAS.length;
    var isArch = ERAS.length > 0 && ERA_VIEW < ERAS.length;
    var timeAxis = isTimeAxis() && (!isArch || eraHasTime(ERA_VIEW));
    // An archived era's summaries are computed from its actual records.
    function summarize(byTier, clipToEra) {
      var out = {};
      curTiers().forEach(function (t) {
        var recs = byTier[t] || [];
        if (clipToEra) recs = recs.filter(function (r) { return eraOfDate(r[0]) === ERA_VIEW; });
        if (!recs.length) { out[t] = null; return; }
        var f = recs[0], l = recs[recs.length - 1];
        var days = (Date.parse(l[0]) - Date.parse(f[0])) / 86400000;
        var ratio = f[1] / l[1];
        out[t] = { first_date: f[0], first_model: f[2], first_cost: f[1],
                   last_date: l[0], last_model: l[2], last_cost: l[1],
                   collapse: Math.round(ratio * 10) / 10,
                   halving_days: ratio > 1 && days ? Math.round(days / (Math.log(ratio) / Math.LN2)) : null };
      });
      return out;
    }
    var summary = curTierSummary();
    if (timeAxis && isArch) {
      summary = summarize(CAP < 0 ? ((DATA.era_tier_time || [])[ERA_VIEW] || {}) : (((DATA.era_cap_tier_time || {})[capMeta().key] || [])[ERA_VIEW] || {}), false);
    } else if (timeAxis) {
      summary = CAP < 0 ? (DATA.tier_time_summary || {}) : ((DATA.cap_tier_time_summary || {})[capMeta().key] || {});
    } else if (isArch) {
      summary = summarize(curTierCost(), true);
    } else if (isCur && DATA.tier_summary_rebased) {
      summary = CAP < 0 ? DATA.tier_summary_rebased : ((DATA.cap_tier_summary_rebased || {})[capMeta().key] || summary);
    }
    curTiers().slice().reverse().forEach(function (t) {
      var s = summary[t];
      var tr = document.createElement('tr');
      tr.append(cell(tierLabel(t), 'pfc-td-tier'));
      if (s) {
        tr.append(cell(event(s.first_date, s.first_model, s.first_cost)));
        tr.append(cell(event(s.last_date, s.last_model, s.last_cost)));
        tr.append(cell(s.collapse + 'x', 'pfc-td-num'));
        tr.append(cell(s.halving_days ? '~' + s.halving_days + ' days' : '', 'pfc-td-num'));
      } else {
        var td = cell('not yet reached'); td.colSpan = 4; tr.append(td);
      }
      tb.append(tr);
    });
    document.querySelectorAll('.pfc-updated').forEach(function (e) { e.textContent = fmtDate(DATA.updated); });
    document.querySelectorAll('.pfc-count').forEach(function (e) { e.textContent = String(DATA.counts.total); });
  }
  var ADV_DAYS_PER_PAGE = 6;
  var advPage = 1;
  function joinAnd(items) {
    if (items.length <= 1) return items.join('');
    if (items.length === 2) return items[0] + ' and ' + items[1];
    return items.slice(0, -1).join(', ') + ', and ' + items[items.length - 1];
  }
  function takenClause(taken, departed) {
    var out = '';
    if (taken.length) {
      out += ', taking it from ' + joinAnd(taken);
      var gone = taken.filter(function (t) { return departed.indexOf(t) >= 0; });
      if (gone.length && gone.length === taken.length) out += taken.length === 1 ? ', which left the frontier' : taken.length === 2 ? ', both of which left the frontier' : ', all of which left the frontier';
      else if (gone.length) out += '; ' + joinAnd(gone) + ' left the frontier';
    }
    var extra = departed.filter(function (d) { return taken.indexOf(d) < 0; });
    if (extra.length) out += '; ' + joinAnd(extra) + ' left the frontier';
    return out;
  }
  function advanceLine(a, withVariant) {
    var text = el('div', 'pfc-adv-body');
    if (withVariant && a.variant) { var v = el('span', 'pfc-adv-variant'); v.textContent = a.variant + ': '; text.append(v); }
    var timeAxis = isTimeAxis();
    var fmtA = timeAxis ? fmtTime : fmt$;
    var superl = timeAxis ? 'fastest' : 'cheapest';
    var term = CAP < 0 ? 'index' : metricName();
    var span = a.owns_to.toFixed(1) === a.owns_from.toFixed(1) ? term + ' ' + fmtScore(a.owns_to) : term + ' ' + fmtScore(a.owns_from) + ' to ' + fmtScore(a.owns_to);
    var ceiling = CAP < 0 ? 'the intelligence ceiling' : 'the ' + metricName() + ' ceiling';
    var s1 = a.kind === 'price change' && a.previous_cost
      ? (timeAxis ? 'speed' : 'price') + ' moved from ' + fmtA(a.previous_cost) + ' to ' + fmtA(a.cost_per_task) + ' per task; now the ' + superl + ' way to reach ' + span
      : (a.ceiling_from !== null && a.ceiling_from !== undefined)
        ? 'pushed ' + ceiling + ' from ' + fmtScore(a.ceiling_from) + ' to ' + fmtScore(a.owns_to) + ', at ' + fmtA(a.cost_per_task) + ' per task'
        : 'now the ' + superl + ' way to reach ' + span + ' at ' + fmtA(a.cost_per_task) + ' per task';
    s1 += takenClause(a.taken_from || [], a.displaced || []) + '. ';
    if (!(withVariant && a.variant)) s1 = s1.charAt(0).toUpperCase() + s1.slice(1);
    text.append(s1);
    if (a.records && a.records.length) { var r = el('span', 'pfc-adv-rec'); r.textContent = 'New ' + (timeAxis ? 'speed' : 'cost') + ' record for ' + joinAnd(a.records.map(function (t) { return (CAP < 0 ? 'index' : metricName()) + ' ' + tierLabel(t); })) + '. '; text.append(r); }
    return text;
  }
  function slugify(name) {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  }
  function advanceGroup(list) {
    // list: advances for one base model on one day, highest index first
    var item = el('div', 'pfc-adv-item');
    var single = list.length === 1 && !list[0].variant;
    var head = el('div', 'pfc-adv-head'); head.append(nameLink(single ? list[0].model : list[0].base, null, list[0].model));
    var kinds = []; list.forEach(function (a) { if (kinds.indexOf(a.kind) < 0) kinds.push(a.kind); });
    kinds.forEach(function (k) { head.append(el('span', 'pfc-adv-kind', isTimeAxis() && k === 'price change' ? 'speed change' : k)); });
    if (list[0].open_weights) head.append(el('span', 'pfc-adv-kind pfc-adv-open', 'open weights'));
    // The pipeline renders one shareable card image per base model per day,
    // named by the same date and slug this derives; capability advances get
    // their cards under a per-metric subdirectory. Cards exist for the cost
    // view only.
    if (!isTimeAxis()) {
      var card = document.createElement('a');
      card.className = 'pfc-adv-kind pfc-adv-card';
      card.textContent = 'chart card';
      card.href = SITE_ROOT + 'images/advances/' + (CAP < 0 ? '' : capMeta().key + '/') + list[0].date + '-' + slugify(list[0].base || list[0].model) + '.png';
      card.target = '_blank';
      card.rel = 'noopener';
      head.append(card);
    }
    item.append(head);
    list.forEach(function (a) { item.append(advanceLine(a, !single)); });
    return item;
  }
  var advLeadDefault = null;
  function renderAdvances() {
    var box = document.getElementById('pfc-advances');
    if (!box || !DATA.advances) return;
    var timeAxis = isTimeAxis();
    var isArch = ERAS.length > 0 && ERA_VIEW < ERAS.length;
    var lead = document.getElementById('pfc-adv-lead');
    if (lead) {
      if (advLeadDefault === null) advLeadDefault = lead.innerHTML;
      if (timeAxis) lead.textContent = 'Each entry is a date on which a model became the fastest way to reach some level of ' + (CAP < 0 ? 'the Intelligence Index' : metricName()) + '. ' + (isArch ? 'Archive entries use only the speeds measured in that era.' : 'Speeds are the latest measured values, so dates before a model\u2019s first speed measurement are approximate.') + ' The Atom feed covers cost advances on the Overall view only.';
      else if (CAP < 0) lead.innerHTML = advLeadDefault;
      else lead.textContent = 'Each entry is a date on which a model became the cheapest way to reach some level of ' + metricName() + ', through a release or a price change, derived from release dates, observed prices, and the latest measured scores. The Atom feed covers the Overall view only.';
    }
    var list;
    if (timeAxis) {
      list = isArch
        ? (CAP < 0 ? ((DATA.era_time_advances || [])[ERA_VIEW] || []) : (((DATA.era_cap_time_advances || {})[capMeta().key] || [])[ERA_VIEW] || []))
        : (CAP < 0 ? (DATA.time_advances || []) : ((DATA.cap_time_advances || {})[capMeta().key] || []));
    } else {
      list = CAP < 0 ? DATA.advances : ((DATA.cap_advances || {})[capMeta().key] || []);
    }
    var days = [], byDay = {};
    list.forEach(function (a) {
      if (!byDay[a.date]) { byDay[a.date] = []; days.push(a.date); }
      byDay[a.date].push(a);
    });
    var total = Math.max(1, Math.ceil(days.length / ADV_DAYS_PER_PAGE));
    advPage = Math.min(Math.max(1, advPage), total);
    box.replaceChildren();
    if (!days.length) box.append(el('p', 'pfc-lead', 'No advances recorded for this metric.'));
    days.slice((advPage - 1) * ADV_DAYS_PER_PAGE, advPage * ADV_DAYS_PER_PAGE).forEach(function (d) {
      var row = el('div', 'pfc-adv-day');
      var col = el('div');
      var groups = [], byBase = {};
      byDay[d].forEach(function (a) {
        var key = a.base || a.model;
        if (!byBase[key]) { byBase[key] = []; groups.push(key); }
        byBase[key].push(a);
      });
      groups.forEach(function (k) { col.append(advanceGroup(byBase[k])); });
      row.append(el('div', 'pfc-adv-date', fmtDate(d)), col);
      box.append(row);
    });
    var pager = document.getElementById('pfc-adv-pager'), prev = document.getElementById('pfc-adv-prev'), next = document.getElementById('pfc-adv-next'), lab = document.getElementById('pfc-adv-page');
    if (pager) {
      pager.hidden = total <= 1;
      prev.disabled = advPage === 1; next.disabled = advPage === total;
      lab.textContent = 'Page ' + advPage + ' of ' + total;
      if (!pager.dataset.wired) {
        pager.dataset.wired = '1';
        prev.addEventListener('click', function () { advPage -= 1; renderAdvances(); document.getElementById('advances').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
        next.addEventListener('click', function () { advPage += 1; renderAdvances(); document.getElementById('advances').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
      }
    }
  }
  function renderAll() { if (!DATA) return; renderTabs(); renderEraNav(); renderAxisNav(); renderSearch(); renderLead(); renderFrontier(); renderCapTable(); renderRecords(); renderTable(); renderAdvances(); }
  // If the data file has not been redeployed in well over an update cycle,
  // the pipeline is stuck; say so instead of quietly serving stale numbers.
  // Updates run every three hours, but GitHub can delay or drop scheduled
  // runs for many hours, so the threshold leaves room for that; an outright
  // failure opens a pipeline-failure issue right away.
  var STALE_HOURS = 15;
  function renderStale(lastModified) {
    var box = document.getElementById('pfc-stale');
    if (!box || !lastModified) return;
    var age = (Date.now() - Date.parse(lastModified)) / 3600000;
    if (!(age > STALE_HOURS)) return;
    box.hidden = false;
    box.textContent = 'The last data update was ' + (age > 48 ? Math.round(age / 24) + ' days' : Math.round(age) + ' hours') +
      ' ago; updates normally land every three hours, so the pipeline may be stuck. The numbers below are the last good measurements.';
  }
  fetch(DATA_URL, { cache: 'no-cache' }).then(function (r) {
    renderStale(r.headers.get('last-modified'));
    return r.json();
  }).then(function (d) {
    loadData(d);
    if (PAGE_CAP) for (var i = 0; i < CAPS.length; i++) if (CAPS[i].key === PAGE_CAP) CAP = i;
    applyHash();
    function start() { addExpandButtons(); renderAll(); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
  }).catch(function (err) {
    var box = document.getElementById('pfc-frontier');
    if (box) box.textContent = 'Could not load the frontier data (' + err + ').';
  });
  var rt = null;
  window.addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(renderAll, 150); });
  // Hash-only navigation (a shared link pasted over an open page, or the
  // back button) re-applies the view state; syncHash uses replaceState, so
  // the app's own updates never land here.
  window.addEventListener('hashchange', function () {
    if (!DATA) return;
    if ((location.hash || '') === hashFor()) return;
    CAP = -1; ERA_VIEW = ERAS.length; AXIS = 'cost'; SEL = null; SEL_KIND = 'model';
    SLIDE = null; SLIDE_LO = null;
    applyHash();
    advPage = 1;
    hideTip();
    renderAll();
  });
})();
