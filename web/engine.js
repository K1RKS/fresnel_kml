"use strict";

// Pyodide worker. It loads the fresnelkml package and answers the same requests the
// CGI script does. Progress is reported on this thread: Pyodide does not run the
// background thread the CGI stream uses.
//
// This is a classic worker. Pyodide's runtime is loaded with importScripts, which
// module workers do not provide.

importScripts("pyodide/pyodide.js");

const DRIVER = `
import json
import os
import sys

sys.path.insert(0, "/fresnel/lib")
os.environ["FRESNEL_CONFIG"] = "/fresnel/config.json"

from fresnelkml.cache import close_open_caches
from fresnelkml.webapi import handle_request, run_progress

def dispatch(method, query, body, progress):
    try:
        if progress is not None:
            item = run_progress(body.encode("utf-8"), None, progress)
            return json.dumps({"kind": "progress", "item": item}, ensure_ascii=False)
        raw = body.encode("utf-8") if body else b""
        resp = handle_request(method, query, raw)
        return json.dumps({"kind": "response", "status": resp.status,
                           "body": resp.body.decode("utf-8")}, ensure_ascii=False)
    finally:
        close_open_caches()
`;

let pyodide = null;
let persist = false;
const inbox = [];
let pumping = false;

function syncfs(read) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const done = (err) => {
      if (settled) return;
      settled = true;
      if (err) reject(err);
      else resolve();
    };
    try {
      const ret = pyodide.FS.syncfs(read, done);
      if (ret && typeof ret.then === "function") ret.then(() => done(), done);
    } catch (err) {
      done(err);
    }
  });
}

async function loadText(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`Could not load ${url} (HTTP ${resp.status}).`);
  return resp.text();
}

async function boot() {
  const base = new URL("./", self.location.href);
  const indexURL = new URL("pyodide/", base).href;
  pyodide = await loadPyodide({ indexURL, packageBaseUrl: indexURL });
  await pyodide.loadPackage("sqlite3");
  const names = JSON.parse(await loadText(new URL("lib/fresnelkml.json", base)));
  pyodide.FS.mkdirTree("/fresnel/lib/fresnelkml");
  for (const name of names) {
    const text = await loadText(new URL(`lib/fresnelkml/${name}`, base));
    pyodide.FS.writeFile(`/fresnel/lib/fresnelkml/${name}`, text);
  }
  pyodide.FS.writeFile("/fresnel/config.json", await loadText(new URL("config.json", base)));
  try {
    pyodide.FS.mkdirTree("/cache");
    pyodide.FS.mount(pyodide.FS.filesystems.IDBFS, {}, "/cache");
    await syncfs(true);
    persist = true;
  } catch (err) {
    persist = false;
  }
  pyodide.FS.mkdirTree("/cache/points");
  pyodide.runPython(DRIVER);
}

function pyDispatch(method, query, body, progressFn) {
  pyodide.globals.set("_method", method);
  pyodide.globals.set("_query", query);
  pyodide.globals.set("_body", body);
  // A JS null is not Python None, so a boolean selects the callback.
  pyodide.globals.set("_use_progress", progressFn != null);
  if (progressFn) pyodide.globals.set("_progress", progressFn);
  const text = pyodide.runPython(`
def _p(stage, done=None, total=None):
    _progress(stage, done, total)
dispatch(_method, _query, _body, _p if _use_progress else None)
`);
  const raw = typeof text === "string" ? text : String(text);
  return JSON.parse(raw);
}

function wantsProgress(body) {
  try {
    return JSON.parse(body).progress === true;
  } catch (err) {
    return false;
  }
}

async function handle(msg) {
  const { id, method, query, body } = msg;
  const progress = method === "POST" && !/(^|&)nodes(=|&|$)/.test(query || "") && wantsProgress(body || "")
      ? (stage, done, total) => {
        const num = (value) => {
          if (value === undefined || value === null) return null;
          const n = Number(value);
          return Number.isFinite(n) ? n : null;
        };
        self.postMessage({ id, item: { progress: { stage: String(stage), done: num(done), total: num(total) } } });
      }
    : null;
  try {
    const result = pyDispatch(method || "GET", query || "", body || "", progress);
    if (persist) {
      try {
        await syncfs(false);
      } catch (err) {
        /* a failed sync only drops the saved copy; this answer still stands */
      }
    }
    if (result.kind === "progress") self.postMessage({ id, done: true, item: result.item });
    else self.postMessage({ id, done: true, status: result.status, body: result.body });
  } catch (err) {
    self.postMessage({ id, done: true, error: (err && err.message) || String(err) });
  }
}

async function pump() {
  if (pumping) return;
  pumping = true;
  try {
    while (inbox.length && pyodide) {
      const msg = inbox.shift();
      await handle(msg);
    }
  } finally {
    pumping = false;
    if (inbox.length && pyodide) pump();
  }
}

self.onmessage = (ev) => {
  inbox.push(ev.data);
  if (pyodide) pump();
};

boot().then(() => {
  self.postMessage({ ready: true });
  pump();
}).catch((err) => {
  self.postMessage({ ready: true, error: (err && err.message) || String(err) });
});
