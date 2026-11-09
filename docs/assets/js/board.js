// R-Delay 포워더 브리핑 화면: 상황 띠 → 화물판(시간축 위 오늘 관리 화물) → 브리핑 문서(화주 안내·배차 요청·사내 보고·선사·터미널 협의).
// 판단 규칙은 derive.js, 문서 문장은 briefing.js가 만든다. 여기서는 그리기와 클릭만 다룬다.
import { statusView, sortedCargo, classifyCargo, cargoView, nodeState, STATE_WORD, trustLines } from "./derive.js?v=20261109b";
import { SCENARIO_CHIP, clock, clock10, fmtP, fmtRd, fmtDur, asOfText, nextUpdateText, CARGO_SHORT } from "./format.js?v=20261109b";
import { DOC_KINDS, buildDoc, docToText, docToHTML, driverLink, cargoFacts, DEFAULT_SENDER, josa } from "./briefing.js?v=20261109b";

const $ = (id) => document.getElementById(id);
const HOURS = 20; // 10:00 → 내일 06:00
const pos = (t) => Math.max(0, Math.min(100, (t / HOURS) * 100));
const T = (t) => clock(t, false);
const ORDER = ["base", "terminal_failure", "typhoon_diversion", "holiday_surge", "epidemic", "h2_trucks"];
const SENDER_KEY = "rdelay-sender";
// 배차 요청에 넣는 기사님 링크는 받는 사람(운송사·기사님)이 열 수 있는 공개 주소여야 한다(로컬·오프라인 시연 중에도)
const PUBLIC_BASE = "https://shinshinjae.github.io/R-Delay/";
const linkBase = () => (/^https?:$/.test(location.protocol) && !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname) ? location.href : PUBLIC_BASE);
const esc = (s) => String(s).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));

function loadSender() {
  try { return { ...DEFAULT_SENDER, ...JSON.parse(localStorage.getItem(SENDER_KEY) || "{}") }; } catch { return { ...DEFAULT_SENDER }; }
}
function saveSender(s) {
  try { localStorage.setItem(SENDER_KEY, JSON.stringify(s)); } catch { /* 저장 못 해도 이번 화면에서는 쓴다 */ }
}

const state = { pre: null, valid: null, s: "terminal_failure", key: null, doc: "shipper", applied: {}, sender: loadSender(), edited: false, lastDoc: null };
const run = () => state.pre.runs[state.s];

// ---------- 공통 표시 ----------
const SHAPE = { critical: "sq", serious: "tri", warning: "tri", good: "dot", hold: "dia", done: "chk" };
const PILL = {
  notify: ["critical", "화주 안내 필요"], change: ["warning", "시간 변경"], "hold-conservative": ["hold", "안내 후 재확인"],
  "hold-uncertain": ["hold", "판단 보류"], optional: ["good", "바꿔도 됨"], ok: ["good", "계획대로"],
};
const DEFAULT_DOC = { notify: "shipper", "hold-conservative": "shipper", change: "driver", optional: "driver", ok: "driver", "hold-uncertain": "shipper" };
function riskLevel(p, eps) { return p > eps ? "high" : p > eps / 2 ? "mid" : "low"; }
function bigP(p) {
  if (p >= 0.995) return ["100", "%"];
  if (p < 0.01) return ["<1", "%"];
  return [String(Math.round(p * 100)), "%"];
}
const isH2 = (x) => !!x && x.vehicle === "수소";

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.dataset.on = "1";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.dataset.on = "0"; }, 2200);
}

// ---------- 상황 띠 ----------
const ICON = {
  sea: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 15h18l-2.5 4.5h-13z"/><path d="M6 15V9h12v6"/><path d="M9 9V5h4v4"/><path d="M2 21c2 0 2-1 4-1s2 1 4 1 2-1 4-1 2 1 4 1 2-1 4-1"/></svg>',
  land: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 6h12v10H2z"/><path d="M14 9h4l3 3.5V16h-7"/><circle cx="6" cy="17.5" r="1.8"/><circle cx="17" cy="17.5" r="1.8"/></svg>',
  air: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 13.5l7.5-2.2L14 4.5l2 .5-1.6 6 5.2-1.4c1-.3 2 .4 2 1.4 0 .6-.4 1.2-1 1.4l-15 4.2z"/><path d="M4 20h16"/></svg>',
};
function worst(states) {
  return states.includes("jam") ? "jam" : states.includes("watch") ? "watch" : "ok";
}
function renderRibbon(r) {
  const st = statusView(r);
  const level = st.hold ? "warning" : st.level;
  $("ribbon").dataset.level = level;
  const sh = $("rb-title").querySelector(".shape");
  sh.dataset.s = st.hold ? "dia" : SHAPE[level];
  $("rb-title-text").textContent = st.titleParts[0];
  $("rb-sub").textContent = st.titleParts[1];
  const idx = (id) => r.nodes.findIndex((n) => n.id === id);
  const stations = [
    ["sea", "해상", "신항 터미널", worst([nodeState(r, idx("AT")), nodeState(r, idx("PN"))])],
    ["land", "육상", "게이트·도로", nodeState(r, idx("L"))],
    ["air", "항공", "인천 반입", nodeState(r, idx("A"))],
  ];
  $("chain").dataset.spread = r.now.p_spread >= 0.5 ? "1" : "0";
  $("chain").innerHTML = stations.map(([d, a, b, s]) =>
    `<li class="stn" data-state="${s}"><span class="stn-dot">${ICON[d]}</span><span class="stn-name">${a} · ${b}</span><span class="stn-word">${STATE_WORD[s]}</span></li>`).join("");
  const facts = [];
  facts.push(["원인", st.cause ? st.cause.text : "특이 원인 없음"]);
  facts.push(["전망", st.outlook]);
  $("rb-facts").innerHTML = facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
  $("rd-v").textContent = fmtRd(r.now.rd_median);
  const g = Math.max(0, Math.min(100, ((r.now.rd_median - 0.5) / 1.0) * 100));
  $("rd-i").style.left = `${g}%`;
  $("rd").title = `번짐 지수 ${fmtRd(r.now.rd_median)} (범위 ${fmtRd(r.now.rd_q10)}~${fmtRd(r.now.rd_q90)}) · 번지는 중일 확률 ${Math.round(r.now.p_spread * 100)}%`;
  $("asof").textContent = asOfText(r);
  $("next").textContent = nextUpdateText(r);
}

// ---------- 화물판 ----------
function renderAxis() {
  const ticks = [];
  for (let t = 0; t <= HOURS; t += 2) {
    const h = (10 + t) % 24;
    const label = t === 0 ? `${h}시<small>지금</small>` : t === 14 ? `0시<small>내일</small>` : `${h}시`;
    ticks.push(`<span class="tick${t === 0 ? " tick-now" : ""}" style="left:${pos(t)}%">${label}</span>`);
  }
  $("axis").innerHTML = ticks.join("");
}

function rowAction(r, key, f, v) {
  const { cls, plan, choice, best, verb, c } = f;
  if (f.changed) return `${T(choice.time)} ${verb}${isH2(choice) ? "(수소)" : ""}로 변경 — 운송사에 배차 요청을 보내세요`;
  if (cls === "change") return v.action;
  if (cls === "notify") return "화주에게 미리 알리세요";
  if (cls === "hold-conservative") return `화주에게 알리고 ${nextUpdateText(r)}에 다시 확인하세요`;
  if (cls === "hold-uncertain") return `계산이 아직 불확실합니다 — ${nextUpdateText(r)}에 다시 확인하세요`;
  if (cls === "optional") return isH2(choice) ? `수소 트럭으로 바꿀 수 있습니다 (${T(choice.time)} ${verb})` : `${T(choice.time)} ${verb}로 바꿔도 됩니다`;
  return `계획대로 ${T(plan.time)} ${verb}`;
}
function rowTip(r, f) {
  const { c, cls, best } = f;
  if (f.changed) return "";
  if (cls === "change" && c.gate_closed) return `터미널 공지: ${T(c.gate_closed[0])}~${T(c.gate_closed[1])} 수출 반입 금지`;
  if (cls === "change") return `지금 계획이면 ${fmtP(f.plan.p_fail)} → 바꾸면 ${fmtP(f.choice.p_fail)}`;
  if (cls === "notify") {
    const why = c.ready && f.later ? `컨테이너가 ${clock10(c.ready.q50, false)}쯤 나와 어느 시각에 가도 위험이 비슷합니다` : `어느 시각에 가도 ${best.p_fail >= 0.5 ? "절반 넘게 놓칠 전망" : "위험이 큽니다"}`;
    return why;
  }
  if (cls === "hold-conservative") return "위험은 낮지만 계산이 충분히 확실하지 않습니다";
  if (cls === "optional" && isH2(f.choice) && c.h2_note) return "충전·복귀 제약을 반영한 시각입니다";
  return "";
}

function laneHTML(f, eps) {
  const { c, plan, choice, cls } = f;
  const parts = ['<div class="lane-grid"></div>', '<div class="lane-mid" title="자정(내일)"></div>', '<div class="lane-track"></div>'];
  if (c.gate_closed) {
    const [a, b] = c.gate_closed;
    parts.push(`<div class="m m-closed" style="left:${pos(a)}%;width:${pos(b) - pos(a)}%"><span>반입 금지</span></div>`);
  }
  const willRec = !f.changed && (cls === "change" || cls === "optional") && choice && !choice.is_plan;
  if (c.kind === "import" && c.ready) {
    const a = c.ready.q50, b = c.ready.q90;
    parts.push(`<div class="m m-ready" style="left:${pos(a)}%;width:${Math.max(0, pos(b) - pos(a))}%" title="컨테이너 반출 예상 ${clock10(a)} (늦으면 ${clock10(b)})">${willRec ? "" : `<span>반출 ${clock10(a, false)}</span>`}</div>`);
  }
  const shown = f.changed ? choice : plan;
  const bandOpt = cls === "change" || f.changed ? shown : (f.least || plan);
  const a50 = bandOpt.arrival_q50, a90 = bandOpt.arrival_q90;
  const cut = a90 > HOURS ? 1 : 0;
  const rl = riskLevel(bandOpt.p_fail, eps);
  parts.push(`<div class="m m-band" data-role="band" data-risk="${rl}" data-cut="${cut}" style="left:${pos(a50)}%;width:${Math.max(1.2, pos(a90) - pos(a50))}%" title="도착 예상 ${clock10(a50)} (늦으면 ${clock10(a90)})"></div>`);
  if (cut) parts.push('<span class="m-cut" aria-hidden="true">›</span>');
  const flip = c.deadline > HOURS - 3 ? 1 : 0;
  parts.push(`<div class="m m-dl" data-flip="${flip}" style="left:${pos(c.deadline)}%"><span>마감 ${T(c.deadline)}</span></div>`);
  const showRec = !f.changed && (cls === "change" || cls === "optional") && choice && !choice.is_plan;
  if (showRec) {
    const l = Math.min(pos(plan.time), pos(choice.time)), w = Math.abs(pos(plan.time) - pos(choice.time));
    parts.push(`<div class="m-arrow" data-role="arrow" data-dir="${choice.time < plan.time ? "left" : "right"}" style="left:${l}%;width:${w}%"></div>`);
    parts.push(`<div class="m m-rec" data-role="rec" style="left:${pos(choice.time)}%"></div>`);
    parts.push(`<span class="m-lbl m-lbl-rec" data-role="rec-l" style="left:${pos(choice.time)}%">추천<b>${T(choice.time)}</b></span>`);
  }
  parts.push(`<div class="m m-plan" data-role="plan" style="left:${pos(shown.time)}%"></div>`);
  parts.push(`<span class="m-lbl m-lbl-plan" data-role="plan-l" style="left:${pos(shown.time)}%">${f.changed ? "변경" : "계획"}<b>${T(shown.time)}</b></span>`);
  const axis = [0, 4, 8, 12, 16, 20].map((t) => `<span style="left:${pos(t)}%">${(10 + t) % 24}</span>`).join("");
  parts.push(`<div class="lane-axis" aria-hidden="true">${axis}</div>`);
  return parts.join("");
}

function decisionHTML(f) {
  const { cls, plan, choice, best, c } = f;
  const eps = c.epsilon;
  let risk, btns;
  if (f.changed) {
    const [v, u] = bigP(choice.p_fail);
    risk = `<span class="risk-from">${bigP(plan.p_fail).join("")}</span><span class="risk-v" data-risk="${riskLevel(choice.p_fail, eps)}">${v}<small>${u}</small></span>`;
    btns = `<button type="button" class="btn" data-act="driver">배차 요청</button><button type="button" class="btn-quiet undo" data-act="undo">되돌리기</button>`;
  } else if (cls === "change" || cls === "optional") {
    const p = cls === "change" ? plan.p_fail : plan.p_fail;
    const [v, u] = bigP(p);
    risk = cls === "change"
      ? `<span class="risk-v" data-risk="${riskLevel(p, eps)}">${v}<small>${u}</small></span>`
      : `<span class="risk-v" data-risk="${riskLevel(p, eps)}">${v}<small>${u}</small></span>`;
    const label = isH2(choice) ? "수소 트럭으로 변경" : `${josa(T(choice.time), ["으로", "로"])} 변경`;
    btns = `<button type="button" class="${cls === "change" ? "btn-primary" : "btn"}" data-act="apply">${label}</button>`;
    if (cls === "change") risk += `<span class="sr-only">변경하면 ${fmtP(choice.p_fail)}</span>`;
  } else if (cls === "notify" || cls === "hold-conservative") {
    const [v, u] = cls === "notify" ? bigP(best.p_fail) : [`${Math.round(best.p_fail * 100)}~${Math.round(best.p_upper * 100)}`, "%"];
    risk = `<span class="risk-v" data-risk="${cls === "notify" ? "high" : "mid"}">${v}<small>${u}</small></span>`;
    btns = `<button type="button" class="btn-primary" data-act="shipper">화주 안내문</button>`;
  } else if (cls === "hold-uncertain") {
    risk = `<span class="risk-v" data-risk="mid">?</span>`;
    btns = `<button type="button" class="btn" data-act="shipper">확인 중 안내</button>`;
  } else {
    const [v, u] = bigP(plan.p_fail);
    risk = `<span class="risk-v" data-risk="${riskLevel(plan.p_fail, eps)}">${v}<small>${u}</small></span>`;
    btns = `<button type="button" class="btn" data-act="driver">배차 요청</button>`;
  }
  return `<p class="risk"><span class="risk-l">마감 놓칠 확률</span>${risk}</p>${btns}`;
}

function renderBoard(r) {
  const keys = sortedCargo(r);
  if (!state.key || !keys.includes(state.key)) state.key = keys[0];
  $("n").textContent = keys.length;
  const cnt = { critical: 0, warning: 0, good: 0, hold: 0, done: 0 };
  const rows = keys.map((key) => {
    const v = cargoView(r, key, state.valid);
    const f = cargoFacts(r, key, !!state.applied[key]);
    let [lv, word] = PILL[f.cls];
    if (f.changed) { lv = "done"; word = "변경함"; }
    cnt[lv] += 1;
    const sel = key === state.key;
    const route = `${f.meta.origin} → ${f.meta.dest} · 마감 ${clock(f.c.deadline)}`;
    const cust = f.meta.customer || f.meta.shipper;
    const tip = rowTip(r, f);
    return `<li class="row grid3" data-key="${key}" data-cls="${f.cls}" aria-selected="${sel}" tabindex="0">
      <div class="row-info">
        <span class="pill" data-level="${lv}"><span class="shape" data-s="${SHAPE[lv] || "dia"}" aria-hidden="true"></span>${word}</span><span class="row-cust">화주 ${esc(cust)}</span>
        <h3 class="row-name">${esc(CARGO_SHORT[key] || f.c.label)}</h3>
        <p class="row-route">${esc(route)}</p>
        <p class="row-act">${esc(rowAction(r, key, f, v))}</p>
        ${tip ? `<p class="row-tip">${esc(tip)}</p>` : ""}
      </div>
      <div class="lane" aria-hidden="true">${laneHTML(f, f.c.epsilon)}</div>
      <div class="row-dec">${decisionHTML(f)}</div>
    </li>`;
  });
  $("rows").innerHTML = rows.join("");
  const C = [["critical", "안내 필요"], ["warning", "시간 변경"], ["done", "변경함"], ["hold", "보류"], ["good", "계획대로"]];
  $("counts").innerHTML = C.filter(([k]) => cnt[k]).map(([k, w]) => `<span class="count" data-level="${k === "done" ? "good" : k}">${w} ${cnt[k]}</span>`).join("");
}

// ---------- 브리핑 ----------
function renderBrief(r) {
  const kinds = DOC_KINDS;
  $("tabs").innerHTML = kinds.map((k) => `<button type="button" class="tab" role="tab" data-doc="${k.key}" aria-selected="${state.doc === k.key}">${k.label}<small>${k.scope === "day" ? "오늘 전체" : "선택 화물"}</small></button>`).join("");
  const kind = kinds.find((k) => k.key === state.doc);
  const c = r.cargo[state.key];
  $("brief-for").textContent = kind.scope === "day" ? "오늘 전체 화물" : `${CARGO_SHORT[state.key] || c.label}`;
  const doc = buildDoc(state.doc, r, state.key, {
    sender: state.sender, applied: state.applied,
    driverUrl: driverLink(linkBase(), r, state.key, !!state.applied[state.key]),
  });
  state.lastDoc = doc;
  state.edited = false;
  $("paper").innerHTML = docToHTML(doc);
  $("paper").scrollTop = 0;
}

function renderAll() {
  const r = run();
  renderRibbon(r);
  renderBoard(r);
  renderBrief(r);
  const u = new URL(location.href);
  u.searchParams.set("s", state.s);
  u.searchParams.set("c", state.key);
  u.searchParams.set("doc", state.doc);
  history.replaceState(null, "", u);
}

// ---------- 동작 ----------
function select(key, doc = null) {
  state.key = key;
  const cls = classifyCargo(run(), key).cls;
  const dayDoc = DOC_KINDS.find((k) => k.key === state.doc).scope === "day";
  state.doc = doc || (dayDoc ? DEFAULT_DOC[cls] : DEFAULT_DOC[cls]);
  renderBoard(run());
  renderBrief(run());
  history.replaceState(null, "", `?s=${state.s}&c=${key}&doc=${state.doc}`);
}

function apply(key) {
  const r = run();
  const f = cargoFacts(r, key, false);
  if (!f.choice || f.choice.is_plan) return;
  const row = document.querySelector(`.row[data-key="${key}"]`);
  const to = pos(f.choice.time);
  const band = row && row.querySelector('[data-role="band"]');
  // 계획 표시를 추천 시각으로 옮기고, 도착 띠를 바뀐 안의 것으로 바꾼 뒤 다시 그린다
  if (row) {
    for (const role of ["plan", "plan-l"]) {
      const el = row.querySelector(`[data-role="${role}"]`);
      if (el) el.style.left = `${to}%`;
    }
    for (const role of ["rec", "rec-l", "arrow"]) {
      const el = row.querySelector(`[data-role="${role}"]`);
      if (el) el.style.opacity = "0";
    }
    if (band) {
      band.style.left = `${pos(f.choice.arrival_q50)}%`;
      band.style.width = `${Math.max(1.2, pos(f.choice.arrival_q90) - pos(f.choice.arrival_q50))}%`;
      band.dataset.risk = riskLevel(f.choice.p_fail, f.c.epsilon);
    }
  }
  state.applied[key] = true;
  state.key = key;
  state.doc = "driver";
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  setTimeout(() => { renderBoard(run()); renderBrief(run()); }, reduce ? 0 : 950);
  toast(`${CARGO_SHORT[key]}: ${f.verb} 시각을 ${josa(T(f.choice.time), ["으로", "로"])} 바꿨습니다`);
}
function undo(key) {
  delete state.applied[key];
  renderBoard(run());
  renderBrief(run());
  toast("변경을 되돌렸습니다");
}

function docText() {
  return state.edited ? $("paper").innerText.trim() : docToText(state.lastDoc);
}
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    return ok;
  }
}

function setScenario(s) {
  state.s = s;
  state.applied = {};
  state.key = null;
  const r = run();
  const first = sortedCargo(r)[0];
  state.key = first;
  state.doc = DEFAULT_DOC[classifyCargo(r, first).cls];
  $("scn").value = s;
  renderAll();
}

// ---------- 근거 시트 ----------
function renderWhy(r) {
  const st = statusView(r);
  $("why-lead").textContent = `${SCENARIO_CHIP[r.meta.scenario] || r.meta.scenario_label}: ${r.meta.scenario_summary.split(". ")[0].replace(/\(.*?\)/g, "")}.`;
  const F = Object.fromEntries((r.now.factors || []).map((x) => [x.key, x]));
  const rows = [["now", "지금"], ["volume", "물량을 평소로(반입 분산)"], ["capacity", "처리능력 복구(장비·인력)"], ["beta", "날씨·장비 영향 제거"]];
  $("cf").innerHTML = rows.filter(([k]) => F[k]).map(([k, label]) => {
    const m = F[k].median;
    return `<div class="cf-row"><span>${label}</span><span class="cf-bar"><i data-over="${m >= 1 ? 1 : 0}" style="width:${Math.min(100, (m / 2) * 100)}%"></i><b style="left:50%"></b></span><span class="cf-v">${fmtRd(m)}</span></div>`;
  }).join("") + `<p style="font-size:12.5px;color:var(--muted)">검은 선 = 1(이보다 크면 번짐) · ${st.cause ? esc(st.cause.text) : "특이 원인 없음"}</p>`;
  // 번짐 지수 추이
  const W = 520, H = 150, x0 = 34, x1 = 510, y0 = 128, y1 = 10;
  const X = (t) => x0 + ((t + 24) / 48) * (x1 - x0);
  const Y = (v) => y0 - (Math.min(2, Math.max(0, v)) / 2) * (y0 - y1);
  const th = r.t_hist, tf = r.rd.fore_t;
  const band = (ts, lo, hi) => `M${ts.map((t, i) => `${X(t).toFixed(1)},${Y(hi[i]).toFixed(1)}`).join(" L")} L${ts.map((t, i) => `${X(t).toFixed(1)},${Y(lo[i]).toFixed(1)}`).reverse().join(" L")} Z`;
  const line = (ts, v) => `M${ts.map((t, i) => `${X(t).toFixed(1)},${Y(v[i]).toFixed(1)}`).join(" L")}`;
  const fq = r.rd.fore_q;
  const svg = [
    `<line x1="${x0}" x2="${x1}" y1="${Y(1)}" y2="${Y(1)}" stroke="currentColor" stroke-opacity=".55" stroke-dasharray="3 3"/>`,
    `<text x="${x0 - 6}" y="${Y(1) + 4}" font-size="11" text-anchor="end" fill="currentColor">1</text>`,
    `<text x="${x0 - 6}" y="${Y(2) + 8}" font-size="11" text-anchor="end" fill="currentColor">2</text>`,
    `<path d="${band(th, r.rd.filter_q10, r.rd.filter_q90)}" fill="var(--brand)" fill-opacity=".15"/>`,
    `<path d="${line(th, r.rd.filter_mean)}" fill="none" stroke="var(--brand)" stroke-width="2.2"/>`,
    `<path d="${band(tf, fq.map((q) => q[0]), fq.map((q) => q[2]))}" fill="var(--brand)" fill-opacity=".08"/>`,
    `<path d="${line(tf, fq.map((q) => q[1]))}" fill="none" stroke="var(--brand)" stroke-width="2.2" stroke-dasharray="5 4"/>`,
    `<line x1="${X(0)}" x2="${X(0)}" y1="${y1}" y2="${y0}" stroke="currentColor" stroke-opacity=".35"/>`,
    `<text x="${X(0) + 4}" y="${y1 + 10}" font-size="11" fill="currentColor">지금 10:00</text>`,
    `<text x="${x0}" y="${H - 4}" font-size="11" fill="currentColor">어제 10시</text>`,
    `<text x="${x1}" y="${H - 4}" font-size="11" text-anchor="end" fill="currentColor">내일 10시</text>`,
  ];
  $("spark").innerHTML = svg.join("");
  $("spark").style.color = "var(--muted)";
  const t = trustLines(state.valid, r);
  $("trust").innerHTML = t.good.map((x) => `<li class="trust-ok">${esc(x)}</li>`).join("") + t.limits.slice(0, 3).map((x) => `<li>${esc(x)}</li>`).join("");
  $("why-analysis").href = `analysis.html?s=${r.meta.scenario}&c=${state.key}`;
}

// ---------- 시작 ----------
function bind() {
  $("scn").addEventListener("change", (e) => setScenario(e.target.value));
  $("rows").addEventListener("click", (e) => {
    const row = e.target.closest(".row");
    if (!row) return;
    const key = row.dataset.key;
    const act = e.target.closest("[data-act]");
    if (!act) { select(key); return; }
    const a = act.dataset.act;
    if (a === "apply") apply(key);
    else if (a === "undo") undo(key);
    else if (a === "shipper" || a === "driver") {
      select(key, a);
      if (window.matchMedia("(max-width: 1240px)").matches) $("paper").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
  $("rows").addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("row")) { e.preventDefault(); select(e.target.dataset.key); }
  });
  $("tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-doc]");
    if (!b) return;
    state.doc = b.dataset.doc;
    renderBrief(run());
    history.replaceState(null, "", `?s=${state.s}&c=${state.key}&doc=${state.doc}`);
  });
  $("paper").addEventListener("input", () => { state.edited = true; });
  $("copy").addEventListener("click", async () => {
    const ok = await copyText(docText());
    toast(ok ? `${state.lastDoc.title.replace(/^\[[^\]]+\]\s*/, "")} — 복사했습니다` : "복사하지 못했습니다. 문서를 길게 눌러 직접 복사하세요");
  });
  $("share").addEventListener("click", async () => {
    const text = docText();
    if (navigator.share) {
      try { await navigator.share({ title: state.lastDoc.title, text }); return; } catch (err) { if (err && err.name === "AbortError") return; }
    }
    const ok = await copyText(text);
    toast(ok ? "이 브라우저는 공유를 지원하지 않아 복사했습니다. 카카오톡에 붙여 넣으세요" : "공유하지 못했습니다");
  });
  $("print").addEventListener("click", () => window.print());
  $("save").addEventListener("click", () => {
    const blob = new Blob([docText()], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    const kind = DOC_KINDS.find((k) => k.key === state.doc).label.replace(/\s/g, "");
    a.href = URL.createObjectURL(blob);
    a.download = `R-Delay_${kind}_${(state.lastDoc.short || "").replace(/[\\/:*?"<>|&\s]/g, "")}.txt`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
    toast("텍스트 파일로 저장했습니다");
  });
  $("why-btn").addEventListener("click", () => { renderWhy(run()); $("why").showModal(); });
  for (const b of document.querySelectorAll("[data-close]")) b.addEventListener("click", () => b.closest("dialog").close());
  $("why").addEventListener("click", (e) => { if (e.target === $("why")) $("why").close(); });
  $("sender-btn").addEventListener("click", () => {
    const f = $("sender-form");
    f.company.value = state.sender.company; f.name.value = state.sender.name; f.phone.value = state.sender.phone;
    $("sender").showModal();
  });
  $("sender").addEventListener("close", () => {
    if ($("sender").returnValue !== "ok") return;
    const f = $("sender-form");
    state.sender = { company: f.company.value.trim() || DEFAULT_SENDER.company, name: f.name.value.trim() || DEFAULT_SENDER.name, phone: f.phone.value.trim() };
    saveSender(state.sender);
    renderBrief(run());
    toast("보내는 사람을 바꿨습니다");
  });
  let deferred = null;
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferred = e; $("install").hidden = false; });
  $("install").addEventListener("click", async () => {
    if (!deferred) return;
    deferred.prompt();
    try { await deferred.userChoice; } catch { /* 무시 */ }
    deferred = null;
    $("install").hidden = true;
  });
}

async function main() {
  const q = new URLSearchParams(location.search);
  if (q.get("theme")) document.documentElement.dataset.theme = q.get("theme");
  const [pre, valid] = await Promise.all([
    fetch("data/precomputed.json").then((r) => r.json()),
    fetch("data/validation.json").then((r) => r.json()).catch(() => null),
  ]);
  state.pre = pre;
  state.valid = valid;
  const sel = $("scn");
  sel.innerHTML = ORDER.filter((s) => pre.runs[s]).map((s) => `<option value="${s}">${SCENARIO_CHIP[s]}</option>`).join("");
  renderAxis();
  bind();
  const s = q.get("s");
  state.s = s && pre.runs[s] ? s : "terminal_failure";
  sel.value = state.s;
  const r = run();
  const c = q.get("c");
  state.key = c && r.cargo[c] ? c : sortedCargo(r)[0];
  for (const k of (q.get("apply") || "").split(",").filter(Boolean)) if (r.cargo[k]) state.applied[k] = true;
  const d = q.get("doc");
  state.doc = d && DOC_KINDS.some((k) => k.key === d) ? d : DEFAULT_DOC[classifyCargo(r, state.key).cls];
  renderAll();
  document.documentElement.dataset.ready = "1";
  // 시연 녹화용 손잡이(화면 동작을 스크립트로 재현할 때만 쓴다)
  window.__rdelay = { select, apply, undo, setScenario, setDoc: (k) => { state.doc = k; renderBrief(run()); }, openWhy: () => { renderWhy(run()); $("why").showModal(); }, closeWhy: () => $("why").close() };
  if ("serviceWorker" in navigator && /^https?:$/.test(location.protocol)) navigator.serviceWorker.register("sw.js").catch(() => {});
}
main().catch((err) => {
  const p = document.createElement("p");
  p.style.cssText = "padding:16px;color:#a51f1f;font-weight:700";
  p.textContent = `화면을 불러오지 못했습니다(${err.message}). 새로 고침해 보세요.`;
  document.body.prepend(p);
});
