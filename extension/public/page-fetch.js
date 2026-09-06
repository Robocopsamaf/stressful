(() => {
  const responses = new Map(); // key -> { body, url }
  const waiters = new Map();

  function keyFromUrl(rawUrl) {
    try {
      const u = new URL(rawUrl, location.origin);
      const v = u.searchParams.get("v") || "";
      const lang = u.searchParams.get("lang") || "";
      const tlang = u.searchParams.get("tlang") || "";
      return `${v}|${lang}|${tlang}`;
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

  window.addEventListener("message", async (ev) => {
    if (ev.source !== window) return;
    const d = ev.data;
    if (!d || d.type !== "sr-captions-req") return;
    const { id, videoId, lang, timeoutMs } = d;
    console.log("[sr-bridge] req", { id, videoId, lang });

    const srcEntry = await waitFor(`${videoId || ""}|${lang}|`, timeoutMs ?? 120000);
    const src = srcEntry ? srcEntry.body : "";

    window.postMessage({ type: "sr-captions-resp", id, src }, "*");
  });
})();
