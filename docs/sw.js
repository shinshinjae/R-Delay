// 오프라인 시연용 서비스 워커(선상 본선처럼 인터넷이 불안정한 곳에서도 열리도록).
// - 같은 출처 파일 중 ?v=가 붙은 화면 파일: 버전마다 내용이 고정이므로 같은 주소의 캐시만 쓴다(다른 버전과 섞지 않음).
//   화면 파일을 고치면 V와 모든 ?v= 값을 함께 올린다(scripts/check-assets.mjs가 CI에서 확인).
// - 그 밖의 같은 출처 파일: 네트워크 우선(3초 안에 응답이 없으면 캐시), 실패하면 캐시. 주소 뒤 ?s= 등은 페이지 이동에서만 무시한다.
// - 버전이 고정된 CDN 파일(Pyodide·KaTeX): 캐시 우선. CORS로 받아 성공(200) 응답만 저장한다(실패 응답이 굳지 않게).
// - 페이지가 'warm' 메시지를 보내면 Pyodide 핵심 파일을 미리 내려받아 캐시에 넣는다.
const V = "20261023c"; // 화면 파일 주소의 ?v= 값(index.html·method.html·app.js·derive.js·method.js와 같게)
const CACHE = `rdelay-app-${V}`;
// 버전이 고정된 외부 파일(Pyodide·KaTeX)은 앱 버전이 바뀌어도 지우지 않는 별도 캐시에 둔다.
const CDN_CACHE = "rdelay-cdn-pyodide0.27.2-katex0.16.11";
const CORE = [
  "./", "index.html", "method.html",
  ...["assets/css/style.css", "assets/css/method.css", "assets/js/app.js", "assets/js/charts.js", "assets/js/format.js",
    "assets/js/derive.js", "assets/js/method.js", "assets/js/validation-tables.js", "assets/js/engine-worker.js"].map((f) => `${f}?v=${V}`),
  "assets/img/icon.svg",
  "data/precomputed.json", "data/ai_model.json",
  "data/validation.json",
  "py/rdelay/__init__.py", "py/rdelay/config.py", "py/rdelay/stats.py", "py/rdelay/sis.py",
  "py/rdelay/volume.py", "py/rdelay/ai.py", "py/rdelay/engine.py", "py/rdelay/queue.py",
  "py/rdelay/cargo.py", "py/rdelay/api.py",
];
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.2/full/";
const KATEX = "https://cdn.jsdelivr.net/npm/katex@0.16.11/";
const CDN_WARM = [
  PYODIDE + "pyodide.js", PYODIDE + "pyodide.mjs", PYODIDE + "pyodide.asm.js", PYODIDE + "pyodide.asm.wasm",
  PYODIDE + "python_stdlib.zip", PYODIDE + "pyodide-lock.json",
  PYODIDE + "numpy-2.0.2-cp312-cp312-pyodide_2024_0_wasm32.whl",
  KATEX + "dist/katex.min.css", KATEX + "dist/katex.min.js", KATEX + "dist/contrib/auto-render.min.js",
  // 작동 원리 페이지 수식에 쓰는 글꼴(오프라인에서도 괄호·적분 기호가 제 모양으로 나오게)
  ...["Main-Regular", "Math-Italic", "Size1-Regular", "Size2-Regular", "AMS-Regular", "Caligraphic-Regular"].map((f) => `${KATEX}dist/fonts/KaTeX_${f}.woff2`),
];
const isCdn = (url) => url.startsWith(PYODIDE) || url.startsWith(KATEX);
const LOCAL = /^(localhost|127\.0\.0\.1|\[::1\])$/;

self.addEventListener("install", (event) => {
  // HTTP 캐시를 거치지 않고 서버의 최신 파일로 오프라인 캐시를 채운다.
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE.map((u) => new Request(u, { cache: "reload" }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const cdn = await caches.open(CDN_CACHE);
    for (const k of await caches.keys()) {
      // shinshinjae.github.io는 다른 프로젝트와 같은 출처이므로 R-Delay 캐시(rdelay-*)만 정리한다
      if (!k.startsWith("rdelay-") || k === CACHE || k === CDN_CACHE) continue;
      // 옛 버전 캐시에 있던 Pyodide·KaTeX는 CDN 캐시로 옮긴 뒤 지운다(휴대폰에서 오프라인 계산을 잃지 않게)
      const old = await caches.open(k);
      for (const req of await old.keys()) {
        if (!isCdn(req.url) || (await cdn.match(req.url))) continue;
        const res = await old.match(req);
        if (res && res.ok) await cdn.put(req.url, res);
      }
      await caches.delete(k);
    }
    await self.clients.claim();
  })());
});

async function warm() {
  const cache = await caches.open(CDN_CACHE);
  const missing = [];
  for (const url of CDN_WARM) {
    const hit = await cache.match(url);
    if (hit && hit.type !== "opaque" && hit.ok) continue;
    try {
      const res = await fetch(url, { mode: "cors" });
      if (res.ok) await cache.put(url, res);
      else missing.push(url);
    } catch {
      missing.push(url);
    }
  }
  return missing;
}

self.addEventListener("message", (event) => {
  if (!event.data || event.data.type !== "warm") return;
  const port = event.ports && event.ports[0];
  event.waitUntil(warm().then((missing) => port && port.postMessage({ type: "warm-done", missing })));
});

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("timeout")), ms);
    promise.then((v) => { clearTimeout(timer); resolve(v); }, (e) => { clearTimeout(timer); reject(e); });
  });
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = req.url;
  if (isCdn(url)) {
    event.respondWith((async () => {
      const cache = await caches.open(CDN_CACHE);
      const hit = await cache.match(url, { ignoreVary: true });
      if (hit && hit.ok) return hit;
      // jsDelivr는 CORS를 허용하므로 importScripts(no-cors) 요청도 CORS로 받아 상태를 확인한다.
      // KaTeX는 응답이 없으면 5초 뒤 포기한다(수식은 TeX 원문으로 보이고, 페이지의 나머지는 바로 그려지게).
      const net = fetch(new Request(url, { mode: "cors", credentials: "omit" }));
      const res = await (url.startsWith(KATEX) ? withTimeout(net, 5000) : net);
      if (res.ok) cache.put(url, res.clone());
      return res;
    })());
    return;
  }
  const u = new URL(url);
  if (u.origin !== self.location.origin) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    // 로컬 개발(localhost)에서는 고친 파일이 바로 보이도록 버전 파일도 네트워크 우선으로 둔다
    if (u.searchParams.has("v") && !LOCAL.test(u.hostname)) {
      const hit = await cache.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) cache.put(req, res.clone());
      return res;
    }
    try {
      const res = await withTimeout(fetch(req), 3000);
      if (res.ok) cache.put(req, res.clone());
      return res;
    } catch {
      const hit = await cache.match(req, req.mode === "navigate" ? { ignoreSearch: true } : undefined);
      if (hit) return hit;
      return fetch(req);
    }
  })());
});
