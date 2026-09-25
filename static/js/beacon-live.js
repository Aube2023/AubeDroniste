/* AubeBeacon : drones en direct sur la carte MapLibre d'AubePilot.
 *
 * Deux modes, selon l'objet posé par le gabarit :
 *   window.AUBEBEACON        page « Mes drones → AubeBeacon » : balises en direct
 *                            (WebSocket du hub, repli sondage), trace du vol en cours,
 *                            mise à jour des lignes de la liste.
 *   window.AUBEBEACON_FLIGHT page d'un vol : trace figée, départ/arrivée, profil.
 *
 * Page des balises, panneau AubeLink (si `cfg.aubelinkUrl`) : drone AubeLink qui
 * porte chaque balise, sondé toutes les 15 s sur /api/v1/beacon/aubelink. Son
 * état vit dans `aubelinkState`, jamais dans `devices` (que `applyLive`
 * remplace en entier), et son rendu passe uniquement par textContent.
 * TODO : les boutons associer et dissocier ne suivent pas ce sondage ; si
 * l'état a changé depuis le rendu serveur, la note invite à recharger.
 *
 * La carte vient de templates/_map.html, qui expose `window.AubeMap.map` et émet
 * `aube:map-ready`. Les marqueurs sont des éléments DOM (ils survivent au
 * changement de fond) ; la trace est une source GeoJSON, recréée si le style
 * la perd (`styledata`). Aucune dépendance en dehors de MapLibre.
 */
(function () {
  'use strict';

  var DRONE_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M12 3l3.5 9H8.5L12 3z" fill="currentColor" stroke="none" opacity=".9"/>' +
    '<circle cx="4.5" cy="4.5" r="2"/><circle cx="19.5" cy="4.5" r="2"/><circle cx="4.5" cy="19.5" r="2"/><circle cx="19.5" cy="19.5" r="2"/>' +
    '<path d="M6 6l3.2 3.2M18 6l-3.2 3.2M6 18l3.2-3.2M18 18l-3.2-3.2"/></svg>';

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function fmt(v, digits) { return v == null ? '·' : Number(v).toFixed(digits == null ? 0 : digits); }
  function kmh(mps) { return mps == null ? null : mps * 3.6; }
  function parseTs(s) { if (!s) return null; var t = Date.parse(s.indexOf('T') < 0 ? s.replace(' ', 'T') + 'Z' : s); return isNaN(t) ? null : t; }

  function whenMapReady(cb) {
    var m = window.AubeMap && window.AubeMap.map;
    if (m && m.loaded && m.loaded() && m.getStyle && m.getStyle()) { cb(m); return; }
    document.addEventListener('aube:map-ready', function (e) { cb(e.detail.map); }, { once: true });
  }
  function isDark() { return !!(window.AubeMap && window.AubeMap.isDark && window.AubeMap.isDark()); }

  // ---- Trace (source GeoJSON partagée par les deux modes) ---------------------
  function ensureTraceLayers(map) {
    if (!map.getSource('aube-beacon-trace')) {
      map.addSource('aube-beacon-trace', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.addLayer({ id: 'aube-beacon-trace-halo', type: 'line', source: 'aube-beacon-trace',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': isDark() ? '#0a0a0c' : '#ffffff', 'line-width': 6, 'line-opacity': .7 } });
      map.addLayer({ id: 'aube-beacon-trace', type: 'line', source: 'aube-beacon-trace',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#1e7a6a', 'line-width': 3 } });
    }
    if (!map.getSource('aube-beacon-ends')) {
      map.addSource('aube-beacon-ends', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      map.addLayer({ id: 'aube-beacon-ends', type: 'circle', source: 'aube-beacon-ends',
        paint: { 'circle-radius': 6, 'circle-color': ['get', 'color'], 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2 } });
    }
  }
  function lineFeature(id, coords) {
    return { type: 'Feature', properties: { id: id }, geometry: { type: 'LineString', coordinates: coords } };
  }

  // ===========================================================================
  // Mode vol : trace figée + départ / arrivée + profil hauteur / vitesse
  // ===========================================================================
  function flightMode(cfg) {
    var track = cfg.track || [];
    whenMapReady(function (map) {
      function draw() {
        ensureTraceLayers(map);
        var coords = track.map(function (p) { return [p[0], p[1]]; });
        map.getSource('aube-beacon-trace').setData({ type: 'FeatureCollection', features: coords.length > 1 ? [lineFeature('flight', coords)] : [] });
        var ends = [];
        if (coords.length) ends.push({ type: 'Feature', properties: { color: '#1e7a6a' }, geometry: { type: 'Point', coordinates: coords[0] } });
        if (coords.length > 1) ends.push({ type: 'Feature', properties: { color: '#c0413a' }, geometry: { type: 'Point', coordinates: coords[coords.length - 1] } });
        map.getSource('aube-beacon-ends').setData({ type: 'FeatureCollection', features: ends });
      }
      draw();
      map.on('styledata', function () { if (!map.getSource('aube-beacon-trace')) draw(); });
      if (track.length > 1) {
        var b = new maplibregl.LngLatBounds();
        track.forEach(function (p) { b.extend([p[0], p[1]]); });
        map.fitBounds(b, { padding: 60, maxZoom: 17, duration: 0 });
      } else if (track.length === 1) {
        map.jumpTo({ center: [track[0][0], track[0][1]], zoom: 15 });
      }
    });
    drawProfile(track);
  }

  function drawProfile(track) {
    var svg = document.getElementById('bcn-profile');
    if (!svg || track.length < 2) return;
    var W = 800, H = 120, pad = 6;
    var t0 = parseTs(track[0][3]), t1 = parseTs(track[track.length - 1][3]);
    var useTime = t0 != null && t1 != null && t1 > t0;
    function x(i) { if (!useTime) return pad + (W - 2 * pad) * i / (track.length - 1); var t = parseTs(track[i][3]); return pad + (W - 2 * pad) * ((t == null ? t0 : t) - t0) / (t1 - t0); }
    var hs = track.map(function (p) { return p[2] == null ? 0 : p[2]; });
    var vs = track.map(function (p) { return p[4] == null ? 0 : p[4] * 3.6; });
    var hmax = Math.max(1, Math.max.apply(null, hs)), vmax = Math.max(1, Math.max.apply(null, vs));
    function path(vals, max) {
      return vals.map(function (v, i) { return (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + (H - pad - (H - 2 * pad) * v / max).toFixed(1); }).join(' ');
    }
    var area = path(hs, hmax) + ' L' + x(track.length - 1).toFixed(1) + ' ' + (H - pad) + ' L' + x(0).toFixed(1) + ' ' + (H - pad) + ' Z';
    svg.innerHTML = '<path d="' + area + '" fill="#1e7a6a" opacity=".18"/>' +
      '<path d="' + path(hs, hmax) + '" fill="none" stroke="#1e7a6a" stroke-width="2" vector-effect="non-scaling-stroke"/>' +
      '<path d="' + path(vs, vmax) + '" fill="none" stroke="#4257b2" stroke-width="1.6" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>' +
      '<text x="' + (pad + 4) + '" y="14" font-size="11" font-family="ui-monospace, Menlo, monospace" fill="#1e7a6a">' + Math.round(hmax) + ' m</text>' +
      '<text x="' + (W - pad - 4) + '" y="14" text-anchor="end" font-size="11" font-family="ui-monospace, Menlo, monospace" fill="#4257b2">' + Math.round(vmax) + ' km/h</text>';
  }

  // ===========================================================================
  // Mode direct : balises, WebSocket, sondage de repli, lignes de la liste
  // ===========================================================================
  function liveMode(cfg) {
    var L = cfg.l10n, TH = cfg.thresholds || { online_s: 10, degraded_s: 30 };
    var devices = {};
    (cfg.devices || []).forEach(function (d) { devices[d.id] = d; d.trackCoords = (d.track || []).map(function (p) { return [p[0], p[1]]; }); });
    var map = null, markers = {}, popups = {};

    function statusOf(d, now) {
      var t = parseTs(d.last_seen_at);
      if (t == null) return { status: 'NEVER', age: null };
      var age = Math.max(0, ((now || Date.now()) - t) / 1000);
      return { status: age < TH.online_s ? 'ONLINE' : (age < TH.degraded_s ? 'DEGRADED' : 'OFFLINE'), age: age };
    }
    function agoText(age) {
      if (age == null) return L.never;
      if (age < 60) return L.agoS.replace('{n}', Math.round(age));
      if (age < 3600) return L.agoMin.replace('{n}', Math.round(age / 60));
      return L.agoH.replace('{n}', Math.round(age / 3600));
    }
    function stateText(s) { return (L.state && L.state[s]) || s || '·'; }
    function motionText(p) { return p ? fmt(p.relative_altitude_m) + ' m · ' + fmt(kmh(p.speed_mps)) + ' km/h · ' + fmt(p.heading_deg) + '°' : '·'; }
    function gpsText(p) { return p ? (p.gnss_fix ? L.satellites.replace('{n}', p.satellites == null ? '?' : p.satellites) : L.noFix) : '·'; }

    // ---- Lignes de la liste
    function setField(row, name, text) { var el = row.querySelector('[data-field="' + name + '"]'); if (el && el.textContent !== text) el.textContent = text; }
    function renderRow(d, st) {
      var row = document.querySelector('[data-beacon-id="' + d.id + '"]');
      if (!row) return;
      var pill = row.querySelector('[data-field="status"]');
      if (pill) { pill.setAttribute('data-status', st.status); pill.textContent = L.status[st.status] || st.status; }
      setField(row, 'age', agoText(st.age));
      setField(row, 'state', stateText(d.state));
      setField(row, 'motion', motionText(d.position));
      setField(row, 'position', d.position ? d.position.latitude.toFixed(5) + ', ' + d.position.longitude.toFixed(5) : '·');
      setField(row, 'gps', gpsText(d.position));
      setField(row, 'signal', d.signal_dbm != null ? ((d.network_type || '') + ' ' + d.signal_dbm + ' dBm').trim() : '·');
      setField(row, 'battery', d.battery_percent != null ? d.battery_percent + ' %' : '·');
    }

    // ---- Marqueurs
    function popupHtml(d, st) {
      var name = d.label || d.uid, drone = d.drone ? (d.drone.brand + ' ' + d.drone.model) : L.noDrone;
      var head = '<div class="aube-pop-head" style="background:' + (st.status === 'ONLINE' ? '#1e7a6a' : st.status === 'DEGRADED' ? '#b87519' : '#c0413a') + '"><span>' + esc(L.status[st.status] || st.status) + ' · ' + esc(stateText(d.state)) + '</span></div>';
      var lines = [
        esc(drone) + (d.owner && d.owner.name ? ' · ' + esc(d.owner.name) : ''),
        esc(L.height + ' ' + fmt(d.position && d.position.relative_altitude_m) + ' m · ' + L.speed + ' ' + fmt(kmh(d.position && d.position.speed_mps)) + ' km/h · ' + L.heading + ' ' + fmt(d.position && d.position.heading_deg) + '°'),
        esc(L.lastData + ' : ' + agoText(st.age)) + (st.status === 'OFFLINE' && d.last_seen_at ? ' · <b style="color:#c0413a">' + esc(L.status.OFFLINE) + '</b> ' + esc(String(d.last_seen_at).replace('T', ' ').slice(0, 19)) : ''),
        esc(L.battery + ' ' + (d.battery_percent == null ? '·' : d.battery_percent + ' %') + ' · ' + L.signal + ' ' + (d.signal_dbm == null ? '·' : d.signal_dbm + ' dBm') + ' · ' + gpsText(d.position))
      ];
      var link = d.flight && d.flight.id ? '<div style="margin-top:6px"><a href="' + esc(cfg.flightUrl.replace(/0$/, d.flight.id)) + '">' + esc(L.viewFlight) + ' →</a></div>' : '';
      return '<div class="aube-pop">' + head + '<div class="aube-pop-body"><b>' + esc(name) + '</b>' + lines.map(function (l) { return '<div class="aube-pop-muted">' + l + '</div>'; }).join('') + link + '</div></div>';
    }
    function markerEl(d) {
      var el = document.createElement('div');
      el.className = 'aube-drone-marker';
      el.innerHTML = DRONE_SVG + '<span class="lbl">' + esc(d.label || d.uid) + (d.drone ? ' <small>' + esc(d.drone.model || '') + '</small>' : '') + '</span>';
      el.style.color = '#1e7a6a';
      return el;
    }
    function renderMarker(d, st) {
      if (!map || !d.position) return;
      var ll = [d.position.longitude, d.position.latitude];
      var m = markers[d.id];
      if (!m) {
        var el = markerEl(d);
        popups[d.id] = new maplibregl.Popup({ offset: 22, maxWidth: '300px' });
        m = markers[d.id] = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat(ll).setPopup(popups[d.id]).addTo(map);
      } else {
        m.setLngLat(ll);
      }
      var el2 = m.getElement();
      el2.setAttribute('data-status', st.status);
      el2.style.color = st.status === 'ONLINE' ? '#1e7a6a' : (st.status === 'DEGRADED' ? '#b87519' : '#c0413a');
      var svg = el2.querySelector('svg');
      if (svg) svg.style.transform = 'rotate(' + (d.position.heading_deg || 0) + 'deg)';
      if (popups[d.id].isOpen()) popups[d.id].setHTML(popupHtml(d, st));
      else popups[d.id].setHTML(popupHtml(d, st));
    }
    function renderTraces() {
      if (!map) return;
      ensureTraceLayers(map);
      var feats = [];
      Object.keys(devices).forEach(function (id) {
        var d = devices[id];
        if (d.trackCoords && d.trackCoords.length > 1) feats.push(lineFeature(id, d.trackCoords));
      });
      map.getSource('aube-beacon-trace').setData({ type: 'FeatureCollection', features: feats });
    }
    function renderAll() {
      var now = Date.now();
      Object.keys(devices).forEach(function (id) {
        var d = devices[id], st = statusOf(d, now);
        renderRow(d, st);
        renderMarker(d, st);
      });
      renderTraces();
    }
    function fitAll() {
      if (!map) return;
      var b = new maplibregl.LngLatBounds(), n = 0;
      Object.keys(devices).forEach(function (id) {
        var d = devices[id];
        if (d.position) { b.extend([d.position.longitude, d.position.latitude]); n++; }
        (d.trackCoords || []).forEach(function (c) { b.extend(c); n++; });
      });
      if (n === 1) map.jumpTo({ center: b.getCenter(), zoom: 15 });
      else if (n > 1) map.fitBounds(b, { padding: { top: 70, bottom: 60, left: 80, right: 130 }, maxZoom: 16, duration: 0 });
    }

    // ---- Mise à jour depuis un événement ou une lecture /live
    function applyTelemetry(ev) {
      var d = devices[ev.device_id];
      if (!d) { d = devices[ev.device_id] = { id: ev.device_id, uid: ev.device_uid, label: null, drone: null, trackCoords: [] }; }
      d.last_seen_at = ev.server_time || ev.timestamp;
      d.state = ev.state;
      if (ev.battery_percent != null) d.battery_percent = ev.battery_percent;
      if (ev.signal_dbm != null) d.signal_dbm = ev.signal_dbm;
      if (ev.network_type) d.network_type = ev.network_type;
      var jump = (ev.flags || []).indexOf('jump') >= 0;
      if (ev.latitude != null && !jump) {
        d.position = { latitude: ev.latitude, longitude: ev.longitude, relative_altitude_m: ev.relative_altitude_m,
          speed_mps: ev.speed_mps, heading_deg: ev.heading_deg, satellites: ev.satellites, gnss_fix: ev.gnss_fix, timestamp: ev.timestamp };
        if (ev.flight_id) {
          if (!d.flight || d.flight.id !== ev.flight_id) { d.flight = { id: ev.flight_id }; d.trackCoords = []; }
          d.trackCoords.push([ev.longitude, ev.latitude]);
          if (d.trackCoords.length > 5000) d.trackCoords.splice(0, d.trackCoords.length - 5000);
        } else if (ev.state === 'READY' || ev.state === 'LANDED') {
          d.flight = null;
        }
      }
      var st = statusOf(d);
      renderRow(d, st);
      renderMarker(d, st);
      renderTraces();
    }
    function applyLive(json) {
      (json.devices || []).forEach(function (nd) {
        nd.trackCoords = (nd.track || []).map(function (p) { return [p[0], p[1]]; });
        devices[nd.id] = nd;
      });
      if (json.thresholds) TH = json.thresholds;
      renderAll();
    }

    // ---- Temps réel : WebSocket du hub, sinon sondage toutes les 5 s
    var ws = null, wsBackoff = 1000, pollTimer = null, wsDead = false;
    function startPolling() {
      if (pollTimer) return;
      var tick = function () {
        if (document.hidden) return;
        fetch(cfg.liveUrl, { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) { if (j) applyLive(j); }).catch(function () {});
      };
      pollTimer = setInterval(tick, 5000);
    }
    function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
    function connect() {
      if (wsDead || !(cfg.realtime && cfg.realtime.enabled) || !window.WebSocket) { startPolling(); return; }
      fetch(cfg.ticketUrl, { credentials: 'same-origin' })
        .then(function (r) { if (r.status === 503) { wsDead = true; startPolling(); return null; } return r.ok ? r.json() : null; })
        .then(function (t) {
          if (!t) { startPolling(); return; }
          var url = t.url + (t.url.indexOf('?') < 0 ? '?' : '&') + 'ticket=' + encodeURIComponent(t.ticket);
          ws = new WebSocket(url);
          ws.onopen = function () {
            wsBackoff = 1000; stopPolling();
            ws.send(JSON.stringify({ op: 'subscribe', rooms: t.rooms }));
            // Rattrapage : ce qui s'est passé entre la page et la connexion.
            fetch(cfg.liveUrl, { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) { if (j) applyLive(j); }).catch(function () {});
          };
          ws.onmessage = function (e) {
            var msg; try { msg = JSON.parse(e.data); } catch (err) { return; }
            if (msg.event === 'drone:telemetry' && msg.data) applyTelemetry(msg.data);
            else if (msg.event === 'drone:device' && msg.data) {
              fetch(cfg.liveUrl, { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (j) { if (j) applyLive(j); }).catch(function () {});
            }
          };
          ws.onclose = function () { ws = null; startPolling(); setTimeout(connect, wsBackoff); wsBackoff = Math.min(wsBackoff * 2, 30000); };
          ws.onerror = function () { try { ws.close(); } catch (err) {} };
        })
        .catch(function () { startPolling(); setTimeout(connect, wsBackoff); wsBackoff = Math.min(wsBackoff * 2, 30000); });
    }

    // ---- AubeLink : drone AubeLink de chaque balise (sondage 15 s, état à part)
    function startAubeLink() {
      if (!cfg.aubelinkUrl) return;
      var AL = L.aubelink || {};
      var PILL = { CONNECTED: 'ONLINE', DEGRADED: 'DEGRADED', LOST: 'OFFLINE', UNKNOWN: 'NEVER' };
      var aubelinkState = {};          // id de balise -> dernière vue connue (linked true ou false)
      var lastFetch = 0;

      function panels() { return Array.prototype.slice.call(document.querySelectorAll('[data-aubelink-for]')); }
      function part(row, name) { return row.querySelector('[data-al="' + name + '"]'); }
      function setText(el, text) { if (el && el.textContent !== text) el.textContent = text; }
      function show(el, on) { if (el) el.hidden = !on; }
      function safeUrl(u) { return typeof u === 'string' && /^https?:\/\//.test(u) ? u : null; }
      function when(ts) { return String(ts).slice(0, 16).replace('T', ' ') + ' UTC'; }
      function setNote(row, text) { setText(row.querySelector('[data-field="al-note"]'), text || ''); }
      function fillList(ul, items, empty, line) {
        if (!ul) return;
        while (ul.firstChild) ul.removeChild(ul.firstChild);
        if (!items || !items.length) {
          var none = document.createElement('li');
          none.className = 'empty';
          none.textContent = empty || '';
          ul.appendChild(none);
          return;
        }
        items.forEach(function (it) { var li = document.createElement('li'); li.textContent = line(it); ul.appendChild(li); });
      }
      function alertLine(a) { return (a.code || '·') + ' · ' + (a.severity || '·') + (a.message ? ' · ' + a.message : ''); }
      function messageLine(m) {
        var body = (m.kind === 'command' ? m.command : (m.text || m.category)) || '·';
        return (m.direction || '·') + ' · ' + body + ' · ' + (m.status || '·') + (m.createdAt ? ' · ' + when(m.createdAt) : '');
      }
      function renderAge(row) {
        var el = part(row, 'age');
        if (!el) return;
        var t = parseTs(el.getAttribute('data-ts'));
        setText(el, t == null ? L.never : agoText(Math.max(0, (Date.now() - t) / 1000)));
      }
      function renderAubeLink(row, view) {
        var dr = view && view.linked ? view.drone : null;
        var pill = part(row, 'pill');
        show(pill, !!dr); show(part(row, 'grid'), !!dr); show(part(row, 'lists'), !!dr);
        if (dr) {
          var ls = dr.linkState || 'UNKNOWN';
          if (pill) { pill.setAttribute('data-status', PILL[ls] || 'NEVER'); setText(pill, ls); }
          setText(part(row, 'drone'), (dr.name || '') + ' · ' + (dr.droneId || ''));
          setText(part(row, 'flight'), dr.flightState || '·');
          setText(part(row, 'battery'), dr.batteryPercent == null ? '·' : dr.batteryPercent + ' %');
          var age = part(row, 'age');
          if (age) age.setAttribute('data-ts', dr.lastSeen || '');
          setText(part(row, 'alert-count'), String(dr.activeAlerts == null ? 0 : dr.activeAlerts));
          fillList(part(row, 'alerts'), view.alerts, AL.noAlerts, alertLine);
          fillList(part(row, 'messages'), view.messages, AL.noMessages, messageLine);
          renderAge(row);
        }
        var urls = dr && view.urls ? view.urls : null;
        var u1 = urls && safeUrl(urls.drone), u2 = urls && safeUrl(urls.flight);
        var open = part(row, 'open'), openFlight = part(row, 'open-flight');
        show(part(row, 'links'), !!u1);
        if (open && u1) open.href = u1;
        show(openFlight, !!u2);
        if (openFlight && u2) openFlight.href = u2;
      }
      function applyAubeLink(json) {
        var beacons = (json && json.beacons) || {};
        var deferred = false;
        Object.keys(beacons).forEach(function (id) {
          var row = document.querySelector('[data-aubelink-for="' + id + '"]');
          var v = beacons[id];
          if (!row || !v) return;
          if (v.linked === true || v.linked === false) {
            aubelinkState[id] = v;
            renderAubeLink(row, v);
            // TODO : les boutons de .bcn-actions (dissocier, sélecteur) restent
            // ceux du rendu serveur (data-al-rendered) ; quand l'état AubeLink
            // a changé depuis, la note invite à recharger la page.
            var stale = row.getAttribute('data-al-rendered') !== String(v.linked);
            var note = v.linked ? '' : AL.notLinked;
            if (stale && AL.reload) note = note ? note + ' ' + AL.reload : AL.reload;
            setNote(row, note);
          } else if (v.reason === 'deferred') {
            deferred = true;          // budget épuisé côté serveur : servie au prochain tour
          } else if (v.reason === 'owner_deleted') {
            setNote(row, AL.ownerDeleted);   // aucun appel au nom d'un compte supprimé
          } else {
            // AubeLink n'a pas répondu : les dernières valeurs restent affichées.
            setNote(row, v.reason === 'rate_limited' ? AL.busy : AL.unavailable);
          }
        });
        return deferred;
      }
      function failAll(text) { panels().forEach(function (row) { setNote(row, text); }); }
      function tick() {
        if (document.hidden) return;
        lastFetch = Date.now();
        fetch(cfg.aubelinkUrl, { credentials: 'same-origin' })
          .then(function (r) {
            if (r.status === 429) { failAll(AL.busy); return null; }
            if (!r.ok) { failAll(AL.unavailable); return null; }
            return r.json();
          })
          .then(function (j) { if (j) applyAubeLink(j); })
          .catch(function () { failAll(AL.unavailable); });
      }

      if (cfg.aubelink && applyAubeLink(cfg.aubelink)) setTimeout(tick, 1500);
      setInterval(tick, 15000);
      setInterval(function () {
        Object.keys(aubelinkState).forEach(function (id) {
          var row = aubelinkState[id].linked && document.querySelector('[data-aubelink-for="' + id + '"]');
          if (row) renderAge(row);
        });
      }, 1000);
      document.addEventListener('visibilitychange', function () { if (!document.hidden && Date.now() - lastFetch > 15000) tick(); });
    }

    // ---- Démarrage
    whenMapReady(function (m) {
      map = m;
      ensureTraceLayers(map);
      map.on('styledata', function () { if (map.getStyle() && !map.getSource('aube-beacon-trace')) { renderTraces(); } });
      renderAll();
      fitAll();
    });
    renderAll();
    setInterval(function () {
      var now = Date.now();
      Object.keys(devices).forEach(function (id) { var d = devices[id], st = statusOf(d, now); renderRow(d, st); if (markers[d.id]) { var el = markers[d.id].getElement(); if (el.getAttribute('data-status') !== st.status) renderMarker(d, st); } });
    }, 1000);
    document.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('[data-beacon-focus]');
      if (!a) return;
      var d = devices[a.getAttribute('data-beacon-focus')];
      if (!d || !d.position || !map) return;
      e.preventDefault();
      document.getElementById('aube-map').scrollIntoView({ behavior: 'smooth', block: 'center' });
      map.flyTo({ center: [d.position.longitude, d.position.latitude], zoom: Math.max(map.getZoom(), 15), duration: 600 });
      if (popups[d.id] && !popups[d.id].isOpen()) markers[d.id].togglePopup();
    });
    document.addEventListener('visibilitychange', function () { if (!document.hidden && !ws) connect(); });
    startAubeLink();
    connect();
  }

  if (window.AUBEBEACON_FLIGHT) flightMode(window.AUBEBEACON_FLIGHT);
  else if (window.AUBEBEACON) liveMode(window.AUBEBEACON);
})();
