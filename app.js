/* FaithMap — US houses of worship */
(function () {
  "use strict";

  const APP_VERSION = "v97";
  window.__APP_VERSION = APP_VERSION;
  const ASSET_V = APP_VERSION.replace(/^v/, "");

  /* Continent stays a spec so Christian density does not blob.
     Size climbs as soon as you leave continent; street caps at 6. */
  const PIN_SIZE_STOPS = [
    [2, 0.1],
    [4, 1.0],
    [7, 2.8],
    [9, 3.8],
    [14, 5.7],
    [18, 6],
  ];

  function pinCssPx(z) {
    const stops = PIN_SIZE_STOPS;
    let css = stops[stops.length - 1][1];
    if (z <= stops[0][0]) {
      css = stops[0][1];
    } else {
      for (let i = 1; i < stops.length; i++) {
        if (z <= stops[i][0]) {
          const z0 = stops[i - 1][0];
          const v0 = stops[i - 1][1];
          const span = stops[i][0] - z0;
          const u = span ? (z - z0) / span : 1;
          css = v0 + (stops[i][1] - v0) * u;
          break;
        }
      }
    }
    return z >= 9 ? Math.max(3, css) : css;
  }

  /* iOS-full-bleed Bug B (GovDash copy): PWA fillH + iPad --pwa-extra-b. */
  let lastFillKey = "";
  let lastSafeInset = { w: 0, h: 0, v: 0 };

  function isStandaloneDisplay() {
    return (
      window.navigator.standalone === true ||
      window.matchMedia("(display-mode: standalone)").matches ||
      window.matchMedia("(display-mode: fullscreen)").matches ||
      window.matchMedia("(display-mode: minimal-ui)").matches
    );
  }

  function isTouchShell() {
    if (/iPad|iPhone|iPod/i.test(navigator.userAgent || "")) return true;
    if (navigator.platform === "MacIntel" && (navigator.maxTouchPoints || 0) > 1) {
      return true;
    }
    return window.matchMedia("(hover: none) and (pointer: coarse)").matches;
  }

  function isTabletShell() {
    const minSide = Math.min(window.innerWidth || 0, window.innerHeight || 0);
    if (minSide < 600) return false;
    if (/iPhone|iPod/i.test(navigator.userAgent || "")) return false;
    return isTouchShell();
  }

  function syncTabletClass() {
    document.documentElement.classList.toggle("is-tablet", isTabletShell());
  }

  function pwaFillHeightPx() {
    const iw = window.innerWidth || 0;
    const ih = window.innerHeight || 0;
    const sw = window.screen?.width || 0;
    const sh = window.screen?.height || 0;
    const screenMax = Math.max(sw, sh);
    const screenMin = Math.min(sw, sh);
    return ih >= iw ? Math.max(ih, screenMax) : Math.max(ih, screenMin);
  }

  function readSafeInsetBottom() {
    const w = window.innerWidth || 0;
    const h = window.innerHeight || 0;
    if (lastSafeInset.w === w && lastSafeInset.h === h) return lastSafeInset.v;
    if (!document.body) return 0;
    const probe = document.createElement("div");
    probe.style.cssText =
      "position:fixed;visibility:hidden;pointer-events:none;padding-bottom:env(safe-area-inset-bottom,0px)";
    document.body.appendChild(probe);
    const px = parseFloat(getComputedStyle(probe).paddingBottom) || 0;
    probe.remove();
    lastSafeInset = { w, h, v: px };
    return px;
  }

  function pwaExtraBottomPx() {
    const iw = window.innerWidth || 0;
    const ih = window.innerHeight || 0;
    const sw = window.screen?.width || 0;
    const sh = window.screen?.height || 0;
    const screenMax = Math.max(sw, sh);
    if (Math.min(iw, ih) >= 600 && screenMax < ih - 10) {
      return Math.max(readSafeInsetBottom(), 20);
    }
    return 0;
  }

  function pinShellViewport() {
    const root = document.documentElement;
    syncTabletClass();
    const standalone =
      isStandaloneDisplay() || root.classList.contains("pwa-standalone");
    if (!standalone) {
      root.classList.remove("pwa-standalone");
      root.style.removeProperty("--pwa-fill-h");
      root.style.removeProperty("--pwa-extra-b");
      root.style.removeProperty("height");
      root.style.removeProperty("min-height");
      lastFillKey = "";
      return;
    }
    const fillH = pwaFillHeightPx();
    const extra = pwaExtraBottomPx();
    const total = fillH + extra;
    const key = "pwa:" + fillH + "+" + extra;
    root.classList.add("pwa-standalone");
    if (key === lastFillKey) return;
    lastFillKey = key;
    root.style.setProperty("--pwa-fill-h", fillH + "px");
    root.style.setProperty("--pwa-extra-b", extra + "px");
    root.style.height = total + "px";
    root.style.minHeight = total + "px";
  }

  const CARTO_KEY = "cb1_27ow_1_73656a41346af19fc01d4d26";
  const CARTO_STYLE =
    "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

  const RELIGIONS = [
    { id: "christian", label: "Christian", color: "#ffd58a" },
    { id: "jewish", label: "Jewish", color: "#7eb6ff" },
    { id: "muslim", label: "Muslim", color: "#3dffa3" },
    { id: "hindu", label: "Hindu", color: "#ff7a3a" },
    { id: "buddhist", label: "Buddhist", color: "#c9a8ff" },
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
    stack: null,
    stackKey: null,
    stackIdx: 0,
    focusState: null,
    focusCounty: null,
    outlineState: null,
    outlineCounty: null,
    outlineNation: false,
    canvas: null,
    pinGrid: null,
    lastPinCam: "",
    modeGen: 0,
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

  function countyLookupKey(stAbbr, countyName, fips) {
    const id = fips != null && String(fips) !== "" ? String(fips) : "";
    if (id) return "f:" + id;
    return String(stAbbr || "") + "|" + normCountyName(countyName);
  }

  function placeCountyKey(place) {
    if (!place) return "";
    return countyLookupKey(place.s, place.c, place.f);
  }

  function focusCountyKey(co) {
    if (!co) return "";
    return countyLookupKey(co.s, co.c, co.id);
  }

  function invalidatePins() {
    state.pinGrid = null;
    state.lastPinCam = "";
  }

  const PIN_CELL = 0.2;

  function pinCellKey(lon, lat) {
    return Math.floor(lon / PIN_CELL) + ":" + Math.floor(lat / PIN_CELL);
  }

  function ensurePinGrid() {
    if (
      state.pinGrid &&
      state.pinGrid.n === state.places.length &&
      state.pinGrid.mode === state.mode
    ) {
      return state.pinGrid;
    }
    const cells = Object.create(null);
    for (let i = 0; i < state.places.length; i++) {
      const p = state.places[i];
      const k = pinCellKey(p.o, p.a);
      if (!cells[k]) cells[k] = [];
      cells[k].push(p);
    }
    state.pinGrid = {
      cells: cells,
      n: state.places.length,
      mode: state.mode,
    };
    return state.pinGrid;
  }

  function forEachViewportPlace(bounds, fn) {
    const grid = ensurePinGrid();
    const west = bounds.getWest();
    const east = bounds.getEast();
    const south = bounds.getSouth();
    const north = bounds.getNorth();
    const i0 = Math.floor(west / PIN_CELL);
    const i1 = Math.floor(east / PIN_CELL);
    const j0 = Math.floor(south / PIN_CELL);
    const j1 = Math.floor(north / PIN_CELL);
    for (let i = i0; i <= i1; i++) {
      for (let j = j0; j <= j1; j++) {
        const arr = grid.cells[i + ":" + j];
        if (!arr) continue;
        for (let k = 0; k < arr.length; k++) fn(arr[k], west, east, south, north);
      }
    }
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
    for (let i = 0; i < state.mappedPlaces.length; i++) {
      const p = state.mappedPlaces[i];
      const r = p.r;
      if (r < 0 || r >= RELIGIONS.length) continue;
      const ck = placeCountyKey(p);
      if (!byCountyRel[r][ck]) byCountyRel[r][ck] = [];
      byCountyRel[r][ck].push(p);
    }

    // Exactly `need` pins per county × religion: real Mapped first, then synthetics.
    // Excess Mapped pins are omitted in Census mode so pin count matches the badge.
    const out = [];
    const counties = state.census.c || [];
    for (let ci = 0; ci < counties.length; ci++) {
      const row = counties[ci];
      const rawKey = String(row[0] || "");
      const counts = row[1];
      let fips = /^\d{5}$/.test(rawKey) ? rawKey : "";
      let st = row[2] || "";
      let pretty = row[3] || "";
      if (!fips && rawKey.indexOf("|") >= 0) {
        const pipe = rawKey.indexOf("|");
        st = rawKey.slice(0, pipe);
        pretty = rawKey.slice(pipe + 1);
      }
      const ck = countyLookupKey(st, pretty, fips);
      for (let r = 0; r < RELIGIONS.length; r++) {
        const need = counts[r] || 0;
        if (need <= 0) continue;
        const candidates = byCountyRel[r][ck] || [];
        const take = Math.min(need, candidates.length);
        for (let i = 0; i < take; i++) out.push(candidates[i]);
        const extra = need - take;
        if (extra <= 0) continue;
        /* Stay inside the county: only jitter around Mapped pins in this FIPS. */
        if (!candidates.length) continue;
        for (let i = 0; i < extra; i++) {
          const t = candidates[i % candidates.length];
          const j = jitterAround(t.a, t.o, ck + ":" + r + ":x", i);
          out.push({
            n: "Census congregation",
            r: r,
            a: j.a,
            o: j.o,
            s: st || t.s,
            c: pretty || t.c,
            y: "",
            f: fips || t.f || "",
            census: true,
          });
        }
      }
    }
    state.censusPlaces = out;
  }

  function applyMode(mode) {
    const next = mode === "census" ? "census" : "mapped";
    if (next === "census" && !state.census) return;
    const gen = ++state.modeGen;
    if (next === "census" && state.census && !state.censusPlaces.length) {
      setStatus("Building census dots…");
      window.setTimeout(function () {
        if (gen !== state.modeGen) return;
        buildCensusPlaces();
        if (gen !== state.modeGen) return;
        state.mode = "census";
        state.places = state.censusPlaces;
        closeSheet();
        paintMode();
        invalidatePins();
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
    invalidatePins();
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
    const z = map.getZoom();
    const cssPx = pinCssPx(z);
    const s = Math.max(1, Math.round(cssPx * dpr));
    const center = map.getCenter();
    const cam =
      z.toFixed(4) +
      "|" +
      center.lng.toFixed(5) +
      "|" +
      center.lat.toFixed(5) +
      "|" +
      w +
      "x" +
      h +
      "|" +
      dpr +
      "|" +
      s +
      "|" +
      state.mode +
      "|" +
      state.places.length +
      "|" +
      Array.from(state.active).join(",");
    if (cam === state.lastPinCam) return;
    state.lastPinCam = cam;

    const ctx = canvas.getContext("2d", { alpha: true });
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const pad = s + 1;
    const b = map.getBounds();
    const buckets = [[], [], [], [], [], []];
    forEachViewportPlace(b, function (p, west, east, south, north) {
      const rel = RELIGIONS[p.r];
      if (!rel || !state.active.has(rel.id)) return;
      if (p.o < west || p.o > east || p.a < south || p.a > north) return;
      buckets[p.r].push(p);
    });
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
    /* Touch-sized target; do not shrink when a state is focused. */
    const hit = 24;
    let bestD = hit * hit;
    const b = state.map.getBounds();
    const near = [];
    forEachViewportPlace(b, function (p, westB, eastB, southB, northB) {
      const rel = RELIGIONS[p.r];
      if (!rel || !state.active.has(rel.id)) return;
      if (p.o < westB || p.o > eastB || p.a < southB || p.a > northB) return;
      const pt = state.map.project([p.o, p.a]);
      const dx = pt.x - point.x;
      const dy = pt.y - point.y;
      const d = dx * dx + dy * dy + (p.census ? 36 : 0);
      if (d <= hit * hit) near.push({ p: p, d: d });
      if (d < bestD) bestD = d;
    });
    if (!near.length) return null;
    /* Same IRS geocode often stacks many orgs on one lat/lon. Keep everyone
       tied for closest, then cycle on repeated taps at that spot. */
    const stack = near
      .filter(function (x) {
        return x.d <= bestD + 0.25;
      })
      .map(function (x) {
        return x.p;
      });
    const counts = Object.create(null);
    for (let i = 0; i < stack.length; i++) {
      const r = stack[i].r;
      counts[r] = (counts[r] || 0) + 1;
    }
    stack.sort(function (a, b) {
      const ca = counts[a.r] || 0;
      const cb = counts[b.r] || 0;
      /* Rarer religion in the stack first — otherwise 5 churches bury 1 synagogue. */
      if (ca !== cb) return ca - cb;
      if (a.r !== b.r) return a.r - b.r;
      return String(a.n || "").localeCompare(String(b.n || ""));
    });
    const key =
      stack[0].a.toFixed(5) + "," + stack[0].o.toFixed(5) + ":" + stack.length;
    if (state.stackKey === key && stack.length > 1) {
      state.stackIdx = (state.stackIdx + 1) % stack.length;
    } else {
      state.stackKey = key;
      state.stackIdx = 0;
    }
    state.stack = stack;
    return stack[state.stackIdx];
  }

  function openSheet(place) {
    state.selected = place;
    if (place && place.s) state.focusState = place.s;
    if (place && place.s && place.c) {
      state.focusCounty = { s: place.s, c: place.c, id: place.f ? String(place.f) : null };
    }
    const rel = RELIGIONS[place.r];
    const stackN = state.stack && state.stack.length > 1 ? state.stack.length : 0;
    const stackHint =
      stackN > 1
        ? " · " + (state.stackIdx + 1) + " of " + stackN + " here — tap again"
        : "";
    if (place.census) {
      el.sheetName.textContent = "Census placement";
      el.sheetRel.textContent = rel ? rel.label : "";
      el.sheetRel.style.color = rel ? rel.color : "";
      el.sheetWhere.textContent =
        [place.c, place.s].filter(Boolean).join(" · ") +
        " · not a street address" +
        stackHint;
      el.sheetMaps.classList.add("hidden");
    } else {
      el.sheetName.textContent = place.n || "Unnamed";
      el.sheetRel.textContent = rel ? rel.label : "";
      el.sheetRel.style.color = rel ? rel.color : "";
      const bits = [place.y, place.c, place.s].filter(Boolean);
      el.sheetWhere.textContent = bits.join(" · ") + stackHint;
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
    state.stack = null;
    state.stackKey = null;
    state.stackIdx = 0;
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
        const row = counties[ci];
        const rawKey = String(row[0] || "");
        const vals = row[1];
        let n = 0;
        for (const i of activeIdx) n += vals[i] || 0;
        if (!n) continue;
        let fips = /^\d{5}$/.test(rawKey) ? rawKey : "";
        let stAbbr = row[2] || "";
        let cname = row[3] || "";
        if (!fips && rawKey.indexOf("|") >= 0) {
          const pipe = rawKey.indexOf("|");
          stAbbr = rawKey.slice(0, pipe);
          cname = rawKey.slice(pipe + 1);
        }
        byCounty[countyLookupKey(stAbbr, cname, fips)] = n;
      }
    } else {
      for (const p of state.places) {
        if (!activeIdx.has(p.r)) continue;
        us += 1;
        byState[p.s] = (byState[p.s] || 0) + 1;
        if (p.c || p.f) {
          const ck = placeCountyKey(p);
          byCounty[ck] = (byCounty[ck] || 0) + 1;
        }
      }
    }

    /* One total: pin/county tap → county, state tap → state, else US. */
    let lab = "US";
    let val = fmt(us);
    if (state.selected && (state.selected.c || state.selected.f) && state.selected.s) {
      lab = state.selected.c || lab;
      val = fmt(byCounty[placeCountyKey(state.selected)] || 0);
    } else if (state.focusCounty && state.focusCounty.s && state.focusCounty.c) {
      lab = state.focusCounty.c;
      val = fmt(byCounty[focusCountyKey(state.focusCounty)] || 0);
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
        layer.paint["line-width"] = 0.4;
        layer.layout = Object.assign({}, layer.layout || {}, {
          "line-cap": "round",
          "line-join": "round",
        });
      } else if (id === "boundary_country_inner") {
        layer.minzoom = 0;
        delete layer.paint["line-dasharray"];
        layer.paint["line-color"] = "#e8ecf2";
        layer.paint["line-opacity"] = 1;
        layer.paint["line-width"] = 0.5;
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
      data: "/geo/counties.geojson?v=" + ASSET_V,
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
        "line-width": 0.3,
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
        "line-width": 1.2,
      },
    });
  }

  function addNationHighlight(map) {
    if (!map || map.getSource("wo-usa")) return;
    map.addSource("wo-usa", {
      type: "geojson",
      data: "/geo/usa.geojson?v=" + ASSET_V,
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
        "line-width": 1.2,
      },
    });
  }

  function addStateHighlight(map) {
    if (!map || map.getSource("wo-states")) return;
    map.addSource("wo-states", {
      type: "geojson",
      data: "/geo/states.geojson?v=" + ASSET_V,
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
        "line-width": 1.2,
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
      (state.selected.f && co.id
        ? String(state.selected.f) !== String(co.id)
        : state.selected.s !== co.s ||
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
      bearing: 0,
      pitch: 0,
      maxPitch: 0,
      attributionControl: false,
      fadeDuration: 0,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      rollEnabled: false,
      transformRequest: cartoTransformRequest,
    });
    /* Pan + zoom only — no twist / tilt (mouse or touch). */
    state.map.dragRotate.disable();
    state.map.touchZoomRotate.disableRotation();
    if (state.map.touchPitch) state.map.touchPitch.disable();

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
      const pinOk = z >= 9;
      const co = countyOk ? countyAtClick(ev.point) : null;
      const st = stateAtClick(ev.point);

      /* Pins win over county/state toggle — otherwise a focused county
         eats the first tap (toggle off) and the pin needs a second. */
      if (pinOk) {
        const place = placeAtClick(ev.point);
        if (place) {
          openSheet(place);
          return;
        }
      }
      if (co && sameCounty(state.focusCounty, co)) {
        toggleFocusCounty(co);
        return;
      }
      if (!co && st && state.focusState === st && !state.focusCounty) {
        toggleFocusState(st);
        return;
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
      pinShellViewport();
      setTimeout(function () {
        if (!state.map) return;
        state.map.resize();
        if (state.map.getZoom() <= 5) refit();
      }, 200);
    });
    window.addEventListener("orientationchange", function () {
      pinShellViewport();
      setTimeout(function () {
        if (!state.map) return;
        state.map.resize();
        if (state.map.getZoom() <= 5) refit();
      }, 280);
    });
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", function () {
        pinShellViewport();
        if (state.map) state.map.resize();
      });
    }
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
      state.stackKey = null;
      state.stackIdx = 0;
      state.stack = null;
      if (state.selected) {
        const rel = RELIGIONS[state.selected.r];
        if (!rel || !state.active.has(rel.id)) closeSheet();
      }
      paintChips();
      invalidatePins();
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
    const res = await fetch("/data/places.json?v=" + ASSET_V, { cache: "no-store" });
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
          f: row[7] ? String(row[7]) : "",
        };
      }
      return row;
    });
    state.places = state.mappedPlaces;

    try {
      const cres = await fetch("/data/census.json?v=" + ASSET_V, { cache: "no-store" });
      if (cres.ok) {
        state.census = await cres.json();
      }
    } catch (err) {
      console.warn("census load failed", err);
    }

    if (!state.census && el.modeCensus) {
      el.modeCensus.disabled = true;
      el.modeCensus.title = "Census file missing";
    }

    if (el.aboutSrc) {
      const bits = [];
      bits.push(fmt(state.meta.n || state.mappedPlaces.length) + " mapped");
      if (state.census && state.census.us) {
        bits.push(
          fmt(state.census.us.reduce(function (a, b) { return a + b; }, 0)) + " census"
        );
      }
      if (state.meta.built) bits.push("built " + state.meta.built);
      el.aboutSrc.textContent = bits.join(" · ");
    }
    paintMode();
  }

  async function start() {
    pinShellViewport();
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
      let last = null;
      for (let i = 0; i < 5; i++) {
        try {
          await load();
          last = null;
          break;
        } catch (err) {
          last = err;
          setStatus("Loading places…");
          await new Promise((r) => setTimeout(r, 1000 * (i + 1)));
        }
      }
      if (last) throw last;
    } catch (err) {
      console.error(err);
      setStatus("No places file yet. Still building data.");
      return;
    }
    pinShellViewport();
    if (state.map) state.map.resize();
    render();
    recount();
    setStatus("");
  }

  start();
})();
