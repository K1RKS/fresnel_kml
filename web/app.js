"use strict";

const API = "api/fresnel.cgi";
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const DECIMAL_RE = /^[+-]?(\d+(\.\d*)?|\.\d+)$/;

let lastKml = null;
let lastFilename = "fresnel.kml";

function siteFieldset(key) {
  return $(`fieldset[data-site="${key}"]`);
}

function usesSinglePosition(fs) {
  return !$(".pos-single", fs).hidden;
}

function setupSite(fs) {
  const toggle = $(".toggle-pos", fs);
  toggle.addEventListener("click", () => {
    const single = !usesSinglePosition(fs);
    $(".pos-single", fs).hidden = !single;
    $(".pos-latlon", fs).hidden = single;
    toggle.textContent = single
      ? "Enter latitude and longitude separately instead"
      : "Paste a whole position instead (UTM, MGRS, grid locator, Plus code\u2026)";
    $(".decoded", fs).textContent = "";
  });
  const ref = $("select[name=height_ref]", fs);
  ref.addEventListener("change", () => {
    $(".ground-row", fs).hidden = ref.value === "asl";
    updateLookupHint();
  });
  $$("input[data-axis]", fs).forEach((input) => {
    const axis = input.dataset.axis;
    const handle = async () => {
      if (!axis || !(await splitPosition(fs, input))) decodeField(fs, input);
      syncOverrides(fs.dataset.site);
    };
    input.addEventListener("change", handle);
    input.addEventListener("input", () => input.classList.remove("invalid"));
    // A coordinate box holds one value, so a paste replaces it instead of
    // being inserted into the prefilled text.
    input.addEventListener("paste", (ev) => {
      const text = (ev.clipboardData && ev.clipboardData.getData("text")) || "";
      if (!text.trim()) return;
      ev.preventDefault();
      input.value = text.trim();
      input.classList.remove("invalid");
      handle();
    });
  });
  $("input[name=ground]", fs).addEventListener("input", updateLookupHint);
}

async function parseText(text, axis) {
  const params = new URLSearchParams({ parse: text });
  if (axis) params.set("axis", axis);
  const resp = await fetch(`${API}?${params}`);
  return resp.ok ? resp.json() : null;
}

// When a whole position (e.g. "37.0341, -122.0833") lands in the Latitude or
// Longitude field, put its first part in Latitude and its second in Longitude.
async function splitPosition(fs, input) {
  const text = input.value.trim();
  if (!text || DECIMAL_RE.test(text)) return false;
  const latIn = $("input[name=lat]", fs);
  const lonIn = $("input[name=lon]", fs);
  const parts = text.split(/[,;]/).map((s) => s.trim());
  const textPair = parts.length === 2 && parts.every(Boolean) && !/^geo:/i.test(text);
  let position = null;
  try {
    if (await parseText(text, input.dataset.axis)) return false;  // a valid single coordinate
    position = await parseText(text, "");
  } catch (err) {
    /* server unreachable: fall back to splitting on the comma */
  }
  if (!position && !textPair) return false;
  if (textPair && (!position || ["decimal", "dms", "ddm", "mixed"].includes(position.format))) {
    latIn.value = parts[0];
    lonIn.value = parts[1];
  } else {
    latIn.value = position.lat.toFixed(7);
    lonIn.value = position.lon.toFixed(7);
  }
  latIn.classList.remove("invalid");
  lonIn.classList.remove("invalid");
  const out = $(".decoded", fs);
  out.classList.remove("bad");
  out.textContent = position
    ? `Split into latitude and longitude: = ${position.display} (from ${position.format})`
    : "Split into latitude and longitude.";
  return true;
}

async function decodeField(fs, input) {
  const out = $(".decoded", fs);
  const text = input.value.trim();
  if (!text) {
    out.textContent = "";
    return;
  }
  const axis = input.dataset.axis;
  if (axis && DECIMAL_RE.test(text)) {
    out.textContent = "";
    out.classList.remove("bad");
    return;
  }
  const params = new URLSearchParams({ parse: text });
  if (axis) params.set("axis", axis);
  try {
    const resp = await fetch(`${API}?${params}`);
    const data = await resp.json();
    if (!resp.ok) {
      out.textContent = data.error || "Not recognised";
      out.classList.add("bad");
      input.classList.add("invalid");
      return;
    }
    out.classList.remove("bad");
    const value = axis ? `${data[axis].toFixed(7)}` : data.display;
    const extra = [...(data.notes || []), ...(data.warnings || [])].join(" ");
    out.textContent = `= ${value} (from ${data.format})${extra ? ". " + extra : ""}`;
  } catch (err) {
    out.textContent = "";
  }
}

function anyLookupNeeded() {
  return ["site_a", "site_b"].some((key) => {
    const fs = siteFieldset(key);
    return $("select[name=height_ref]", fs).value === "agl" && $("input[name=ground]", fs).value.trim() === "";
  });
}

function updateLookupHint() {
  const tc = $("#terrain_check").value;
  const lookup = anyLookupNeeded();
  const terrain = tc === "on" || (tc === "auto" && (lookup || treeMode() !== "none"));
  let text;
  if (!lookup && !terrain) text = "No elevation lookup will be made.";
  else if (terrain) text = "Ground elevations along the path will be looked up for the terrain check.";
  else text = "Ground elevation will be looked up for the tower site(s) given above ground.";
  const notes = [...nodeWarnings];
  const [pa, pb] = [pickedNode("site_a"), pickedNode("site_b")];
  if (pa && pb && pa.freq_mhz && pb.freq_mhz && pa.freq_mhz !== pb.freq_mhz) {
    notes.push(`The two nodes report different frequencies (${pa.freq_mhz} vs ${pb.freq_mhz} MHz).`);
  }
  $("#lookup-hint").innerHTML = esc(text) + notes.map((n) => `<span class="warn">${esc(n)}</span>`).join("");
}

// How far each picked node's antenna azimuth is from the bearing to the other site.
function aimRow(r, picks) {
  const lines = [];
  const bearings = [r.bearing_ab, r.bearing_ba];
  r.sites.forEach((s, i) => {
    const node = picks[i];
    if (!node || node.azimuth_deg === null || node.azimuth_deg === undefined) return;
    const off = Math.abs(((node.azimuth_deg - bearings[i] + 540) % 360) - 180);
    const bw = node.beamwidth_deg;
    const narrow = bw !== null && bw !== undefined && bw > 0 && bw < 360;
    let text = `${esc(s.name)} aims ${Number(node.azimuth_deg.toFixed(1))}\u00b0, link ${bearings[i].toFixed(1)}\u00b0 `
      + `(${off.toFixed(1)}\u00b0 off${narrow ? `, beamwidth ${Number(bw.toFixed(1))}\u00b0` : ""})`;
    if (narrow && off > bw / 2) text = `<span class="aim-off">${text}, outside the beam</span>`;
    lines.push(text);
  });
  return lines.length ? ["Antenna aim", lines.join("<br>")] : null;
}

function readSite(key) {
  const fs = siteFieldset(key);
  const site = { name: $("input[name=name]", fs).value.trim() };
  if (usesSinglePosition(fs)) {
    site.position = $("input[name=position]", fs).value.trim();
  } else {
    for (const axis of ["lat", "lon"]) {
      const v = $(`input[name=${axis}]`, fs).value.trim();
      site[axis] = DECIMAL_RE.test(v) ? Number(v) : v;
    }
  }
  const height = $("input[name=height]", fs).value.trim();
  const ref = $("select[name=height_ref]", fs).value;
  site[ref === "asl" ? "asl_m" : "agl_m"] = height === "" ? null : Number(height);
  const ground = $("input[name=ground]", fs).value.trim();
  if (ref === "agl" && ground !== "") site.ground_m = Number(ground);
  return site;
}

function treeMode() {
  const checked = $("input[name=tree_mode]:checked");
  return checked ? checked.value : "none";
}

function setTreeMode(mode) {
  const radio = $(`input[name=tree_mode][value=${mode}]`);
  if (radio && !radio.disabled) radio.checked = true;
  syncTreeInputs();
}

// Each choice enables the fields it uses; in LIDAR mode the tree height only fills gaps in the lidar.
function syncTreeInputs() {
  const mode = treeMode();
  for (const id of ["#tree_height", "#tree_clearance_a", "#tree_clearance_b"]) $(id).disabled = mode === "none";
  $("#lidar_width").disabled = mode !== "lidar";
  $(".lidar-only").hidden = mode !== "lidar";
  $("#tree_height_name").textContent = mode === "lidar" ? "Fallback tree height" : "Tree height";
}

function num(id) {
  const v = $(`#${id}`).value.trim();
  return v === "" ? undefined : Number(v);
}

function buildRequest() {
  const tc = $("#terrain_check").value;
  const options = {
    freq_ghz: num("freq_ghz"),
    zone: num("zone"),
    sixty: $("#sixty").checked,
    k: num("k"),
    clutter_m: num("clutter"),
    end_window_m: num("end_window"),
    elev_source: $("#elev_source").value,
    stations: num("stations"),
    ring_points: num("ring_points"),
    style: $("#style").value,
    ring_every: num("ring_every"),
    terrain_check: tc === "auto" ? null : tc === "on",
    mode: $("#mode").value,
    units: $("#units").value,
    color: $("#color_random").checked ? "random" : $("#color").value,
    coord_display: $("#coord_display").value,
    trees: treeMode() !== "none",
    tree_source: treeMode() === "lidar" ? "lidar" : "fixed",
    lidar_width: treeMode() === "lidar" ? num("lidar_width") : undefined,
    tree_height_m: num("tree_height"),
    tree_clearance_a_m: num("tree_clearance_a"),
    tree_clearance_b_m: num("tree_clearance_b"),
  };
  Object.keys(options).forEach((k) => options[k] === undefined && delete options[k]);
  return { site_a: readSite("site_a"), site_b: readSite("site_b"), options, format: "json" };
}

function showError(message, field) {
  const box = $("#error");
  box.textContent = message;
  box.hidden = false;
  if (field) {
    const [site, name] = field.split(".");
    const fs = siteFieldset(site);
    if (fs) {
      const input = $(`input[name=${{ agl_m: "height", asl_m: "height", ground_m: "ground" }[name] || name}]`, fs);
      if (input) {
        input.classList.add("invalid");
        input.focus();
      }
    }
  }
}

const FEET = 0.3048;
let resultUnits = "m";

// The API always reports metres; show heights in the units the request used.
function hfmt(metres, digits = 1, units = resultUnits) {
  if (metres === null || metres === undefined) return "unknown";
  const value = units === "ft" ? metres / FEET : metres;
  return `${value.toFixed(digits)} ${units}`;
}

const MILE = 1609.344;

// Short distances in m or ft, longer ones in km or miles.
function dfmt(metres, digits = 3, units = resultUnits) {
  if (units === "ft") {
    const feet = metres / FEET;
    return Math.abs(feet) < 1000 ? `${feet.toFixed(0)} ft` : `${(metres / MILE).toFixed(digits)} mi`;
  }
  return Math.abs(metres) < 1000 ? `${metres.toFixed(0)} m` : `${(metres / 1000).toFixed(digits)} km`;
}

function gapText(metres, what = "terrain") {
  return `${what} ${hfmt(Math.abs(metres))} ${metres >= 0 ? "below" : "above"} the line of sight`;
}

function updateUnitLabels() {
  const unit = $("#units").value;
  $$(".unit").forEach((el) => { el.textContent = unit; });
}

function esc(text) {
  const d = document.createElement("div");
  d.textContent = String(text);
  return d.innerHTML;
}

function showResult(data, units, picks = [], feedHeights = []) {
  resultUnits = units === "ft" ? "ft" : "m";
  const r = data.report;
  lastKml = data.kml;
  const [a, b] = r.sites;
  lastFilename = `Fresnel_${a.name}_${b.name}`.replace(/[^A-Za-z0-9._-]+/g, "_") + ".kml";

  const badge = $("#status-badge");
  badge.textContent = r.status || "terrain not checked";
  badge.className = `badge ${r.status || ""}`;

  const rows = [];
  for (const s of r.sites) {
    const ground = s.ground_m === null ? "unknown" : `${hfmt(s.ground_m)} (${s.ground_source})`;
    rows.push([esc(s.name), `${esc(s.display)} <small>(from ${esc(s.from)})</small><br>ground ${esc(ground)}, AGL ${hfmt(s.agl_m)}, ASL ${hfmt(s.asl_m)}`]);
  }
  rows.push(["Distance", dfmt(r.distance_m)]);
  rows.push(["Bearings", `A\u2192B ${r.bearing_ab.toFixed(1)}\u00b0, B\u2192A ${r.bearing_ba.toFixed(1)}\u00b0`]);
  const aim = aimRow(r, picks);
  if (aim) rows.push(aim);
  rows.push(["Max r1 / 0.6\u00b7r1", `${hfmt(r.r1_max_m, 2)} / ${hfmt(r.r60_max_m, 2)}`]);
  if (r.zone !== 1) rows.push([`Max r${r.zone}`, hfmt(r.zone_max_m, 2)]);
  if (r.terrain_checked) {
    const c = r.min_clearance === null ? "n/a"
      : `${r.min_clearance.toFixed(2)} \u00d7 r1 (${gapText(r.min_clearance_m)}) at ${dfmt(r.min_clearance_d_m, 2)}`;
    rows.push(["Min clearance", c]);
    const t = r.trees;
    if (t && t.enabled) {
      const tc = t.min_clearance === null ? "n/a"
        : `${t.min_clearance.toFixed(2)} \u00d7 r1 (${gapText(t.min_clearance_m, "tree tops")}) at ${dfmt(t.min_clearance_d_m, 2)}`;
      rows.push(["Min clearance over trees",
        `<span class="badge ${t.status}">${t.status}</span> ${tc}<br><small>${hfmt(t.height_m)} trees, ignored within ${dfmt(t.clearance_a_m)} of ${esc(a.name)} and ${dfmt(t.clearance_b_m)} of ${esc(b.name)}</small>`]);
    }
    const sf = r.surface;
    if (sf && sf.enabled) {
      const w = sf.worst;
      let detail = "n/a";
      if (w && w.clearance !== null) {
        const what = w.lidar ? `lidar surface ${hfmt(w.height_m)} above ground${w.dataset ? `, ${esc(datasetLabel(w.dataset))} lidar` : ""}`
          : `${hfmt(sf.fallback_height_m)} trees, no lidar here`;
        const side = w.offset_m ? `, ${dfmt(Math.abs(w.offset_m))} off the line of sight` : "";
        detail = `${w.clearance.toFixed(2)} \u00d7 r1 (${what}${side}) at ${dfmt(w.d_m, 2)}`;
      }
      const sources = sf.sources.length ? sf.sources.join(", ") : "no lidar";
      const span = sf.flown;
      const flown = !span ? "" : `<br><small>${span.oldest === span.newest ? `Lidar was flown in ${span.oldest}`
        : `Lidar was flown between ${span.oldest} and ${span.newest}`}</small>`;
      rows.push(["Min clearance over lidar surface",
        `<span class="badge ${sf.status}">${sf.status}</span> ${detail}${flown}<br><small>${esc(sources)}: ${Math.round(100 * sf.coverage)}% of the checked path, `
        + `every ${dfmt(sf.spacing_m)}${sf.near_spacing_m && sf.near_spacing_m < sf.spacing_m - 0.01 ? ` (every ${dfmt(sf.near_spacing_m)} near the sites)` : ""}`
        + ` across \u00b1${sf.width} \u00d7 r1; ${hfmt(sf.fallback_height_m)} trees where there's no lidar; `
        + `ignored within ${dfmt(sf.clearance_a_m)} of ${esc(a.name)} and ${dfmt(sf.clearance_b_m)} of ${esc(b.name)}</small>`]);
    }
  }
  rows.push(["Elevation source", esc(r.elevation_sources.join(", ") || "none")]);
  $("#summary-table").innerHTML = rows.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join("");

  $("#swatch").style.background = r.zone_color.hex;
  $("#swatch-text").textContent = `Zone color ${r.zone_color.hex} (${r.zone_color.random ? "random" : "chosen"})`;
  if (r.zone_color.random) $("#color").value = r.zone_color.hex;

  const warnings = (data.warnings || []).map((w) => `<li>${esc(w)}</li>`);
  r.sites.forEach((s, i) => {
    if (!feedHeights[i] || s.agl_m === null) return;
    let issue = null;
    if (s.agl_m > TALL_AGL_M) {
      issue = `${s.name}: the antenna works out to ${hfmt(s.agl_m)} above ground. The node's antenna height `
        + "may be incorrect unless it's on a high tower.";
    } else if (s.height_ref === "asl" && s.agl_m < 0) {
      issue = `${s.name}: the node's antenna height of ${hfmt(s.asl_m)} can't be right either way: above sea level `
        + `it's underground, and above ground it would be a ${hfmt(s.asl_m)} tower. Enter the real antenna height.`;
    }
    if (issue) warnings.push(`<li class="issue">${esc(issue)}</li>`);
  });
  $("#warnings").innerHTML = warnings.join("");
  drawProfile(r);
  $("#result").hidden = false;
  drawMap(r);
  $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function drawProfile(r) {
  const box = $("#profile-box");
  if (!r.terrain_checked || !r.profile.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const [a, b] = r.sites;
  const D = r.distance_m;
  const p = r.profile;
  const masts = [
    { d: 0, name: a.name, ground: p[0].ground_m, top: p[0].los_m },
    { d: D, name: b.name, ground: p[p.length - 1].ground_m, top: p[p.length - 1].los_m },
  ];
  const surface = r.surface && r.surface.enabled ? r.surface : null;
  const trees = Boolean(r.trees && r.trees.enabled);
  drawPlot($("#profile-main"), p, { d0: 0, d1: D, masts, from: "a", surface, trees });
  const feet = r.end_window_m / FEET;
  const win = resultUnits === "ft" ? (feet < 5280 ? `${feet.toFixed(0)} ft` : dfmt(r.end_window_m, 2))
    : (r.end_window_m < 1000 ? `${r.end_window_m.toFixed(0)} m` : dfmt(r.end_window_m, 2));
  const zoom = [[r.profile_a, "a", "#profile-a", "#profile-title-a", `First ${win} from ${a.name}`],
                [r.profile_b, "b", "#profile-b", "#profile-title-b", `Last ${win} to ${b.name}`]];
  for (const [pts, from, svgSel, titleSel, title] of zoom) {
    const show = pts && pts.length > 1;
    $(svgSel).hidden = $(titleSel).hidden = !show;
    if (readoutFor($(svgSel))) readoutFor($(svgSel)).hidden = !show;
    if (!show) continue;
    $(titleSel).textContent = `${title} (distance ${from === "a" ? "from " + a.name : "to " + b.name})`;
    const closeUp = surface && { ...surface, plot: (from === "a" ? surface.plot_a : surface.plot_b) || surface.plot };
    drawPlot($(svgSel), pts, { d0: pts[0].d_m, d1: pts[pts.length - 1].d_m, masts, from, surface: closeUp, trees });
  }
}

let mapConfig = null;
let map = null;
const mapGroups = {};

async function loadMapConfig() {
  try {
    const resp = await fetch(`${API}?map`);
    if (resp.ok) mapConfig = await resp.json();
  } catch (err) {
    /* no tile servers: the map stays blank but overlays still draw */
  }
}

function ensureMap() {
  if (map) return map;
  map = L.map("map", { scrollWheelZoom: false });
  map.on("click", () => map.scrollWheelZoom.enable());
  map.on("mouseout", () => map.scrollWheelZoom.disable());
  const bases = {};
  for (const s of (mapConfig && mapConfig.tile_servers) || []) {
    const opts = { maxZoom: s.max_zoom || 19, attribution: s.attribution || "" };
    if (s.subdomains) opts.subdomains = s.subdomains;
    if (s.max_native_zoom) opts.maxNativeZoom = s.max_native_zoom;
    bases[s.name] = L.tileLayer(s.url, opts);
  }
  const first = bases[mapConfig && mapConfig.default] || Object.values(bases)[0];
  if (first) first.addTo(map);
  for (const name of ["Fresnel zone", "60% zone", "Line of sight", "Sites", "Obstructions"]) {
    mapGroups[name] = L.layerGroup();
    if (name !== "60% zone") mapGroups[name].addTo(map);
  }
  L.control.layers(bases, mapGroups, { collapsed: true }).addTo(map);
  L.control.scale({ metric: true, imperial: true }).addTo(map);
  map.on("dragstart", () => $$(".map-views button").forEach((btn) => btn.classList.remove("active")));
  return map;
}

function drawMap(r) {
  const box = $("#map-box");
  if (typeof L === "undefined" || !r.map) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  ensureMap();
  Object.values(mapGroups).forEach((g) => g.clearLayers());
  const color = r.zone_color.hex;
  L.polygon(r.map.zone, { color, weight: 1, fillColor: color, fillOpacity: 0.35 })
    .bindTooltip(`Fresnel zone ${r.zone}: max radius ${hfmt(r.zone_max_m, 2)}`).addTo(mapGroups["Fresnel zone"]);
  if (r.map.zone60) {
    L.polygon(r.map.zone60, { color: "#e8590c", weight: 1, fillColor: "#ffa500", fillOpacity: 0.3 })
      .bindTooltip("60% of zone 1").addTo(mapGroups["60% zone"]);
  }
  L.polyline(r.map.los, { color: "#c9a000", weight: 2 }).addTo(mapGroups["Line of sight"]);
  for (const s of r.sites) {
    L.circleMarker([s.lat, s.lon], { radius: 6, color: "#1d232b", weight: 2, fillColor: "#fff", fillOpacity: 1 })
      .bindTooltip(esc(s.name), { permanent: true, direction: "top", offset: [0, -6], className: "site-tip" })
      .addTo(mapGroups["Sites"]);
  }
  const samples = [...(r.profile || []), ...(r.profile_a || []), ...(r.profile_b || [])];
  const a = r.sites[0].name;
  for (const q of samples) {
    if (q.clearance !== null && q.clearance < 0.6) {
      L.circleMarker([q.lat, q.lon], { radius: 4, color: "#cf222e", weight: 1, fillColor: "#cf222e", fillOpacity: 0.9 })
        .bindTooltip(`terrain: ${q.clearance.toFixed(2)} \u00d7 r1 at ${dfmt(q.d_m, 2)} from ${esc(a)}`)
        .addTo(mapGroups["Obstructions"]);
    }
    if (q.tree_clearance !== null && q.tree_clearance !== undefined && q.tree_clearance < 0.6) {
      L.circleMarker([q.lat, q.lon], { radius: 5, color: "#b02a37", weight: 2, fill: false })
        .bindTooltip(`trees: ${q.tree_clearance.toFixed(2)} \u00d7 r1 at ${dfmt(q.d_m, 2)} from ${esc(a)}`)
        .addTo(mapGroups["Obstructions"]);
    }
  }
  if (r.surface && r.surface.enabled) {
    for (const q of r.surface.markers) {
      L.circleMarker([q.lat, q.lon], { radius: 5, color: "#b02a37", weight: 2, fill: false })
        .bindTooltip(surfaceTip(q, a)).addTo(mapGroups["Obstructions"]);
    }
  }
  map.invalidateSize();
  mapReport = r;
  $$(".map-views button").forEach((btn) => {
    const site = { a: r.sites[0], b: r.sites[1] }[btn.dataset.view];
    if (site) {
      btn.textContent = site.name;
      btn.title = `Zoom to ${site.name}`;
    }
  });
  showMapView("path");
}

const SITE_ZOOM = 17;  // close enough to see buildings and treelines around a site
let mapReport = null;

function showMapView(view) {
  if (!map || !mapReport) return;
  $$(".map-views button").forEach((btn) => btn.classList.toggle("active", btn.dataset.view === view));
  const site = { a: mapReport.sites[0], b: mapReport.sites[1] }[view];
  if (site) map.setView([site.lat, site.lon], Math.min(SITE_ZOOM, map.getMaxZoom()));
  else map.fitBounds(L.latLngBounds(mapReport.map.los).pad(0.15));
}

function setupMapViews() {
  $$(".map-views button").forEach((btn) => btn.addEventListener("click", () => showMapView(btn.dataset.view)));
}

// Consecutive samples that have trees on them.
function treeRuns(p) {
  const runs = [];
  let run = [];
  for (const q of p) {
    if (q.tree_top_m !== null && q.tree_top_m !== undefined) run.push(q);
    else if (run.length) { runs.push(run); run = []; }
  }
  if (run.length) runs.push(run);
  return runs.filter((r) => r.length > 1);
}

// Consecutive lidar-check stations that share a source: lidar, or the fixed tree height where there's none.
function surfaceRuns(p) {
  const runs = [];
  for (const q of p) {
    const last = runs[runs.length - 1];
    if (last && last[0].lidar === q.lidar) last.push(q);
    else runs.push([q]);
  }
  return runs.filter((r) => r.length > 1);
}

// "OLC Santiam 2018", or "Some Survey (2016)" when the name has no year in it.
function datasetLabel(d) {
  const name = d.name || "unnamed lidar";
  return d.year && !name.includes(String(d.year)) ? `${name} (${d.year})` : name;
}

function surfaceTip(q, from) {
  const what = q.lidar ? `lidar surface ${hfmt(q.height_m)} above ground${q.dataset ? ` (${esc(datasetLabel(q.dataset))})` : ""}`
    : "trees (no lidar here)";
  const side = q.offset_m ? `, ${dfmt(Math.abs(q.offset_m))} off the line of sight` : "";
  return `${what}${side}: ${q.clearance.toFixed(2)} \u00d7 r1 at ${dfmt(q.d_m, 2)} from ${esc(from)}`;
}

// One terrain-profile chart. Every chart has the same pixel size; each picks its own X and Y scale.
// opts.from is "a" (label distances from site A) or "b" (label distances to site B).
function drawPlot(svg, p, opts) {
  const W = 800, H = 260, L = 60, R = 14, T = 26, B = 30;
  const D = opts.masts[1].d;
  const { d0, d1 } = opts;
  const span = d1 - d0 || 1;
  const eps = span * 1e-6;
  const visible = opts.masts.filter((m) => m.d >= d0 - eps && m.d <= d1 + eps);
  const inView = (q) => q.d_m >= d0 - eps && q.d_m <= d1 + eps;
  const sp = opts.surface ? opts.surface.plot.filter(inView) : [];
  const sm = opts.surface ? opts.surface.markers.filter(inView) : [];
  const ys = p.flatMap((q) => [q.effective_m, q.los_m, q.zone_bottom_m])
    .concat(p.filter((q) => q.tree_top_m !== null && q.tree_top_m !== undefined).map((q) => q.tree_top_m))
    .concat(sp.map((q) => q.peak_m))
    .concat(visible.flatMap((m) => [m.ground, m.top]));
  let yMin = Math.min(...ys), yMax = Math.max(...ys);
  const pad = Math.max(3, (yMax - yMin) * 0.08);
  yMin -= pad;
  yMax += pad;
  const x = (d) => L + ((d - d0) / span) * (W - L - R);
  const y = (v) => T + (1 - (v - yMin) / (yMax - yMin)) * (H - T - B);
  const line = (key) => p.map((q, i) => `${i ? "L" : "M"}${x(q.d_m).toFixed(1)},${y(q[key]).toFixed(1)}`).join("");
  const ground = `M${x(d0)},${y(yMin)}` + p.map((q) => `L${x(q.d_m).toFixed(1)},${y(q.effective_m).toFixed(1)}`).join("") + `L${x(d1)},${y(yMin)}Z`;
  const parts = [];
  for (let i = 0; i <= 4; i++) {
    const v = yMin + ((yMax - yMin) * i) / 4;
    parts.push(`<line class="axis" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${hfmt(v, 0)}</text>`);
  }
  for (let i = 0; i <= 5; i++) {
    const d = d0 + (span * i) / 5;
    const anchor = i === 0 ? "start" : i === 5 ? "end" : "middle";
    const label = dfmt(opts.from === "b" ? Math.max(0, D - d) : d, 2);
    parts.push(`<text x="${x(d)}" y="${H - 10}" text-anchor="${anchor}">${label}</text>`);
  }
  parts.push(`<path class="ground" d="${ground}"/>`);
  if (p.some((q) => hasValue(q.bulge_m))) {
    const bulge = (q) => q.bulge_m || 0;
    const arc = p.map((q) => `${x(q.d_m).toFixed(1)},${y(yMin + bulge(q)).toFixed(1)}`);
    parts.push(`<path class="bulge" d="M${x(d0)},${y(yMin)}L${arc.join("L")}L${x(d1)},${y(yMin)}Z"/>`,
      `<path class="bulge-edge" d="M${arc.join("L")}"/>`,
      `<path class="flat-ground" d="M${p.map((q) => `${x(q.d_m).toFixed(1)},${y(q.effective_m - bulge(q)).toFixed(1)}`).join("L")}"/>`);
  }
  for (const run of treeRuns(p)) {
    const top = run.map((q) => `${x(q.d_m).toFixed(1)},${y(q.tree_top_m).toFixed(1)}`);
    const bottom = run.slice().reverse().map((q) => `${x(q.d_m).toFixed(1)},${y(q.effective_m).toFixed(1)}`);
    parts.push(`<path class="trees" d="M${top.join("L")}L${bottom.join("L")}Z"/>`);
  }
  for (const run of surfaceRuns(sp)) {
    const top = run.map((q) => `${x(q.d_m).toFixed(1)},${y(q.peak_m).toFixed(1)}`);
    const bottom = run.slice().reverse().map((q) => `${x(q.d_m).toFixed(1)},${y(q.effective_m).toFixed(1)}`);
    parts.push(`<path class="${run[0].lidar ? "lidar" : "trees"}" d="M${top.join("L")}L${bottom.join("L")}Z"/>`);
  }
  parts.push(`<path class="r60" d="${line("r60_bottom_m")}"/><path class="zone" d="${line("zone_bottom_m")}"/>`,
    `<path class="los" d="${line("los_m")}"/>`);
  for (const q of p.filter((q) => q.clearance !== null && q.clearance < 0.6)) {
    parts.push(`<circle class="obstruction" cx="${x(q.d_m).toFixed(1)}" cy="${y(q.effective_m).toFixed(1)}" r="3"><title>clearance ${q.clearance.toFixed(2)} \u00d7 r1 at ${dfmt(q.d_m, 2)} from ${esc(opts.masts[0].name)}</title></circle>`);
  }
  for (const q of p.filter((q) => q.tree_clearance !== null && q.tree_clearance !== undefined && q.tree_clearance < 0.6)) {
    parts.push(`<circle class="tree-obstruction" cx="${x(q.d_m).toFixed(1)}" cy="${y(q.tree_top_m).toFixed(1)}" r="3.5"><title>clearance over trees ${q.tree_clearance.toFixed(2)} \u00d7 r1 at ${dfmt(q.d_m, 2)} from ${esc(opts.masts[0].name)}</title></circle>`);
  }
  for (const q of sm) {
    parts.push(`<circle class="tree-obstruction" cx="${x(q.d_m).toFixed(1)}" cy="${y(q.peak_m).toFixed(1)}" r="3.5"><title>${surfaceTip(q, opts.masts[0].name)}</title></circle>`);
  }
  for (const m of opts.masts) {
    if (visible.includes(m)) {
      const mx = x(m.d);
      const anchor = mx < L + 60 ? "start" : mx > W - R - 60 ? "end" : "middle";
      const tx = anchor === "start" ? mx + 5 : anchor === "end" ? mx - 5 : mx;
      parts.push(`<line class="mast" x1="${mx}" x2="${mx}" y1="${y(m.ground)}" y2="${y(m.top)}"/>`,
        `<circle class="mast-top" cx="${mx}" cy="${y(m.top)}" r="4"/>`,
        `<text class="site-label" x="${tx}" y="${y(m.top) - 8}" text-anchor="${anchor}">${esc(m.name)}</text>`);
    } else {
      const left = m.d < d0;
      const away = dfmt(left ? d0 - m.d : m.d - d1, 2);
      const text = left ? `\u2190 ${esc(m.name)} (${away})` : `${esc(m.name)} (${away}) \u2192`;
      parts.push(`<text class="offsite" x="${left ? L + 4 : W - R - 4}" y="${T - 10}" text-anchor="${left ? "start" : "end"}">${text}</text>`);
    }
  }
  parts.push(`<g class="measure-cursor"></g>`);
  svg.innerHTML = parts.join("");
  // what the measuring cursor needs: the same scales and data the picture was drawn from
  svg._plot = {
    x, y, d0, d1, L, R, W, T, B, H, p, sp, D, from: opts.from, masts: opts.masts,
    mode: opts.surface ? "lidar" : opts.trees ? "trees" : "none",
    dAt: (px) => d0 + ((px - L) / (W - L - R)) * span,
  };
  resetMeasure(svg);
}

// ----- Measuring cursor on the profile graphs -----

const MEASURE_HINT = "Hover over the profile to measure at any point. Click to add a marker (drag to move it, \u00d7 to remove it); "
  + "the arrow keys move the selected marker and Esc removes it.";
const PIN_COLORS = ["#d6336c", "#1971c2", "#2f9e44", "#f08c00", "#7048e8", "#0c8599", "#e8590c", "#5c940d"];
let nextPin = 0;  // markers take the next colour in turn, across all the graphs

function hasValue(v) {
  return v !== null && v !== undefined;
}

// The two points either side of d (the same point twice on an exact hit), or null outside them.
function bracket(points, d) {
  const n = points.length;
  if (!n || d < points[0].d_m - 1e-6 || d > points[n - 1].d_m + 1e-6) return null;
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid].d_m <= d) lo = mid;
    else hi = mid;
  }
  if (Math.abs(points[lo].d_m - d) < 1e-6) return [points[lo], points[lo]];
  if (Math.abs(points[hi].d_m - d) < 1e-6) return [points[hi], points[hi]];
  return [points[lo], points[hi]];
}

// Linear interpolation of key at d, as the graph draws it; null where either side has no value.
function interp(points, d, key) {
  const pair = bracket(points, d);
  if (!pair) return null;
  const [a, b] = pair;
  if (!hasValue(a[key]) || !hasValue(b[key])) return null;
  if (a === b || b.d_m === a.d_m) return a[key];
  return a[key] + ((d - a.d_m) / (b.d_m - a.d_m)) * (b[key] - a[key]);
}

// The lidar (or fallback tree) top at d, only within one drawn run, like the blue/green fill.
function surfaceTopAt(sp, d) {
  const pair = bracket(sp, d);
  if (!pair) return null;
  const [a, b] = pair;
  if (a.lidar !== b.lidar) return null;
  const top = a === b || b.d_m === a.d_m ? a.peak_m : a.peak_m + ((d - a.d_m) / (b.d_m - a.d_m)) * (b.peak_m - a.peak_m);
  return { top, station: d - a.d_m <= b.d_m - d ? a : b };
}

function measureAt(plot, d) {
  const p = plot.p;
  const m = {
    d, ground: interp(p, d, "effective_m"), los: interp(p, d, "los_m"), zone: interp(p, d, "zone_bottom_m"),
    sixty: interp(p, d, "r60_bottom_m"), bulge: interp(p, d, "bulge_m"), top: null, topKind: null, dataset: null,
  };
  if (plot.mode === "trees") {
    m.top = interp(p, d, "tree_top_m");
    if (hasValue(m.top)) m.topKind = "tree";
  } else if (plot.mode === "lidar") {
    const s = surfaceTopAt(plot.sp, d);
    if (s) {
      m.top = s.top;
      m.topKind = s.station.lidar ? "lidar" : "fallback";
      m.dataset = s.station.dataset || null;
    }
  }
  return m;
}

function cursorLine(plot, d, cls, color) {
  const cx = plot.x(d).toFixed(1);
  const style = color ? ` style="stroke:${color}"` : "";
  return `<line class="cursor-line ${cls}"${style} x1="${cx}" x2="${cx}" y1="${plot.T}" y2="${plot.H - plot.B}"/>`;
}

function pinRemoveIcon(plot, pin) {
  const cx = plot.x(pin.d).toFixed(1);
  return `<g class="pin-x" data-pin="${pin.id}" transform="translate(${cx},${plot.T - 9})"><title>Remove this marker</title>`
    + `<circle r="7" style="fill:${pin.color}"/><path d="M-3,-3L3,3M3,-3L-3,3"/></g>`;
}

function cursorDots(plot, m) {
  const cx = plot.x(m.d).toFixed(1);
  const dot = (v, cls) => (hasValue(v) ? `<circle class="cursor-dot ${cls}" cx="${cx}" cy="${plot.y(v).toFixed(1)}" r="4"/>` : "");
  const topCls = m.topKind === "lidar" ? "lidar" : "trees";
  return dot(m.ground, "ground") + dot(m.top, topCls) + dot(m.zone, "zone") + dot(m.sixty, "r60") + dot(m.los, "los");
}

// A gap between two heights: positive is clear, negative is how far the lower thing reaches past the line.
function gapHtml(v, past) {
  if (!hasValue(v)) return "n/a";
  return v >= 0 ? hfmt(v) : `<span class="into">${hfmt(-v)} ${past}</span>`;
}

function whereText(plot, d) {
  const [a, b] = plot.masts;
  return plot.from === "b" ? `${dfmt(Math.max(0, plot.D - d), 2)} to ${b.name}` : `${dfmt(d, 2)} from ${a.name}`;
}

// One measurement in the readout: the live hover reading (pin null) or a saved marker.
function readoutEntry(plot, m, pin) {
  const [a, b] = plot.masts;
  const from = `${dfmt(m.d, 2)} from ${esc(a.name)}`;
  const to = `${dfmt(Math.max(0, plot.D - m.d), 2)} to ${esc(b.name)}`;
  const swatch = pin ? `<span class="pin-swatch" style="background:${pin.color}"></span>` : '<span class="pin-swatch hover"></span>';
  const remove = pin ? `<button type="button" class="pin-remove" data-pin="${pin.id}" title="Remove this marker" aria-label="Remove this marker">&times;</button>` : "";
  const lines = [`${remove}${swatch}<strong>${plot.from === "b" ? `${to}</strong> (${from})` : `${from}</strong> (${to})`}`];
  if (hasValue(m.ground)) {
    lines.push(`Ground to Fresnel zone bottom: ${gapHtml(m.zone - m.ground, "into the zone")}`
      + ` &middot; to the 60% line: ${gapHtml(m.sixty - m.ground, "past the 60% line")}`);
  }
  if (hasValue(m.bulge) && $("#show_curvature").checked) lines.push(`Earth curvature here: ${hfmt(m.bulge, 2)} (included in the ground height)`);
  if (hasValue(m.top) && hasValue(m.ground)) {
    const name = { lidar: "lidar top", fallback: "tree top (no lidar here)", tree: "tree top" }[m.topKind];
    const ds = m.dataset ? ` (${esc(datasetLabel(m.dataset))})` : "";
    const cap = name.charAt(0).toUpperCase() + name.slice(1);
    lines.push(`Ground to ${name}: ${hfmt(m.top - m.ground)}${ds}`);
    lines.push(`${cap} to Fresnel zone bottom: ${gapHtml(m.zone - m.top, "into the zone")}${m.zone >= m.top ? " clear" : ""}`
      + ` &middot; to the 60% line: ${gapHtml(m.sixty - m.top, "past the 60% line")}${m.sixty >= m.top ? " clear" : ""}`);
  } else if (plot.mode !== "none") {
    lines.push("No trees or lidar checked here.");
  }
  const style = pin ? ` style="border-left-color:${pin.color}"` : "";
  return `<div class="measure-entry${pin ? "" : " hover"}"${style}>${lines.join("<br>")}</div>`;
}

function readoutFor(svg) {
  const el = svg.nextElementSibling;
  return el && el.classList.contains("measure-readout") ? el : null;
}

function renderMeasure(svg) {
  const st = svg._measure, plot = svg._plot;
  const g = svg.querySelector(".measure-cursor");
  if (!st || !plot || !g) return;
  const hovering = hasValue(st.hover) && st.dragging === null;
  const pins = [...st.pins].sort((p, q) => p.d - q.d);
  let html = "";
  for (const pin of pins) {
    html += cursorLine(plot, pin.d, `pinned${pin.id === st.selected ? " selected" : ""}`, pin.color)
      + cursorDots(plot, measureAt(plot, pin.d)) + pinRemoveIcon(plot, pin);
  }
  if (hovering) html += cursorLine(plot, st.hover, "hover") + cursorDots(plot, measureAt(plot, st.hover));
  g.innerHTML = html;
  const readout = readoutFor(svg);
  if (readout) {
    const entries = (hovering ? [readoutEntry(plot, measureAt(plot, st.hover), null)] : [])
      .concat(pins.map((pin) => readoutEntry(plot, measureAt(plot, pin.d), pin)));
    if (entries.length) readout.innerHTML = entries.join("");
    else readout.textContent = MEASURE_HINT;
  }
  if (hovering) hoverSource = svg;
  else if (hoverSource === svg) hoverSource = null;
  syncMapMarkers();
}

// ----- The measuring markers on the map -----

const MEASURED_PLOTS = ["#profile-main", "#profile-a", "#profile-b"];
let hoverSource = null;  // the profile graph being hovered, whose cursor the faded map marker follows
let mapHover = null;
const mapPins = new Map();  // marker id -> its map marker

function latLonAt(plot, d) {
  const lat = interp(plot.p, d, "lat"), lon = interp(plot.p, d, "lon");
  return hasValue(lat) && hasValue(lon) ? [lat, lon] : null;
}

function syncMapMarkers() {
  if (!map) return;
  const hs = hoverSource && hoverSource._measure;
  const hoverAt = hs && hasValue(hs.hover) ? latLonAt(hoverSource._plot, hs.hover) : null;
  if (!hoverAt) {
    if (mapHover) mapHover.remove();
    mapHover = null;
  } else {
    if (!mapHover) {
      mapHover = L.circleMarker(hoverAt, { radius: 7, color: "#1d232b", weight: 2, fillColor: "#ffd43b", fillOpacity: 0.6,
        dashArray: "3 3", interactive: false, pane: "markerPane" }).addTo(map);
      mapHover.bindTooltip("", { direction: "right", offset: [8, 0], className: "cursor-tip" });
    }
    mapHover.setLatLng(hoverAt);
    mapHover.setTooltipContent(esc(whereText(hoverSource._plot, hs.hover)));
    if (!mapHover.isTooltipOpen()) mapHover.openTooltip();
  }
  const live = new Set();
  for (const sel of MEASURED_PLOTS) {
    const svg = $(sel), st = svg._measure, plot = svg._plot;
    if (!st || !plot || svg.hidden) continue;
    for (const pin of st.pins) {
      const at = latLonAt(plot, pin.d);
      if (!at) continue;
      live.add(pin.id);
      let mk = mapPins.get(pin.id);
      if (!mk) {
        mk = L.circleMarker(at, { radius: 7, color: "#fff", weight: 2, fillColor: pin.color, fillOpacity: 1,
          pane: "markerPane" }).addTo(map);
        mk.bindTooltip("", { direction: "right", offset: [8, 0], className: "cursor-tip" });
        mapPins.set(pin.id, mk);
      }
      mk.setLatLng(at);
      mk.setTooltipContent(esc(whereText(plot, pin.d)));
    }
  }
  for (const [id, mk] of mapPins) {
    if (!live.has(id)) {
      mk.remove();
      mapPins.delete(id);
    }
  }
}

function resetMeasure(svg) {
  svg._measure = { hover: null, pins: [], selected: null, dragging: null };
  renderMeasure(svg);
}

function removePin(svg, id) {
  const st = svg._measure;
  st.pins = st.pins.filter((pin) => pin.id !== id);
  if (st.selected === id) st.selected = st.pins.length ? st.pins[st.pins.length - 1].id : null;
  if (st.dragging === id) st.dragging = null;
  renderMeasure(svg);
}

function svgX(svg, ev) {
  const pt = svg.createSVGPoint();
  pt.x = ev.clientX;
  pt.y = ev.clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse()).x;
}

// The marker within a few pixels of the pointer, to drag it instead of adding another.
function pinNear(svg, ev) {
  const plot = svg._plot, px = svgX(svg, ev);
  let best = null;
  for (const pin of svg._measure.pins) {
    const dx = Math.abs(plot.x(pin.d) - px);
    if (dx <= 6 && (!best || dx < best.dx)) best = { pin, dx };
  }
  return best ? best.pin : null;
}

function measureDistance(svg, ev) {
  const plot = svg._plot;
  return plot.dAt(Math.min(plot.W - plot.R, Math.max(plot.L, svgX(svg, ev))));
}

// The next drawn sample (profile or lidar) from d, `steps` along in direction dir.
function stepDistance(plot, d, dir, steps) {
  const ds = [...new Set(plot.p.map((q) => q.d_m).concat(plot.sp.map((q) => q.d_m)))]
    .filter((v) => v >= plot.d0 - 1e-6 && v <= plot.d1 + 1e-6).sort((x, y) => x - y);
  if (!ds.length) return d;
  let i = dir > 0 ? ds.findIndex((v) => v > d + 1e-6) : ds.length - 1 - [...ds].reverse().findIndex((v) => v < d - 1e-6);
  if (i < 0 || i >= ds.length) i = dir > 0 ? ds.length - 1 : 0;
  return ds[Math.max(0, Math.min(ds.length - 1, i + dir * (steps - 1)))];
}

function addPin(svg, d) {
  const pin = { id: nextPin, d, color: PIN_COLORS[nextPin % PIN_COLORS.length] };
  nextPin += 1;
  svg._measure.pins.push(pin);
  return pin;
}

function setupMeasure(svg) {
  const ready = () => svg._plot && svg._measure;
  const pinById = (id) => svg._measure.pins.find((pin) => pin.id === id);
  svg.addEventListener("pointermove", (ev) => {
    if (!ready()) return;
    const st = svg._measure, d = measureDistance(svg, ev);
    if (st.dragging !== null && pinById(st.dragging)) pinById(st.dragging).d = d;
    else st.hover = d;
    renderMeasure(svg);
  });
  svg.addEventListener("pointerleave", () => {
    if (!ready() || svg._measure.dragging !== null) return;
    svg._measure.hover = null;
    renderMeasure(svg);
  });
  svg.addEventListener("pointerdown", (ev) => {
    if (!ready() || ev.button !== 0) return;
    const remove = ev.target.closest && ev.target.closest(".pin-x");
    if (remove) {
      ev.preventDefault();
      removePin(svg, Number(remove.dataset.pin));
      return;
    }
    const st = svg._measure;
    const pin = pinNear(svg, ev) || addPin(svg, measureDistance(svg, ev));
    Object.assign(st, { selected: pin.id, dragging: pin.id, hover: null });
    svg.setPointerCapture(ev.pointerId);
    svg.focus({ preventScroll: true });
    renderMeasure(svg);
  });
  const stopDrag = (ev) => {
    if (!ready() || svg._measure.dragging === null) return;
    svg._measure.dragging = null;
    if (svg.hasPointerCapture(ev.pointerId)) svg.releasePointerCapture(ev.pointerId);
    renderMeasure(svg);
  };
  svg.addEventListener("pointerup", stopDrag);
  svg.addEventListener("pointercancel", stopDrag);
  svg.addEventListener("keydown", (ev) => {
    if (!ready()) return;
    const st = svg._measure, plot = svg._plot;
    const selected = pinById(st.selected);
    if ((ev.key === "Escape" || ev.key === "Delete" || ev.key === "Backspace") && selected) {
      removePin(svg, selected.id);
    } else if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
      const dir = ev.key === "ArrowRight" ? 1 : -1;
      const pin = selected || addPin(svg, hasValue(st.hover) ? st.hover : (plot.d0 + plot.d1) / 2);
      pin.d = stepDistance(plot, pin.d, dir, ev.shiftKey ? 10 : 1);
      Object.assign(st, { selected: pin.id, hover: null });
      renderMeasure(svg);
    } else {
      return;
    }
    ev.preventDefault();
  });
  const readout = readoutFor(svg);
  if (readout) {
    readout.addEventListener("click", (ev) => {
      const button = ev.target.closest(".pin-remove");
      if (button && ready()) removePin(svg, Number(button.dataset.pin));
    });
  }
}

async function submit(ev) {
  ev.preventDefault();
  $("#error").hidden = true;
  $$("input.invalid").forEach((i) => i.classList.remove("invalid"));
  const problem = sitesProblem();
  if (problem) {
    showError(problem);
    return;
  }
  const btn = $("#generate");
  btn.disabled = true;
  $("#busy").hidden = false;
  const request = { ...buildRequest(), progress: true };
  const picks = [pickedNode("site_a"), pickedNode("site_b")];
  const feedHeights = SITE_KEYS.map(feedHeight);
  showProgress({ stage: "start" });
  try {
    const resp = await fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    if (!resp.ok) {
      let data = {};
      try {
        data = await resp.json();
      } catch (err) {
        /* not JSON: report the status */
      }
      showError(data.error || `Server returned HTTP ${resp.status}.`, data.field);
      return;
    }
    let data = null;
    await readLines(resp, (item) => {
      if (item.progress) showProgress(item.progress);
      else data = item;
    });
    if (!data) throw new Error("The server's answer ended early.");
    if (data.error) {
      showError(data.error, data.field);
      return;
    }
    showResult(data.result, request.options.units, picks, feedHeights);
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    btn.disabled = false;
    $("#busy").hidden = true;
    $("#progress").hidden = true;
  }
}

// The API streams one JSON object per line while it works.
async function readLines(resp, onItem) {
  const handle = (line) => {
    if (line.trim()) onItem(JSON.parse(line));
  };
  if (!resp.body || !resp.body.getReader) {
    (await resp.text()).split("\n").forEach(handle);
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffered += decoder.decode(value, { stream: true });
    const lines = buffered.split("\n");
    buffered = lines.pop();
    lines.forEach(handle);
  }
  handle(buffered + decoder.decode());
}

function showProgress(p) {
  const bar = $("#progress-bar");
  const text = $("#progress-text");
  $("#progress").hidden = false;
  if (p.stage === "lidar" && p.total) {
    bar.value = p.done / p.total;
    text.textContent = `Fetching lidar: ${Math.round((100 * p.done) / p.total)}% `
      + `(${p.done.toLocaleString()} of ${p.total.toLocaleString()} samples)`;
    return;
  }
  bar.removeAttribute("value");
  text.textContent = { start: "Starting\u2026", ground: "Looking up ground elevations\u2026",
    report: "Building the report\u2026" }[p.stage] || "Working\u2026";
}

function download() {
  if (!lastKml) return;
  const blob = new Blob([lastKml], { type: "application/vnd.google-earth.kml+xml" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = lastFilename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function disableLidar() {
  const radio = $("input[name=tree_mode][value=lidar]");
  radio.disabled = true;
  $("#lidar-choice").classList.add("disabled");
  if (radio.checked) {
    setTreeMode("trees");
    save();
  }
}

async function loadSources() {
  try {
    const resp = await fetch(`${API}?sources`);
    if (!resp.ok) return;
    const data = await resp.json();
    const sel = $("#elev_source");
    for (const s of data.sources || []) {
      if (s.name === "auto") continue;
      const opt = document.createElement("option");
      opt.value = s.name;
      opt.textContent = `${s.name} (${s.type})`;
      sel.appendChild(opt);
    }
    const surface = data.surface || [];
    $("#lidar-choice").title = surface.length ? `Lidar from ${surface.map((s) => s.label).join(", ")}`
      : "No lidar sources are configured on this server";
    if (!surface.length) disableLidar();
  } catch (err) {
    /* the dropdown keeps just "auto" */
  }
}

function syncSiteName(key) {
  const name = $("input[name=name]", siteFieldset(key)).value.trim();
  $(`.site-name[data-site=${key}]`).textContent = name || (key === "site_a" ? "Site A" : "Site B");
}

function init() {
  ["site_a", "site_b"].forEach((k) => setupSite(siteFieldset(k)));
  $("#color_random").addEventListener("change", (e) => { $("#color").disabled = e.target.checked; });
  $("#terrain_check").addEventListener("change", updateLookupHint);
  $("#units").addEventListener("change", () => {
    updateUnitLabels();
    unitsChanged();
  });
  $$("input[name=tree_mode]").forEach((r) => r.addEventListener("change", () => {
    syncTreeInputs();
    updateLookupHint();
  }));
  syncTreeInputs();
  for (const key of ["site_a", "site_b"]) {
    $("input[name=name]", siteFieldset(key)).addEventListener("input", () => syncSiteName(key));
    syncSiteName(key);
  }
  updateUnitLabels();
  $("#link-form").addEventListener("submit", submit);
  $("#download").addEventListener("click", download);
  const curl = $("#curl-example");
  curl.textContent = curl.textContent.replace("https://HOST/site/api/fresnel.cgi", new URL(API, location.href).href);
  updateLookupHint();
  loadSources();
  loadMapConfig();
  setupMapViews();
  MEASURED_PLOTS.forEach((sel) => setupMeasure($(sel)));
  const curvature = $("#show_curvature");
  const syncCurvature = () => {
    $("#profile-box").classList.toggle("hide-curvature", !curvature.checked);
    MEASURED_PLOTS.forEach((sel) => renderMeasure($(sel)));
  };
  curvature.addEventListener("change", syncCurvature);
  syncCurvature();
  initSites();
}

init();
