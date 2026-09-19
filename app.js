/* FaithMap — US houses of worship */
(function () {
  "use strict";

  const APP_VERSION = "v63";
  window.__APP_VERSION = APP_VERSION;

  const CARTO_KEY = "cb1_27ow_1_73656a41346af19fc01d4d26";
  const CARTO_STYLE =
    "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

  const RELIGIONS = [
    { id: "christian", label: "Christian", color: "#ffd58a" },
    { id: "jewish", label: "Jewish", color: "#7eb6ff" },
    { id: "muslim", label: "Muslim", color: "#3dffa3" },
    { id: "hindu", label: "Hindu", color: "#ff7a3a" },
    { id: "buddhist", label: "Buddhist", color: "#ffe566" },
    { id: "sikh", label: "Sikh", color: "#ffb020" },
  ];

  const CONUS = [
    [24.5, -124.8],
    [49.4, -66.9],
  ];

  const el = {
    chips: document.getElementById("chips"),
    totLab: document.getElementById("tot-lab"),
    totVal: document.getElementById("tot-val"),
    mapBadge: document.getElementById("map-badge"),
    verLabel: document.getElementById("ver-label"),
    status: document.getElementById("status"),
    sheet: document.getElementById("sheet"),
    sheetName: document.getElementById("sheet-name"),
    sheetRel: document.getElementById("sheet-rel"),
    sheetWhere: document.getElementById("sheet-where"),
    sheetMaps: document.getElementById("sheet-maps"),
    sheetClose: document.getElementById("sheet-close"),
    about: document.getElementById("about"),
    aboutBtn: document.getElementById("about-btn"),
    aboutClose: document.getElementById("about-close"),
    aboutSrc: document.getElementById("about-src"),
    sourceLine: document.getElementById("source-line"),
    modeMapped: document.getElementById("mode-mapped"),
    modeCensus: document.getElementById("mode-census"),
  };

  const state = {
    active: new Set(["christian"]),
    mode: "mapped",
    mappedPlaces: [],
    censusPlaces: [],
    places: [],
    meta: null,
    census: null,
    censusByCounty: null,
    censusByState: null,
    map: null,
    totalsTimer: 0,
    selected: null,
    focusState: null,
    focusCounty: null,
    outlineState: null,
    outlineCounty: null,
    outlineNation: false,
    canvas: null,
  };

  function normCountyName(name) {
    let s = String(name || "")
      .toUpperCase()
      .replace(/\./g, "")
      .replace(/['’]/g, "")
      .replace(/-/g, " ");
    try {
      s = s.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    } catch (err) {
      /* older engines */
    }
    s = s.replace(/\s+/g, " ").trim();
    s = s.replace(/\s*\(CITY\)\s*$/g, "");
    s = s.replace(/\s+CITY\s*$/g, "");
    s = s.replace(/\s+COUNTY\s*$/g, "");
    s = s.replace(/\s+PARISH\s*$/g, "");
    s = s.replace(/\bDE\s+/g, "DE");
    s = s.replace(/\bDU\s+/g, "DU");
    s = s.replace(/\bLA\s+/g, "LA");
    s = s.replace(/\bLE\s+/g, "LE");
    s = s.replace(/\bST\s+/g, "ST");
    return s.replace(/\s+/g, " ").trim();
  }

  function countyLookupKey(stAbbr, countyName) {
    return String(stAbbr || "") + "|" + normCountyName(countyName);
  }

  function fmt(n) {
    return Number(n || 0).toLocaleString("en-US");
  }

  function setStatus(msg) {
    if (!el.status) return;
    if (!msg) {
      el.status.classList.add("is-off");
      el.status.textContent = "";
      return;
    }
    el.status.classList.remove("is-off");
    el.status.textContent = msg;
  }

  function openInMaps(lat, lon, label) {
    const la = Number(lat);
    const lo = Number(lon);
    if (!Number.isFinite(la) || !Number.isFinite(lo)) return;
    const name = (label || la + ", " + lo).trim();
    const q = encodeURIComponent(name);
    const ll = la + "," + lo;
    const pinQ = encodeURIComponent(ll + " (" + name + ")");
    const ua = navigator.userAgent || "";
    const isiOS =
      /iPad|iPhone|iPod/i.test(ua) ||
      (navigator.platform === "MacIntel" && (navigator.maxTouchPoints || 0) > 1);
    const isAndroid = /Android/i.test(ua);

    if (isAndroid) {
      window.location.href = "geo:" + la + "," + lo + "?q=" + pinQ;
      return;
    }

    if (isiOS) {
      const gmaps = "comgooglemaps://?q=" + pinQ + "&zoom=16";
      const apple = "maps://?ll=" + ll + "&q=" + q;
      let handedOff = false;
      let timer = 0;
      function cleanup() {
        document.removeEventListener("visibilitychange", onHide);
        window.removeEventListener("pagehide", onHide);
        window.removeEventListener("blur", onHide);
        if (timer) {
          window.clearTimeout(timer);
          timer = 0;
        }
      }
      function onHide() {
        handedOff = true;
        cleanup();
      }
      document.addEventListener("visibilitychange", onHide);
      window.addEventListener("pagehide", onHide);
      window.addEventListener("blur", onHide);
      window.location.href = gmaps;
      timer = window.setTimeout(function () {
        cleanup();
        if (handedOff || document.hidden || document.visibilityState === "hidden") {
          return;
        }
        window.location.href = apple;
      }, 2200);
      return;
    }

    window.open(
      "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(ll),
      "_blank",
      "noopener,noreferrer"
    );
  }

  function chipDim(hex) {
    const n = (hex || "").replace("#", "");
    const r = parseInt(n.slice(0, 2), 16);
    const g = parseInt(n.slice(2, 4), 16);
    const b = parseInt(n.slice(4, 6), 16);
    if (Number.isNaN(r)) return "rgba(232,168,56,0.2)";
    return "rgba(" + r + "," + g + "," + b + ",0.2)";
  }

  function paintMode() {
    const mapped = state.mode === "mapped";
    if (el.modeMapped) el.modeMapped.setAttribute("aria-pressed", mapped ? "true" : "false");
    if (el.modeCensus) el.modeCensus.setAttribute("aria-pressed", mapped ? "false" : "true");
  }

  function hash01(str, i) {
    let h = 2166136261 >>> 0;
    const s = String(str);
    for (let k = 0; k < s.length; k++) {
      h ^= s.charCodeAt(k);
      h = Math.imul(h, 16777619);
    }
    h ^= (i + 0x9e3779b9) >>> 0;
    h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
    h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
  }

  function jitterAround(lat, lon, key, i) {
    const u = hash01(key, i);
    const v = hash01(key, i + 7919);
    const ang = u * Math.PI * 2;
    const dist = 0.01 + v * 0.045;
    const cos = Math.max(0.2, Math.cos((lat * Math.PI) / 180));
    return {
      a: lat + Math.sin(ang) * dist,
      o: lon + (Math.cos(ang) * dist) / cos,
    };
  }

  function buildCensusPlaces() {
    if (!state.census || !state.mappedPlaces.length) {
      state.censusPlaces = [];
      return;
    }
    const byCountyRel = Array.from({ length: RELIGIONS.length }, () => Object.create(null));
    const byStateRel = Array.from({ length: RELIGIONS.length }, () => Object.create(null));
    const mappedCount = Array.from({ length: RELIGIONS.length }, () => Object.create(null));
    for (let i = 0; i < state.mappedPlaces.length; i++) {
      const p = state.mappedPlaces[i];
      const r = p.r;
      if (r < 0 || r >= RELIGIONS.length) continue;
      const ck = countyLookupKey(p.s, p.c);
      if (!byCountyRel[r][ck]) byCountyRel[r][ck] = [];
      byCountyRel[r][ck].push(p);
      mappedCount[r][ck] = (mappedCount[r][ck] || 0) + 1;
      if (!byStateRel[r][p.s]) byStateRel[r][p.s] = [];
      byStateRel[r][p.s].push(p);
    }

    // Keep every Mapped pin; only synthesize the census surplus per county × religion.
    const out = state.mappedPlaces.slice();
    const counties = state.census.c || [];
    for (let ci = 0; ci < counties.length; ci++) {
      const key = counties[ci][0];
      const counts = counties[ci][1];
      const pipe = key.indexOf("|");
      const st = pipe >= 0 ? key.slice(0, pipe) : "";
      const cname = pipe >= 0 ? key.slice(pipe + 1) : key;
      const ck = countyLookupKey(st, cname);
      const pretty = String(cname || "")
        .toLowerCase()
        .replace(/\b[a-z]/g, function (ch) {
          return ch.toUpperCase();
        });
      for (let r = 0; r < RELIGIONS.length; r++) {
        const need = counts[r] || 0;
        const have = mappedCount[r][ck] || 0;
        const extra = need - have;
        if (extra <= 0) continue;
        const templates = byCountyRel[r][ck] || byStateRel[r][st] || [];
        for (let i = 0; i < extra; i++) {
          let lat = 39.8;
          let lon = -98.5;
          if (templates.length) {
            const t = templates[i % templates.length];
            lat = t.a;
            lon = t.o;
          }
          const j = jitterAround(lat, lon, ck + ":" + r + ":x", i);
          out.push({
            n: "Census congregation",
            r: r,
            a: j.a,
            o: j.o,
            s: st,
            c: pretty,
            y: "",
            census: true,
          });
        }
      }
    }
    state.censusPlaces = out;
  }

  function applyMode(mode) {
    const next = mode === "census" ? "census" : "mapped";
    if (next === "census" && state.census && !state.censusPlaces.length) {
      setStatus("Building census dots…");
      window.setTimeout(function () {
        buildCensusPlaces();
        state.mode = "census";
        state.places = state.censusPlaces;
        closeSheet();
        paintMode();
        render();
        recount();
        setStatus("");
      }, 30);
      return;
    }
    state.mode = next;
    state.places = state.mode === "census" ? state.censusPlaces : state.mappedPlaces;
    closeSheet();
    paintMode();
    render();
    recount();
  }

  function paintChips() {
    el.chips.innerHTML = RELIGIONS.map((r) => {
      const on = state.active.has(r.id);
      return (
        '<button type="button" class="chip" data-rel="' +
        r.id +
        '" aria-pressed="' +
        (on ? "true" : "false") +
        '" style="--chip:' +
        r.color +
        ";--chip-dim:" +
        chipDim(r.color) +
        '">' +
        r.label +
        "</button>"
      );
    }).join("");
  }

  function render() {
    if (!state.map || !state.canvas) return;
    const map = state.map;
    const canvas = state.canvas;
    const size = map.getContainer().getBoundingClientRect();
    const w = Math.max(1, Math.round(size.width));
    const h = Math.max(1, Math.round(size.height));
    const dpr = Math.max(1, Math.round(window.devicePixelRatio || 1));
    const bw = Math.round(w * dpr);
    const bh = Math.round(h * dpr);
    if (canvas.width !== bw || canvas.height !== bh) {
      canvas.width = bw;
      canvas.height = bh;
      canvas.style.width = w + "px";
      canvas.style.height = h + "px";
    }
    const ctx = canvas.getContext("2d", { alpha: true });
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const z = map.getZoom();
    const zMin = map.getMinZoom();
    const zMax = map.getMaxZoom();
    const t = Math.max(0, Math.min(1, (z - zMin) / Math.max(1e-6, zMax - zMin)));
    const ease = t * t * (3 - 2 * t);
    const s = Math.max(1, Math.round((0.1 + (4 - 0.1) * ease) * dpr));
    const pad = s + 1;
    const b = map.getBounds();
    const west = b.getWest();
    const east = b.getEast();
    const south = b.getSouth();
    const north = b.getNorth();
    const buckets = [[], [], [], [], [], []];
    for (let i = 0; i < state.places.length; i++) {
      const p = state.places[i];
      const rel = RELIGIONS[p.r];
      if (!rel || !state.active.has(rel.id)) continue;
      if (p.o < west || p.o > east || p.a < south || p.a > north) continue;
      buckets[p.r].push(p);
    }
    for (let r = 0; r < RELIGIONS.length; r++) {
      const pts = buckets[r];
      if (!pts.length) continue;
      ctx.fillStyle = RELIGIONS[r].color;
      for (let i = 0; i < pts.length; i++) {
        const p = pts[i];
        const pt = map.project([p.o, p.a]);
        const x = Math.round(pt.x * dpr);
        const y = Math.round(pt.y * dpr);
        if (x < -pad || y < -pad || x > canvas.width + pad || y > canvas.height + pad) continue;
        ctx.fillRect(x, y, s, s);
      }
    }
  }

  function placeAtClick(point) {
    if (!state.map) return null;
    const hit = state.focusState ? 6 : 10;
    let best = null;
    let bestD = hit * hit;
    const b = state.map.getBounds();
    for (let i = 0; i < state.places.length; i++) {
      const p = state.places[i];
      const rel = RELIGIONS[p.r];
      if (!rel || !state.active.has(rel.id)) continue;
      if (p.o < b.getWest() || p.o > b.getEast() || p.a < b.getSouth() || p.a > b.getNorth())
        continue;
      const pt = state.map.project([p.o, p.a]);
      const dx = pt.x - point.x;
      const dy = pt.y - point.y;
      const d = dx * dx + dy * dy + (p.census ? 36 : 0);
      if (d < bestD) {
        bestD = d;
        best = p;
      }
    }
    return best;
  }

  function openSheet(place) {
    state.selected = place;
    if (place && place.s) state.focusState = place.s;
    if (place && place.s && place.c) {
      state.focusCounty = { s: place.s, c: place.c, id: null };
    }
    const rel = RELIGIONS[place.r];
    if (place.census) {
      el.sheetName.textContent = "Census placement";
      el.sheetRel.textContent = rel ? rel.label : "";
      el.sheetRel.style.color = rel ? rel.color : "";
      el.sheetWhere.textContent =
        (place.c ? place.c + " County" : "County") +
        (place.s ? " · " + place.s : "") +
        " · not a street address";
      el.sheetMaps.classList.add("hidden");
    } else {
      el.sheetName.textContent = place.n || "Unnamed";
      el.sheetRel.textContent = rel ? rel.label : "";
      el.sheetRel.style.color = rel ? rel.color : "";
      const bits = [place.y, place.c ? place.c + " County" : "", place.s].filter(Boolean);
      el.sheetWhere.textContent = bits.join(" · ");
      el.sheetMaps.classList.remove("hidden");
      el.sheetMaps.href =
        "https://www.google.com/maps/search/?api=1&query=" +
        encodeURIComponent(place.a + "," + place.o);
      el.sheetMaps.setAttribute("data-lat", String(place.a));
      el.sheetMaps.setAttribute("data-lon", String(place.o));
      el.sheetMaps.setAttribute("data-label", place.n || "");
    }
    el.sheet.classList.remove("hidden");
    recount();
  }

  function closeSheet() {
    state.selected = null;
    el.sheet.classList.add("hidden");
    recount();
  }

  function recount() {
    const activeIdx = new Set();
    RELIGIONS.forEach((r, i) => {
      if (state.active.has(r.id)) activeIdx.add(i);
    });
    let us = 0;
    const byState = Object.create(null);
    const byCounty = Object.create(null);

    if (state.mode === "census" && state.census) {
      const usArr = state.census.us || [];
      for (const i of activeIdx) us += usArr[i] || 0;
      const st = state.census.s || {};
      for (const abbr in st) {
        let n = 0;
        for (const i of activeIdx) n += st[abbr][i] || 0;
        if (n) byState[abbr] = n;
      }
      const counties = state.census.c || [];
      for (let ci = 0; ci < counties.length; ci++) {
        const key = counties[ci][0];
        const vals = counties[ci][1];
        let n = 0;
        for (const i of activeIdx) n += vals[i] || 0;
        if (!n) continue;
        const pipe = key.indexOf("|");
        const stAbbr = pipe >= 0 ? key.slice(0, pipe) : "";
        const cname = pipe >= 0 ? key.slice(pipe + 1) : key;
        byCounty[countyLookupKey(stAbbr, cname)] = n;
      }
    } else {
      for (const p of state.places) {
        if (!activeIdx.has(p.r)) continue;
        us += 1;
        byState[p.s] = (byState[p.s] || 0) + 1;
        if (p.c) {
          byCounty[countyLookupKey(p.s, p.c)] = (byCounty[countyLookupKey(p.s, p.c)] || 0) + 1;
        }
      }
    }

    /* One total: pin/county tap → county, state tap → state, else US. */
    let lab = "US";
    let val = fmt(us);
    if (state.selected && state.selected.c && state.selected.s) {
      lab = state.selected.c;
      val = fmt(byCounty[countyLookupKey(state.selected.s, state.selected.c)] || 0);
    } else if (state.focusCounty && state.focusCounty.s && state.focusCounty.c) {
      lab = state.focusCounty.c;
      val = fmt(byCounty[countyLookupKey(state.focusCounty.s, state.focusCounty.c)] || 0);
    } else if (state.focusState) {
      lab = state.focusState;
      val = fmt(byState[state.focusState] || 0);
    }
    if (el.totLab) el.totLab.textContent = lab;
    if (el.totVal) el.totVal.textContent = val;
    const atNation = !state.focusCounty && !state.focusState;
    setCountyOutline(state.focusCounty);
    setStateOutline(state.focusCounty ? null : state.focusState);
    setNationOutline(atNation);
  }

  function setNationOutline(on) {
    const next = !!on;
    if (
      next === state.outlineNation &&
      state.map &&
      state.map.getLayer("wo-nation-hl")
    ) {
      return;
    }
    state.outlineNation = next;
    if (!state.map || !state.map.getLayer("wo-nation-hl")) return;
    state.map.setLayoutProperty(
      "wo-nation-hl",
      "visibility",
      next ? "visible" : "none"
    );
  }

  function setStateOutline(abbr) {
    const next = abbr && /^[A-Z]{2}$/.test(abbr) ? abbr : null;
    if (
      next === state.outlineState &&
      state.map &&
      state.map.getLayer("wo-state-hl")
    ) {
      return;
    }
    state.outlineState = next;
    if (!state.map || !state.map.getLayer("wo-state-hl")) return;
    state.map.setFilter(
      "wo-state-hl",
      next ? ["==", ["get", "s"], next] : ["==", ["get", "s"], "__none__"]
    );
  }

  function setCountyOutline(co) {
    const next = co && co.s && co.c ? co : null;
    const sig = next ? (next.id || "") + "|" + next.s + "|" + normCountyName(next.c) : null;
    if (
      sig === state.outlineCounty &&
      state.map &&
      state.map.getLayer("wo-county-hl")
    ) {
      return;
    }
    state.outlineCounty = sig;
    if (!state.map || !state.map.getLayer("wo-county-hl")) return;
    let filter = ["==", ["get", "id"], "__none__"];
    if (next) {
      if (next.id) {
        filter = ["==", ["get", "id"], String(next.id)];
      } else {
        filter = [
          "all",
          ["==", ["get", "s"], next.s],
          ["==", ["downcase", ["get", "c"]], String(next.c).toLowerCase()],
        ];
      }
    }
    state.map.setFilter("wo-county-hl", filter);
  }

  function scheduleTotals() {
    clearTimeout(state.totalsTimer);
    state.totalsTimer = setTimeout(recount, 120);
  }

  function fit(bounds) {
    if (!state.map) return;
    state.map.resize();
    /* CONUS stored as Leaflet-style [[lat,lng],[lat,lng]] → MapLibre [lng,lat]. */
    const sw = [bounds[0][1], bounds[0][0]];
    const ne = [bounds[1][1], bounds[1][0]];
    state.map.fitBounds([sw, ne], { padding: 20, animate: false });
  }

  function refit() {
    fit(CONUS);
  }

  function brightenAdminLines(style) {
    const layers = style.layers || [];
    for (let i = 0; i < layers.length; i++) {
      const layer = layers[i];
      const id = layer.id || "";
      if (!layer.paint) continue;
      if (id === "boundary_state") {
        /* Stock style hides states until z4 and dashes them — undo both. */
        layer.minzoom = 2;
        delete layer.paint["line-dasharray"];
        layer.paint["line-color"] = "#d4dbe6";
        layer.paint["line-opacity"] = 1;
        layer.paint["line-width"] = 0.5;
        layer.layout = Object.assign({}, layer.layout || {}, {
          "line-cap": "round",
          "line-join": "round",
        });
      } else if (id === "boundary_country_inner") {
        layer.minzoom = 0;
        delete layer.paint["line-dasharray"];
        layer.paint["line-color"] = "#e8ecf2";
        layer.paint["line-opacity"] = 1;
        layer.paint["line-width"] = 0.7;
      } else if (id === "boundary_country_outline") {
        layer.paint["line-opacity"] = 0.22;
      } else if (id === "boundary_county") {
        /* Carto tiles omit counties until z9 — hide and draw our own earlier. */
        layer.layout = Object.assign({}, layer.layout || {}, {
          visibility: "none",
        });
      }
    }
    return style;
  }

  function cartoTransformRequest(url) {
    if (!CARTO_KEY) return { url: url };
    if (url.indexOf("cartocdn.com") === -1) return { url: url };
    if (url.indexOf("key=") !== -1) return { url: url };
    return {
      url: url + (url.indexOf("?") >= 0 ? "&" : "?") + "key=" + encodeURIComponent(CARTO_KEY),
    };
  }

  function addEarlyCountyLines(map) {
    if (!map || map.getSource("wo-counties")) return;
    map.addSource("wo-counties", {
      type: "geojson",
      data: "/geo/counties.geojson",
    });
    map.addLayer({
      id: "wo-county-fill",
      type: "fill",
      source: "wo-counties",
      minzoom: 5,
      paint: {
        "fill-color": "#000",
        "fill-opacity": 0,
      },
    });
    map.addLayer({
      id: "wo-counties-line",
      type: "line",
      source: "wo-counties",
      minzoom: 5,
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
      paint: {
        "line-color": "#c5d0e0",
        "line-opacity": 0.9,
        "line-width": 0.4,
      },
    });
    map.addLayer({
      id: "wo-county-hl",
      type: "line",
      source: "wo-counties",
      filter: ["==", ["get", "id"], "__none__"],
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
      paint: {
        "line-color": "#3b82f6",
        "line-opacity": 1,
        "line-width": 1.8,
      },
    });
  }

  function addNationHighlight(map) {
    if (!map || map.getSource("wo-usa")) return;
    map.addSource("wo-usa", {
      type: "geojson",
      data: "/geo/usa.geojson",
    });
    map.addLayer({
      id: "wo-nation-hl",
      type: "line",
      source: "wo-usa",
      layout: {
        visibility: "none",
        "line-cap": "round",
        "line-join": "round",
      },
      paint: {
        "line-color": "#3b82f6",
        "line-opacity": 1,
        "line-width": 1.8,
      },
    });
  }

  function addStateHighlight(map) {
    if (!map || map.getSource("wo-states")) return;
    map.addSource("wo-states", {
      type: "geojson",
      data: "/geo/states.geojson",
    });
    /* Invisible fill so taps can hit a state body, not just the outline. */
    map.addLayer({
      id: "wo-state-fill",
      type: "fill",
      source: "wo-states",
      paint: {
        "fill-color": "#000",
        "fill-opacity": 0,
      },
    });
    map.addLayer({
      id: "wo-state-hl",
      type: "line",
      source: "wo-states",
      filter: ["==", ["get", "s"], "__none__"],
      layout: {
        "line-cap": "round",
        "line-join": "round",
      },
      paint: {
        "line-color": "#3b82f6",
        "line-opacity": 1,
        "line-width": 1.8,
      },
    });
  }

  function stateAtClick(point) {
    if (!state.map || !state.map.getLayer("wo-state-fill")) return null;
    const hits = state.map.queryRenderedFeatures(point, {
      layers: ["wo-state-fill"],
    });
    if (!hits.length) return null;
    const abbr = hits[0].properties && hits[0].properties.s;
    return abbr && /^[A-Z]{2}$/.test(abbr) ? abbr : null;
  }

  function countyAtClick(point) {
    if (!state.map || !state.map.getLayer("wo-county-fill")) return null;
    const hits = state.map.queryRenderedFeatures(point, {
      layers: ["wo-county-fill"],
    });
    if (!hits.length) return null;
    const pr = hits[0].properties || {};
    if (!pr.s || !pr.c) return null;
    return { id: pr.id ? String(pr.id) : null, s: pr.s, c: pr.c };
  }

  function sameCounty(a, b) {
    if (!a || !b) return false;
    if (a.id && b.id) return String(a.id) === String(b.id);
    return a.s === b.s && normCountyName(a.c) === normCountyName(b.c);
  }

  function clearFocusToNation() {
    state.focusCounty = null;
    state.focusState = null;
    closeSheet();
  }

  function toggleFocusState(abbr) {
    if (state.focusState === abbr && !state.focusCounty) {
      clearFocusToNation();
      return;
    }
    state.focusCounty = null;
    state.focusState = abbr;
    if (state.selected && state.selected.s !== abbr) closeSheet();
    else recount();
  }

  function toggleFocusCounty(co) {
    if (sameCounty(state.focusCounty, co)) {
      clearFocusToNation();
      return;
    }
    state.focusCounty = co;
    state.focusState = co.s;
    if (
      state.selected &&
      (state.selected.s !== co.s ||
        normCountyName(state.selected.c) !== normCountyName(co.c))
    ) {
      closeSheet();
    } else recount();
  }

  async function initMap() {
    if (!window.maplibregl) throw new Error("MapLibre missing");
    const res = await fetch(CARTO_STYLE, { cache: "force-cache" });
    if (!res.ok) throw new Error("style " + res.status);
    const style = brightenAdminLines(await res.json());

    state.map = new maplibregl.Map({
      container: "map",
      style: style,
      minZoom: 2,
      maxZoom: 18,
      attributionControl: false,
      fadeDuration: 0,
      dragRotate: false,
      pitchWithRotate: false,
      rollEnabled: false,
      transformRequest: cartoTransformRequest,
    });

    const canvas = document.createElement("canvas");
    canvas.className = "wo-dots";
    canvas.setAttribute("aria-hidden", "true");
    /* Sibling overlay on the map container — reproject every map paint so
       dots stay locked to the basemap (CSS-transform riding does not). */
    state.map.getContainer().appendChild(canvas);
    state.canvas = canvas;

    await new Promise(function (resolve, reject) {
      state.map.once("load", resolve);
      state.map.once("error", function (ev) {
        reject((ev && ev.error) || new Error("map error"));
      });
    });

    addEarlyCountyLines(state.map);
    addStateHighlight(state.map);
    addNationHighlight(state.map);
    if (state.outlineState) {
      const cur = state.outlineState;
      state.outlineState = null;
      setStateOutline(cur);
    }
    if (state.focusCounty) {
      const cur = state.focusCounty;
      state.outlineCounty = null;
      setCountyOutline(cur);
    }
    setNationOutline(!state.focusState && !state.focusCounty);
    fit(CONUS);

    /* Draw in the same turn as MapLibre's paint — RAF here is what made
       pins trail the basemap by a frame. */
    state.map.on("render", render);
    state.map.on("moveend", scheduleTotals);
    state.map.on("zoomend", scheduleTotals);
    state.map.on("click", function (ev) {
      const z = state.map.getZoom();
      const countyOk = z >= 5;
      const pinOk = z >= 7;
      const co = countyOk ? countyAtClick(ev.point) : null;
      const st = stateAtClick(ev.point);

      if (co && sameCounty(state.focusCounty, co)) {
        toggleFocusCounty(co);
        return;
      }
      if (!co && st && state.focusState === st && !state.focusCounty) {
        toggleFocusState(st);
        return;
      }
      if (pinOk) {
        const place = placeAtClick(ev.point);
        if (place) {
          openSheet(place);
          return;
        }
      }
      if (co) {
        toggleFocusCounty(co);
        return;
      }
      if (st) {
        toggleFocusState(st);
        return;
      }
      clearFocusToNation();
    });
    window.addEventListener("resize", function () {
      setTimeout(function () {
        if (!state.map) return;
        state.map.resize();
        if (state.map.getZoom() <= 5) refit();
      }, 200);
    });
    window.addEventListener("orientationchange", function () {
      setTimeout(function () {
        if (!state.map) return;
        state.map.resize();
        if (state.map.getZoom() <= 5) refit();
      }, 280);
    });
  }

  function wire() {
    el.chips.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-rel]");
      if (!btn) return;
      const id = btn.getAttribute("data-rel");
      if (state.active.has(id)) {
        state.active.delete(id);
      } else {
        state.active.add(id);
      }
      if (state.selected) {
        const rel = RELIGIONS[state.selected.r];
        if (!rel || !state.active.has(rel.id)) closeSheet();
      }
      paintChips();
      render();
      recount();
    });
    if (el.modeMapped) {
      el.modeMapped.addEventListener("click", () => applyMode("mapped"));
    }
    if (el.modeCensus) {
      el.modeCensus.addEventListener("click", () => applyMode("census"));
    }
    const tot = document.querySelector(".totals .tot");
    if (tot) {
      tot.style.cursor = "pointer";
      tot.title = "US total";
      tot.addEventListener("click", function () {
        if (state.focusState || state.focusCounty || state.selected) {
          clearFocusToNation();
        } else {
          refit();
          recount();
        }
      });
    }
    el.sheetClose.addEventListener("click", closeSheet);
    el.sheetMaps.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (state.selected && state.selected.census) return;
      openInMaps(
        el.sheetMaps.getAttribute("data-lat"),
        el.sheetMaps.getAttribute("data-lon"),
        el.sheetMaps.getAttribute("data-label") || ""
      );
    });
    el.aboutBtn.addEventListener("click", () => {
      const hidden = el.about.classList.toggle("hidden");
      el.aboutBtn.setAttribute("aria-expanded", hidden ? "false" : "true");
    });
    el.aboutClose.addEventListener("click", () => {
      el.about.classList.add("hidden");
      el.aboutBtn.setAttribute("aria-expanded", "false");
    });
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        closeSheet();
        el.about.classList.add("hidden");
        el.aboutBtn.setAttribute("aria-expanded", "false");
      }
    });
  }

  async function load() {
    setStatus("Loading places…");
    const res = await fetch("/data/places.json?v=1", { cache: "no-store" });
    if (!res.ok) throw new Error("data " + res.status);
    const payload = await res.json();
    state.meta = payload.meta || {};
    state.mappedPlaces = (payload.p || []).map((row) => {
      if (Array.isArray(row)) {
        return {
          n: row[0],
          r: row[1],
          a: row[2],
          o: row[3],
          s: row[4],
          c: row[5],
          y: row[6],
        };
      }
      return row;
    });
    state.places = state.mappedPlaces;

    try {
      const cres = await fetch("/data/census.json?v=1", { cache: "no-store" });
      if (cres.ok) {
        state.census = await cres.json();
      }
    } catch (err) {
      console.warn("census load failed", err);
    }

    if (el.aboutSrc) {
      const bits = [];
      if (state.meta.source) bits.push(state.meta.source);
      if (state.meta.built) bits.push("built " + state.meta.built);
      bits.push(fmt(state.meta.n || state.mappedPlaces.length) + " mapped");
      if (state.census && state.census.us) {
        bits.push(fmt(state.census.us.reduce(function (a, b) { return a + b; }, 0)) + " census");
      }
      el.aboutSrc.textContent = bits.join(" · ");
    }
    paintMode();
  }

  async function start() {
    if (el.mapBadge) el.mapBadge.textContent = APP_VERSION;
    if (el.verLabel) el.verLabel.textContent = APP_VERSION;
    paintChips();
    paintMode();
    wire();
    try {
      await initMap();
    } catch (err) {
      console.error(err);
      setStatus("Map failed to load.");
      return;
    }
    try {
      await load();
    } catch (err) {
      setStatus("No places file yet. Still building data.");
      return;
    }
    render();
    recount();
    setStatus("");
  }

  start();
})();
