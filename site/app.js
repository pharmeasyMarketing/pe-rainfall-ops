/* Rainfall Ops dashboard — renders from window.__RAINOPS__ (data.js).
   Payload is compact: day-arrays live once per CELL; pincodes reference cells.
   pins: [pincode, area, state, lat, lon, cell_id, priority]
   cells: {cell_id: [[mm,p10,p90,p30,p60,band] x7]}   band: 0 NONE / 1 WATCH / 2 ACT
   hubs: [city, cell_id, lat, lon]
   History strips are sharded (site/hist/<XX>.json) and fetched on drawer open. */
(function () {
  "use strict";
  var D = window.__RAINOPS__;
  if (!D) { document.body.innerHTML = "<p style='padding:24px'>No data.js found — run scripts/build_site.py.</p>"; return; }

  var LEAD_LABEL = ["Today", "Tomorrow", "In 2 days", "In 3 days", "In 4 days", "In 5 days", "In 6 days"];
  var BAND_NAME = ["NONE", "WATCH", "ACT"];
  var BAND_CLS = ["none", "watch", "act"];
  var RENDER_CAP = 400;

  var pins = D.pins.map(function (a) {
    return { pin: a[0], area: a[1], state: a[2], lat: a[3], lon: a[4], cell: a[5], pri: !!a[6] };
  });
  var byPin = {}; pins.forEach(function (p) { byPin[p.pin] = p; });
  var cellPins = {};
  pins.forEach(function (p) { (cellPins[p.cell] = cellPins[p.cell] || []).push(p.pin); });

  // worst band over D0-D2 per cell
  var cellWorst = {};
  Object.keys(D.cells).forEach(function (cid) {
    var days = D.cells[cid], best = { b: 0, p30: -1, lead: 0 };
    for (var L = 0; L <= 2 && L < days.length; L++) {
      var d = days[L]; if (!d) continue;
      if (d[5] > best.b || (d[5] === best.b && d[3] > best.p30)) best = { b: d[5], p30: d[3], lead: L };
    }
    cellWorst[cid] = best;
  });
  function worstLabel(w) {
    if (w.b === 0) return "No heavy-rain signal (72h)";
    return (w.b === 2 ? "Heavy rain likely" : "Watch: rain likely") + " · " + LEAD_LABEL[w.lead].toLowerCase();
  }

  var state = { filter: null, search: "", sortKey: "worst", sortDir: -1, selected: null, cellFilter: null };
  var histCache = {};

  // ---- geo projection ------------------------------------------------------
  var LON0 = 68, LON1 = 98, LAT0 = 6, LAT1 = 38, PAD = 18, W = 483, H = 560;
  function px(lon) { return PAD + (lon - LON0) / (LON1 - LON0) * (W - 2 * PAD); }
  function py(lat) { return PAD + (LAT1 - lat) / (LAT1 - LAT0) * (H - 2 * PAD); }

  var $ = function (s) { return document.querySelector(s); };
  function el(tag, attrs, txt) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (txt != null) e.textContent = txt;
    return e;
  }
  function fmtMM(x) { return x >= 10 ? Math.round(x) : (Math.round(x * 10) / 10); }
  function pct(x) { return Math.round(x * 100) + "%"; }
  function pill(bcode, extraCls) {
    return el("span", { class: "pill " + (extraCls || BAND_CLS[bcode]) },
      extraCls === "pri" ? "PRIORITY" : (bcode === 0 ? "—" : BAND_NAME[bcode]));
  }

  // ---- header ---------------------------------------------------------------
  $("#stamp").textContent = D.generated_ist;
  $("#meta").textContent = D.meta.n_pincodes.toLocaleString() + " courier pincodes · " +
    D.meta.n_cells.toLocaleString() + " cells · archive since " + D.archive_start;
  (function () {
    var btn = $("#dlBtn");
    if (btn) {
      btn.href = "downloads/latest.csv?v=" + Date.now();          // always fresh
      btn.setAttribute("download", "pe_courier_rainfall_" + (D.meta.latest_run || "today") + ".csv");
    }
  })();

  // ---- summary cards ---------------------------------------------------------
  var hubsAtRisk = 0;
  D.hubs.forEach(function (h) { if ((cellWorst[h[1]] || { b: 0 }).b >= 1) hubsAtRisk++; });
  (function () {
    var act = 0, watch = 0;
    pins.forEach(function (p) {
      var b = (cellWorst[p.cell] || { b: 0 }).b;
      if (b === 2) act++; else if (b === 1) watch++;
    });
    var cards = [
      { k: "Pincodes on ACT (72h)", v: act.toLocaleString(), cls: "act", sub: "heavy-rain action" },
      { k: "On WATCH (72h)", v: watch.toLocaleString(), cls: "watch", sub: "pre-emptive watch" },
      { k: "Hubs at risk", v: hubsAtRisk + "/" + D.hubs.length, cls: hubsAtRisk ? "act" : "", sub: "origin network" },
      { k: "Refreshed", v: (D.generated_ist.split(" ")[1] || "05:00"), cls: "", sub: D.generated_ist.split(" ")[0] }
    ];
    var wrap = $("#cards");
    cards.forEach(function (c) {
      var d = el("div", { class: "card " + c.cls });
      d.appendChild(el("div", { class: "k" }, c.k));
      var v = el("div", { class: "v" }); v.textContent = c.v; v.appendChild(el("small", null, " " + c.sub));
      d.appendChild(v); wrap.appendChild(d);
    });
  })();

  // ---- hub strip --------------------------------------------------------------
  (function () {
    var host = $("#hubs");
    host.appendChild(el("span", { class: "hlabel" }, "Origin hubs"));
    D.hubs.slice().sort(function (a, b) {
      return (cellWorst[b[1]] || { b: 0 }).b - (cellWorst[a[1]] || { b: 0 }).b || (a[0] < b[0] ? -1 : 1);
    }).forEach(function (h) {
      var w = cellWorst[h[1]] || { b: 0 };
      var chip = el("span", { class: "hub " + BAND_CLS[w.b], title: h[0] + " hub — " + worstLabel(w) });
      chip.appendChild(el("span", { class: "dot2" }));
      chip.appendChild(document.createTextNode(h[0]));
      if (w.b > 0) chip.appendChild(el("small", null, "· " + BAND_NAME[w.b]));
      host.appendChild(chip);
    });
  })();

  // ---- map ---------------------------------------------------------------------
  var NS = "http://www.w3.org/2000/svg";
  function buildMap() {
    var svg = $("#map"), parts = "";
    parts += "<rect x='0' y='0' width='" + W + "' height='" + H + "' fill='#f7fafb' rx='10'/>";
    for (var lon = 70; lon <= 95; lon += 5) parts += "<line class='grat' x1='" + px(lon).toFixed(1) + "' y1='" + PAD + "' x2='" + px(lon).toFixed(1) + "' y2='" + (H - PAD) + "'/><text class='glabel' x='" + (px(lon) + 2).toFixed(1) + "' y='" + (H - PAD - 3) + "'>" + lon + "°E</text>";
    for (var lat = 10; lat <= 35; lat += 5) parts += "<line class='grat' x1='" + PAD + "' y1='" + py(lat).toFixed(1) + "' x2='" + (W - PAD) + "' y2='" + py(lat).toFixed(1) + "'/><text class='glabel' x='" + (PAD + 2) + "' y='" + (py(lat) - 3).toFixed(1) + "'>" + lat + "°N</text>";
    svg.innerHTML = parts;

    var frag = document.createDocumentFragment();
    Object.keys(D.cells).map(function (cid) {
      return { cid: cid, w: cellWorst[cid] || { b: 0 } };
    }).sort(function (a, b) { return a.w.b - b.w.b; }).forEach(function (c) {
      var ll = c.cid.split("_");
      var dot = document.createElementNS(NS, "circle");
      dot.setAttribute("cx", px(+ll[1]).toFixed(1));
      dot.setAttribute("cy", py(+ll[0]).toFixed(1));
      dot.setAttribute("r", c.w.b === 2 ? 3.2 : c.w.b === 1 ? 2.8 : 2.0);
      dot.setAttribute("class", "dot " + BAND_CLS[c.w.b]);
      dot.setAttribute("data-cell", c.cid);
      var n = (cellPins[c.cid] || []).length;
      var t = document.createElementNS(NS, "title");
      t.textContent = "Cell " + c.cid + " — " + worstLabel(c.w) + " · " + n + " pincode" + (n === 1 ? "" : "s");
      dot.appendChild(t);
      frag.appendChild(dot);
    });
    // hubs on top (diamonds)
    D.hubs.forEach(function (h) {
      var w = cellWorst[h[1]] || { b: 0 };
      var r = document.createElementNS(NS, "rect");
      var x = px(h[3]), y = py(h[2]), s = 4.6;
      r.setAttribute("x", (x - s / 2).toFixed(1)); r.setAttribute("y", (y - s / 2).toFixed(1));
      r.setAttribute("width", s); r.setAttribute("height", s);
      r.setAttribute("transform", "rotate(45 " + x.toFixed(1) + " " + y.toFixed(1) + ")");
      r.setAttribute("class", "hubmark" + (w.b ? " " + BAND_CLS[w.b] : ""));
      var t = document.createElementNS(NS, "title");
      t.textContent = h[0] + " hub — " + worstLabel(w);
      r.appendChild(t);
      frag.appendChild(r);
    });
    svg.appendChild(frag);

    svg.addEventListener("click", function (e) {
      var dot = e.target.closest ? e.target.closest(".dot") : null;
      if (!dot) return;
      setCellFilter(dot.getAttribute("data-cell"));
    });
  }
  function refreshMap() {
    var dots = document.querySelectorAll("#map .dot");
    for (var i = 0; i < dots.length; i++) {
      var c = dots[i], cid = c.getAttribute("data-cell");
      c.classList.toggle("dim", !!state.cellFilter && cid !== state.cellFilter);
      c.classList.toggle("sel", state.cellFilter === cid ||
        (!!state.selected && byPin[state.selected] && byPin[state.selected].cell === cid));
    }
  }

  // ---- filtering -----------------------------------------------------------------
  function matches(p) {
    if (state.cellFilter && p.cell !== state.cellFilter) return false;
    var b = (cellWorst[p.cell] || { b: 0 }).b;
    if (state.filter === "PRI" && !p.pri) return false;
    if (state.filter === "WATCH" && b < 1) return false;
    if (state.filter === "ACT" && b < 2) return false;
    if (state.search) {
      var s = state.search.toLowerCase();
      if (p.pin.indexOf(s) < 0 && p.area.toLowerCase().indexOf(s) < 0 &&
          p.state.toLowerCase().indexOf(s) < 0) return false;
    }
    return true;
  }
  function setCellFilter(cid) {
    state.cellFilter = cid;
    var chip = $("#cellchip");
    if (cid) {
      var n = (cellPins[cid] || []).length;
      $("#cellchipLabel").textContent = "cell " + cid + " · " + n + " pincode" + (n === 1 ? "" : "s");
      chip.classList.add("show");
    } else chip.classList.remove("show");
    renderTable(); refreshMap();
  }

  // ---- table -----------------------------------------------------------------------
  function dayOf(p, L) { var ds = D.cells[p.cell]; return ds ? ds[L] : null; }
  function sortVal(p, key) {
    if (key === "pincode") return p.pin;
    if (key === "area") return p.area || "￿";
    if (key === "state") return p.state || "￿";
    if (key[0] === "d") { var d = dayOf(p, +key.slice(1)); return d ? d[0] : -1; }
    var w = cellWorst[p.cell] || { b: 0, p30: 0 };
    var d1 = dayOf(p, 1);
    return w.b * 1000 + (d1 ? d1[3] * 10 : 0);
  }
  function renderTable() {
    var rows = pins.filter(matches).sort(function (a, b) {
      var va = sortVal(a, state.sortKey), vb = sortVal(b, state.sortKey);
      if (va < vb) return -state.sortDir; if (va > vb) return state.sortDir; return 0;
    });
    var shown = rows.slice(0, RENDER_CAP);
    var tb = $("#tbody"); tb.innerHTML = "";
    var frag = document.createDocumentFragment();
    shown.forEach(function (p) {
      var tr = el("tr"); tr.setAttribute("data-pin", p.pin);
      if (state.selected === p.pin) tr.className = "sel";
      var tdPin = el("td"); tdPin.appendChild(el("span", { class: "pin" }, p.pin));
      if (p.pri && state.filter !== "PRI") { tdPin.appendChild(document.createTextNode(" ")); tdPin.appendChild(pill(0, "pri")); }
      tr.appendChild(tdPin);
      tr.appendChild(el("td", { class: "muted" }, p.area || "—"));
      tr.appendChild(el("td", { class: "muted" }, p.state));
      for (var L = 0; L <= 3; L++) tr.appendChild(dayCell(dayOf(p, L)));
      var td = el("td"); td.appendChild(pill((cellWorst[p.cell] || { b: 0 }).b)); tr.appendChild(td);
      frag.appendChild(tr);
    });
    tb.appendChild(frag);
    var note = "Showing " + shown.length.toLocaleString() + " of " + rows.length.toLocaleString() +
      " matching pincodes (" + D.meta.n_pincodes.toLocaleString() + " total)";
    if (rows.length > RENDER_CAP) note += " — refine the search or filters to narrow down";
    $("#tfoot").textContent = note;
    if (!rows.length) {
      var msg = state.search
        ? "No match for “" + state.search + "”. This dashboard covers the "
          + D.meta.n_pincodes.toLocaleString() + " courier pincodes only — metros served by hyperlocal/express aren't included."
        : "No pincodes match.";
      var tr0 = el("tr"); tr0.appendChild(el("td", { colspan: 8, class: "muted", style: "padding:18px" }, msg));
      tb.appendChild(tr0);
    }
  }
  function dayCell(d) {
    var td = el("td", { class: "num" });
    if (!d) { td.textContent = "—"; return td; }
    var box = el("span", { class: "cell" + (d[5] > 0 ? " wet" : "") });
    box.appendChild(el("span", { class: "mm" }, fmtMM(d[0]) + " mm"));
    box.appendChild(el("span", { class: "pp" }, "P30 " + pct(d[3])));
    td.appendChild(box); return td;
  }

  // ---- trust panel ---------------------------------------------------------------
  function renderTrust() {
    var v = D.verification && D.verification.by_lead;
    var host = $("#trust");
    if (!v || !v.length) { host.innerHTML = "<p class='muted'>No verification data yet.</p>"; return; }
    var d1 = v.find(function (x) { return x.lead === 1; }) || v[0];
    var d3 = v.find(function (x) { return x.lead === 3; });
    var d6 = v.find(function (x) { return x.lead === 6; });
    var base = d1.base_rate ? pct(d1.base_rate) : "~5%";
    var lead = el("p", { class: "lead" });
    lead.innerHTML = "Bands are <b>calibrated against real rainfall</b>. For <b>tomorrow (D1)</b>: an " +
      "<b>ACT</b> call verifies <b>" + pct(d1.act_reliability || 0) + "</b> of the time (≥" + D.event_mm +
      " mm actually falls), a <b>WATCH+</b> call <b>" + pct(d1.watch_reliability) + "</b> — both well above the " +
      "<b>" + base + "</b> base rate. ACT is the precise trigger for proactive action; WATCH the wide cheap net. " +
      "Skill fades with lead, so <b>D0–D2 are actionable</b>, D3+ directional.";
    host.appendChild(lead);
    var bars = el("div", { class: "bars" });
    v.forEach(function (x) {
      var b = el("div", { class: "bar " + (x.lead <= 2 ? "act-lead" : "dir-lead") });
      var track = el("div", { class: "track" });
      var fill = el("div", { class: "fill" });
      fill.style.height = Math.round((x.watch_reliability || 0) * 100) + "%";
      track.appendChild(fill); b.appendChild(track);
      b.appendChild(el("div", { class: "val" }, pct(x.watch_reliability || 0)));
      b.appendChild(el("div", { class: "lab" }, "D" + x.lead));
      bars.appendChild(b);
    });
    host.appendChild(bars);
    var foot = el("div", { class: "foot" });
    foot.appendChild(el("span", null, "Bars = WATCH+ reliability: P(≥" + D.event_mm + " mm fell | flagged), by lead day."));
    foot.appendChild(el("span", null, "D1 catches " + pct(d1.pod) + " of heavy-rain days · " +
      (D.verification.n_scored || 0).toLocaleString() + " pincode-days scored since " + D.archive_start + "."));
    host.appendChild(foot);
  }

  // ---- backbone scoreboard ------------------------------------------------
  function renderScoreboard() {
    var sb = D.scoreboard;
    if (!sb || !sb.providers || !sb.n_comparisons) return;
    var g = sb.providers.gefs.overall, e = sb.providers.ecmwf.overall;
    if (!g || !e) return;
    $("#scoreboardPanel").hidden = false;
    var host = $("#scoreboard");

    host.appendChild(el("p", { class: "sb-note" },
      "Both are free & commercially clean (GEFS US public-domain, ECMWF CC-BY-4.0). Scored cell-by-cell on IMERG truth over " +
      sb.n_days + " overlap day" + (sb.n_days === 1 ? "" : "s") + " (" + sb.n_comparisons.toLocaleString() +
      " comparisons) — ECMWF has no long archive, so this accumulates daily."));

    var cols = [
      { k: "mae", label: "MAE (mm)", better: "low", fmt: function (v) { return v.toFixed(1); } },
      { k: "bias", label: "Bias (mm)", better: "zero", fmt: function (v) { return (v > 0 ? "+" : "") + v.toFixed(1); } },
      { k: "corr", label: "Correlation", better: "high", fmt: function (v) { return v.toFixed(2); } },
      { k: "heavy_recall", label: "Heavy-rain recall", better: "high", fmt: function (v) { return v == null ? "—" : Math.round(v * 100) + "%"; } }
    ];
    function wins(c, a, b) {
      if (a == null || b == null) return 0;
      if (c.better === "low") return a < b ? -1 : a > b ? 1 : 0;
      if (c.better === "high") return a > b ? -1 : a < b ? 1 : 0;
      return Math.abs(a) < Math.abs(b) ? -1 : Math.abs(a) > Math.abs(b) ? 1 : 0;
    }
    var tbl = el("table", { class: "sb-table" });
    var thead = el("thead"), htr = el("tr");
    htr.appendChild(el("th", null, "Backbone"));
    cols.forEach(function (c) { htr.appendChild(el("th", null, c.label)); });
    thead.appendChild(htr); tbl.appendChild(thead);
    var tb = el("tbody");
    [["gefs", g, "NOAA GEFS", "ensemble median · free"], ["ecmwf", e, "ECMWF IFS", "HRES · free"]].forEach(function (row) {
      var key = row[0], m = row[1], tr = el("tr");
      var pc = el("td", { class: "prov" }); pc.innerHTML = row[2] + "<small>" + row[3] + "</small>"; tr.appendChild(pc);
      cols.forEach(function (c) {
        var w = wins(c, g[c.k], e[c.k]);
        var isWin = (key === "gefs" && w === -1) || (key === "ecmwf" && w === 1);
        tr.appendChild(el("td", isWin ? { class: "win" } : null, m[c.k] == null ? "—" : c.fmt(m[c.k])));
      });
      tb.appendChild(tr);
    });
    tbl.appendChild(tb); host.appendChild(tbl);

    var h = sb.head_to_head;
    if (h && h.gefs_win_pct != null) {
      var wrap = el("div", { class: "sb-h2h" });
      wrap.appendChild(el("span", null, "Closer to truth:"));
      var bar = el("div", { class: "sb-bar" });
      var gs = el("div", { class: "g" }); gs.style.width = h.gefs_win_pct + "%";
      var es = el("div", { class: "e" }); es.style.width = h.ecmwf_win_pct + "%";
      bar.appendChild(gs); bar.appendChild(es); wrap.appendChild(bar);
      wrap.appendChild(el("span", null, "GEFS " + h.gefs_win_pct + "% · ECMWF " + h.ecmwf_win_pct + "%"));
      host.appendChild(wrap);
    }
    host.appendChild(el("p", { class: "sb-note", style: "margin-top:12px" },
      "Read: GEFS's ensemble median is smoother (lower error); ECMWF deterministic catches more heavy days. " +
      "As the overlap grows this becomes the evidence base for whether to switch backbones or pay a vendor."));
  }

  // ---- detail drawer ---------------------------------------------------------------
  function select(pin) {
    state.selected = pin; openDrawer(byPin[pin]); refreshMap();
    var trs = document.querySelectorAll("#tbody tr");
    for (var i = 0; i < trs.length; i++) trs[i].classList.toggle("sel", trs[i].getAttribute("data-pin") === pin);
  }
  function openDrawer(p) {
    var w = cellWorst[p.cell] || { b: 0 };
    var shared = (cellPins[p.cell] || []).length;
    $("#dTitle").textContent = (p.area ? p.area + " · " : "") + p.pin;
    $("#dMeta").innerHTML = p.state + " &nbsp;·&nbsp; " + worstLabel(w) +
      " &nbsp;·&nbsp; cell " + p.cell + " (" + (shared === 1 ? "1 pincode shares" : shared + " pincodes share") + " this forecast)" +
      (p.pri ? " &nbsp;·&nbsp; <b style='color:#075985'>priority</b>" : "") +
      " &nbsp;·&nbsp; <span class='muted'>vuln score: phase 2</span>";
    var body = $("#dBody"); body.innerHTML = "";

    body.appendChild(el("div", { class: "sec-t" }, "7-day rainfall outlook (ensemble)"));
    var days = D.cells[p.cell] || [];
    var scale = 50;
    days.forEach(function (d) { if (d && d[2] > scale) scale = d[2]; });
    days.forEach(function (d, L) {
      if (!d) return;
      var row = el("div", { class: "fc-row" });
      var dd = el("div", { class: "d" });
      dd.innerHTML = "<b>" + LEAD_LABEL[L] + "</b><small>" + (L > 2 ? "directional" : "actionable") + "</small>";
      row.appendChild(dd);
      var track = el("div", { class: "fc-track" });
      var rng = el("div", { class: "rng" });
      rng.style.left = (d[1] / scale * 100) + "%";
      rng.style.width = Math.max(1, (d[2] - d[1]) / scale * 100) + "%";
      track.appendChild(rng);
      var med = el("div", { class: "med" });
      med.style.left = "calc(" + Math.min(100, d[0] / scale * 100) + "% - 1px)";
      track.appendChild(med);
      row.appendChild(track);
      var mv = el("div", { class: "mmv" });
      mv.innerHTML = fmtMM(d[0]) + " mm<small>P30 " + pct(d[3]) + " · P60 " + pct(d[4]) + "</small>";
      row.appendChild(mv);
      body.appendChild(row);
      if (d[5] > 0) {
        var pr = el("div", { style: "grid-column:1/-1;margin:-2px 0 2px" });
        pr.appendChild(pill(d[5]));
        body.appendChild(pr);
      }
    });
    body.appendChild(el("p", { class: "dir-note" },
      "Whiskers = ensemble p10–p90 spread; tick = median. All pincodes in cell " + p.cell +
      " share this forecast — pincode-level differences come from the phase-2 vulnerability layer."));

    body.appendChild(el("div", { class: "sec-t" }, "Track record — D1 forecast vs observed (last " + D.meta.history_days + " days)"));
    var histHost = el("div"); body.appendChild(histHost);
    loadHistory(p.pin, histHost);

    $("#drawer").classList.add("open"); $("#drawer").setAttribute("aria-hidden", "false");
    $("#drawerBg").classList.add("open");
  }

  function loadHistory(pin, host) {
    host.innerHTML = "<p class='muted' style='font-size:12px'>Loading history…</p>";
    if (location.protocol === "file:") {
      host.innerHTML = "<p class='muted' style='font-size:12px'>History needs an http server (python -m http.server) — file:// can't fetch shards.</p>";
      return;
    }
    var prefix = pin.slice(0, 2);
    (histCache[prefix] ? Promise.resolve(histCache[prefix])
      : fetch("hist/" + prefix + ".json").then(function (r) {
          if (!r.ok) throw new Error(r.status);
          return r.json();
        }).then(function (j) { histCache[prefix] = j; return j; })
    ).then(function (shard) {
      var rows = shard[pin] || [];
      host.innerHTML = "";
      host.appendChild(historySvg(rows));
      var hl = el("div", { class: "hist-legend" });
      hl.innerHTML = "<span><span class='sw' style='background:#94a3b8'></span>Observed</span>" +
        "<span><span class='sw' style='background:#0e7c7b'></span>Forecast (D1)</span>" +
        "<span><span class='sw' style='background:#e11d48;height:2px;border-radius:0'></span>30 mm event line</span>";
      host.appendChild(hl);
    }).catch(function () {
      host.innerHTML = "<p class='muted' style='font-size:12px'>History unavailable.</p>";
    });
  }

  function historySvg(hist) {
    var w = 400, h = 120, pad = 6;
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + w + " " + h); svg.setAttribute("class", "hist");
    if (!hist.length) { svg.innerHTML = "<text x='10' y='60' fill='#94a3b8' font-size='12'>No history yet.</text>"; return svg; }
    var scale = 40;
    hist.forEach(function (r) { scale = Math.max(scale, r[1], r[2]); });
    var n = hist.length, bw = (w - 2 * pad) / n, base = h - 16;
    function Y(v) { return base - v / scale * (base - 8); }
    var g = "<line x1='" + pad + "' y1='" + Y(30).toFixed(1) + "' x2='" + (w - pad) + "' y2='" + Y(30).toFixed(1) + "' stroke='#e11d48' stroke-width='1' stroke-dasharray='3 3' opacity='.7'/>";
    hist.forEach(function (r, i) {
      var x = pad + i * bw;
      g += "<rect x='" + (x + bw * .18).toFixed(1) + "' y='" + Y(r[1]).toFixed(1) + "' width='" + (bw * .44).toFixed(1) + "' height='" + Math.max(0, base - Y(r[1])).toFixed(1) + "' fill='#94a3b8' rx='1'/>";
      g += "<rect x='" + (x + bw * .5).toFixed(1) + "' y='" + Y(r[2]).toFixed(1) + "' width='" + (bw * .32).toFixed(1) + "' height='" + Math.max(0, base - Y(r[2])).toFixed(1) + "' fill='#0e7c7b' rx='1'/>";
    });
    g += "<text x='" + pad + "' y='" + (h - 3) + "' fill='#94a3b8' font-size='9'>" + hist[0][0].slice(5) + "</text>";
    g += "<text x='" + (w - pad) + "' y='" + (h - 3) + "' fill='#94a3b8' font-size='9' text-anchor='end'>" + hist[n - 1][0].slice(5) + "</text>";
    svg.innerHTML = g; return svg;
  }

  function closeDrawer() {
    state.selected = null;
    $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true");
    $("#drawerBg").classList.remove("open"); refreshMap();
    var sel = document.querySelectorAll("#tbody tr.sel");
    for (var i = 0; i < sel.length; i++) sel[i].classList.remove("sel");
  }

  // ---- wiring -----------------------------------------------------------------------
  $("#tbody").addEventListener("click", function (e) {
    var tr = e.target.closest ? e.target.closest("tr[data-pin]") : null;
    if (tr) select(tr.getAttribute("data-pin"));
  });
  $("#dClose").addEventListener("click", closeDrawer);
  $("#drawerBg").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") { closeDrawer(); setCellFilter(null); } });
  $("#cellchipClear").addEventListener("click", function () { setCellFilter(null); });
  $("#search").addEventListener("input", function (e) { state.search = e.target.value.trim(); renderTable(); });
  $("#filter").addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("button") : null; if (!b) return;
    state.filter = b.getAttribute("data-f");
    var btns = document.querySelectorAll("#filter button");
    for (var i = 0; i < btns.length; i++) btns[i].classList.toggle("on", btns[i] === b);
    renderTable();
  });
  document.querySelectorAll("#tbl thead th").forEach(function (th) {
    th.addEventListener("click", function () {
      var k = th.getAttribute("data-s");
      if (state.sortKey === k) state.sortDir *= -1;
      else { state.sortKey = k; state.sortDir = (k === "pincode" || k === "area" || k === "state") ? 1 : -1; }
      renderTable();
    });
  });

  // default filter: Priority if the membership list shipped, else All
  var hasPri = pins.some(function (p) { return p.pri; });
  state.filter = hasPri ? "PRI" : "ALL";
  var defBtn = document.querySelector('#filter button[data-f="' + state.filter + '"]');
  if (defBtn) defBtn.classList.add("on");

  (function () {
    var cal = D.calibration || { by_lead: {}, fallback: { watch_median_mm: "?", act_median_mm: "?" } };
    var th = (cal.by_lead && cal.by_lead["1"]) || cal.fallback;
    $("#footer").innerHTML = "Backbones: NOAA GEFS (public domain) + ECMWF IFS (CC-BY-4.0) · Truth: NASA GPM IMERG · " +
      "Geocoding: <a href='https://www.geonames.org/'>GeoNames</a> (CC BY 4.0) · " +
      "Bands calibrated to real skill (ensemble median, tuned per lead): tomorrow WATCH ≥" +
      Math.round(th.watch_median_mm) + "mm, ACT ≥" + Math.round(th.act_median_mm) + "mm · " +
      "One 0.25° cell ≈ 27km — pincodes in a cell share its forecast · Courier network only " +
      "(metros served by hyperlocal are not in this dataset). Built for the PharmEasy delivery team.";
  })();

  buildMap(); renderTable(); renderTrust(); renderScoreboard();
})();
