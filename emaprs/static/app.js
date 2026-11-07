// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 Nicholas Heath
"use strict";

(() => {
  const el = (id) => document.getElementById(id);

  // ---------- state ----------
  let map = null;
  let tileLayer = null;
  let trailLayer = null;        // all station trails; on the map only when shown
  let mapCentered = false;      // first GPS fix centers the map once
  let ownMarker = null;
  let ownFix = null;
  let lastStatus = null;
  let mycall = "";
  let limits = { raw: 1000, messages: 500, station_packets: 500 };
  // name -> { packets (oldest first), pkt (newest positioned), marker, trail, symKey,
  //           histOpen (popup packet list expanded) }
  const stations = new Map();
  const unread = { raw: 0, msg: 0, msgToMe: 0 };
  let imperial = true;          // Display Options: mph / ft instead of km/h / m
  let followMe = false;         // Display Options: keep the map panned to my position

  // ---------- helpers ----------
  function h(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }

  function fmtTime(ts, withZone = false) {
    const opts = { hour12: false };
    if (withZone) opts.timeZoneName = "short";  // e.g. "09:13:21 CST"
    return new Date(ts * 1000).toLocaleTimeString([], opts);
  }

  // Packets and the server use metric (aprslib converts APRS's knots and
  // feet to km/h and metres); convert only for display.
  function fmtSpeed(kmh) {
    return imperial ? `${Math.round(kmh / 1.609344)} mph` : `${Math.round(kmh)} km/h`;
  }

  function fmtAlt(m) {
    return imperial ? `${Math.round(m / 0.3048)} ft` : `${Math.round(m)} m`;
  }

  function ago(ts) {
    const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.round(s / 60)}m ago`;
    return `${(s / 3600).toFixed(1)}h ago`;
  }

  // ---------- map ----------
  function initMap(settings) {
    if (!map) {
      map = L.map("map", { zoomControl: true }).setView(settings.center, settings.zoom);
      addCenterControl();
      trailLayer = L.layerGroup();
      if (el("opt-trails").checked) trailLayer.addTo(map);
    }
    if (tileLayer) tileLayer.remove();
    tileLayer = L.tileLayer(settings.tile_url, {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        + ' | APRS symbols: <a href="https://github.com/hessu/aprs-symbols">aprs.fi / OH7LZB</a>',
    }).addTo(map);
  }

  function addCenterControl() {
    const Ctl = L.Control.extend({
      options: { position: "topleft" },
      onAdd() {
        const wrap = L.DomUtil.create("div", "leaflet-bar");
        const btn = L.DomUtil.create("button", "center-me", wrap);
        btn.id = "center-me";
        btn.type = "button";
        btn.title = "Center on my position";
        btn.textContent = "◎";
        btn.disabled = true;
        L.DomEvent.disableClickPropagation(wrap);
        L.DomEvent.on(btn, "click", () => {
          if (ownFix) map.setView([ownFix.lat, ownFix.lon], Math.max(map.getZoom(), 13));
        });
        return wrap;
      },
    });
    new Ctl().addTo(map);
  }

  function popupContent(name) {
    const s = stations.get(name);
    const p = s.pkt;
    const div = h("div", "popup");
    const dl = h("dl");
    const add = (k, v) => {
      if (v === null || v === undefined || v === "") return;
      dl.append(h("dt", null, k), h("dd", null, v));
    };
    add("Name", p.name);
    if (p.name !== p.from) add("From", p.from);
    add("Heard", `${fmtTime(p.ts, true)} (${ago(p.ts)})`);
    add("Position", `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`);
    if (p.symbol) {
      const dd = h("dd", "sym-cell");
      dd.append(symbolElement({ ...p, from_me: false }), h("span", null, `${p.symbol_table || ""}${p.symbol}`));
      dl.append(h("dt", null, "Symbol"), dd);
    }
    add("Course", p.course !== null ? `${p.course}°` : null);
    add("Speed", p.speed !== null ? fmtSpeed(p.speed) : null);
    add("Altitude", p.altitude !== null ? fmtAlt(p.altitude) : null);
    add("Comment", p.comment);
    add("Path", (p.path || []).join(","));
    add("Raw", p.raw);
    const n = s.packets.length;
    add("Packets", n >= limits.station_packets ? `${n} (max kept; oldest dropped)` : n);
    div.append(dl, packetHistory(name, s));
    return div;
  }

  // Collapsible list of every stored packet for a station, newest first.
  function packetHistory(name, s) {
    const det = h("details", "history");
    det.open = !!s.histOpen;
    det.append(h("summary", null, `Show packets (${s.packets.length})`));
    const ol = h("ol");
    // Build the rows only when expanded; a busy station can hold hundreds.
    const fill = () => {
      if (ol.childElementCount) return;
      for (let i = s.packets.length - 1; i >= 0; i--) {
        const q = s.packets[i];
        const li = h("li");
        if (q.error) li.classList.add("error");
        const meta = h("div", "meta");
        meta.append(h("span", null, fmtTime(q.ts)), h("span", "fmt", q.format || ""));
        li.append(meta, h("div", "raw", q.raw));
        ol.append(li);
      }
    };
    if (det.open) fill();
    det.addEventListener("toggle", () => {
      const cur = stations.get(name);
      // Ignore toggles from a popup rebuild: setting det.open above fires one,
      // and so does any copy that update() has since replaced. Reacting to
      // those loops the popup and snaps the list back open after a collapse.
      if (!cur || !det.isConnected || cur.histOpen === det.open) return;
      cur.histOpen = det.open;
      if (det.open) fill();
      // Popup changed height: let Leaflet re-anchor and pan it into view.
      cur?.marker?.getPopup()?.update();
    });
    det.append(ol);
    return det;
  }

  // ---------- APRS symbols (aprs.fi sprite sheets, 16x6 grid, index = char - 33) ----------
  const SYM_SIZE = 24;

  // Returns {sheet, idx, overlay} or null if the symbol can't be drawn.
  function symbolSprite(table, code) {
    if (!table || !code) return null;
    const idx = code.charCodeAt(0) - 33;
    if (idx < 0 || idx > 93) return null;
    if (table === "/") return { sheet: 0, idx, overlay: null };
    if (table === "\\") return { sheet: 1, idx, overlay: null };
    // Compressed positions encode overlay digits as a-j.
    if (table >= "a" && table <= "j") table = String.fromCharCode(table.charCodeAt(0) - 49);
    if (/^[0-9A-Z]$/.test(table)) {
      return { sheet: 1, idx, overlay: table.charCodeAt(0) - 33 };
    }
    return null;
  }

  function spriteDiv(sheet, idx) {
    const d = document.createElement("div");
    d.className = `sym sym-${sheet}`;
    d.style.backgroundPosition = `-${(idx % 16) * SYM_SIZE}px -${Math.floor(idx / 16) * SYM_SIZE}px`;
    return d;
  }

  function symbolElement(p) {
    const wrap = document.createElement("div");
    wrap.className = "sym-wrap" + (p.from_me ? " from-me" : "");
    const s = symbolSprite(p.symbol_table, p.symbol);
    if (!s) {
      wrap.append(h("div", "sym-dot"));
      return wrap;
    }
    wrap.append(spriteDiv(s.sheet, s.idx));
    if (s.overlay !== null) wrap.append(spriteDiv(2, s.overlay));
    return wrap;
  }

  function stationIcon(p) {
    return L.divIcon({
      html: symbolElement(p),
      className: "station-icon",
      iconSize: [SYM_SIZE, SYM_SIZE],
      iconAnchor: [SYM_SIZE / 2, SYM_SIZE / 2],
      popupAnchor: [0, -SYM_SIZE / 2],
      tooltipAnchor: [SYM_SIZE / 2, 0],
    });
  }

  function symbolKey(p) {
    return `${p.symbol_table || ""}${p.symbol || ""}${p.from_me ? "*" : ""}`;
  }

  const TRAIL_STYLE = { color: "#d9480f", weight: 2, opacity: 0.7, interactive: false };

  // Redraw a station's marker and trail from its packet history.
  function renderStation(name) {
    const s = stations.get(name);
    if (!s) return;
    const pts = [];
    let p = null;
    for (const q of s.packets) {
      if (!q.has_position) continue;
      p = q;
      const last = pts[pts.length - 1];
      if (!last || last[0] !== q.lat || last[1] !== q.lon) pts.push([q.lat, q.lon]);
    }
    s.pkt = p;
    if (!p) {
      // No position left in the history: nothing to draw.
      if (s.marker) { s.marker.remove(); s.marker = null; }
      if (s.trail) { trailLayer.removeLayer(s.trail); s.trail = null; }
      return;
    }
    const ll = [p.lat, p.lon];
    if (!s.marker) {
      s.marker = L.marker(ll, { icon: stationIcon(p), keyboard: false })
        // An element, not a string: Leaflet inserts a string as HTML, and an
        // object or item name is whatever another station sent.
        .bindTooltip(h("span", null, name), { permanent: true, direction: "right", className: "callsign" })
        .bindPopup(() => popupContent(name), { maxWidth: 360 })
        .on("popupclose", () => {
          // Reopen fresh: packet list collapsed, and content rebuilt rather
          // than the snapshot a live update left behind.
          const cur = stations.get(name);
          if (cur) cur.histOpen = false;
          cur?.marker?.setPopupContent(() => popupContent(name));
        })
        .addTo(map);
      s.symKey = symbolKey(p);
    } else {
      s.marker.setLatLng(ll);
      const key = symbolKey(p);
      if (key !== s.symKey) {
        s.marker.setIcon(stationIcon(p));
        s.symKey = key;
      }
      if (s.marker.isPopupOpen()) s.marker.setPopupContent(popupContent(name));
    }
    if (pts.length >= 2) {
      if (s.trail) s.trail.setLatLngs(pts);
      else s.trail = L.polyline(pts, TRAIL_STYLE).addTo(trailLayer);
    } else if (s.trail) {
      trailLayer.removeLayer(s.trail);
      s.trail = null;
    }
  }

  function stationPush(p) {
    let s = stations.get(p.name);
    if (!s) {
      s = { packets: [], pkt: null, marker: null, trail: null, symKey: null };
      stations.set(p.name, s);
    }
    s.packets.push(p);
    if (s.packets.length > limits.station_packets) {
      s.packets.splice(0, s.packets.length - limits.station_packets);
    }
    renderStation(p.name);
  }

  function setStation(name, packets) {
    removeStation(name);
    stations.set(name, { packets, pkt: null, marker: null, trail: null, symKey: null });
    renderStation(name);
  }

  // Same rule as the server: drop packets older than cutoff from station
  // histories and the Messages list, then remove empty stations.
  function expireStations(cutoff) {
    const msgs = el("panel-msg");  // newest first, so the oldest are at the end
    while (msgs.lastElementChild && Number(msgs.lastElementChild.dataset.ts) < cutoff) {
      msgs.lastElementChild.remove();
    }
    for (const [name, s] of [...stations]) {
      const n = s.packets.findIndex((q) => q.ts >= cutoff);
      if (n === 0) continue;
      if (n < 0) removeStation(name);
      else {
        s.packets.splice(0, n);
        renderStation(name);
      }
    }
  }

  function removeStation(name) {
    const s = stations.get(name);
    if (s) {
      if (s.marker) s.marker.remove();
      if (s.trail) trailLayer.removeLayer(s.trail);
      stations.delete(name);
    }
  }

  function ownPopupContent() {
    const fix = ownFix;
    const parts = [`${fix.lat.toFixed(5)}, ${fix.lon.toFixed(5)}`];
    if (fix.alt !== null) parts.push(fmtAlt(fix.alt));
    if (fix.speed !== null) parts.push(fmtSpeed(fix.speed * 3.6));  // gpsd: m/s
    const src = fix.source === "static" ? "fixed, from config" : "GPS";
    return `My position (${src})<br>${parts.join(" &middot; ")}`;
  }

  // Redraw open popups after a units change.
  function refreshPopups() {
    for (const [name, s] of stations) {
      if (s.marker && s.marker.isPopupOpen()) s.marker.setPopupContent(popupContent(name));
    }
    if (ownMarker && ownFix && ownMarker.isPopupOpen()) ownMarker.setPopupContent(ownPopupContent());
  }

  function setOwnFix(fix) {
    ownFix = fix;
    const btn = el("center-me");
    if (btn) btn.disabled = !fix;
    if (!fix) return;
    const ll = [fix.lat, fix.lon];
    if (!ownMarker) {
      ownMarker = L.circleMarker(ll, {
        radius: 9, weight: 3, color: "#fff", fillColor: "#1f6feb", fillOpacity: 1,
      })
        .bindTooltip(h("span", null, mycall || "Me"), { permanent: true, direction: "right", offset: [10, 0], className: "callsign" })
        .bindPopup(ownPopupContent)
        .addTo(map);
    } else {
      ownMarker.setLatLng(ll);
      if (ownMarker.isPopupOpen()) ownMarker.setPopupContent(ownPopupContent());
    }
    if (lastStatus) setStatus(lastStatus);
    if (!mapCentered) {
      mapCentered = true;
      map.setView(ll, Math.max(map.getZoom(), 12));
    } else if (followMe) {
      map.panTo(ll);              // keep whatever zoom the user has chosen
    }
  }

  // ---------- lists ----------
  function packetItem(p, forMessages) {
    const li = h("li");
    li.dataset.ts = p.ts;
    if (p.error) li.classList.add("error");
    if (p.to_me) li.classList.add("to-me");
    else if (p.from_me) li.classList.add("from-me");

    const meta = h("div", "meta");
    meta.append(h("span", null, fmtTime(p.ts)), h("span", "call", p.from));
    if (p.addressee) meta.append(h("span", null, `→ ${p.addressee}`));
    meta.append(h("span", "fmt", p.format || ""));
    li.append(meta);

    if (forMessages) {
      li.append(h("div", "body", p.text));
    } else {
      li.append(h("div", "body raw", p.raw));
    }
    if (p.error) li.append(h("div", "err-text", p.error));

    if (p.has_position && stations.get(p.name)?.marker) {
      li.classList.add("clickable");
      li.title = "Show on map";
      li.addEventListener("click", () => {
        const s = stations.get(p.name);
        if (!s || !s.marker) return;
        map.setView(s.marker.getLatLng(), Math.max(map.getZoom(), 13));
        s.marker.openPopup();
      });
    }
    return li;
  }

  function prepend(listId, li, max) {
    const ul = el(listId);
    ul.prepend(li);
    while (ul.children.length > max) ul.lastElementChild.remove();
  }

  function activePanel() {
    return document.querySelector(".tab.active").dataset.panel;
  }

  function updateBadges() {
    el("badge-raw").textContent = unread.raw ? String(unread.raw) : "";
    el("badge-msg").textContent = unread.msg ? String(unread.msg) : "";
    el("badge-msg").classList.toggle("me", unread.msgToMe > 0);
  }

  function addPacket(p) {
    if (p.has_position && !p.alive) removeStation(p.name);  // killed object
    else stationPush(p);
    prepend("panel-raw", packetItem(p, false), limits.raw);
    if (!p.has_position) prepend("panel-msg", packetItem(p, true), limits.messages);

    const panel = activePanel();
    if (panel !== "panel-raw") unread.raw++;
    if (!p.has_position && panel !== "panel-msg") {
      unread.msg++;
      if (p.to_me) unread.msgToMe++;
    }
    updateBadges();
  }

  function setupTabs() {
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
        document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === tab.dataset.panel));
        if (tab.dataset.panel === "panel-raw") unread.raw = 0;
        else if (tab.dataset.panel === "panel-msg") { unread.msg = 0; unread.msgToMe = 0; }
        updateBadges();
      });
    });
  }

  // ---------- display options (remembered per browser) ----------
  function loadOption(key, dflt) {
    try {
      const v = localStorage.getItem(`emaprs.${key}`);
      return v === null ? dflt : v === "1";
    } catch { return dflt; }
  }

  function saveOption(key, on) {
    try { localStorage.setItem(`emaprs.${key}`, on ? "1" : "0"); } catch { /* storage blocked */ }
  }

  function setupOptions() {
    const trails = el("opt-trails");
    trails.checked = loadOption("trails", true);
    trails.addEventListener("change", () => {
      saveOption("trails", trails.checked);
      if (!map) return;
      if (trails.checked) trailLayer.addTo(map);
      else trailLayer.remove();
    });
    const units = el("opt-imperial");
    units.checked = imperial = loadOption("imperial", true);
    units.addEventListener("change", () => {
      imperial = units.checked;
      saveOption("imperial", imperial);
      refreshPopups();
    });
    const follow = el("opt-follow");
    follow.checked = followMe = loadOption("follow", false);
    follow.addEventListener("change", () => {
      followMe = follow.checked;
      saveOption("follow", followMe);
      if (followMe && map && ownFix) map.panTo([ownFix.lat, ownFix.lon]);
    });
  }

  // ---------- status ----------
  function setPill(id, cls, text) {
    const p = el(id);
    p.className = `pill ${cls}`;
    p.textContent = text;
  }

  function setStatus(st) {
    lastStatus = st;
    setPill("st-kiss", st.kiss ? "ok" : "bad", st.kiss ? "KISS" : "KISS down");
    const onFixed = ownFix && ownFix.source === "static";
    const gpsPill = el("st-gps");
    if (!st.gpsd_enabled) {
      if (onFixed) setPill("st-gps", "ok", "Fixed position");
      else setPill("st-gps", "warn", "GPS off");
      gpsPill.title = "gpsd is disabled in the config";
      return;
    }
    // gpsd enabled: until its first fix, the fixed position (if configured) is shown.
    const suffix = onFixed ? " \u00B7 fixed pos" : "";
    if (!st.gpsd) setPill("st-gps", "bad", "GPS down" + suffix);
    else if (st.gps_mode >= 3) setPill("st-gps", "ok", "GPS 3D");
    else if (st.gps_mode === 2) setPill("st-gps", "warn", "GPS 2D");
    else setPill("st-gps", "warn", "GPS no fix" + suffix);
    gpsPill.title = onFixed
      ? "gpsd has no fix yet; showing the fixed position from the config"
      : "gpsd connection and fix";
  }

  // ---------- snapshot / websocket ----------
  function applySnapshot(s) {
    mycall = s.mycall || "";
    limits = s.limits || limits;
    el("mycall").textContent = mycall;
    initMap(s.map);
    for (const name of [...stations.keys()]) removeStation(name);
    el("panel-raw").replaceChildren();
    el("panel-msg").replaceChildren();
    // Stations first so list items for positioned packets are clickable.
    // The server's station table is authoritative (expired ones are gone).
    for (const st of s.stations) setStation(st.name, st.packets);
    for (const p of s.raw) prepend("panel-raw", packetItem(p, false), limits.raw);
    for (const p of s.messages) prepend("panel-msg", packetItem(p, true), limits.messages);
    unread.raw = unread.msg = unread.msgToMe = 0;
    updateBadges();
    ownFix = null;
    if (ownMarker && ownMarker.getTooltip()) ownMarker.setTooltipContent(mycall || "Me");
    if (s.own) setOwnFix(s.own);
    setStatus(s.status);
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => setPill("st-server", "ok", "Server");
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      switch (m.type) {
        case "snapshot": applySnapshot(m); break;
        case "packet": addPacket(m.packet); break;
        case "gps": setOwnFix(m.fix); break;
        case "status": setStatus(m.status); break;
        case "remove": m.names.forEach(removeStation); break;
        case "expire": expireStations(m.cutoff); break;
      }
    };
    ws.onclose = () => {
      setPill("st-server", "bad", "Server down");
      setTimeout(connect, 2000);
    };
  }

  setupTabs();
  setupOptions();
  connect();
})();
