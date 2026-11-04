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
  const terrain = tc === "on" || (tc === "auto" && lookup);
  let text;
  if (!lookup && !terrain) text = "No elevation lookup will be made.";
  else if (terrain) text = "Ground elevations along the path will be looked up for the terrain check.";
  else text = "Ground elevation will be looked up for the tower site(s) given above ground.";
  $("#lookup-hint").textContent = text;
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
    trees: $("#trees").checked,
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
function hfmt(metres, digits = 1) {
  if (metres === null || metres === undefined) return "unknown";
  const value = resultUnits === "ft" ? metres / FEET : metres;
  return `${value.toFixed(digits)} ${resultUnits}`;
}

const MILE = 1609.344;

// Short distances in m or ft, longer ones in km or miles.
function dfmt(metres, digits = 3) {
  if (resultUnits === "ft") {
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

function showResult(data, units) {
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
  }
  rows.push(["Elevation source", esc(r.elevation_sources.join(", ") || "none")]);
  $("#summary-table").innerHTML = rows.map(([k, v]) => `<tr><th>${k}</th><td>${v}</td></tr>`).join("");

  $("#swatch").style.background = r.zone_color.hex;
  $("#swatch-text").textContent = `Zone color ${r.zone_color.hex} (${r.zone_color.random ? "random" : "chosen"})`;
  if (r.zone_color.random) $("#color").value = r.zone_color.hex;

  $("#warnings").innerHTML = (data.warnings || []).map((w) => `<li>${esc(w)}</li>`).join("");
  drawProfile(r);
  $("#result").hidden = false;
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
  drawPlot($("#profile-main"), p, { d0: 0, d1: D, masts, from: "a" });
  const zoom = [[r.profile_a, "a", "#profile-a", "#profile-title-a", `First ${dfmt(r.end_window_m)} from ${a.name}`],
                [r.profile_b, "b", "#profile-b", "#profile-title-b", `Last ${dfmt(r.end_window_m)} to ${b.name}`]];
  for (const [pts, from, svgSel, titleSel, title] of zoom) {
    const show = pts && pts.length > 1;
    $(svgSel).hidden = $(titleSel).hidden = !show;
    if (!show) continue;
    $(titleSel).textContent = `${title} (distance ${from === "a" ? "from " + a.name : "to " + b.name})`;
    drawPlot($(svgSel), pts, { d0: pts[0].d_m, d1: pts[pts.length - 1].d_m, masts, from });
  }
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

// One terrain-profile chart. Every chart has the same pixel size; each picks its own X and Y scale.
// opts.from is "a" (label distances from site A) or "b" (label distances to site B).
function drawPlot(svg, p, opts) {
  const W = 800, H = 260, L = 60, R = 14, T = 26, B = 30;
  const D = opts.masts[1].d;
  const { d0, d1 } = opts;
  const span = d1 - d0 || 1;
  const eps = span * 1e-6;
  const visible = opts.masts.filter((m) => m.d >= d0 - eps && m.d <= d1 + eps);
  const ys = p.flatMap((q) => [q.effective_m, q.los_m, q.zone_bottom_m])
    .concat(p.filter((q) => q.tree_top_m !== null && q.tree_top_m !== undefined).map((q) => q.tree_top_m))
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
  for (const run of treeRuns(p)) {
    const top = run.map((q) => `${x(q.d_m).toFixed(1)},${y(q.tree_top_m).toFixed(1)}`);
    const bottom = run.slice().reverse().map((q) => `${x(q.d_m).toFixed(1)},${y(q.effective_m).toFixed(1)}`);
    parts.push(`<path class="trees" d="M${top.join("L")}L${bottom.join("L")}Z"/>`);
  }
  parts.push(`<path class="r60" d="${line("r60_bottom_m")}"/><path class="zone" d="${line("zone_bottom_m")}"/>`,
    `<path class="los" d="${line("los_m")}"/>`);
  for (const q of p.filter((q) => q.clearance !== null && q.clearance < 0.6)) {
    parts.push(`<circle class="obstruction" cx="${x(q.d_m).toFixed(1)}" cy="${y(q.effective_m).toFixed(1)}" r="3"><title>clearance ${q.clearance.toFixed(2)} \u00d7 r1 at ${dfmt(q.d_m, 2)} from ${esc(opts.masts[0].name)}</title></circle>`);
  }
  for (const q of p.filter((q) => q.tree_clearance !== null && q.tree_clearance !== undefined && q.tree_clearance < 0.6)) {
    parts.push(`<circle class="tree-obstruction" cx="${x(q.d_m).toFixed(1)}" cy="${y(q.tree_top_m).toFixed(1)}" r="3.5"><title>clearance over trees ${q.tree_clearance.toFixed(2)} \u00d7 r1 at ${dfmt(q.d_m, 2)} from ${esc(opts.masts[0].name)}</title></circle>`);
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
  svg.innerHTML = parts.join("");
}

async function submit(ev) {
  ev.preventDefault();
  $("#error").hidden = true;
  $$("input.invalid").forEach((i) => i.classList.remove("invalid"));
  const btn = $("#generate");
  btn.disabled = true;
  $("#busy").hidden = false;
  const request = buildRequest();
  try {
    const resp = await fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    let data;
    try {
      data = await resp.json();
    } catch (err) {
      throw new Error(`Server returned HTTP ${resp.status}.`);
    }
    if (!resp.ok) {
      showError(data.error || `Server returned HTTP ${resp.status}.`, data.field);
      return;
    }
    showResult(data, request.options.units);
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    btn.disabled = false;
    $("#busy").hidden = true;
  }
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
  } catch (err) {
    /* the dropdown keeps just "auto" */
  }
}

function init() {
  ["site_a", "site_b"].forEach((k) => setupSite(siteFieldset(k)));
  $("#color_random").addEventListener("change", (e) => { $("#color").disabled = e.target.checked; });
  $("#terrain_check").addEventListener("change", updateLookupHint);
  $("#units").addEventListener("change", updateUnitLabels);
  $("#trees").addEventListener("change", (e) => {
    for (const id of ["#tree_height", "#tree_clearance_a", "#tree_clearance_b"]) $(id).disabled = !e.target.checked;
  });
  for (const key of ["site_a", "site_b"]) {
    const nameInput = $("input[name=name]", siteFieldset(key));
    const sync = () => { $(`.site-name[data-site=${key}]`).textContent = nameInput.value.trim() || (key === "site_a" ? "Site A" : "Site B"); };
    nameInput.addEventListener("input", sync);
    sync();
  }
  updateUnitLabels();
  $("#link-form").addEventListener("submit", submit);
  $("#download").addEventListener("click", download);
  const curl = $("#curl-example");
  curl.textContent = curl.textContent.replace("https://HOST/site/api/fresnel.cgi", new URL(API, location.href).href);
  updateLookupHint();
  loadSources();
}

init();
