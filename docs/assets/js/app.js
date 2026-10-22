// R-Delay 상황판: 미리 계산된 결과를 먼저 보여 주고, 조건을 바꾸면 브라우저 안 Python 엔진으로 다시 계산한다.
// 메인 화면은 쉬운 문장(derive.js)만, 연구·검증용 원자료는 「자세히 보기」 서랍에만 그린다.
import { timeChart, columnChart, barList, networkChart, legend, niceTicks } from "./charts.js?v=20261023c";
import {
  NODE_NAME, SCENARIO_CHIP, SCENARIO_LINE, pct, num, clock, clock10, tickLabel, fmtP, fmtDur, fmtDurShort, fmtRd, fmtWon,
  asOfText, nextUpdateText,
} from "./format.js?v=20261023c";
import {
  statusView, todoView, sortedCargo, cargoView, cargoDetail, driverAdvice, nodeView, whereNote,
  trendLine, trustLines, CLASS_PILL,
} from "./derive.js?v=20261023c";

const COLORS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"];
const DEFAULT_SCENARIO = "terminal_failure";
const LOAD_TIMEOUT_MS = 180000;
const RUN_TIMEOUT_MS = 60000;
const $ = (id) => document.getElementById(id);

let PRE = null;
let VALID = null;
let MODEL = null; // AI 보조 모듈 가중치(엔진 재계산에 넘김)
let current = null;
let worker = null;
let workerReady = null;
let engineInfo = null;
let reqSeq = 0;
let latestReq = 0;
let offlineNote = "";
const pending = new Map();
const overrides = { eps: {}, cost: {} }; // 화물별 허용 위험·마감 실패 비용(원)
const RELOAD_KEY = "rdelay-reload-once"; // index.html의 안전장치와 같은 키
const APP_VERSION = "1.1.0"; // 화면 버전(저장소 태그와 같게). 엔진 버전은 계산 결과의 meta.version
const state = { scenario: DEFAULT_SCENARIO, expanded: null, tab: "trend", lastMs: null, engineReady: false };

// ---------- 작은 도우미 ----------
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const SVGNS = "http://www.w3.org/2000/svg";

// 상태는 모양 + 단어 + 색을 함께 쓴다(색만으로 구분하지 않음).
function shapeIcon(level) {
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", "0 0 14 14");
  svg.setAttribute("class", "shape-ic");
  svg.setAttribute("aria-hidden", "true");
  svg.dataset.level = level;
  const add = (tag, attrs) => { const n = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); svg.appendChild(n); return n; };
  if (level === "critical") add("rect", { x: 2, y: 2, width: 10, height: 10, rx: 1.5 });
  else if (level === "serious") add("path", { d: "M7 1.2 L12.8 7 L7 12.8 L1.2 7 Z" });
  else if (level === "warning") add("path", { d: "M7 1.6 L12.8 12 H1.2 Z" });
  else if (level === "hold") {
    add("circle", { cx: 7, cy: 7, r: 5.6, class: "ring" });
    const t = add("text", { x: 7, y: 10.2, "text-anchor": "middle", "font-size": 9, "font-weight": 700 });
    t.textContent = "?";
  } else add("circle", { cx: 7, cy: 7, r: 5 });
  return svg;
}
function pillNode(level, text) {
  const p = el("span", "pill");
  p.dataset.level = level;
  p.append(shapeIcon(level), el("span", null, text));
  return p;
}

let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 2200);
}
const announce = (msg) => { $("sr-live").textContent = msg; };

function table(tbl, head, rows, numCols = []) {
  tbl.replaceChildren();
  const thead = tbl.createTHead().insertRow();
  head.forEach((h, i) => { const c = document.createElement("th"); if (numCols.includes(i)) c.className = "num"; c.textContent = h; thead.appendChild(c); });
  const body = tbl.createTBody();
  for (const r of rows) {
    const tr = body.insertRow();
    if (r.cls) tr.className = r.cls;
    r.cells.forEach((v, i) => { const c = tr.insertCell(); if (numCols.includes(i)) c.className = "num"; c.textContent = v; });
  }
}
function kv(dl, pairs) {
  dl.replaceChildren();
  for (const [k, v] of pairs) dl.append(el("dt", null, k), el("dd", null, v));
}

// ---------- 설정과 주소 ----------
const cargoPreset = (name) => PRE.presets.cargo[name];
const epsFor = (name) => overrides.eps[name] ?? cargoPreset(name).epsilon;
const costFor = (name) => overrides.cost[name] ?? cargoPreset(name).fail_cost;
const scenarioH2 = (name) => !!PRE.presets.scenarios[name].h2;
const fleetH2 = () => $("fleet-1").checked;
const setFleet = (h2) => { $("fleet-1").checked = h2; $("fleet-0").checked = !h2; };

function readParams() {
  const d = PRE.presets.defaults;
  return {
    scenario: state.scenario,
    cargo: state.expanded || Object.keys(PRE.presets.cargo)[0],
    kappa: +$("kappa").value,
    particles: +$("particles").value,
    world: Math.max(1, Math.floor(+$("world").value || d.world)),
    seed: Math.max(1, Math.floor(+$("seed").value || d.seed)),
    h2: fleetH2(),
  };
}

function isDefault(p) {
  const d = PRE.presets.defaults;
  return Math.abs(p.kappa - d.kappa) < 1e-9 && p.particles === d.particles && p.world === d.world && p.seed === d.seed &&
    p.h2 === scenarioH2(p.scenario) && !Object.keys(overrides.eps).length && !Object.keys(overrides.cost).length;
}

const encodeMap = (m, scale = 1) => Object.entries(m).map(([k, v]) => `${k}:${+(v / scale).toFixed(4)}`).join(",");
function decodeMap(s, scale = 1) {
  const out = {};
  for (const part of (s || "").split(",")) {
    const [k, v] = part.split(":");
    if (k && PRE.presets.cargo[k] && v !== "" && isFinite(+v)) out[k] = +v * scale;
  }
  return out;
}

function writeURL(p) {
  const q = new URLSearchParams({ s: p.scenario });
  if (state.expanded) q.set("c", state.expanded);
  const d = PRE.presets.defaults;
  if (Object.keys(overrides.eps).length) q.set("eps", encodeMap(overrides.eps));
  if (Object.keys(overrides.cost).length) q.set("cost", encodeMap(overrides.cost, 10000));
  if (p.kappa !== d.kappa) q.set("kappa", p.kappa);
  if (p.particles !== d.particles) q.set("n", p.particles);
  if (p.world !== d.world) q.set("world", p.world);
  if (p.seed !== d.seed) q.set("seed", p.seed);
  if (p.h2 !== scenarioH2(p.scenario)) q.set("h2", p.h2 ? 1 : 0);
  history.replaceState(null, "", `${location.pathname}?${q}${location.hash}`);
}

function applyURL() {
  const q = new URLSearchParams(location.search);
  const s = q.get("s");
  state.scenario = s && PRE.presets.scenarios[s] ? s : DEFAULT_SCENARIO;
  const c = q.get("c");
  state.expanded = c && PRE.presets.cargo[c] ? c : null;
  for (const [k, v] of Object.entries(decodeMap(q.get("eps")))) overrides.eps[k] = Math.min(0.3, Math.max(0.01, v));
  for (const [k, v] of Object.entries(decodeMap(q.get("cost"), 10000))) overrides.cost[k] = Math.max(0, v);
  if (q.get("kappa")) $("kappa").value = q.get("kappa");
  if (q.get("n") && [...$("particles").options].some((o) => o.value === q.get("n"))) $("particles").value = q.get("n");
  if (q.get("world")) $("world").value = q.get("world");
  if (q.get("seed")) $("seed").value = q.get("seed");
  setFleet(q.has("h2") ? q.get("h2") === "1" : scenarioH2(state.scenario));
  if (["kappa", "n", "world", "seed"].some((k) => q.has(k))) $("expert").open = true;
  return { hadCargo: !!state.expanded };
}

function resetToDefaults() {
  const d = PRE.presets.defaults;
  for (const k of Object.keys(overrides.eps)) delete overrides.eps[k];
  for (const k of Object.keys(overrides.cost)) delete overrides.cost[k];
  $("kappa").value = d.kappa;
  $("particles").value = String(d.particles);
  $("world").value = d.world;
  $("seed").value = d.seed;
  setFleet(scenarioH2(state.scenario));
  syncSettings();
}

// ---------- 엔진 ----------
const ENGINE_TEXT = {
  idle: "기본 결과", prep: "계산 준비 중…", run: "계산 중…", failed: "계산 엔진을 못 불러옴 · 기본 결과 표시",
};
const readyText = () => (state.lastMs != null
  ? `방금 다시 계산함(${(state.lastMs / 1000).toFixed(1)}초)`
  : `계산 준비됨${offlineNote ? ` · ${offlineNote}` : ""}`);

// st: idle | loading | ready | failed. prep이면 다시 계산 버튼을 잠시 막는다(엔진 첫 준비).
function setEngine(st, text, { title = "", prep = false } = {}) {
  $("engine").dataset.state = st;
  $("engine-text").textContent = `계산 엔진: ${text}`;
  $("settings-engine").textContent = `계산 엔진: ${text}`;
  $("engine").title = `처음 한 번 약 8MB를 내려받습니다${engineInfo ? ` · Python ${engineInfo.python} · NumPy ${engineInfo.numpy}` : ""}${title ? ` · ${title}` : ""}`;
  const btn = $("run-btn");
  if (prep) { btn.disabled = true; btn.textContent = "계산 준비 중…(처음 한 번만)"; }
  else if (!document.body.classList.contains("busy")) { btn.disabled = false; btn.textContent = "이 조건으로 다시 계산"; }
}

function resetWorker() {
  if (worker) worker.terminate();
  worker = null;
  workerReady = null;
  for (const [, p] of pending) p.reject(new Error("엔진을 다시 시작합니다"));
  pending.clear();
}

async function ensureWorker() {
  if (workerReady) return workerReady;
  setEngine("loading", ENGINE_TEXT.prep, { prep: true });
  // 서비스 워커가 제어를 시작한 뒤에 워커를 만들어야 Pyodide 파일이 오프라인 캐시에 들어간다.
  if ("serviceWorker" in navigator) await Promise.race([navigator.serviceWorker.ready, sleep(3000)]);
  worker = new Worker("assets/js/engine-worker.js?v=20261023c");
  const ready = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("엔진 준비 시간 초과")), LOAD_TIMEOUT_MS);
    worker.onmessage = (e) => {
      const d = e.data;
      if (d.type === "ready") {
        clearTimeout(timer);
        engineInfo = d.info;
        state.engineReady = true;
        setEngine("ready", readyText());
        warmOffline();
        resolve();
        return;
      }
      if (d.type === "failed") {
        clearTimeout(timer);
        reject(new Error(d.error));
        return;
      }
      const p = pending.get(d.id);
      if (!p) return;
      pending.delete(d.id);
      if (d.ok) p.resolve(d);
      else p.reject(new Error(d.error));
    };
    worker.onerror = (e) => { clearTimeout(timer); reject(new Error(e.message || "엔진 실행 오류")); };
  });
  workerReady = ready;
  ready.catch((err) => {
    setEngine("failed", ENGINE_TEXT.failed, { title: err.message });
    if (workerReady === ready) resetWorker();
  });
  return ready;
}

async function runEngine(options) {
  await ensureWorker();
  const id = ++reqSeq;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error("계산 시간 초과"));
      resetWorker();
    }, RUN_TIMEOUT_MS);
    pending.set(id, {
      resolve: (v) => { clearTimeout(timer); resolve(v); },
      reject: (e) => { clearTimeout(timer); reject(e); },
    });
    worker.postMessage({ id, options, model: MODEL });
  });
}

function warmOffline() {
  if (!("serviceWorker" in navigator) || !navigator.serviceWorker.controller) return;
  navigator.serviceWorker.ready.then((reg) => {
    const ch = new MessageChannel();
    ch.port1.onmessage = (e) => {
      offlineNote = e.data.missing && e.data.missing.length ? "오프라인 준비 일부 실패" : "오프라인 가능";
      if ($("engine").dataset.state === "ready") setEngine("ready", readyText());
    };
    if (reg.active) reg.active.postMessage({ type: "warm" }, [ch.port2]);
  }).catch(() => {});
}

// ---------- 갱신 ----------
function setBusy(on) {
  document.body.classList.toggle("busy", on);
  $("run-btn").disabled = on;
  if (on) $("source-tag").textContent = "계산 중…";
}

async function update({ force = false } = {}) {
  const p = readParams();
  writeURL(p);
  const pre = PRE.runs[p.scenario];
  const mine = ++latestReq;
  if (!force && isDefault(p)) {
    setBusy(false);
    state.lastMs = null;
    if (state.engineReady) setEngine("ready", readyText());
    render(pre, { kind: "pre" });
    return;
  }
  if (!current || current.r.meta.scenario !== p.scenario) render(pre, { kind: "pre", note: "기본 조건 결과를 먼저 보여 줍니다" });
  setBusy(true);
  if (workerReady) setEngine("loading", ENGINE_TEXT.run);
  try {
    const out = await runEngine({
      scenario: p.scenario, cargo: p.cargo, kappa: p.kappa, particles: p.particles, world: p.world, seed: p.seed,
      h2: p.h2, epsilon: { ...overrides.eps }, fail_cost: { ...overrides.cost },
    });
    if (mine !== latestReq) { if (state.engineReady && !pending.size) setEngine("ready", readyText()); return; }
    state.lastMs = out.ms;
    render(out.result, { kind: "engine", ms: out.ms });
    setEngine("ready", readyText());
    toast("다시 계산했습니다");
    flash();
  } catch (err) {
    if (mine !== latestReq) { if (state.engineReady && !pending.size) setEngine("ready", readyText()); return; }
    const msg = String(err.message || err).trim().split("\n").pop();
    setEngine("failed", ENGINE_TEXT.failed, { title: msg });
    render(pre, { kind: "pre", note: "계산하지 못해 기본 조건 결과를 표시합니다" });
  } finally {
    if (mine === latestReq) setBusy(false);
  }
}

function flash() {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  for (const id of ["status", "cargo", "driver"]) {
    const n = $(id);
    n.classList.add("flash");
    setTimeout(() => n.classList.remove("flash"), 80); // 강조색에서 원래 색으로 1초 동안 돌아간다
  }
}

// ---------- 그리기: 메인 ----------
function render(r, src) {
  current = { r, src };
  drawScenarioBar(r, src);
  drawStatus(r);
  drawWhere(r);
  drawCargoList(r);
  drawDriver(r);
  drawTrend(r);
  drawTrust(r);
  drawFooter(r, src);
  if ($("detail").open) drawTab(state.tab);
  document.documentElement.dataset.ready = "1";
  for (const n of document.querySelectorAll("p.err[data-guard]")) n.remove(); // 안전장치가 띄운 안내는 그려지면 지운다
  try { sessionStorage.removeItem(RELOAD_KEY); } catch { /* 저장소를 못 써도 화면은 그대로 */ }
}

function drawScenarioBar(r, src) {
  const s = r.meta.scenario;
  for (const b of $("scenario-chips").children) {
    const on = b.dataset.key === s;
    b.setAttribute("aria-checked", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
  }
  $("scenario-line").textContent = `가상 설정: ${SCENARIO_LINE[s] || r.meta.scenario_label}`;
  $("scenario-summary").textContent = r.meta.scenario_summary;
  $("jump-scn").textContent = `${SCENARIO_CHIP[s]} ▾`;
  $("jump-scn").setAttribute("aria-label", `${SCENARIO_CHIP[s]} — 상황 바꾸기`);
  const asOf = `가상 판단 시각 ${asOfText(r)}(시연 고정) · 실제 운영 땐 2시간마다 갱신`;
  $("as-of").textContent = asOf;
  $("as-of-m-text").textContent = `가상 판단 시각 ${asOfText(r)}(시연 고정)`;
  const changed = src.kind === "engine";
  const tag = changed ? "바꾼 조건으로 계산함" : src.note ? src.note : "기본 조건";
  $("source-tag").textContent = tag;
  $("src-m").textContent = changed || src.note ? `· ${tag}` : "";
  $("reset-defaults").hidden = !changed;
  $("reset-defaults-m").hidden = !changed;
}

function drawStatus(r) {
  const v = statusView(r);
  const sec = $("status");
  sec.dataset.level = v.level;
  const h = $("status-title");
  h.replaceChildren(shapeIcon(v.icon));
  const t = el("span", "title-text");
  t.append(el("span", "t1", v.titleParts[0]), el("span", "sep", " · "), el("span", "t2", v.titleParts[1]));
  h.appendChild(t);
  // 원인
  const dt = $("st-cause-dt"), dd = $("st-cause");
  dd.replaceChildren();
  if (v.cause) {
    dt.hidden = false; dd.hidden = false;
    dt.textContent = v.cause.label;
    if (v.cause.hold) dd.appendChild(shapeIcon("hold"));
    dd.append(el("span", v.cause.hold ? "hold-text" : null, v.cause.text));
    const more = el("button", "link", "근거 ▸");
    more.type = "button";
    more.addEventListener("click", () => openDetail("cause", more));
    dd.append(" ", more);
    if (v.cause.sub) dd.appendChild(el("span", "aside block", v.cause.sub));
  } else { dt.hidden = true; dd.hidden = true; }
  $("st-outlook").textContent = v.outlook;
  const todo = todoView(r);
  $("st-todo").textContent = `${todo.text} ↓`;
  // 번짐 지수(정직성 고정 요소: 늘 예상 범위와 함께)
  const ix = v.index;
  const p = $("st-index");
  p.replaceChildren(
    el("span", null, `지연 번짐 지수 ${ix.rd} `),
    el("span", "long", `(예상 범위 ${ix.lo}~${ix.hi})`), el("span", "short", `(${ix.lo}~${ix.hi})`),
    el("span", null, ` · 지금 번지는 중일 확률 ${ix.p}%`), " ",
  );
  $("rd-info-text").replaceChildren(
    `지연 1건이 새 지연을 몇 건 낳는지 나타냅니다(감염병의 재생산수와 같은 개념). 1보다 크면 지연이 불어납니다. ` +
    `예상 범위는 모형이 80% 확률로 보는 범위입니다${coverageText()}. 번지는 중일 확률이 50%를 넘으면 경보, 35~65%면 판단을 보류합니다. `,
    Object.assign(el("a", null, "계산 방법 →"), { href: "method.html#model" }),
  );
  const info = el("button", "info", "ⓘ");
  info.type = "button";
  info.setAttribute("aria-expanded", $("rd-info-text").hidden ? "false" : "true");
  info.setAttribute("aria-controls", "rd-info-text");
  info.setAttribute("aria-label", "지연 번짐 지수 설명");
  info.addEventListener("click", () => {
    const open = $("rd-info-text").hidden;
    $("rd-info-text").hidden = !open;
    info.setAttribute("aria-expanded", open ? "true" : "false");
  });
  p.appendChild(info);
  announce(`${SCENARIO_CHIP[r.meta.scenario]} 상황: ${v.title}`);
}

function drawWhere(r) {
  const ol = $("node-strip");
  ol.replaceChildren();
  r.nodes.forEach((_, k) => {
    const v = nodeView(r, k);
    const li = el("li");
    li.dataset.state = v.state;
    li.dataset.id = v.id;
    const b = el("button", "node");
    b.type = "button";
    b.setAttribute("aria-label", `${v.word} ${v.name}: 지연 중 ${v.x}%, 막힘 기준 ${v.thr}%${v.notes.length ? ", " + v.notes.join(", ") : ""}. 거점별 추이 보기`);
    const l1 = el("span", "n1");
    l1.append(shapeIcon(v.level), el("b", "word", v.word), el("span", "name", v.name));
    const l2 = el("span", "n2", `지연 중 ${v.x}%${v.range ? `(${v.range})` : ""} · 막힘 기준 ${v.thr}%`);
    b.append(l1, l2);
    if (v.notes.length) b.appendChild(el("span", "n3", v.notes.join(" · ")));
    if (v.origin) b.appendChild(el("span", "tag origin", v.origin));
    b.addEventListener("click", () => openDetail("nodes", b));
    li.appendChild(b);
    ol.appendChild(li);
  });
}

function drawCargoList(r) {
  const keys = sortedCargo(r);
  const todo = todoView(r);
  $("cargo-h").textContent = `오늘 화물 ${todo.n}건`;
  $("jump-n").textContent = todo.n;
  const counts = $("cargo-counts");
  counts.replaceChildren();
  for (const [cls, word] of [["change", "시간 변경"], ["notify", "화주 안내"], ["hold-conservative", "다시 확인"], ["hold-uncertain", "판단 보류"], ["optional", "바꿔도 됨"], ["ok", "계획대로"]]) {
    if (todo.counts[cls]) counts.appendChild(pillNode(CLASS_PILL[cls][0], `${word} ${todo.counts[cls]}`));
  }
  const ol = $("cargo-list");
  // 다시 그리면 카드 안 버튼이 사라지므로, 포커스가 있던 카드의 펼치기 버튼으로 돌려놓는다
  const fk = document.activeElement && document.activeElement.closest && document.activeElement.closest("#cargo-list li.cargo-card")?.dataset.cargo;
  ol.replaceChildren();
  for (const key of keys) {
    const v = cargoView(r, key, VALID);
    const li = el("li", "cargo-card");
    li.dataset.cargo = key;
    li.dataset.level = v.level;
    li.id = `cargo-${key}`;
    const head = el("div", "cc-head");
    head.append(pillNode(v.level, v.pill), el("span", "deadline", `마감 ${v.deadline}`));
    li.append(head, el("p", "cc-label", v.label), el("p", "action", v.action), el("p", "reason", v.reason));
    if (v.note) li.appendChild(el("p", "extra", v.note));
    const btns = el("div", "cc-btns");
    if (v.canCopy) {
      const cp = el("button", v.copyPrimary ? "primary" : "btn-ghost copy2", "안내 문구 복사");
      cp.type = "button";
      cp.addEventListener("click", async () => {
        await copyText(v.notice, li, null, "복사했습니다 — 펼친 카드에서 문구를 확인하세요");
        if (state.expanded !== key) toggleCargo(key);
      });
      btns.appendChild(cp);
    }
    if (v.cls === "hold-uncertain" && !(((r.meta.params && r.meta.params.particles) || 0) >= 1024)) {
      const more = el("button", "btn-ghost", "표본 늘려 다시 계산");
      more.type = "button";
      more.addEventListener("click", () => { $("particles").value = "1024"; update({ force: true }); });
      btns.appendChild(more);
    }
    const ex = el("button", "expand", state.expanded === key ? "접기 ▴" : "펼치기 ▾");
    ex.type = "button";
    ex.setAttribute("aria-expanded", state.expanded === key ? "true" : "false");
    ex.setAttribute("aria-controls", `cd-${key}`);
    ex.addEventListener("click", () => toggleCargo(key));
    btns.appendChild(ex);
    li.appendChild(btns);
    if (state.expanded === key) li.appendChild(cargoDetailNode(r, key));
    ol.appendChild(li);
  }
  if (fk && (!document.activeElement || document.activeElement === document.body)) {
    const b = document.querySelector(`#cargo-${fk} .expand`);
    if (b) b.focus({ preventScroll: true });
  }
}

function cargoDetailNode(r, key) {
  const d = cargoDetail(r, key);
  const box = el("div", "cargo-detail");
  box.id = `cd-${key}`;
  box.setAttribute("role", "region");
  box.setAttribute("aria-label", `${r.cargo[key].label} 자세히`);
  box.appendChild(el("h3", null, "왜 이렇게 판단했나"));
  const why = el("ul", "plain why");
  for (const w of d.why) why.appendChild(el("li", null, w));
  box.appendChild(why);
  box.appendChild(el("h3", null, "시간별 선택지"));
  const ul = el("ul", "options");
  d.options.forEach((o, i) => {
    const li = el("li");
    if (o.chosen) li.className = "chosen";
    if (o.infeasible) li.classList.add("infeasible");
    const top = el("div", "o-top");
    top.appendChild(el("span", "o-name", o.name));
    for (const t of o.tags) top.appendChild(el("span", "tag", t));
    top.appendChild(el("span", o.chosen ? "o-status rec" : "o-status", o.status));
    li.appendChild(top);
    if (!o.infeasible) {
      const bar = el("div", "riskbar");
      bar.setAttribute("role", "img");
      bar.setAttribute("aria-label", `놓칠 확률 ${fmtP(o.p)}, 오차 감안 ${fmtP(o.upper)}, 허용 위험 ${fmtP(o.eps)}`);
      const up = el("div", "fill-upper");
      up.style.width = `${Math.min(100, o.upper * 100)}%`;
      const fill = el("div", "fill");
      fill.style.width = `${Math.min(100, o.p * 100)}%`;
      if (o.danger) bar.classList.add("danger");
      const eps = el("div", "eps");
      eps.style.left = `calc(${o.eps * 100}% - 1px)`;
      bar.append(up, fill, eps);
      if (i === 0) {
        const lab = el("span", "eps-label", `허용 위험 ${fmtP(o.eps)}`);
        lab.style.left = `${o.eps * 100}%`;
        bar.appendChild(lab);
      }
      li.append(bar, el("div", "o-text", o.text), el("div", "o-sub", o.sub));
    }
    ul.appendChild(li);
  });
  box.appendChild(ul);
  box.appendChild(el("p", "aside", d.rule));
  if (d.h2Note) box.appendChild(el("p", "note-box", d.h2Note));
  if (d.notice) {
    box.appendChild(el("h3", null, "화주 안내 문구"));
    const ta = el("textarea", "notice-text");
    ta.readOnly = true;
    ta.rows = 4;
    ta.value = d.notice;
    ta.setAttribute("aria-label", "화주 안내 문구");
    const cp = el("button", "primary wide", "복사");
    cp.type = "button";
    cp.addEventListener("click", () => copyText(d.notice, box, ta));
    box.append(ta, cp);
  }
  const set = el("button", "btn-ghost", "이 화물 기준 바꾸기");
  set.type = "button";
  set.addEventListener("click", () => openSettings(key, set));
  box.appendChild(set);
  const raw = el("details", "raw");
  raw.appendChild(el("summary", null, "엔진 원문"));
  for (const t of d.raw) raw.appendChild(el("p", null, t));
  box.appendChild(raw);
  return box;
}

function toggleCargo(key, focus = true) {
  state.expanded = state.expanded === key ? null : key;
  writeURL(readParams());
  drawCargoList(current.r);
  const btn = document.querySelector(`#cargo-${key} .expand`);
  if (btn && focus) btn.focus();
}

async function copyText(text, scope, ta, okMsg = "복사했습니다") {
  try {
    await navigator.clipboard.writeText(text);
    toast(okMsg);
    return;
  } catch { /* 아래 대체 방법 */ }
  const prev = document.activeElement;
  const area = ta || (() => { const t = el("textarea", "sr-only"); t.value = text; scope.appendChild(t); return t; })();
  area.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  if (!ta) {
    area.remove();
    if (prev && prev.isConnected && prev !== document.body) prev.focus({ preventScroll: true }); // 임시 글상자를 지우면 포커스가 사라지므로 되돌린다
  }
  toast(ok ? okMsg : "길게 눌러 복사하세요");
}

function drawDriver(r) {
  const d = driverAdvice(r);
  $("drv-warn").hidden = !d.roadWarn;
  $("drv-warn").textContent = d.roadWarn ? `■ ${d.roadWarn}` : "";
  const big = $("drv-big");
  big.replaceChildren();
  const neutral = !!d.roadWarn;
  if (d.flat) big.appendChild(el("p", "drv-flat", d.flatText));
  for (const row of d.rows) {
    const div = el("div", `drv-row${row.warn ? " warn" : neutral ? "" : " best"}`);
    div.append(el("span", "drv-label", row.label), el("span", "drv-value", row.value));
    if (row.sub) div.appendChild(el("span", "drv-sub", row.sub));
    big.appendChild(div);
  }
  $("drv-best-sr").textContent = d.rows.map((x) => `${x.label}: ${x.value}`).join(". ");
  // 시간대 띠: 칸 하나에 시각과 소요. 한 칸만 Tab으로 들어가고 ←/→로 옮긴다.
  const band = $("drv-band");
  band.replaceChildren();
  band.classList.toggle("flat", d.flat);
  const sel = { i: null };
  const select = (i, focus) => {
    const btns = [...band.querySelectorAll(".cell")];
    btns.forEach((b, j) => { b.setAttribute("aria-pressed", j === i ? "true" : "false"); b.tabIndex = j === i ? 0 : -1; });
    sel.i = i;
    const c = d.cells[i];
    $("drv-readout").textContent = c.aria;
    if (focus) btns[i].focus();
  };
  d.cells.forEach((c, i) => {
    const b = el("button", "cell");
    b.type = "button";
    b.dataset.level = c.level;
    if (c.best) b.classList.add("best");
    if (c.early) b.classList.add("early");
    if (c.closed) b.classList.add("closed");
    b.setAttribute("aria-label", c.aria + (c.best ? " · 컨테이너 찾기 좋은 시간" : ""));
    b.setAttribute("aria-pressed", "false");
    b.tabIndex = i === 0 ? 0 : -1;
    if (c.readyHere) b.appendChild(el("span", "ready-tag", `▼${d.ready.label}`));
    if (!d.flat) b.appendChild(shapeIcon(c.level));
    b.append(el("span", "h", c.label.replace("시", "")), el("span", "dur", fmtDurShort(c.q50)));
    b.addEventListener("click", () => select(i, false));
    b.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
      e.preventDefault();
      const n = d.cells.length;
      const cur = sel.i ?? i;
      const j = e.key === "Home" ? 0 : e.key === "End" ? n - 1 : Math.max(0, Math.min(n - 1, cur + (e.key === "ArrowRight" ? 1 : -1)));
      select(j, true);
    });
    band.appendChild(b);
  });
  $("drv-readout").textContent = "";
  $("lg-best").hidden = !d.cells.some((c) => c.best) || d.flat;
  $("lg-early").hidden = !d.cells.some((c) => c.early);
  $("lg-closed").hidden = !d.cells.some((c) => c.closed);
  for (const x of document.querySelectorAll(".drv-legend .lg-lv")) x.hidden = d.flat;
  $("drv-ready").textContent = d.readyLine || "";
  $("drv-ready").hidden = !d.readyLine;
}

function drawTrend(r) {
  $("trend-line").textContent = trendLine(r);
  drawRdChart($("trend-chart"), $("trend-legend"), r, { smooth: false, truth: false, height: innerWidth < 600 ? 200 : 220, nowLabel: `지금 ${asOfText(r)}`, main: true });
}

function drawTrust(r) {
  const t = trustLines(VALID, r);
  const fill = (ul, items, level) => {
    ul.replaceChildren();
    for (const text of items) {
      const li = el("li");
      li.dataset.level = level;
      li.append(shapeIcon(level), el("span", null, text));
      ul.appendChild(li);
    }
  };
  fill($("trust-good"), t.good, "good");
  fill($("trust-limits"), t.limits, "warning");
  $("trust-good-h").hidden = !t.good.length;
  $("trust-good").hidden = !t.good.length;
}

// 가상 시험에서 판단 시각 예상 범위가 정답을 담은 비율(상황별 최소~최대)
function coverageText() {
  if (!VALID) return "";
  const cs = Object.values(VALID.scenarios).map((s) => s.coverage_80_now).filter((v) => v != null);
  if (!cs.length) return "";
  return `(가상 시험에서 정답을 담은 비율 ${Math.round(Math.min(...cs) * 100)}~${Math.round(Math.max(...cs) * 100)}%)`;
}

function drawFooter(r, src) {
  const v = r.meta.version;
  $("foot-ver").textContent = `v${APP_VERSION} · 엔진 v${v}`;
  $("about-ver").textContent = `v${v}`;
  const p = r.meta.params;
  $("build-info").textContent = src.kind === "engine"
    ? `브라우저에서 다시 계산한 결과(${(src.ms / 1000).toFixed(1)}초) · 입자 ${p.particles} · κ ${num(p.kappa, 1)} · 세계 ${p.world} · 시드 ${p.seed}${r.meta.h2 ? " · 수소 비교" : ""}`
    : "기본 조건으로 미리 계산한 결과 · 모든 수치는 합성 데이터";
}

// ---------- 차트(메인·서랍 공용) ----------
function drawRdChart(box, legendBox, r, opts) {
  const th = r.t_hist, tf = r.rd.fore_t;
  const zip = (ts, vs) => ts.map((t, i) => [t, vs[i]]);
  const cut = opts.main ? tf.length - 1 : tf.length; // 메인 차트는 예측 창 끝 점(가장자리 효과)을 그리지 않는다
  const tfm = tf.slice(0, cut), fq = r.rd.fore_q.slice(0, cut);
  const lines = [
    { name: "실시간 추정", color: "var(--s1)", pts: zip(th, r.rd.filter_mean) },
    { name: "예측", color: "var(--s1)", pts: zip(tfm, fq.map((q) => q[1])), dash: "5 4" },
  ];
  if (opts.smooth) lines.push({ name: "사후 재추정", color: "var(--smooth)", pts: zip(th, r.rd.smooth), width: 1.5 });
  if (opts.truth) {
    lines.push({ name: "검증용 정답", color: "var(--truth)", pts: zip(th, r.rd.truth), width: 1.5 });
    lines.push({ name: "검증용 정답(미래)", color: "var(--truth)", pts: zip(r.t_fore, r.rd.truth_fore), width: 1.5, opacity: 0.6 });
  }
  const all = [...r.rd.filter_q90, ...fq.map((q) => q[2]), ...(opts.truth ? [...r.rd.truth, ...r.rd.truth_fore] : [])].filter((v) => v != null);
  const yMax = Math.max(opts.main ? 2 : 1.4, Math.ceil(Math.max(...all) * 5 + 0.5) / 5);
  const fmt = (v) => (v == null ? "–" : num(v, 2));
  const at = (ts, vs, t) => { const i = ts.indexOf(t); return i < 0 ? null : vs[i]; };
  legend(legendBox, [
    { label: "실시간 추정(10:00까지)", color: "var(--s1)" },
    { label: "예측(10:00 이후)", color: "var(--s1)", kind: "dash" },
    { label: "예상 범위(모형 80%)", color: "var(--s1)", kind: "band" },
    ...(opts.smooth ? [{ label: "사후 재추정", color: "var(--smooth)" }] : []),
    ...(opts.truth ? [{ label: "검증용 정답", color: "var(--truth)" }] : []),
    { label: "1 = 유지, 위는 불어남", kind: "rule" },
  ]);
  const now = r.now;
  const soft = trendLine(r);
  timeChart(box, {
    height: opts.height || 260, aria: `지연 번짐 지수: 지금 ${fmtRd(now.rd_median)}. ${soft}`,
    x: { domain: [-24, 24], ticks: [-24, -18, -12, -6, 0, 6, 12, 18, 24], format: tickLabel, tipFormat: (t) => clock(t) },
    y: { domain: [0, yMax], ticks: niceTicks(0, yMax, 4), format: (v) => v.toFixed(1) },
    bands: [
      { lo: zip(th, r.rd.filter_q10), hi: zip(th, r.rd.filter_q90), color: "var(--s1)", opacity: 0.16 },
      { lo: zip(tfm, fq.map((q) => q[0])), hi: zip(tfm, fq.map((q) => q[2])), color: "var(--s1)", opacity: 0.08 },
    ],
    lines, refLines: [{ y: 1, label: "1" }], nowLine: 0, nowLabel: opts.nowLabel || "판단 10:00",
    tooltip: (t) => {
      const rows = [];
      if (t <= 0) {
        rows.push({ name: "추정", value: fmt(at(th, r.rd.filter_mean, t)), color: "var(--s1)" });
        rows.push({ name: "예상 범위", value: `${fmt(at(th, r.rd.filter_q10, t))}~${fmt(at(th, r.rd.filter_q90, t))}` });
        rows.push({ name: "번지는 중일 확률", value: pct(at(th, r.rd.p_gt1, t)) });
        if (opts.smooth) rows.push({ name: "사후 재추정", value: fmt(at(th, r.rd.smooth, t)), color: "var(--smooth)" });
        if (opts.truth) rows.push({ name: "검증용 정답", value: fmt(at(th, r.rd.truth, t)), color: "var(--truth)" });
      } else {
        const q = fq[tfm.indexOf(t)];
        if (q) {
          rows.push({ name: "예측", value: fmt(q[1]), color: "var(--s1)" });
          rows.push({ name: "예상 범위", value: `${fmt(q[0])}~${fmt(q[2])}` });
          rows.push({ name: "번지는 중일 확률", value: pct(q[3]) });
        }
        if (opts.truth) rows.push({ name: "검증용 정답", value: fmt(at(r.t_fore, r.rd.truth_fore, t)), color: "var(--truth)" });
      }
      return rows;
    },
  });
}

// ---------- 자세히 보기 서랍 ----------
const TABS = ["trend", "nodes", "cause", "network", "queue", "obs", "valid"];
let detailOpener = null;

function openDetail(tab, opener) {
  detailOpener = opener || document.activeElement;
  $("detail-h").textContent = `자세히 보기 — ${SCENARIO_CHIP[current.r.meta.scenario]} 상황`;
  const dlg = $("detail");
  if (!dlg.open) dlg.showModal();
  selectTab(tab || state.tab);
}

function selectTab(tab) {
  state.tab = tab;
  for (const t of TABS) {
    const b = $(`tab-${t}`), p = $(`panel-${t}`);
    const on = t === tab;
    b.setAttribute("aria-selected", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
    p.hidden = !on;
    if (on) b.scrollIntoView({ inline: "nearest", block: "nearest" });
  }
  requestAnimationFrame(() => drawTab(tab));
}

function drawTab(tab) {
  const r = current.r;
  if (tab === "trend") {
    drawRdChart($("rd-chart"), $("rd-legend"), r, { smooth: $("show-smooth").checked, truth: $("show-truth").checked, height: 260 });
    const cap = $("rd-caption");
    cap.hidden = !$("show-truth").checked;
    if (!cap.hidden) cap.textContent = rdCaption(r);
  } else if (tab === "nodes") drawNodes(r);
  else if (tab === "cause") drawCause(r);
  else if (tab === "network") drawNetwork(r);
  else if (tab === "queue") { drawQueue(r); drawGateChart(r); }
  else if (tab === "obs") drawObs(r);
  else if (tab === "valid") drawSceneValid(r);
}

function rdCaption(r) {
  const tn = r.truth_now.rd;
  const inside = tn >= r.now.rd_q10 && tn <= r.now.rd_q90;
  const hidden = r.meta.hidden_shocks || [];
  const vs = VALID && VALID.scenarios[r.meta.scenario];
  const covNow = vs ? ` 이 장면의 60개 가상 세계에서 판단 시각 예상 범위가 정답을 담은 비율은 ${pct(vs.coverage_80_now)}입니다.` : "";
  let why = "예상 범위는 정의상 10번 중 2번은 정답을 놓칩니다.";
  if (hidden.includes("숨은 전파율 상승")) why = "공지되지 않은 숨은 전파율 상승을 AI 사전평균이 작게 잡아 추정이 지속적으로 낮게 나옵니다.";
  else if (hidden.includes("공지되지 않은 생산성 저하")) why = "공지되지 않은 생산성(해소율) 저하를 계산이 모르기 때문입니다.";
  return inside
    ? `판단 시각의 검증용 정답 ${num(tn, 2)}이 예상 범위 안에 있습니다.${covNow}`
    : `판단 시각의 검증용 정답 ${num(tn, 2)}이 예상 범위(${num(r.now.rd_q10, 2)}~${num(r.now.rd_q90, 2)}) 밖에 있습니다. ${why}${covNow} 지금 번지는 중일 확률과 막힘 경보를 함께 보세요.`;
}

function drawNodes(r) {
  const showTruth = $("show-truth").checked;
  const grid = $("nodes-grid");
  grid.replaceChildren();
  const th = r.t_hist, tf = r.t_fore;
  const col = (rows, k) => rows.map((row) => (row == null ? null : row[k]));
  const vals = [...r.x.filter_q90.flat(), ...r.x.fore_q90.flat(), ...r.x.threshold, ...(showTruth ? r.x.truth.flat() : [])].filter((v) => v != null);
  const yMax = Math.min(1, Math.ceil(Math.max(...vals) * 20 + 0.5) / 20);
  legend($("nodes-legend"), [
    { label: "추정·예측", color: "var(--ink-2)" },
    { label: "예상 범위", color: "var(--ink-2)", kind: "band" },
    ...(showTruth ? [{ label: "검증용 정답", color: "var(--truth)" }] : []),
    { label: "막힘 기준", kind: "rule" },
  ]);
  r.nodes.forEach((nd, k) => {
    const wrap = el("div", "mini");
    const now = r.now;
    const sub = el("div", "now", `지금 ${pct(now.x_mean[k], 1)} · 기준 ${pct(r.x.threshold[k])} · 물동량 평소의 ${num(now.xi[k], 2)}배` +
      (now.capacity[k] < 0.999 ? ` · 처리능력 평소의 ${pct(now.capacity[k])}` : ""));
    const c = el("div", "chart");
    wrap.append(el("h4", null, NODE_NAME[nd.id] || nd.name), sub, c);
    grid.appendChild(wrap);
    const zip = (ts, vs) => ts.map((t, i) => [t, vs[i]]);
    const lines = [
      { name: "추정", color: COLORS[k], pts: zip(th, col(r.x.filter_mean, k)) },
      { name: "예측", color: COLORS[k], pts: zip(tf, col(r.x.fore_q50, k)), opacity: 0.75 },
    ];
    if (showTruth) lines.push({ name: "검증용 정답", color: "var(--truth)", pts: zip(th, col(r.x.truth, k)), width: 1.5 });
    const at = (ts, vs, t) => { const i = ts.indexOf(t); return i < 0 ? null : vs[i]; };
    timeChart(c, {
      height: 170, aria: `${NODE_NAME[nd.id]} 지연 중 비율`,
      x: { domain: [-24, 24], ticks: [-24, -12, 0, 12, 24], format: tickLabel, tipFormat: (t) => clock(t) },
      y: { domain: [0, yMax], ticks: niceTicks(0, yMax, 3), format: (v) => `${Math.round(v * 100)}%` },
      bands: [
        { lo: zip(th, col(r.x.filter_q10, k)), hi: zip(th, col(r.x.filter_q90, k)), color: COLORS[k], opacity: 0.14 },
        { lo: zip(tf, col(r.x.fore_q10, k)), hi: zip(tf, col(r.x.fore_q90, k)), color: COLORS[k], opacity: 0.09 },
      ],
      lines, refLines: [{ y: r.x.threshold[k] }], nowLine: 0, nowLabel: "",
      tooltip: (t) => {
        const rows = t <= 0
          ? [{ name: "추정", value: pct(at(th, col(r.x.filter_mean, k), t), 1), color: COLORS[k] },
             { name: "예상 범위", value: `${pct(at(th, col(r.x.filter_q10, k), t), 1)}~${pct(at(th, col(r.x.filter_q90, k), t), 1)}` }]
          : [{ name: "예측", value: pct(at(tf, col(r.x.fore_q50, k), t), 1), color: COLORS[k] },
             { name: "예상 범위", value: `${pct(at(tf, col(r.x.fore_q10, k), t), 1)}~${pct(at(tf, col(r.x.fore_q90, k), t), 1)}` }];
        if (showTruth && t <= 0) rows.push({ name: "검증용 정답", value: pct(at(th, col(r.x.truth, k), t), 1), color: "var(--truth)" });
        return rows;
      },
    });
  });
}

const FACTOR_NAME = { now: "지금", volume: "물량이 평소 수준이었다면", capacity: "처리능력이 정상이었다면", beta: "날씨·장비 충격이 없었다면" };
function drawCause(r) {
  const box = $("factor-bars");
  box.replaceChildren();
  const S = 2;
  const pos = (v) => `${Math.max(0, Math.min(100, (v / S) * 100))}%`;
  for (const f of r.now.factors) {
    const row = el("div", `fbar${f.key === "now" ? " now" : ""}`);
    row.appendChild(el("span", "f-name", FACTOR_NAME[f.key] || f.label));
    const track = el("span", "f-track");
    track.setAttribute("role", "img");
    track.setAttribute("aria-label", `${FACTOR_NAME[f.key] || f.label}: ${num(f.median, 2)} (예상 범위 ${num(f.q10, 2)}~${num(f.q90, 2)})`);
    const one = el("span", "f-one");
    one.style.left = pos(1);
    const rng = el("span", "f-range");
    rng.style.left = pos(f.q10);
    rng.style.width = `calc(${pos(f.q90)} - ${pos(f.q10)})`;
    const dot = el("span", "f-dot");
    dot.style.left = pos(f.median);
    track.append(one, rng, dot);
    row.appendChild(track);
    row.appendChild(el("span", "f-val", num(f.median, 2)));
    row.appendChild(el("span", `f-word ${f.p_gt1 >= 0.5 ? "go" : "stop"}`, f.p_gt1 >= 0.5 ? "계속 번짐" : "멈춤"));
    box.appendChild(row);
  }
  const theta = (r.meta.params && r.meta.params.theta) || 0;
  const d0 = r.now.decomposition[theta.toFixed(1)] || r.now.decomposition["0.0"];
  const notes = $("cause-notes");
  notes.replaceChildren(
    el("li", null, `지금 번지는 중이라면, 물량을 평소로 돌려도 계속 번질 확률 ${pct(d0.p_noxi_given_spread)}`),
    el("li", null, "지금 번지는 중일 확률이 50%를 넘으면 경보, 35~65%면 원인은 판단 보류"),
    el("li", null, "원인 판정은 모형 계산상 배분이며 책임 소재 판단에 쓰지 않습니다"),
  );
  table($("cause-table"), ["거점 규모 가정 θ", "물량을 평소로 돌린 번짐 지수", "번지는 중일 때 계속 번질 확률", "판정"],
    Object.entries(r.now.decomposition).map(([th, d]) => ({
      cls: th === "0.0" ? "chosen" : "", cells: [`θ = ${th}${th === "0.0" ? " (기본)" : ""}`, num(d.rd_noxi_median, 3), pct(d.p_noxi_given_spread, 1), d.label],
    })), [1, 2]);
  table($("xi-table"), ["거점", "물동량(평소 대비)", "규모 비", "지연 중 비율"],
    r.nodes.map((nd, k) => ({ cells: [NODE_NAME[nd.id] || nd.name, num(r.now.xi[k], 3), num(r.now.ratio[k], 3), pct(r.now.x_mean[k], 1)] })), [1, 2, 3]);
}

function drawNetwork(r) {
  const c = r.contrib;
  const k = c.node_share.indexOf(Math.max(...c.node_share));
  const lead = NODE_NAME[r.nodes[k].id];
  const air = r.cargo.sea_air;
  const airPlan = air ? air.candidates.find((x) => x.is_plan) : null;
  $("self-share").textContent = `이번 장면은 ${lead} 안의 전파가 연쇄의 ${pct(c.matrix[k][k], 0)}를 차지하고(모든 거점 내부 ${pct(c.self_share, 1)}, 거점 사이 ${pct(1 - c.self_share, 1)}), ` +
    `하류로는 화물 마감 경로로 번집니다` + (airPlan ? `. Sea&Air 화물의 인천 반입 마감 놓칠 확률(${pct(airPlan.p_fail, 0)})은 주로 컨테이너 양하 지연 때문입니다.` : ".");
  $("where-note").textContent = whereNote(r) || "";
  const H = Math.round(c.generation_hours * 6);
  $("amp-h").textContent = `항공 반입 거점으로 번지는 지연(약 ${H}시간 안에, 건)`;
  $("amp-note").textContent = `각 거점의 지연 1건(선박 작업·차량·항공 화물)이 약 ${H}시간 동안 항공 반입 거점에 낳는 기대 지연 건수(거점 규모 반영)입니다. ‘항공’ 막대는 항공 거점 안의 재발생입니다.`;
  const nodes = r.nodes.map((n) => ({ ...n, short: { AT: "터미널 A", PN: "다른 터미널", L: "게이트·도로", A: "항공" }[n.id] || n.short }));
  networkChart($("net-chart"), { nodes, shares: c.node_share, edges: c.edges, colors: COLORS, format: (v) => pct(v, v < 0.1 ? 1 : 0) });
  barList($("share-chart"), nodes.map((nd, i) => ({ label: nd.short, value: c.node_share[i], color: COLORS[i] })), { format: (v) => pct(v, 1), max: 1, aria: "거점별 기여 몫" });
  barList($("amp-chart"), nodes.map((nd, i) => ({ label: nd.id === "A" ? "항공(내부)" : nd.short, value: c.amplification_to_air[i], color: COLORS[i] })), { format: (v) => `${num(v, 2)}건`, aria: "항공 반입으로 번지는 기대 지연" });
}

function drawQueue(r) {
  const q = r.queue;
  columnChart($("queue-chart"), {
    values: q.dist, labels: q.dist.map((_, i) => String(i)), color: "var(--s1)", labelEvery: 2,
    yFormat: (v) => `${Math.round(v * 100)}%`, yFormatTip: (v) => pct(v, 1),
    tipTitle: (i) => `앞에 선박 ${i}척`, tipName: "확률", xTitle: "앞에 있는 선박 수(척)", aria: "선석 대기 선박 수 분포",
  });
  kv($("queue-kv"), [
    ["터미널", `터미널 A(가상)(선석 ${q.servers}개)`],
    ["대상 선박 대기", `보통 ${fmtDur(q.wait_median_h)} · 늦으면 ${q.wait_q90_h == null ? "예측 범위 밖" : fmtDur(q.wait_q90_h)}`],
    ["12시간 넘게 기다릴 확률", pct(q.p_wait_gt12, 1)],
    ["계산 상한(60척) 넘침 확률", q.overflow < 1e-4 ? "0.01% 미만" : pct(q.overflow, 2)],
    ["적용 범위", "정기선 선석 윈도 배정과 달라, 윈도를 벗어난 선박의 대기에만 적용합니다."],
  ]);
}

function drawGateChart(r) {
  const g = r.gate;
  const ts = g.map((x) => x.t);
  const yMax = Math.max(1.5, Math.ceil(Math.max(...g.map((x) => x.q90)) * 2 + 0.5) / 2);
  timeChart($("gate-chart"), {
    height: 220, aria: "도착 시각별 터미널 반출 예상 소요",
    x: { domain: [0, 14], ticks: [0, 2, 4, 6, 8, 10, 12, 14], format: tickLabel, tipFormat: (t) => `${clock(t)} 도착` },
    y: { domain: [0, yMax], ticks: niceTicks(0, yMax, 3), format: (v) => (v === 0 ? "0" : `${v}시간`) },
    bands: [{ lo: g.map((x) => [x.t, x.q10]), hi: g.map((x) => [x.t, x.q90]), color: "var(--s3)", opacity: 0.16 }],
    lines: [{ name: "가운데 값", color: "var(--s3)", pts: g.map((x) => [x.t, x.q50]) }],
    dots: g.filter((x) => x.recommended).map((x) => ({ t: x.t, v: x.q50, color: "var(--s3)" })),
    tooltip: (t) => {
      const x = g[ts.indexOf(t)];
      return x ? [{ name: "가운데 값", value: fmtDur(x.q50), color: "var(--s3)" }, { name: "예상 범위", value: `${fmtDur(x.q10)}~${fmtDur(x.q90)}` },
        ...(x.recommended ? [{ name: "덜 걸리는 시간", value: "●" }] : [])] : [];
    },
  });
}

function drawObs(r) {
  const ids = r.nodes.map((n) => n.id);
  const head = ["시각", ...r.nodes.map((n) => `${NODE_NAME[n.id]} 새 지연 · 표본 중 지연`), "계산 표본 상태(ESS)", "재표본"];
  table($("obs-table"), head, r.observations.map((o) => ({
    cells: [clock(o.t), ...ids.map((id, k) => `${o.y[id]} · ${o.h[id]}/${r.snap_n[k]}`), num(o.ess, 0), o.resampled ? "예" : "아니오"],
  })), [1, 2, 3, 4, 5]);
}

function drawSceneValid(r) {
  const tn = r.truth_now;
  const jam = (tn.congested || []).map((s) => NODE_NAME[(r.nodes.find((n) => n.short === s) || {}).id] || s);
  $("truth-line").textContent = `검증용 정답(가상 세계에서만 알 수 있는 값): 번짐 지수 ${num(tn.rd, 2)} · 판정 ${tn.label}${jam.length ? ` · 막힘 ${jam.join(", ")}` : ""}`;
  const v = r.validation;
  const inside = tn.rd >= r.now.rd_q10 && tn.rd <= r.now.rd_q90;
  const S = VALID && VALID.scenarios[r.meta.scenario];
  kv($("valid-kv"), [
    ["번짐 지수 오차(RMSE) · 실시간 / 사후 재추정", `${num(v.rmse_filter, 3)} / ${num(v.rmse_smooth, 3)}`],
    ["예상 범위가 정답을 담은 비율(이 세계, 지난 24시간)", `${pct(v.coverage_80, 1)} (목표 80%)`],
    ["판단 시각 번짐 지수 · 정답", `${num(r.now.rd_median, 2)} (예상 범위 ${num(r.now.rd_q10, 2)}~${num(r.now.rd_q90, 2)}) · ${num(tn.rd, 2)} → ${inside ? "범위 안" : "범위 밖"}`],
    ["판단 시각 원인 판정 · 정답", `${r.now.cause} · ${tn.label}`],
    ...(S ? [["이 상황 60회 시험의 포함률(24시간 평균 / 마지막 6시간 / 판단 시각)", `${pct(S.coverage_80_mean)} / ${pct(S.coverage_80_last6h)} / ${pct(S.coverage_80_now)}`]] : []),
  ]);
  if (!inside) $("truth-line").textContent += ` — ${rdCaption(r).split(". ").slice(1).join(". ")}`;
  const p = r.meta.params;
  const src = current.src;
  kv($("calc-info"), [
    ["엔진", `v${r.meta.version}${engineInfo ? ` · Python ${engineInfo.python} · NumPy ${engineInfo.numpy}` : ""}`],
    ["결과 출처", src.kind === "engine" ? `이 브라우저에서 다시 계산(${(src.ms / 1000).toFixed(1)}초)` : "기본 조건으로 미리 계산"],
    ["계산 조건", `입자 ${p.particles} · κ ${num(p.kappa, 1)} · 가상 세계 ${p.world} · 시드 ${p.seed}`],
    ["판단 시각 계산 표본 상태(ESS)", num(v.ess_now, 0)],
  ]);
}

// ---------- 조건 바꾸기 ----------
let settingsOpener = null;
function buildSettings() {
  const box = $("cargo-sets");
  box.replaceChildren();
  for (const [key, c] of Object.entries(PRE.presets.cargo)) {
    const d = el("details", "cargo-set");
    d.dataset.cargo = key;
    const sum = el("summary");
    sum.append(el("b", null, c.label), el("span", "aside set-sum", ""), el("span", "set-more", "바꾸기 ▸"));
    d.appendChild(sum);
    const f1 = el("div", "field range");
    const l1 = el("label", null, "허용 위험(감수할 놓칠 확률)");
    l1.htmlFor = `eps-${key}`;
    const row = el("div", "row");
    const rng = el("input");
    Object.assign(rng, { type: "range", id: `eps-${key}`, min: "0.01", max: "0.30", step: "0.01" });
    const out = el("output");
    out.htmlFor = rng.id;
    row.append(rng, out);
    f1.append(l1, row);
    const f2 = el("div", "field");
    const l2 = el("label", null, "마감 놓치면 드는 비용(만 원)");
    l2.htmlFor = `cost-${key}`;
    const inp = el("input");
    Object.assign(inp, { type: "number", id: `cost-${key}`, min: "0", step: "10" });
    f2.append(l2, inp);
    d.append(f1, f2);
    rng.addEventListener("input", () => { out.textContent = pct(+rng.value); rng.setAttribute("aria-valuetext", pct(+rng.value)); });
    rng.addEventListener("change", () => {
      const v = +rng.value;
      if (Math.abs(v - c.epsilon) < 1e-9) delete overrides.eps[key]; else overrides.eps[key] = v;
      syncSettings();
    });
    inp.addEventListener("change", () => {
      const v = Math.max(0, +inp.value || 0) * 10000;
      if (Math.abs(v - c.fail_cost) < 1) delete overrides.cost[key]; else overrides.cost[key] = v;
      syncSettings();
    });
    box.appendChild(d);
  }
}
function syncSettings() {
  for (const key of Object.keys(PRE.presets.cargo)) {
    const rng = $(`eps-${key}`), inp = $(`cost-${key}`);
    rng.value = epsFor(key);
    rng.nextElementSibling.textContent = pct(epsFor(key));
    rng.setAttribute("aria-valuetext", pct(epsFor(key)));
    inp.value = Math.round(costFor(key) / 10000);
    rng.closest("details").querySelector(".set-sum").textContent = `허용 위험 ${pct(epsFor(key))} · 놓치면 ${fmtWon(costFor(key)).replace("약 ", "")}`;
  }
  $("kappa-out").textContent = (+$("kappa").value).toFixed(1);
}
function openSettings(cargoKey, opener) {
  settingsOpener = opener || document.activeElement;
  state.settingsCargo = cargoKey || null;
  syncSettings();
  const dlg = $("settings");
  for (const d of dlg.querySelectorAll(".cargo-set")) d.open = d.dataset.cargo === cargoKey;
  if (!dlg.open) dlg.showModal();
  if (cargoKey) $(`eps-${cargoKey}`).focus();
  ensureWorker().catch(() => {});
}

// ---------- 시작 ----------
function bindDialogs() {
  for (const dlg of document.querySelectorAll("dialog")) {
    dlg.addEventListener("click", (e) => { if (e.target === dlg || e.target.closest("[data-close]")) dlg.close(); });
  }
  $("detail").addEventListener("close", () => { if (detailOpener && detailOpener.focus) detailOpener.focus(); });
  $("settings").addEventListener("close", () => {
    // 다시 그리면 연 버튼이 사라지므로, 화물 카드에서 열었으면 그 카드의 펼치기 버튼으로 돌아간다
    if (settingsOpener && settingsOpener.isConnected) settingsOpener.focus();
    else {
      const b = (state.settingsCargo && document.querySelector(`#cargo-${state.settingsCargo} .expand`)) || $("open-settings");
      b.focus();
    }
  });
  let aboutOpener = null;
  for (const id of ["about-btn", "about-btn-m"]) $(id).addEventListener("click", (e) => { aboutOpener = e.currentTarget; $("about").showModal(); });
  $("about").addEventListener("close", () => { if (aboutOpener) aboutOpener.focus(); });
  // 탭: 클릭과 좌우 화살표
  const tablist = document.querySelector("#detail [role=tablist]");
  tablist.addEventListener("click", (e) => { const b = e.target.closest("[role=tab]"); if (b) selectTab(b.id.replace("tab-", "")); });
  tablist.addEventListener("keydown", (e) => {
    const i = TABS.indexOf(state.tab);
    let j = null;
    if (e.key === "ArrowRight") j = (i + 1) % TABS.length;
    else if (e.key === "ArrowLeft") j = (i - 1 + TABS.length) % TABS.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = TABS.length - 1;
    if (j == null) return;
    e.preventDefault();
    selectTab(TABS[j]);
    $(`tab-${TABS[j]}`).focus();
  });
  $("show-truth").addEventListener("change", () => drawTab(state.tab));
  $("show-smooth").addEventListener("change", () => drawTab(state.tab));
}

function buildChips() {
  const box = $("scenario-chips");
  const keys = Object.keys(PRE.presets.scenarios);
  for (const k of keys) {
    const b = el("button", "chip", SCENARIO_CHIP[k] || PRE.presets.scenarios[k].label);
    b.type = "button";
    b.dataset.key = k;
    b.setAttribute("role", "radio");
    b.title = PRE.presets.scenarios[k].label;
    b.addEventListener("click", () => chooseScenario(k));
    box.appendChild(b);
  }
  box.addEventListener("keydown", (e) => {
    const i = keys.indexOf(state.scenario);
    let j = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") j = (i + 1) % keys.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") j = (i - 1 + keys.length) % keys.length;
    else if (e.key === "Home") j = 0;
    else if (e.key === "End") j = keys.length - 1;
    if (j == null) return;
    e.preventDefault();
    chooseScenario(keys[j]);
    box.children[j].focus();
  });
}

function chooseScenario(k) {
  if (k === state.scenario && current && current.src.kind === "pre") return;
  const before = readParams();
  const wasCustom = !isDefault(before);
  state.scenario = k;
  if (wasCustom) { resetToDefaults(); toast("조건을 기본값으로 되돌렸습니다"); }
  setFleet(scenarioH2(k));
  update();
}

// 예전 주소의 구역 해시를 새 화면으로 옮긴다
function routeHash() {
  const h = location.hash.replace("#", "");
  const toTab = { rd: "trend", nodes: "nodes", network: "network", cause: "cause", queue: "queue", obs: "obs" };
  if (h === "alarm") { location.replace("#status"); return; }
  if (h === "gate") { location.replace("#driver"); return; }
  if (h === "valid" || h === "model") { location.href = `method.html#${h}`; return; }
  if (toTab[h]) { history.replaceState(null, "", `${location.pathname}${location.search}`); openDetail(toTab[h]); }
}

function watchJump() {
  if (!("IntersectionObserver" in window)) return;
  const links = [...document.querySelectorAll("#jump a")];
  const map = new Map(links.map((a) => [a.getAttribute("href").slice(1), a]));
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      for (const a of links) a.removeAttribute("aria-current");
      const a = map.get(e.target.id);
      if (a) a.setAttribute("aria-current", "true");
    }
  }, { rootMargin: "-45% 0px -50% 0px" });
  for (const id of map.keys()) io.observe($(id));
}

async function init() {
  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    navigator.serviceWorker.register("sw.js", { updateViaCache: "none" }).catch(() => {});
  }
  const [pre, valid, model] = await Promise.all([
    fetch("data/precomputed.json", { cache: "no-cache" }).then((r) => { if (!r.ok) throw new Error("precomputed.json"); return r.json(); }),
    fetch("data/validation.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    fetch("data/ai_model.json", { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]);
  PRE = pre;
  VALID = valid;
  MODEL = model;
  buildChips();
  buildSettings();
  bindDialogs();
  const { hadCargo } = applyURL();
  syncSettings();

  $("scenario-more").addEventListener("click", () => {
    const open = $("scenario-summary").hidden;
    $("scenario-summary").hidden = !open;
    $("scenario-more").setAttribute("aria-expanded", open ? "true" : "false");
    $("scenario-more").textContent = open ? "상황 설명 ▴" : "상황 설명 ▾";
  });
  $("open-detail").addEventListener("click", (e) => openDetail("trend", e.currentTarget));
  $("open-valid").addEventListener("click", (e) => openDetail("valid", e.currentTarget));
  $("open-settings").addEventListener("click", (e) => openSettings(null, e.currentTarget));
  $("kappa").addEventListener("input", syncSettings);
  $("run-btn").addEventListener("click", async () => {
    const dlg = $("settings");
    update({ force: true });
    if (dlg.open) dlg.close();
  });
  const resetAll = () => { resetToDefaults(); update(); toast("조건을 기본값으로 되돌렸습니다"); };
  $("reset-btn").addEventListener("click", () => { resetAll(); $("settings").close(); });
  $("reset-defaults").addEventListener("click", resetAll);
  $("reset-defaults-m").addEventListener("click", resetAll);
  let resizeTimer = null;
  let lastW = innerWidth;
  window.addEventListener("resize", () => {
    if (innerWidth === lastW) return;
    lastW = innerWidth;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (current) { drawTrend(current.r); if ($("detail").open) drawTab(state.tab); } }, 150);
  });
  window.addEventListener("hashchange", routeHash);

  await update();
  watchJump();
  if (hadCargo && state.expanded) {
    const card = $(`cargo-${state.expanded}`);
    if (card) card.scrollIntoView({ block: "start" });
  }
  routeHash();
  // 첫 화면을 그리기 전에 브라우저가 #구역으로 옮겨 둔 위치는 그린 뒤 어긋나므로 다시 맞춘다
  if (!hadCargo && location.hash.length > 1) {
    let t = null;
    try { t = document.getElementById(decodeURIComponent(location.hash.slice(1))); } catch { t = null; }
    if (t) t.scrollIntoView({ block: "start" });
  }
  // 데스크톱에서만 첫 화면을 그린 뒤 엔진을 미리 준비한다(휴대폰은 「조건 바꾸기」를 열 때).
  const saveData = navigator.connection && navigator.connection.saveData;
  if (!saveData && matchMedia("(min-width: 1024px)").matches && !worker) setTimeout(() => ensureWorker().catch(() => {}), 1200);
}

// 배포 직후 옛 서비스 워커가 옛 화면 파일을 섞어 주면 초기화가 실패한다.
// 새 워커가 내려받는 중이면 자리 잡을 때까지(최대 30초), 아니면 4초 기다렸다가 한 번만 새로 고친다(index.html의 안전장치와 같은 규칙).
async function reloadOnce() {
  if (!/^https?:$/.test(location.protocol)) return false;
  try { if (sessionStorage.getItem(RELOAD_KEY)) return false; sessionStorage.setItem(RELOAD_KEY, "1"); } catch { return false; }
  const sw = navigator.serviceWorker;
  let ms = 4000;
  if (sw) {
    const reg = await sw.getRegistration().catch(() => null);
    if (reg) { await reg.update().catch(() => {}); if (reg.installing || reg.waiting) ms = 30000; }
  }
  await Promise.race([sw ? new Promise((r) => sw.addEventListener("controllerchange", r, { once: true })) : sleep(ms), sleep(ms)]);
  location.reload();
  return true;
}

init().catch(async (err) => {
  if (await reloadOnce()) return;
  // 이미 한 번 새로 고쳤다면, 새 워커가 나중에 자리 잡을 때 한 번 더 연다(워커가 바뀔 때만이라 되풀이되지 않음)
  if (navigator.serviceWorker) navigator.serviceWorker.addEventListener("controllerchange", () => location.reload(), { once: true });
  const local = location.protocol === "file:" || /^(localhost|127\.0\.0\.1)$/.test(location.hostname);
  const hint = local ? " 로컬에서는 'python3 -m http.server -d docs 8000'으로 연 뒤 http://localhost:8000 에 접속하세요." : " 새로 고침해 보세요.";
  const p = el("p", "err", `초기화 실패: ${err.message}.${hint}`);
  const box = document.querySelector("main .wrap") || document.body;
  box.prepend(p);
});
