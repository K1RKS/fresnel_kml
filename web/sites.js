"use strict";

// Site A / Site B selection: mesh node picks (Nodographer feeds via GET ?nodes) act as
// defaults, edits are highlighted overrides, and the choice is saved across reloads.
// Loaded before app.js, whose helpers ($, esc, dfmt, FEET...) these functions use.

let meshNodes = [];
const nodeById = new Map();
let nodeWarnings = [];
const MAX_MATCHES = 20;
const SITE_KEYS = ["site_a", "site_b"];
const STORE_KEY = "fresnelkml.sites";
const TRACKED = ["name", "lat", "lon", "height_m", "height_ref", "ground_m"];
const FIELD_INPUT = { height_m: "height", ground_m: "ground" };
const FIELD_LABELS = { name: "name", lat: "latitude", lon: "longitude", height_m: "height",
  height_ref: "height reference", ground_m: "ground elevation" };
const URL_FIELDS = { name: "name", lat: "lat", lon: "lon", pos: "pos", h: "height_m", ref: "height_ref", gnd: "ground_m" };
// Tree inputs, kept as typed: they are in the page's height units, which are saved alongside.
const TREE_FIELDS = { th: "tree_height", ta: "tree_clearance_a", tb: "tree_clearance_b" };
// AREDN's antenna height is above ground, but some nodes enter their height above sea level there.
const TALL_AGL_M = 100 * 0.3048;  // warn when the computed height above ground is over this
const ASL_GUESS_M = 200 * 0.3048;  // above this, take the height as above sea level

// link: in link selection, the RF link chosen for Site B when its node has no location in the feed.
function blankSite() {
  return { node: null, snap: null, link: null, ov: {} };
}

// sites[key].ov holds, for a picked node, only the fields that differ from the node (heights
// in metres); for a site without a node it holds every value entered.
const siteState = { mode: "node", sites: { site_a: blankSite(), site_b: blankSite() }, freq_ov: null };
const missingNotes = {};
let restoring = null;
let stateReady = false;
let applying = false;  // applySite is writing the form, so its events aren't user edits

function otherKey(key) {
  return key === "site_a" ? "site_b" : "site_a";
}

function pickedNode(key) {
  const s = siteState.sites[key];
  return s.node ? nodeById.get(s.node) || s.snap : null;
}

function hasHeight(node) {
  return Boolean(node) && node.height_m !== null && node.height_m !== undefined;
}

function pageUnits() {
  return $("#units").value;
}

function toDisplay(metres) {
  const v = pageUnits() === "ft" ? metres / FEET : metres;
  return String(Number(v.toFixed(1)));
}

function fieldEl(fs, name) {
  return name === "height_ref" ? $("select[name=height_ref]", fs) : $(`input[name=${FIELD_INPUT[name] || name}]`, fs);
}

function nodeDefaults(node) {
  return {
    name: node.name,
    lat: node.lat.toFixed(7),
    lon: node.lon.toFixed(7),
    height_m: hasHeight(node) ? node.height_m : null,
    height_ref: "agl",
    ground_m: null,
  };
}

function readField(fs, name) {
  const raw = fieldEl(fs, name).value.trim();
  if (raw === "") return null;
  if (name === "height_m" || name === "ground_m") {
    const v = Number(raw);
    return pageUnits() === "ft" ? v * FEET : v;
  }
  return raw;
}

function writeField(fs, name, value) {
  const el = fieldEl(fs, name);
  if (name === "height_m" || name === "ground_m") el.value = value === null || value === undefined ? "" : toDisplay(value);
  else if (name === "height_ref") el.value = value === "asl" ? "asl" : "agl";
  else el.value = value ?? "";
}

function sameValue(name, a, b) {
  if (a === null || b === null) return a === b;
  if (name === "height_m" || name === "ground_m") return toDisplay(a) === toDisplay(b);
  if ((name === "lat" || name === "lon") && DECIMAL_RE.test(a) && DECIMAL_RE.test(b)) {
    return Math.abs(Number(a) - Number(b)) < 5e-8;
  }
  return a === b;
}

function describeValue(name, value) {
  if (name === "height_m" || name === "ground_m") return `${toDisplay(value)} ${pageUnits()}`;
  if (name === "height_ref") return value === "asl" ? "above sea level" : "above ground";
  return value;
}

// ----- Defaults and overrides -----

function syncOverrides(key) {
  if (applying) return;
  const fs = siteFieldset(key);
  const s = siteState.sites[key];
  const node = pickedNode(key);
  if (!node) {
    s.ov = {};
    for (const name of TRACKED) {
      const v = readField(fs, name);
      if (v !== null) s.ov[name] = v;
    }
    if (usesSinglePosition(fs)) {
      s.ov.single = true;
      const pos = $("input[name=position]", fs).value.trim();
      if (pos) s.ov.pos = pos;
    }
  } else {
    const d = nodeDefaults(node);
    for (const name of TRACKED) {
      const v = readField(fs, name);
      if (v === null || sameValue(name, v, d[name])) delete s.ov[name];
      else s.ov[name] = v;
    }
  }
  refreshOverrides(key);
  save();
}

// A cleared field goes back to the node's value.
function restoreEmpty(key) {
  const node = pickedNode(key);
  if (!node) return;
  const fs = siteFieldset(key);
  const d = nodeDefaults(node);
  for (const name of TRACKED) {
    if (d[name] !== null && fieldEl(fs, name).value.trim() === "") {
      writeField(fs, name, d[name]);
      fieldEl(fs, name).classList.remove("invalid");
    }
  }
}

function refreshOverrides(key) {
  const fs = siteFieldset(key);
  const s = siteState.sites[key];
  const node = pickedNode(key);
  const d = node ? nodeDefaults(node) : null;
  const changed = [];
  for (const name of TRACKED) {
    const el = fieldEl(fs, name);
    const on = Boolean(d) && d[name] !== null && name in s.ov;
    el.classList.toggle("overridden", on);
    if (on) {
      el.title = `Mesh node value: ${describeValue(name, d[name])}`;
      changed.push(FIELD_LABELS[name]);
    } else {
      el.removeAttribute("title");
    }
  }
  $(".toggle-pos", fs).hidden = Boolean(node);
  const note = $(".node-note", fs);
  if (!node) {
    note.textContent = missingNotes[key] || "";
    return;
  }
  let text = `From mesh node ${node.name}${node.antenna ? ` (${node.antenna})` : ""}.`;
  if (!nodeById.has(s.node)) {
    text = meshNodes.length ? `${node.name} is no longer in the feed; using the saved values.`
      : `The node feed is unavailable; using the saved values for ${node.name}.`;
  }
  if (!hasHeight(node)) text += " No antenna height in the feed; enter it.";
  else if (!("height_m" in s.ov)) text += heightNote(node, s.ov.height_ref === "asl");
  if (changed.length) text += ` Changed from ${node.name}: ${changed.join(", ")}. Clear a field or set it back to restore it.`;
  note.textContent = text;
}

function heightNote(node, asl) {
  if (!asl || !(node.height_m > ASL_GUESS_M)) return "";
  return ` Its antenna height of ${describeValue("height_m", node.height_m)} is over ${describeValue("height_m", ASL_GUESS_M)},`
    + " so it's taken as height above sea level (AREDN asks for height above ground). Set it back to above ground"
    + " if the tower really is that tall.";
}

// The site's antenna height is the picked node's own value from the feed.
function feedHeight(key) {
  return hasHeight(pickedNode(key)) && !("height_m" in siteState.sites[key].ov);
}

// Put the node's values (or the saved manual values) plus any overrides into the form.
function applySite(key) {
  const fs = siteFieldset(key);
  const s = siteState.sites[key];
  const node = pickedNode(key);
  const single = !node && Boolean(s.ov.single);
  applying = true;
  try {
    if (usesSinglePosition(fs) !== single) $(".toggle-pos", fs).click();
  } finally {
    applying = false;
  }
  const d = node ? nodeDefaults(node) : {};
  for (const name of TRACKED) {
    writeField(fs, name, name in s.ov ? s.ov[name] : (node ? d[name] : null));
    fieldEl(fs, name).classList.remove("invalid");
  }
  $("input[name=position]", fs).value = single ? s.ov.pos || "" : "";
  $(".ground-row", fs).hidden = $("select[name=height_ref]", fs).value === "asl";
  $("input[name=node_search]", fs).value = node ? node.name : "";
  $(".decoded", fs).textContent = "";
  syncSiteName(key);
  refreshOverrides(key);
}

function defaultFreqGhz() {
  const a = pickedNode("site_a");
  const b = pickedNode("site_b");
  const mhz = (a && a.freq_mhz) || (b && b.freq_mhz);
  return mhz ? Number((mhz / 1000).toFixed(4)) : null;
}

function applyFreq() {
  const d = defaultFreqGhz();
  if (siteState.freq_ov !== null) $("#freq_ghz").value = String(siteState.freq_ov);
  else if (d !== null) $("#freq_ghz").value = String(d);
  refreshFreq();
}

function syncFreq() {
  const raw = $("#freq_ghz").value.trim();
  const d = defaultFreqGhz();
  siteState.freq_ov = raw === "" || (d !== null && Math.abs(Number(raw) - d) < 1e-9) ? null : Number(raw);
  refreshFreq();
  save();
}

function refreshFreq() {
  const el = $("#freq_ghz");
  const d = defaultFreqGhz();
  const on = d !== null && siteState.freq_ov !== null;
  el.classList.toggle("overridden", on);
  if (on) el.title = `Mesh node value: ${d} GHz`;
  else el.removeAttribute("title");
}

function pickSite(key, node) {
  const s = siteState.sites[key];
  s.node = node.id;
  s.snap = node;
  s.link = null;
  s.ov = hasHeight(node) && node.height_m > ASL_GUESS_M ? { height_ref: "asl" } : {};
  delete missingNotes[key];
  siteState.freq_ov = null;
  $("#error").hidden = true;
  applySite(key);
  if (key === "site_a") keepLinkedB();
  applyFreq();
  fillLinkOptions();
  updateLookupHint();
  save();
}

function unmappedNote(link) {
  return `${link.name || link.id} has no location in the node feed; enter its latitude, longitude and antenna height.`;
}

// A link to a node the feed has no location for: Site B gets its name and the link's frequency,
// and the user enters the rest.
function pickUnmappedLink(link) {
  siteState.sites.site_b = { node: null, snap: null, link: link.id, ov: { name: link.name || link.id, height_ref: "agl" } };
  missingNotes.site_b = unmappedNote(link);
  siteState.freq_ov = null;
  $("#error").hidden = true;
  applySite("site_b");
  applyFreq();
  updateLookupHint();
  save();
}

function chosenLink() {
  const b = siteState.sites.site_b;
  return b.node || b.link;
}

// Clearing the Mesh node box drops the pick; the site keeps its values as manual entry.
function clearPick(key) {
  const s = siteState.sites[key];
  if (!s.node) return;
  s.node = null;
  s.snap = null;
  syncOverrides(key);
  syncFreq();
  fillLinkOptions();
  updateLookupHint();
}

function unitsChanged() {
  for (const key of SITE_KEYS) {
    if (pickedNode(key)) applySite(key);
    else syncOverrides(key);
  }
  fillLinkOptions();
  save();
}

function setupTracking(key) {
  const fs = siteFieldset(key);
  const tracked = (el) => ["name", "lat", "lon", "position", "height", "height_ref", "ground"].includes(el.name);
  fs.addEventListener("input", (ev) => { if (tracked(ev.target)) syncOverrides(key); });
  fs.addEventListener("change", (ev) => {
    if (!tracked(ev.target)) return;
    restoreEmpty(key);
    syncOverrides(key);
    updateLookupHint();
  });
  $(".toggle-pos", fs).addEventListener("click", () => syncOverrides(key));
}

// ----- Mesh node type-ahead -----

function nodeExtra(node) {
  const units = pageUnits();
  const parts = [];
  if (hasHeight(node)) parts.push(`Ht ${hfmt(node.height_m, 0, units)}`);
  if (node.freq_mhz) parts.push(`${node.freq_mhz} MHz`);
  return parts.join(" \u00b7 ");
}

function rfLinks(node) {
  return (node.links || []).filter((l) => l.type === "RF" && l.id !== node.id)
    .sort((x, y) => (x.distance_km ?? Infinity) - (y.distance_km ?? Infinity));
}

function linkWhere(link, units) {
  return [link.distance_km !== null ? dfmt(link.distance_km * 1000, 1, units) : null,
    link.bearing_deg !== null ? `${link.bearing_deg.toFixed(0)}\u00b0` : null].filter(Boolean).join(", ");
}

// Rows for the dropdown: the other site's RF links first, then name matches.
function suggestions(key, query) {
  const q = query.trim().toLowerCase();
  const other = pickedNode(otherKey(key));
  const self = pickedNode(key);
  const rows = [];
  const shown = new Set(self ? [self.id] : []);
  if (other) {
    const units = pageUnits();
    const near = rfLinks(other)
      .filter((l) => nodeById.has(l.id) && !shown.has(l.id))
      .map((l) => ({ node: nodeById.get(l.id), link: l }))
      .filter(({ node }) => !q || node.name.toLowerCase().includes(q));
    if (near.length) {
      rows.push({ group: `RF links of ${other.name}` });
      for (const { node, link } of near) {
        rows.push({ node, extra: [linkWhere(link, units), nodeExtra(node)].filter(Boolean).join(" \u00b7 ") });
        shown.add(node.id);
      }
    }
  }
  if (q) {
    const matches = meshNodes.filter((n) => !shown.has(n.id) && n.name.toLowerCase().includes(q));
    matches.sort((x, y) => Number(!x.name.toLowerCase().startsWith(q)) - Number(!y.name.toLowerCase().startsWith(q)));
    if (rows.length && matches.length) rows.push({ group: "All nodes" });
    for (const node of matches.slice(0, MAX_MATCHES)) rows.push({ node, extra: nodeExtra(node) });
    if (!matches.length && !rows.length) rows.push({ empty: "No matching node" });
  }
  return rows;
}

function markMatch(name, q) {
  const at = q ? name.toLowerCase().indexOf(q) : -1;
  if (at < 0) return esc(name);
  return esc(name.slice(0, at)) + `<b>${esc(name.slice(at, at + q.length))}</b>` + esc(name.slice(at + q.length));
}

function setupPicker(key) {
  const fs = siteFieldset(key);
  const input = $("input[name=node_search]", fs);
  const list = $(".node-list", fs);
  let rows = [];
  let active = -1;
  let typed = "";  // what the user typed, without the inline completion

  const completing = () => input.selectionStart === typed.length && input.selectionEnd > typed.length;
  // Show the rest of the active row's name, selected, so typing over it keeps filtering.
  const complete = () => {
    const row = rows[active];
    const q = typed.trim().toLowerCase();
    if (q && row && row.node && row.node.name.toLowerCase().startsWith(typed.toLowerCase())) {
      input.value = typed + row.node.name.slice(typed.length);
      input.setSelectionRange(typed.length, input.value.length);
    } else if (input.value !== typed) {
      input.value = typed;
    }
  };
  const close = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    active = -1;
  };
  const highlight = () => {
    $$("li", list).forEach((li) => {
      const on = Number(li.dataset.index) === active;
      li.classList.toggle("active", on);
      li.setAttribute("aria-selected", on ? "true" : "false");
      if (on) li.scrollIntoView({ block: "nearest" });
    });
  };
  const open = (autocomplete = false) => {
    rows = suggestions(key, typed);
    const q = typed.trim().toLowerCase();
    const prefix = q ? rows.findIndex((r) => r.node && r.node.name.toLowerCase().startsWith(typed.toLowerCase())) : -1;
    active = prefix >= 0 ? prefix : rows.findIndex((r) => r.node);
    if (!rows.length) return close();
    list.innerHTML = rows.map((r, i) => {
      if (r.group) return `<li class="group" role="presentation">${esc(r.group)}</li>`;
      if (r.empty) return `<li class="empty" role="presentation">${esc(r.empty)}</li>`;
      return `<li role="option" data-index="${i}"><span>${markMatch(r.node.name, q)}</span><small>${esc(r.extra)}</small></li>`;
    }).join("");
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    highlight();
    if (autocomplete && prefix >= 0) complete();
  };
  const choose = (i) => {
    const row = rows[i];
    if (!row || !row.node) return;
    close();
    pickSite(key, row.node);
    typed = input.value;
  };
  const step = (dir) => {
    const idx = rows.map((r, i) => (r.node ? i : -1)).filter((i) => i >= 0);
    if (!idx.length) return;
    const pos = idx.indexOf(active);
    active = idx[(pos + dir + idx.length) % idx.length];
    highlight();
    complete();
  };

  input.addEventListener("input", (ev) => {
    typed = input.value;
    if (!typed.trim()) clearPick(key);
    // Deleting must not re-add the completion the user just removed
    open(!(ev.inputType || "").startsWith("delete"));
  });
  input.addEventListener("focus", () => { typed = input.value; open(); });
  input.addEventListener("click", () => { if (list.hidden) { typed = input.value; open(); } });
  input.addEventListener("blur", () => {
    if (completing()) input.value = typed;
    if (!input.value.trim()) clearPick(key);
    else if (pickedNode(key) && input.value !== pickedNode(key).name) input.value = pickedNode(key).name;
    setTimeout(close, 150);
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      if (list.hidden) open();
      else step(ev.key === "ArrowDown" ? 1 : -1);
    } else if (ev.key === "Enter" || (ev.key === "Tab" && !ev.shiftKey && completing())) {
      if (!list.hidden && active >= 0) {
        ev.preventDefault();
        choose(active);
      }
    } else if (ev.key === "Escape") {
      if (completing()) input.value = typed;
      close();
    }
  });
  list.addEventListener("mousedown", (ev) => {
    const li = ev.target.closest("li[data-index]");
    if (!li) return;
    ev.preventDefault();
    choose(Number(li.dataset.index));
  });
}

// ----- Node selection / Link selection -----

function renderMode() {
  const link = siteState.mode === "link";
  const have = meshNodes.length > 0;
  $(".mode-tabs").hidden = !have;
  $$(".mode-tabs [role=tab]").forEach((tab) => {
    const on = tab.dataset.mode === siteState.mode;
    tab.setAttribute("aria-selected", on ? "true" : "false");
    tab.classList.toggle("active", on);
    tab.tabIndex = on ? 0 : -1;
  });
  $(".node-pick", siteFieldset("site_a")).hidden = !have;
  $(".node-pick", siteFieldset("site_b")).hidden = !have || link;
  $(".link-pick", siteFieldset("site_b")).hidden = !link;
}

function setMode(mode) {
  if (mode === siteState.mode) return;
  siteState.mode = mode;
  if (mode === "node" && siteState.sites.site_b.link) {
    siteState.sites.site_b.link = null;
    delete missingNotes.site_b;
    refreshOverrides("site_b");
  }
  keepLinkedB();
  renderMode();
  fillLinkOptions();
  applyFreq();
  updateLookupHint();
  save();
}

// In link selection, Site B must be one of Site A's RF links.
function keepLinkedB() {
  const a = pickedNode("site_a");
  const id = chosenLink();
  if (siteState.mode !== "link" || !a || !id || rfLinks(a).some((l) => l.id === id)) return;
  siteState.sites.site_b = { node: null, snap: null, link: null, ov: { height_ref: "agl" } };
  delete missingNotes.site_b;
  applySite("site_b");
}

function fillLinkOptions() {
  const sel = $("select[name=link]", siteFieldset("site_b"));
  const a = pickedNode("site_a");
  $(".link-from").textContent = a ? a.name : "Site A";
  if (!a) {
    sel.innerHTML = '<option value="">Pick Site A first</option>';
    sel.disabled = true;
    return;
  }
  const links = rfLinks(a);
  const units = pageUnits();
  const opts = [`<option value="">${links.length ? "Choose a link\u2026" : esc(`No RF links from ${a.name}`)}</option>`];
  for (const l of links) {
    const n = nodeById.get(l.id);
    const parts = [linkWhere(l, units), l.signal_dbm !== null ? `${l.signal_dbm.toFixed(0)} dBm` : null].filter(Boolean);
    const label = `${n ? n.name : l.name || l.id}${parts.length ? " \u2014 " + parts.join(", ") : ""}`
      + (n ? "" : " (no location in the feed)");
    opts.push(`<option value="${esc(l.id)}">${esc(label)}</option>`);
  }
  sel.innerHTML = opts.join("");
  sel.disabled = !links.length;
  const id = chosenLink();
  sel.value = id && links.some((l) => l.id === id) ? id : "";
}

// Why Generate Report can't run yet, or null.
function sitesProblem() {
  if (siteState.mode !== "link") return null;
  const a = pickedNode("site_a");
  if (!a) return "In link selection, Site A must be a mesh node: pick one in its Mesh node box.";
  const id = chosenLink();
  if (!id || !rfLinks(a).some((l) => l.id === id)) {
    return rfLinks(a).length ? `Choose one of ${a.name}'s links for Site B.`
      : `${a.name} has no RF links in the feed; use Node selection to enter Site B.`;
  }
  return null;
}

function setupModes() {
  const tabs = $$(".mode-tabs [role=tab]");
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => setMode(tab.dataset.mode));
    tab.addEventListener("keydown", (ev) => {
      if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
      const next = tabs[(i + (ev.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      next.focus();
      setMode(next.dataset.mode);
    });
  });
  $("select[name=link]", siteFieldset("site_b")).addEventListener("change", (ev) => {
    const node = nodeById.get(ev.target.value);
    const a = pickedNode("site_a");
    const link = a && rfLinks(a).find((l) => l.id === ev.target.value);
    if (node) pickSite("site_b", node);
    else if (link) pickUnmappedLink(link);
  });
}

// ----- Saved state: this browser (localStorage) and shareable links -----

function save() {
  if (!stateReady) return;
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({ v: 1, mode: siteState.mode, units: pageUnits(),
      sites: siteState.sites, freq_ov: siteState.freq_ov, trees: treeState() }));
  } catch (err) {
    /* storage full or disabled: the page still works, it just won't remember */
  }
}

function treeState() {
  const t = { on: $("#trees").checked };
  for (const [short, id] of Object.entries(TREE_FIELDS)) t[short] = $(`#${id}`).value.trim();
  return t;
}

function applyTrees(t) {
  if (!t || typeof t !== "object") return;
  $("#trees").checked = t.on === true;
  for (const [short, id] of Object.entries(TREE_FIELDS)) {
    const v = t[short];
    if (typeof v === "string" && v !== "" && Number.isFinite(Number(v)) && Number(v) >= 0) $(`#${id}`).value = v;
  }
  $("#trees").dispatchEvent(new Event("change"));
}

function loadSaved() {
  try {
    const st = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
    return st && st.v === 1 && st.sites ? st : null;
  } catch (err) {
    return null;
  }
}

function toUrl() {
  const p = new URLSearchParams();
  p.set("mode", siteState.mode);
  p.set("u", pageUnits());
  if (siteState.freq_ov !== null) p.set("f", String(siteState.freq_ov));
  for (const [key, pre] of [["site_a", "a"], ["site_b", "b"]]) {
    const s = siteState.sites[key];
    if (s.node || s.link) p.set(pre, s.node || s.link);
    for (const [short, name] of Object.entries(URL_FIELDS)) {
      const v = s.ov[name];
      if (v === undefined || v === null) continue;
      p.set(`${pre}_${short}`, typeof v === "number" ? String(Number(v.toFixed(2))) : v);
    }
  }
  const t = treeState();
  if (t.on) {
    p.set("t", "1");
    for (const short of Object.keys(TREE_FIELDS)) if (t[short] !== "") p.set(short, t[short]);
  }
  const url = new URL(location.href);
  url.search = p.toString();
  url.hash = "";
  return url.href;
}

// State from ?mode=...&a=...; the parameters are then removed so later edits survive a reload.
function fromUrl() {
  const p = new URLSearchParams(location.search);
  const keys = ["mode", "f", "t", ...Object.keys(TREE_FIELDS),
    ...["a", "b"].flatMap((pre) => [pre, ...Object.keys(URL_FIELDS).map((k) => `${pre}_${k}`)])];
  if (!keys.some((k) => p.has(k))) return null;
  const f = Number(p.get("f"));
  const trees = { on: p.get("t") === "1" };
  for (const short of Object.keys(TREE_FIELDS)) trees[short] = p.get(short) || "";
  const st = { v: 1, mode: p.get("mode") === "link" ? "link" : "node", units: ["m", "ft"].includes(p.get("u")) ? p.get("u") : null,
    freq_ov: p.has("f") && Number.isFinite(f) && f > 0 ? f : null, trees, sites: {} };
  for (const [key, pre] of [["site_a", "a"], ["site_b", "b"]]) {
    const s = blankSite();
    s.node = p.get(pre) || null;
    for (const [short, name] of Object.entries(URL_FIELDS)) {
      if (!p.has(`${pre}_${short}`)) continue;
      const raw = p.get(`${pre}_${short}`);
      if (name.endsWith("_m")) {
        if (raw !== "" && Number.isFinite(Number(raw))) s.ov[name] = Number(raw);
      } else if (raw !== "") {
        s.ov[name] = raw;
      }
    }
    if (s.ov.pos) s.ov.single = true;
    st.sites[key] = s;
  }
  history.replaceState(null, "", location.pathname + location.hash);
  return st;
}

async function copyLink() {
  const url = toUrl();
  const status = $("#copy-status");
  try {
    await navigator.clipboard.writeText(url);
    status.textContent = "Link copied.";
  } catch (err) {
    window.prompt("Copy this link:", url);
    status.textContent = "";
  }
  setTimeout(() => { status.textContent = ""; }, 3000);
}

function finishRestore() {
  const st = restoring;
  restoring = null;
  for (const key of SITE_KEYS) {
    const saved = st && st.sites && st.sites[key];
    if (!saved) {
      syncOverrides(key);
      continue;
    }
    const s = siteState.sites[key];
    s.ov = saved.ov && typeof saved.ov === "object" ? { ...saved.ov } : {};
    s.node = typeof saved.node === "string" && saved.node ? saved.node : null;
    s.snap = (s.node && nodeById.get(s.node)) || saved.snap || null;
    s.link = typeof saved.link === "string" && saved.link ? saved.link : null;
    const a = key === "site_b" && pickedNode("site_a");
    const unmapped = a && rfLinks(a).find((l) => l.id === (s.link || (!s.snap && s.node)));
    if (unmapped) {
      s.node = null;
      s.snap = null;
      s.link = unmapped.id;
      if (!s.ov.name) s.ov.name = unmapped.name || unmapped.id;
      missingNotes[key] = unmappedNote(unmapped);
    } else if (s.node && !s.snap) {
      missingNotes[key] = meshNodes.length ? `Mesh node ${s.node} wasn't found in the feed; enter the site by hand.`
        : `The node feed is unavailable, so mesh node ${s.node} couldn't be loaded.`;
      s.node = null;
    }
    applySite(key);
  }
  if (st) siteState.freq_ov = typeof st.freq_ov === "number" ? st.freq_ov : null;
  if (!meshNodes.length) siteState.mode = "node";
  keepLinkedB();
  applyFreq();
  syncFreq();
  renderMode();
  fillLinkOptions();
  updateLookupHint();
  stateReady = true;
  save();
}

async function loadNodes() {
  try {
    const resp = await fetch(`${API}?nodes`);
    if (!resp.ok) return;
    const data = await resp.json();
    meshNodes = data.nodes || [];
    meshNodes.forEach((n) => nodeById.set(n.id, n));
    nodeWarnings = data.warnings || [];
  } catch (err) {
    /* no feeds: the pickers and tabs stay hidden */
  }
}

function initSites() {
  SITE_KEYS.forEach((key) => {
    setupPicker(key);
    setupTracking(key);
  });
  setupModes();
  $("#freq_ghz").addEventListener("input", syncFreq);
  $("#freq_ghz").addEventListener("change", () => {
    const d = defaultFreqGhz();
    if ($("#freq_ghz").value.trim() === "" && d !== null) $("#freq_ghz").value = String(d);
    syncFreq();
  });
  $("#copy-link").addEventListener("click", copyLink);
  $("#trees").addEventListener("change", save);
  for (const id of Object.values(TREE_FIELDS)) $(`#${id}`).addEventListener("input", save);
  restoring = fromUrl() || loadSaved();
  if (restoring) {
    if (restoring.units === "m" || restoring.units === "ft") {
      $("#units").value = restoring.units;
      updateUnitLabels();
    }
    siteState.mode = restoring.mode === "link" ? "link" : "node";
    applyTrees(restoring.trees);
  }
  renderMode();
  loadNodes().finally(finishRestore);
}
