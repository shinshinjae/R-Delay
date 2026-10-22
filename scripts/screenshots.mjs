// 대시보드 화면 캡처(README·제출서 첨부용). 로컬 서버가 떠 있어야 한다.
//   python3 -m http.server -d docs 8000 &
//   node scripts/screenshots.mjs http://localhost:8000 docs/assets/img
// 크롬을 headless로 띄우고 DevTools 프로토콜로 구역별 화면을 저장한다(추가 패키지 없음, Node 22+).
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2] || "http://localhost:8000";
const OUT = process.argv[3] || "docs/assets/img";
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9333;

// from/to: 페이지 안 구역(문서 좌표), tight: 가로도 그 구역만, dialog: 열린 대화상자 하나(화면 좌표), prep: 찍기 전에 실행할 JS
const OPEN_TAB = (tab) => `document.getElementById('open-detail').click(); await new Promise(r => setTimeout(r, 300)); document.getElementById('tab-${tab}').click(); await new Promise(r => setTimeout(r, 500));`;
const EXPAND = (key) => `document.querySelector('#cargo-${key} .expand').click(); await new Promise(r => setTimeout(r, 300));`;
const SHOTS = [
  { file: "screen-overview.png", page: "?s=terminal_failure", from: "header.appbar", to: "#driver", width: 1280, height: 1000, waitEngine: true },
  { file: "screen-cargo.png", page: "?s=holiday_surge", from: "#cargo", to: "#cargo", tight: true, width: 1280, prep: EXPAND("sea_air") },
  { file: "screen-h2.png", page: "?s=h2_trucks", from: "#cargo", to: "#cargo", tight: true, width: 1280, prep: EXPAND("ulsan_parts") },
  { file: "screen-export.png", page: "?s=terminal_failure", from: "#cargo", to: "#cargo", tight: true, width: 1280, prep: EXPAND("ulsan_export") },
  { file: "screen-gate.png", page: "?s=terminal_failure", from: "#driver", to: "#driver", tight: true, width: 1280 },
  { file: "screen-cause.png", page: "?s=typhoon_diversion", dialog: "#detail", width: 1280, height: 760, prep: OPEN_TAB("cause") },
  { file: "screen-nodes-network.png", page: "?s=terminal_failure", dialog: "#detail", width: 1280, height: 1100, prep: OPEN_TAB("network") },
  { file: "screen-validation.png", page: "method.html#valid", from: "#valid", to: "#valid", width: 1280 },
  { file: "screen-mobile.png", page: "?s=terminal_failure", from: "header.appbar", to: "#where", width: 390, mobile: true },
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  mkdirSync(OUT, { recursive: true });
  const profile = mkdtempSync(join(tmpdir(), "rdelay-shot-"));
  const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    "--hide-scrollbars", "--force-color-profile=srgb", "--lang=ko-KR", "about:blank"], { stdio: "ignore" });
  try {
    let target;
    for (let i = 0; i < 50 && !target; i++) {
      await sleep(200);
      try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === "page"); } catch { /* 기동 대기 */ }
    }
    if (!target) throw new Error("Chrome DevTools에 연결하지 못했습니다");
    const ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
    let seq = 0;
    const waiting = new Map();
    ws.onmessage = (m) => {
      const d = JSON.parse(m.data);
      if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); }
    };
    const send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++seq;
      waiting.set(id, (d) => (d.error ? reject(new Error(`${method}: ${d.error.message}`)) : resolve(d.result)));
      ws.send(JSON.stringify({ id, method, params }));
    });
    const evaluate = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result.value;
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
    for (const s of SHOTS) {
      await send("Emulation.setDeviceMetricsOverride", { width: s.width, height: s.height || 900, deviceScaleFactor: 2, mobile: !!s.mobile });
      await send("Page.navigate", { url: s.page.startsWith("method") ? `${BASE}/${s.page}` : `${BASE}/${s.page}` });
      await sleep(400);
      await evaluate(`new Promise((res) => { const t0 = Date.now(); (function poll() {
        const ok = location.pathname.endsWith('method.html') ? document.querySelector('.katex') && document.querySelector('#valid table tbody tr')
          : document.querySelector('#trend-chart svg') && document.querySelector('.cargo-card');
        if (ok || Date.now() - t0 > 15000) res(true); else setTimeout(poll, 200); })(); })`);
      if (s.waitEngine) {
        // 첫 장면은 'Python 엔진 준비 완료' 상태로 찍는다(Pyodide를 CDN에서 받으므로 최대 120초 대기).
        await evaluate(`new Promise((res) => { const t0 = Date.now(); (function poll() {
          const st = document.getElementById('engine').dataset.state;
          if (st === 'ready' || st === 'failed' || Date.now() - t0 > 120000) res(st); else setTimeout(poll, 500); })(); })`);
      }
      if (s.prep) await evaluate(`(async () => { ${s.prep} return 1; })()`);
      await sleep(600);
      const box = s.dialog
        ? await evaluate(`(() => { const r = document.querySelector(${JSON.stringify(s.dialog)}).getBoundingClientRect(); return { x: r.left, y: r.top, width: r.width, height: r.height }; })()`)
        : await evaluate(`(() => {
        const a = document.querySelector(${JSON.stringify(s.from)}).getBoundingClientRect();
        const b = document.querySelector(${JSON.stringify(s.to)}).getBoundingClientRect();
        const x = ${s.tight ? "Math.max(0, a.left + scrollX - 8)" : "0"};
        const width = ${s.tight ? "Math.min(a.width + 16, document.documentElement.clientWidth - x)" : "document.documentElement.clientWidth"};
        return { x, y: a.top + scrollY - 8, width, height: b.bottom - a.top + 16 };
      })()`);
      const shot = await send("Page.captureScreenshot", { format: "png", clip: { ...box, scale: 1 }, captureBeyondViewport: true });
      writeFileSync(join(OUT, s.file), Buffer.from(shot.data, "base64"));
      console.log(`${s.file}  ${Math.round(box.width)}x${Math.round(box.height)}`);
    }
    ws.close();
  } finally {
    chrome.kill();
  }
}

main().catch((e) => { console.error(e); process.exit(1); });
