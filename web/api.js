"use strict";

// Talks to engine.js (a Pyodide worker) with the same shape the page used for fresnel.cgi.
// `var` so app.js and sites.js, which are separate classic scripts, can see these.

var API = "api/fresnel.cgi";

(function () {
  const engineSrc = (document.currentScript && document.currentScript.dataset.engine) || "engine.js";
  let worker = null;
  let seq = 0;
  const pending = new Map();
  let settled = false;
  let resolveReady, rejectReady;
  const engineReady = new Promise((resolve, reject) => {
    resolveReady = resolve;
    rejectReady = reject;
  });

  function setEngineStatus(text, bad) {
    const el = document.getElementById("engine-status");
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || "";
    el.classList.toggle("bad", Boolean(bad));
  }

  function failEngine(err) {
    const message = (err && err.message) || String(err || "The analysis engine failed to load.");
    setEngineStatus(message, true);
    if (!settled) {
      settled = true;
      rejectReady(new Error(message));
    }
    for (const waiter of pending.values()) waiter.reject(new Error(message));
    pending.clear();
  }

  function bootEngine() {
    const button = document.getElementById("generate");
    if (button) button.disabled = true;
    setEngineStatus("Loading the analysis engine\u2026");
    try {
      worker = new Worker(engineSrc);
    } catch (err) {
      failEngine(err);
      return;
    }
    worker.onmessage = (ev) => {
      const msg = ev.data || {};
      if (msg.ready) {
        if (msg.error) {
          failEngine(new Error(msg.error));
          return;
        }
        setEngineStatus("");
        if (button) button.disabled = false;
        if (!settled) {
          settled = true;
          resolveReady();
        }
        return;
      }
      const waiter = pending.get(msg.id);
      if (!waiter) return;
      if (msg.item && !msg.done && waiter.onItem) waiter.onItem(msg.item);
      if (msg.done) {
        pending.delete(msg.id);
        if (msg.error) waiter.reject(new Error(msg.error));
        else waiter.resolve(msg);
      }
    };
    worker.onerror = (ev) => {
      failEngine(new Error(ev.message || "The analysis engine failed to load."));
    };
  }

  function callEngine(message) {
    const id = ++seq;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject, onItem: message.onItem });
      worker.postMessage({
        id,
        method: message.method,
        query: message.query || "",
        body: message.body || "",
      });
    });
  }

  function lineStream() {
    const encoder = new TextEncoder();
    const chunks = [];
    let waiter = null;
    let closed = false;
    const wake = () => {
      if (!waiter) return;
      const resume = waiter;
      waiter = null;
      resume();
    };
    return {
      push(obj) {
        chunks.push(encoder.encode(JSON.stringify(obj) + "\n"));
        wake();
      },
      close() {
        closed = true;
        wake();
      },
      getReader() {
        return {
          async read() {
            while (!chunks.length && !closed) {
              await new Promise((resolve) => { waiter = resolve; });
            }
            if (!chunks.length) return { done: true, value: undefined };
            return { done: false, value: chunks.shift() };
          },
        };
      },
    };
  }

  function wantsProgress(body) {
    try {
      return JSON.parse(body).progress === true;
    } catch (err) {
      return false;
    }
  }

  async function apiFetch(url, options) {
    try {
      await engineReady;
    } catch (err) {
      throw new Error(err.message || "The analysis engine is not available.");
    }
    const parsed = new URL(url, location.href);
    const method = ((options && options.method) || "GET").toUpperCase();
    const body = (options && options.body) || "";
    const query = parsed.search.replace(/^\?/, "");
    if (method === "POST" && !/(^|&)nodes(=|&|$)/.test(query) && wantsProgress(body)) {
      const stream = lineStream();
      callEngine({
        method, query, body,
        onItem: (item) => stream.push(item),
      }).then((msg) => {
        if (msg.item) stream.push(msg.item);
        stream.close();
      }, (err) => {
        stream.push({ error: err.message || String(err), status: 500 });
        stream.close();
      });
      return { ok: true, status: 200, body: stream, json: async () => null, text: async () => "" };
    }
    const msg = await callEngine({ method, query, body });
    let data = null;
    if (msg.body) {
      try {
        data = JSON.parse(msg.body);
      } catch (err) {
        data = null;
      }
    }
    return {
      ok: msg.status >= 200 && msg.status < 300,
      status: msg.status,
      json: async () => data,
      text: async () => msg.body || "",
      body: null,
    };
  }

  window.apiFetch = apiFetch;
  bootEngine();
})();
