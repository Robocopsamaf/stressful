(() => {
  const responses = new Map(); // key -> { body, url }
  const waiters = new Map();

  function keyFromUrl(rawUrl) {
    try {
      const u = new URL(rawUrl, location.origin);
      const lang = u.searchParams.get("lang") || "";
      const tlang = u.searchParams.get("tlang") || "";
      return `${lang}|${tlang}`;
    } catch {
      return null;
    }
  }

  function deliver(rawUrl, body) {
    if (!rawUrl || !rawUrl.includes("/api/timedtext")) return;
    const key = keyFromUrl(rawUrl);
    console.log("[sr-bridge] deliver", { key, bytes: body.length });
    if (!key) return;
    responses.set(key, { body, url: rawUrl });
    const w = waiters.get(key);
    if (w) {
      waiters.delete(key);
      w({ body, url: rawUrl });
    }
  }

  const origFetch = window.fetch;
  window.fetch = function (...args) {
    const promise = origFetch.apply(this, args);
    try {
      const input = args[0];
      const url = typeof input === "string" ? input : input && input.url;
      if (url && url.includes("/api/timedtext")) {
        promise
          .then((resp) => {
            try {
              resp
                .clone()
                .text()
                .then((body) => deliver(url, body))
                .catch(() => {});
            } catch {}
          })
          .catch(() => {});
      }
    } catch {}
    return promise;
  };

  const OrigXHR = window.XMLHttpRequest;
  function PatchedXHR() {
    const xhr = new OrigXHR();
    let savedUrl = null;
    const origOpen = xhr.open;
    xhr.open = function (method, url) {
      savedUrl = String(url);
      return origOpen.apply(xhr, arguments);
    };
    xhr.addEventListener("load", () => {
      if (savedUrl && savedUrl.includes("/api/timedtext")) {
        deliver(savedUrl, xhr.responseText || "");
      }
    });
    return xhr;
  }
  PatchedXHR.prototype = OrigXHR.prototype;
  window.XMLHttpRequest = PatchedXHR;

  function waitFor(key, timeoutMs) {
    if (responses.has(key)) return Promise.resolve(responses.get(key));
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        if (waiters.get(key)) {
          waiters.delete(key);
          resolve(null);
        }
      }, timeoutMs);
      waiters.set(key, (v) => {
        clearTimeout(timer);
        resolve(v);
      });
    });
  }

  async function fetchTranslated(baseUrl, tlang) {
    try {
      const u = new URL(baseUrl, location.origin);
      u.searchParams.set("tlang", tlang);
      const resp = await origFetch(u.toString(), { credentials: "include" });
      const body = await resp.text();
      console.log("[sr-bridge] translation fetch", { tlang, status: resp.status, bytes: body.length });
      return body;
    } catch (e) {
      console.warn("[sr-bridge] translation fetch failed", e);
      return "";
    }
  }

  window.addEventListener("message", async (ev) => {
    if (ev.source !== window) return;
    const d = ev.data;
    if (!d || d.type !== "sr-captions-req") return;
    const { id, lang, tlang, timeoutMs } = d;
    console.log("[sr-bridge] req", { id, lang, tlang });

    const ruEntry = await waitFor(`${lang}|`, timeoutMs ?? 120000);
    const ru = ruEntry ? ruEntry.body : "";

    let tr = "";
    if (tlang && tlang !== lang && ruEntry && ruEntry.url) {
      tr = await fetchTranslated(ruEntry.url, tlang);
    }

    window.postMessage({ type: "sr-captions-resp", id, ru, tr }, "*");
  });
})();
