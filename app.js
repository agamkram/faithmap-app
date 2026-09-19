/* FaithMap — US houses of worship */
(function () {
  "use strict";

  const APP_VERSION = "v39";
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
    totUs: document.getElementById("tot-us"),
    totState: document.getElementById("tot-state"),
    totCounty: document.getElementById("tot-county"),
    labState: document.getElementById("lab-state"),
    labCounty: document.getElementById("lab-county"),
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
  };

  const state = {
    active: new Set(["christian"]),
    places: [],
    meta: null,
    map: null,
    totalsTimer: 0,
    selected: null,
    canvas: null,
  };

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
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = w + "px";
    canvas.style.height = h + "px";
    const ctx = canvas.getContext("2d", { alpha: true });
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const z = map.getZoom();
    const zMin = map.getMinZoom();
    const zMax = map.getMaxZoom();
    const t = Math.max(0, Math.min(1, (z - zMin) / Math.max(1e-6, zMax - zMin)));
    const ease = t * t * (3 - 2 * t);
    const s = Math.max(1, Math.round((0.6 + 2.9 * ease) * dpr));
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
    const hit = 10;
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
      const d = dx * dx + dy * dy;
      if (d < bestD) {
        bestD = d;
        best = p;
      }
    }
    return best;
  }

  function openSheet(place) {
    state.selected = place;
    const rel = RELIGIONS[place.r];
    el.sheetName.textContent = place.n || "Unnamed";
    el.sheetRel.textContent = rel ? rel.label : "";
    el.sheetRel.style.color = rel ? rel.color : "";
    const bits = [place.y, place.c ? place.c + " County" : "", place.s].filter(Boolean);
    el.sheetWhere.textContent = bits.join(" · ");
    el.sheetMaps.href =
      "https://www.google.com/maps/search/?api=1&query=" +
      encodeURIComponent(place.a + "," + place.o);
    el.sheetMaps.setAttribute("data-lat", String(place.a));
    el.sheetMaps.setAttribute("data-lon", String(place.o));
    el.sheetMaps.setAttribute("data-label", place.n || "");
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
    for (const p of state.places) {
      if (!activeIdx.has(p.r)) continue;
      us += 1;
      byState[p.s] = (byState[p.s] || 0) + 1;
      if (p.c) {
        const key = p.s + "|" + p.c;
        byCounty[key] = (byCounty[key] || 0) + 1;
      }
    }
    el.totUs.textContent = fmt(us);

    let stateName = "State";
    let stateCount = "—";
    let countyName = "County";
    let countyCount = "—";

    if (state.selected) {
      const p = state.selected;
      stateName = p.s || "State";
      stateCount = fmt(byState[p.s] || 0);
      if (p.c) {
        countyName = p.c;
        countyCount = fmt(byCounty[p.s + "|" + p.c] || 0);
      }
    } else if (state.map) {
      const z = state.map.getZoom();
      const b = state.map.getBounds();
      const west = b.getWest();
      const east = b.getEast();
      const south = b.getSouth();
      const north = b.getNorth();
      const visState = Object.create(null);
      const visCounty = Object.create(null);
      for (const p of state.places) {
        if (!activeIdx.has(p.r)) continue;
        if (p.o < west || p.o > east || p.a < south || p.a > north) continue;
        visState[p.s] = (visState[p.s] || 0) + 1;
        if (p.c) {
          const key = p.s + "|" + p.c;
          visCounty[key] = (visCounty[key] || 0) + 1;
        }
      }
      if (z >= 5) {
        let topS = null;
        let topN = 0;
        for (const k in visState) {
          if (visState[k] > topN) {
            topN = visState[k];
            topS = k;
          }
        }
        if (topS) {
          stateName = topS;
          stateCount = fmt(byState[topS] || visState[topS]);
        }
      }
      if (z >= 7) {
        let topC = null;
        let topN = 0;
        for (const k in visCounty) {
          if (visCounty[k] > topN) {
            topN = visCounty[k];
            topC = k;
          }
        }
        if (topC) {
          const parts = topC.split("|");
          countyName = parts[1];
          countyCount = fmt(byCounty[topC] || visCounty[topC]);
        }
      }
    }

    el.labState.textContent = stateName;
    el.totState.textContent = stateCount;
    el.labCounty.textContent = countyName;
    el.totCounty.textContent = countyCount;
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
      attributionControl: true,
      fadeDuration: 0,
      dragRotate: false,
      pitchWithRotate: false,
      rollEnabled: false,
      transformRequest: cartoTransformRequest,
    });

    const canvas = document.createElement("canvas");
    canvas.className = "wo-dots";
    canvas.setAttribute("aria-hidden", "true");
    state.map.getContainer().appendChild(canvas);
    state.canvas = canvas;

    await new Promise(function (resolve, reject) {
      state.map.once("load", resolve);
      state.map.once("error", function (ev) {
        reject((ev && ev.error) || new Error("map error"));
      });
    });

    addEarlyCountyLines(state.map);
    fit(CONUS);

    let renderRaf = 0;
    function scheduleRender() {
      if (renderRaf) return;
      renderRaf = requestAnimationFrame(function () {
        renderRaf = 0;
        render();
      });
    }
    state.map.on("move", scheduleRender);
    state.map.on("zoom", scheduleRender);
    state.map.on("moveend", function () {
      scheduleRender();
      scheduleTotals();
    });
    state.map.on("zoomend", function () {
      scheduleRender();
      scheduleTotals();
    });
    state.map.on("click", function (ev) {
      const place = placeAtClick(ev.point);
      if (place) openSheet(place);
      else closeSheet();
    });
    window.addEventListener("resize", function () {
      setTimeout(function () {
        if (!state.map) return;
        state.map.resize();
        if (state.map.getZoom() <= 5) refit();
        else scheduleRender();
      }, 200);
    });
    window.addEventListener("orientationchange", function () {
      setTimeout(function () {
        if (!state.map) return;
        state.map.resize();
        if (state.map.getZoom() <= 5) refit();
        else scheduleRender();
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
    el.sheetClose.addEventListener("click", closeSheet);
    el.sheetMaps.addEventListener("click", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
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
    state.places = (payload.p || []).map((row) => {
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
    if (el.aboutSrc) {
      el.aboutSrc.textContent =
        (state.meta.source || "") +
        (state.meta.built ? " · built " + state.meta.built : "") +
        " · " +
        fmt(state.meta.n || state.places.length) +
        " mapped buildings";
    }
    if (el.sourceLine && state.meta.n) {
      el.sourceLine.textContent =
        fmt(state.meta.n) + " mapped 501(c)(3) buildings — not a census of congregations.";
    }
  }

  async function start() {
    if (el.mapBadge) el.mapBadge.textContent = APP_VERSION;
    if (el.verLabel) el.verLabel.textContent = APP_VERSION;
    paintChips();
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
