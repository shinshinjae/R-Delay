// 작동 원리·검증 페이지: 수식 렌더(KaTeX, 없으면 TeX 원문 상자), 목차 표시, 검증 표와 AI 모듈 표.
// 장면별 실행 결과는 쓰지 않고 validation.json · ai_model.json · precomputed.json(presets)만 읽는다.
import {
  drawValTable, valNoteText, drawNoticeTable, drawMisspecTable, drawCargoTable, valCargoNoteText, drawAiInfo,
} from "./validation-tables.js?v=20261023c";

const $ = (id) => document.getElementById(id);

// ---------- 수식 ----------
// 수식 TeX 원문(HTML에 글자 그대로 있음)을 KaTeX auto-render로 그린다. 휴대폰 폭에서는 \qquad·\quad로 나뉜 식을
// 한 줄씩 따로 그려 옆으로 밀 일을 줄인다(원문은 그대로 두고 그리는 방식만 바꿈).
// KaTeX(CDN)를 못 불러오면(오프라인 첫 방문 등) TeX 원문을 code 상자로 보여 준다.
const NARROW = window.matchMedia("(max-width: 599px)");
const MATH_OPTS = {
  delimiters: [{ left: "\\[", right: "\\]", display: true }, { left: "\\(", right: "\\)", display: false }], throwOnError: false,
};

function texFallback(eq, src) {
  const code = document.createElement("code");
  code.className = "tex";
  code.textContent = src;
  eq.replaceChildren(code);
  eq.classList.remove("is-math");
  eq.classList.add("is-tex");
}

function splitTex(src) {
  const body = src.trim().replace(/^\\\[/, "").replace(/\\\]$/, "").trim();
  const parts = body.split(/,\s*\\q?quad\s*/);
  return parts.map((p, i) => `\\[ ${p}${i < parts.length - 1 ? "," : ""} \\]`).join("\n");
}

function renderMath() {
  const root = $("model");
  if (!root) return;
  let missing = 0;
  for (const eq of root.querySelectorAll(".eq")) {
    if (eq.dataset.tex == null) eq.dataset.tex = eq.textContent;
    const src = eq.dataset.tex;
    if (!mathOk || !window.renderMathInElement) { texFallback(eq, src); missing++; continue; }
    eq.classList.remove("is-tex", "is-math");
    eq.textContent = NARROW.matches ? splitTex(src) : src;
    try { window.renderMathInElement(eq, MATH_OPTS); } catch { /* 아래에서 원문 상자로 바꾼다 */ }
    if (eq.querySelector(".katex")) eq.classList.add("is-math");
    else { texFallback(eq, src); missing++; }
  }
  const note = $("eq-fallback");
  if (note) note.hidden = missing === 0;
  root.classList.add("math-done");
  updateScrollHints();
}
NARROW.addEventListener?.("change", () => { if (mathOk) renderMath(); });

// KaTeX는 페이지를 막지 않게 따로 불러온다(느린 망에서도 본문·표가 먼저 보이게).
// 8초 안에 못 받으면 TeX 원문 상자로 두고, 나중에라도 받으면 다시 그린다.
const KATEX = "https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/";
let mathOk = false; // 글꼴 스타일과 스크립트를 모두 받았을 때만 KaTeX로 그린다(스타일 없이 그리면 수식이 두 번 보임)
function addAsset(tag, attrs) {
  return new Promise((resolve, reject) => {
    const n = document.createElement(tag);
    Object.assign(n, attrs, { crossOrigin: "anonymous", onload: resolve, onerror: reject });
    document.head.appendChild(n);
  });
}
function loadMath() {
  const ready = Promise.all([
    addAsset("link", { rel: "stylesheet", href: `${KATEX}katex.min.css` }),
    addAsset("script", { src: `${KATEX}katex.min.js` }).then(() => addAsset("script", { src: `${KATEX}contrib/auto-render.min.js` })),
  ]).then(() => { mathOk = true; });
  const late = new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 8000));
  Promise.race([ready, late]).then(renderMath, () => { renderMath(); ready.then(renderMath, () => {}); });
}

// ---------- 목차 ----------
function setupToc() {
  const links = [...document.querySelectorAll(".m-toc a[href^='#']")];
  const mobile = $("toc-mobile");
  links.forEach((a) => a.addEventListener("click", () => { if (mobile && mobile.contains(a)) mobile.open = false; }));
  if (!("IntersectionObserver" in window)) return;
  const sections = [...document.querySelectorAll("main section[id]")];
  const visible = new Map();
  const mark = () => {
    let id = null;
    for (const s of sections) if (visible.get(s.id)) { id = s.id; break; }
    if (!id) return;
    for (const a of links) {
      if (a.getAttribute("href") === `#${id}`) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    }
  };
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) visible.set(e.target.id, e.isIntersecting);
    mark();
  }, { rootMargin: "-10% 0px -55% 0px" });
  sections.forEach((s) => io.observe(s));
}

// 「시연용 가상 데이터」 안내: Esc나 바깥을 누르면 닫는다.
function setupAbout() {
  const about = $("about");
  if (!about) return;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && about.open) { about.open = false; about.querySelector("summary").focus(); }
  });
  document.addEventListener("click", (e) => { if (about.open && !about.contains(e.target)) about.open = false; });
}

// 표·수식이 상자보다 넓을 때만 「옆으로 밀어 보세요」 안내를 보인다.
function updateScrollHints() {
  for (const w of document.querySelectorAll(".m-table-wrap")) {
    w.classList.toggle("is-scroll", w.scrollWidth > w.clientWidth + 1);
  }
  for (const eq of document.querySelectorAll("#model .eq")) {
    const boxes = [...eq.querySelectorAll(".katex-display")];
    const over = (boxes.length ? boxes : [eq]).some((b) => b.scrollWidth > b.clientWidth + 1);
    eq.classList.toggle("is-scroll", over);
  }
}

// 검증 표 머리글: 쉬운 말 ↔ 원래 용어(th의 title)를 바꿔 보인다. 터치 화면에서는 title이 보이지 않기 때문.
function setupHeadToggle() {
  const btn = $("head-toggle");
  if (!btn) return;
  btn.hidden = false;
  btn.addEventListener("click", () => {
    const on = btn.getAttribute("aria-pressed") !== "true";
    btn.setAttribute("aria-pressed", String(on));
    for (const th of document.querySelectorAll("#valid thead th[title]")) {
      const shown = th.textContent;
      th.textContent = th.title;
      th.title = shown;
    }
    updateScrollHints();
  });
}

// 표와 수식이 그려진 뒤 높이가 바뀌므로, 주소에 #이 있으면 그 위치로 다시 맞춘다(사용자가 이미 움직였으면 두지 않음).
let userMoved = false;
["wheel", "touchstart", "keydown"].forEach((t) => window.addEventListener(t, () => { userMoved = true; }, { once: true, passive: true }));
function restoreHash() {
  if (userMoved) return;
  const id = decodeURIComponent(location.hash.slice(1));
  const target = id && document.getElementById(id);
  if (target) target.scrollIntoView({ block: "start" });
}

// ---------- 자료 ----------
const getJSON = (url) => fetch(url, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);

function fail(id, text) {
  const t = $(id);
  if (!t) return;
  const wrap = t.closest(".m-table-wrap") || t;
  const p = document.createElement("p");
  p.className = "m-error";
  p.textContent = text;
  wrap.replaceWith(p);
}

async function init() {
  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    navigator.serviceWorker.register("sw.js", { updateViaCache: "none" }).catch(() => {});
  }
  loadMath();
  setupToc();
  setupAbout();

  const [valid, model, pre] = await Promise.all([
    getJSON("data/validation.json"), getJSON("data/ai_model.json"), getJSON("data/precomputed.json"),
  ]);

  if (valid) {
    drawValTable($("val-table"), valid);
    $("val-note").textContent = valNoteText(valid);
    drawNoticeTable($("val-notice-table"), valid);
    drawMisspecTable($("val-misspec-table"), valid);
    drawCargoTable($("val-cargo-table"), valid, pre?.presets?.cargo || null);
    $("val-cargo-note").textContent = valCargoNoteText(valid);
    setupHeadToggle();
  } else {
    const msg = "검증 결과(validation.json)를 불러오지 못했습니다. 인터넷 연결을 확인한 뒤 새로 고쳐 주세요.";
    ["val-table", "val-notice-table", "val-misspec-table", "val-cargo-table"].forEach((id) => fail(id, msg));
  }
  drawAiInfo($("ai-info"), model);

  updateScrollHints();
  window.addEventListener("resize", updateScrollHints, { passive: true });
  if (document.fonts?.ready) document.fonts.ready.then(updateScrollHints);
  requestAnimationFrame(restoreHash);
  window.addEventListener("load", () => { updateScrollHints(); restoreHash(); }, { once: true });
  document.body.dataset.ready = "1";
}

init();
