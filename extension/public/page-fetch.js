(() => {
  // Captured timedtext responses, newest last. A plain list rather than a map
  // keyed by URL params because the content script asks by *predicate* ("any
  // of these languages for this video") — it cannot know which track the user
  // will turn on, and YouTube's own auto-translate changes the params too.
  const captures = [];
  const MAX_CAPTURES = 20;
  const waiters = new Set();

  function parseUrl(rawUrl) {
    try {
      const u = new URL(rawUrl, location.origin);
      return {
        videoId: u.searchParams.get("v") || "",
        lang: u.searchParams.get("lang") || "",
        tlang: u.searchParams.get("tlang") || "",
      };
    } catch {
      return null;
    }
  }

  function deliver(rawUrl, body) {
    if (!rawUrl || !rawUrl.includes("/api/timedtext")) return;
    // An empty body is not a capture. Storing it would satisfy every later
    // wait for that track instantly and with nothing in it.
    if (!body) return;
    const info = parseUrl(rawUrl);
    if (!info) return;
    const entry = { ...info, body, url: rawUrl };
    console.log("[sr-bridge] deliver", { ...info, bytes: body.length });
    captures.push(entry);
    if (captures.length > MAX_CAPTURES) captures.shift();
    // Every matching waiter is resolved. Overlapping waits on the same track
    // are normal (settings change, navigate away and back), so a single-slot
    // map would strand all but the last one.
    for (const w of Array.from(waiters)) {
      if (!w.match(entry)) continue;
      waiters.delete(w);
      clearTimeout(w.timer);
      w.resolve(entry);
    }
  }

  const origFetch = window.fetch;
  window.fetch = function (...args) {
    const promise = origFetch.apply(this, args);
    try {
      const input = args[0];
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes("/api/timedtext")) {
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
      if (!savedUrl || !savedUrl.includes("/api/timedtext")) return;
      let body = "";
      try {
        // Throws for a non-text responseType; nothing to capture then.
        body = xhr.responseText || "";
      } catch {
        return;
      }
      deliver(savedUrl, body);
    });
    return xhr;
  }
  PatchedXHR.prototype = OrigXHR.prototype;
  window.XMLHttpRequest = PatchedXHR;

  function waitFor(match, timeoutMs) {
    for (let i = captures.length - 1; i >= 0; i--) {
      if (match(captures[i])) return Promise.resolve(captures[i]);
    }
    return new Promise((resolve) => {
      const w = { match, resolve, timer: 0 };
      w.timer = setTimeout(() => {
        waiters.delete(w);
        resolve(null);
      }, timeoutMs);
      waiters.add(w);
    });
  }

  // Refetch a captured timedtext URL with a different `tlang` (empty string
  // strips it). The captured URL carries YouTube's proof-of-origin params, so
  // reusing it verbatim is what keeps the refetch from being rejected.
  async function fetchVariant(baseUrl, tlang) {
    const u = new URL(baseUrl, location.origin);
    if (tlang) u.searchParams.set("tlang", tlang);
    else u.searchParams.delete("tlang");
    const url = u.toString();
    // Tight retry budget: blocking the source-cues response on a slow YT
    // round-trip is the biggest time-to-first-paint cost. Extension always
    // has a backend /translate fallback for 429 / empty / failure.
    const delays = [0, 800];
    let lastStatus = 0;
    for (let i = 0; i < delays.length; i++) {
      if (delays[i]) await new Promise((r) => setTimeout(r, delays[i]));
      try {
        const resp = await origFetch(url, { credentials: "include" });
        const body = await resp.text();
        console.log("[sr-bridge] variant fetch", { tlang, attempt: i + 1, status: resp.status, bytes: body.length });
        lastStatus = resp.status;
        if (resp.status === 200 && body && (body.trimStart().startsWith("{") || body.trimStart().startsWith("<?xml") || body.includes("<text"))) {
          return body;
        }
        if (resp.status !== 429) return "";
      } catch (e) {
        console.warn("[sr-bridge] variant fetch failed", e);
        return "";
      }
    }
    console.warn("[sr-bridge] variant fetch gave up", { tlang, lastStatus });
    return "";
  }

  window.addEventListener("message", async (ev) => {
    if (ev.source !== window) return;
    const d = ev.data;
    if (!d || d.type !== "sr-captions-req") return;
    const { id, videoId, langs, tlang, timeoutMs } = d;
    const wanted = Array.isArray(langs) ? langs : [];
    console.log("[sr-bridge] req", { id, videoId, langs: wanted, tlang });

    const entry = await waitFor(
      (e) => (!videoId || e.videoId === videoId) && wanted.includes(e.lang),
      timeoutMs ?? 120000,
    );

    let src = "";
    let tr = "";
    if (entry) {
      if (entry.tlang) {
        // YouTube's own auto-translate is on, so the only request it made
        // carries a tlang and its body is already translated. Refetch without
        // the tlang to get the source track...
        src = await fetchVariant(entry.url, "");
        // ...and keep the captured body as the translation if it happens to be
        // the language we wanted anyway.
        if (tlang && entry.tlang === tlang) tr = entry.body;
      } else {
        src = entry.body;
      }
      if (tlang && tlang !== entry.lang && !tr) {
        tr = await fetchVariant(entry.url, tlang);
      }
    }

    window.postMessage({ type: "sr-captions-resp", id, src, tr, lang: entry ? entry.lang : "" }, "*");
  });
})();
