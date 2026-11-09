// R-Delay 시연 영상 녹화: 무대(stage.html) 안에서 실제 앱을 조작하며 CDP 스크린캐스트로 프레임을 받는다.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const OUT = process.argv[2] || "frames";
const DSF = +(process.argv[3] || 2);
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: DSF });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => console.log("pageerror", e.message));
  await page.goto("http://localhost:8002/scripts/demo/stage.html", { waitUntil: "networkidle" });
  const app = page.frame({ url: /docs\/index\.html/ });
  await app.waitForFunction(() => document.documentElement.dataset.ready === "1");
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(600);

  const S = (fn, ...args) => page.evaluate(fn, ...args);
  const box = (sel, which = "app") => page.evaluate(([s, w]) => window.appBox(s, w), [sel, which]);
  const wait = (ms) => page.waitForTimeout(ms);
  async function point(sel, dx = 0.5, dy = 0.5) {
    const r = await box(sel);
    if (!r) { console.log("missing", sel); return null; }
    const x = r.x + r.w * dx, y = r.y + r.h * dy;
    await S(([x, y]) => window.moveCursor(x - 6, y - 4), [x, y]);
    await wait(800);
    return { x, y, r };
  }
  async function click(sel, fn, dx = 0.5, dy = 0.5) {
    const p = await point(sel, dx, dy);
    if (p) await S(([x, y]) => window.ripple(x, y), [p.x, p.y]);
    await wait(120);
    if (fn) await app.evaluate(fn);
    return p;
  }
  const hl = (r) => S((r) => window.highlight(r), r);
  // sel: 하나 또는 여러 선택자(합집합), maxH: 위에서부터 이 높이까지만 본다
  const zoom = async (sel, zmax = 1.6, pad = 24, maxH = null) => {
    if (!sel) { await S(() => window.zoom(null)); await wait(950); return; }
    const sels = Array.isArray(sel) ? sel : [sel];
    const rs = (await Promise.all(sels.map((x) => S((q) => window.appRect(q), x)))).filter(Boolean);
    if (!rs.length) { console.log("zoom missing", sel); return; }
    const x1 = Math.min(...rs.map((r) => r.x)), y1 = Math.min(...rs.map((r) => r.y));
    const x2 = Math.max(...rs.map((r) => r.x + r.w)), y2 = Math.max(...rs.map((r) => r.y + r.h));
    const h = maxH ? Math.min(y2 - y1, maxH) : y2 - y1;
    await S(([r, z]) => window.zoom(r, z), [{ x: x1 - pad, y: y1 - pad, w: x2 - x1 + 2 * pad, h: h + 2 * pad }, zmax]);
    await wait(950);
  };

  // ---- 녹화 시작 ----
  const client = await ctx.newCDPSession(page);
  const frames = [];
  let n = 0;
  client.on("Page.screencastFrame", async ({ data, metadata, sessionId }) => {
    const file = path.join(OUT, `f${String(n++).padStart(5, "0")}.jpg`);
    fs.writeFileSync(file, Buffer.from(data, "base64"));
    frames.push({ file, t: metadata.timestamp });
    try { await client.send("Page.screencastFrameAck", { sessionId }); } catch { /* 끝난 뒤 */ }
  });
  await client.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: 1920 * DSF, maxHeight: 1080 * DSF, everyNthFrame: 1 });
  const marks = [];
  const mark = (name) => marks.push({ name, t: Date.now() / 1000 });

  // 1. 오늘 관리 화물부터
  mark("s1");
  await S(() => window.setCap(1, "오전 10시,<br><em>관리 화물</em>부터 봅니다", "신항 지연이 번지는지, 고객 화물 중<br>무엇이 위험한지 한 화면에"));
  await S(() => window.moveCursor(700, 640));
  await wait(900);
  const chain = await box("#chain");
  await hl(chain);
  await point("#chain .stn:nth-child(1) .stn-dot");
  await wait(1600);
  const rows = await box("#rows");
  await hl(rows);
  await point('.row[data-key="ulsan_export"] .row-name');
  await wait(2200);
  await hl(null);

  // 2. 번짐과 원인
  mark("s2");
  await S(() => window.setCap(2, "지연이 바다에서<br><em>육지·하늘로</em> 번집니다", "번짐 지수 1.20 — 원인은 물량이 아니라<br>터미널 처리능력 절반"));
  await click("#why-btn", () => window.__rdelay.openWhy());
  await wait(700);
  await zoom(["#why h3", "#cf"], 1.6, 20);
  const cf = await box("#cf .cf-row:nth-child(3)");
  if (cf) await hl(cf);
  await point("#cf .cf-row:nth-child(3) .cf-v");
  await wait(2600);
  await hl(null);
  await app.evaluate(() => window.__rdelay.closeWhy());
  await zoom(null);

  // 3. 출발 시각 변경
  mark("s3");
  await S(() => window.setCap(3, "위험한 화물은<br><em>출발 시각</em>을 바꿉니다", "울산 수출: 20:00 출발이면 반입 금지에 걸려<br>거의 확실히 놓침 → 16:00 출발 1% 미만"));
  await zoom('.row[data-key="ulsan_export"]', 1.55, 16);
  const row = await box('.row[data-key="ulsan_export"]');
  await hl(row);
  await point('.row[data-key="ulsan_export"] .lane', 0.4, 0.5);
  await wait(1500);
  await click('.row[data-key="ulsan_export"] [data-act="apply"]', () => window.__rdelay.apply("ulsan_export"));
  await wait(2600);
  await hl(null);

  // 4. 운송사 배차 요청 · 기사님 링크
  mark("s4");
  await S(() => window.setCap(4, "운송사에는<br><em>배차 요청 하나</em>로", "바뀐 출발 시각과 기사님 화면 링크를 담아<br>협력 운송사에 바로 전달"));
  await hl(null);
  await zoom(".brief", 1.6, 10, 470);
  const paper = await box("#paper");
  await hl(paper);
  await point("#paper .doc-kv", 0.5, 0.35);
  await wait(1700);
  await hl(null);
  await zoom(null);
  await S(() => window.phone(true, "../../docs/driver.html?s=terminal_failure&c=ulsan_export&a=1"));
  await S(() => window.moveCursor(1180, 760));
  await wait(3600);

  // 5. 화주 안내문
  mark("s5");
  await S(() => window.phone(false));
  await S(() => window.setCap(5, "피할 수 없는 지연은<br><em>화주에게 먼저</em>", "마감 14시간 전, 사전 안내문을 자동으로 쓰고<br>복사해 카톡으로 보냅니다"));
  await wait(400);
  await click('.row[data-key="import_factory"] [data-act="shipper"]', () => window.__rdelay.select("import_factory", "shipper"));
  await wait(700);
  await zoom(".brief", 1.6, 10, 470);
  await hl(await box("#paper"));
  await wait(1900);
  await hl(null);
  await click("#copy", () => document.getElementById("copy").click());
  await wait(1900);

  // 6. 사내 보고·선사·터미널 협의
  mark("s6");
  await S(() => window.setCap(6, "사내 보고와<br><em>선사·터미널 협의</em>까지", "같은 계산으로 오늘 전체 브리핑과<br>우선 하역 요청을 한 번에"));
  await zoom(".brief", 1.6, 10, 470);
  await click('.tab[data-doc="internal"]', () => window.__rdelay.setDoc("internal"));
  await wait(1500);
  await app.evaluate(() => { const p = document.getElementById("paper"); p.scrollTo({ top: 330, behavior: "smooth" }); });
  await wait(1700);
  await click('.tab[data-doc="port"]', () => window.__rdelay.setDoc("port"));
  await wait(900);
  // 선사·터미널 협의: 맨 아래 「4. 요청드립니다」(우선 하역 요청)까지 내려 보여 준다
  await app.evaluate(() => {
    const p = document.getElementById("paper");
    const h = [...p.querySelectorAll("h4")].find((x) => x.textContent.startsWith("4."));
    const top = h ? h.getBoundingClientRect().top - p.getBoundingClientRect().top + p.scrollTop - 24 : 380;
    p.scrollTo({ top, behavior: "smooth" });
  });
  await wait(2600);

  // 7. 상황별
  mark("s7");
  await zoom(null);
  await S(() => window.setCap(7, "태풍·명절·감염병에도<br><em>같은 방식</em>으로", "상황이 바뀌면 판단과 안내문이<br>함께 바뀝니다"));
  await click("#scn", () => window.__rdelay.setScenario("typhoon_diversion"), 0.5, 0.5);
  await S(() => window.setTag("시연용 가상 데이터 · 북중국 태풍 우회 물량 장면"));
  await wait(2600);
  await click("#scn", () => window.__rdelay.setScenario("holiday_surge"), 0.5, 0.5);
  await S(() => window.setTag("시연용 가상 데이터 · 명절 전 물량 급증 장면"));
  await wait(2800);

  // 끝
  mark("end");
  await S(() => window.endCard(true));
  await wait(4200);
  mark("stop");
  await client.send("Page.stopScreencast");
  await wait(300);
  fs.writeFileSync(path.join(OUT, "frames.json"), JSON.stringify({ frames, marks }, null, 0));
  console.log("frames", frames.length, "span", (frames[frames.length - 1].t - frames[0].t).toFixed(1), "s");
  await b.close();
})();
