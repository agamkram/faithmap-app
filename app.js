/* Worship — US houses of worship */
(function () {
  "use strict";

  const APP_VERSION = "v1";
  window.__APP_VERSION = APP_VERSION;

  const CARTO_KEY = "cb1_27ow_1_73656a41346af19fc01d4d26";
  const MAP_TILE_URL =
    "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" +
    (CARTO_KEY ? "?key=" + encodeURIComponent(CARTO_KEY) : "");
  const MAP_TILE_ATTR =
    '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>';

  const RELIGIONS = [
    { id: "christian", label: "Christian", color: "#d7c4a3" },
    { id: "jewish", label: "Jewish", color: "#7aa2ff" },
    { id: "muslim", label: "Muslim", color: "#3dba7a" },
    { id: "hindu", label: "Hindu", color: "#e07a3d" },
    { id: "buddhist", label: "Buddhist", color: "#e0c25c" },
    { id: "sikh", label: "Sikh", color: "#f08a1f" },
  ];

  const CONUS = [
    [24.5, -124.8],
    [49.4, -66.9],
  ];
  const AK = [
    [51.2, -179.1],
    [71.4, -129.9],
  ];
  const HI = [
    [18.9, -160.3],
    [22.3, -154.8],
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
    btnUs: document.getElementById("btn-us"),
    btnAk: document.getElementById("btn-ak"),
    btnHi: document.getElementById("btn-hi"),
  };

  const state = {
    active: new Set(RELIGIONS.map((r) => r.id)),
    places: [],
    meta: null,
    map: null,
    index: null,
    layer: null,
    totalsTimer: 0,
    frame: "us",
    selected: null,
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

  function mapsUrl(lat, lon, name) {
    const ll = lat + "," + lon;
    const isiOS =
      /iPad|iPhone|iPod/.test(navigator.userAgent) ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    if (isiOS) {
      return "https://maps.apple.com/?ll=" + ll + "&q=" + encodeURIComponent(name || "Place");
    }
    return "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(ll);
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
        '">' +
        r.label +
        "</button>"
      );
    }).join("");
  }

  function rebuildIndex() {
    const features = [];
    for (let i = 0; i < state.places.length; i++) {
      const p = state.places[i];
      const rel = RELIGIONS[p.r];
      if (!rel || !state.active.has(rel.id)) continue;
      features.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.o, p.a] },
        properties: { i: i, r: p.r },
      });
    }
    state.index = new Supercluster({
      radius: 58,
      maxZoom: 16,
      minPoints: 3,
      map: (props) => ({ r: props.r }),
      reduce: (acc, props) => {
        if (acc.r !== props.r) acc.r = -1;
      },
    });
    state.index.load(features);
  }

  function clusterColor(props) {
    if (props.i != null) return RELIGIONS[state.places[props.i].r].color;
    if (props.r >= 0 && RELIGIONS[props.r]) return RELIGIONS[props.r].color;
    return "#c8b48a";
  }

  function render() {
    if (!state.map || !state.index) return;
    const z = state.map.getZoom();
    const b = state.map.getBounds();
    const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
    const clusters = state.index.getClusters(bbox, Math.round(z));
    if (state.layer) state.layer.remove();
    state.layer = L.layerGroup();
    for (const c of clusters) {
      const [lon, lat] = c.geometry.coordinates;
      const props = c.properties;
      if (props.cluster) {
        const n = props.point_count;
        const size = n > 5000 ? 42 : n > 1000 ? 36 : n > 200 ? 30 : 24;
        const icon = L.divIcon({
          className: "",
          html:
            '<div class="wo-cluster" style="width:' +
            size +
            "px;height:" +
            size +
            "px;background:" +
            clusterColor(props) +
            ';font-size:' +
            (size > 30 ? 11 : 10) +
            'px">' +
            (n > 999 ? Math.round(n / 1000) + "k" : n) +
            "</div>",
          iconSize: [size, size],
        });
        const m = L.marker([lat, lon], { icon: icon, keyboard: false });
        m.on("click", () => {
          const next = Math.min(state.index.getClusterExpansionZoom(props.cluster_id), 16);
          state.map.setView([lat, lon], next);
        });
        state.layer.addLayer(m);
      } else {
        const place = state.places[props.i];
        const color = RELIGIONS[place.r].color;
        const icon = L.divIcon({
          className: "",
          html:
            '<div class="wo-dot" style="width:12px;height:12px;background:' +
            color +
            '"></div>',
          iconSize: [12, 12],
        });
        const m = L.marker([lat, lon], { icon: icon, keyboard: false });
        m.on("click", () => openSheet(place));
        state.layer.addLayer(m);
      }
    }
    state.layer.addTo(state.map);
  }

  function openSheet(place) {
    state.selected = place;
    const rel = RELIGIONS[place.r];
    el.sheetName.textContent = place.n || "Unnamed";
    el.sheetRel.textContent = rel ? rel.label : "";
    el.sheetRel.style.color = rel ? rel.color : "";
    const bits = [place.y, place.c ? place.c + " County" : "", place.s].filter(Boolean);
    el.sheetWhere.textContent = bits.join(" · ");
    el.sheetMaps.href = mapsUrl(place.a, place.o, place.n);
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

  function fit(bounds, frame) {
    if (!state.map) return;
    if (frame) state.frame = frame;
    state.map.invalidateSize();
    state.map.fitBounds(bounds, { padding: [20, 20], animate: false });
  }

  function refit() {
    if (state.frame === "ak") fit(AK);
    else if (state.frame === "hi") fit(HI);
    else fit(CONUS);
  }

  function initMap() {
    state.map = L.map("map", {
      zoomControl: false,
      attributionControl: true,
      minZoom: 3,
      maxZoom: 18,
      worldCopyJump: false,
    });
    L.tileLayer(MAP_TILE_URL, {
      attribution: MAP_TILE_ATTR,
      subdomains: "abcd",
      maxZoom: 19,
    }).addTo(state.map);
    L.control.zoom({ position: "topleft" }).addTo(state.map);
    fit(CONUS, "us");
    state.map.on("moveend zoomend", () => {
      render();
      scheduleTotals();
    });
    window.addEventListener("resize", () => {
      setTimeout(() => {
        if (!state.map) return;
        state.map.invalidateSize();
        if (state.map.getZoom() <= 5) refit();
        else render();
      }, 200);
    });
    window.addEventListener("orientationchange", () => {
      setTimeout(() => {
        if (!state.map) return;
        state.map.invalidateSize();
        if (state.map.getZoom() <= 5) refit();
        else render();
      }, 280);
    });
  }

  function wire() {
    el.chips.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-rel]");
      if (!btn) return;
      const id = btn.getAttribute("data-rel");
      if (state.active.has(id)) {
        if (state.active.size === 1) return;
        state.active.delete(id);
      } else {
        state.active.add(id);
      }
      paintChips();
      rebuildIndex();
      render();
      recount();
    });
    el.sheetClose.addEventListener("click", closeSheet);
    el.aboutBtn.addEventListener("click", () => {
      el.about.classList.toggle("hidden");
    });
    el.aboutClose.addEventListener("click", () => el.about.classList.add("hidden"));
    el.btnUs.addEventListener("click", () => fit(CONUS, "us"));
    el.btnAk.addEventListener("click", () => fit(AK, "ak"));
    el.btnHi.addEventListener("click", () => fit(HI, "hi"));
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        closeSheet();
        el.about.classList.add("hidden");
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
    el.mapBadge.textContent = APP_VERSION;
    el.verLabel.textContent = APP_VERSION;
    paintChips();
    wire();
    initMap();
    try {
      await load();
    } catch (err) {
      setStatus("No places file yet. Still building data.");
      return;
    }
    if (!window.Supercluster) {
      setStatus("Cluster library failed to load.");
      return;
    }
    rebuildIndex();
    render();
    recount();
    setStatus("");
  }

  start();
})();
