// 상황별 브리핑 초안(중소 포워더용): 엔진 결과(run) → 화주 안내 · 운송사 배차 요청 · 사내 보고 · 선사·터미널 협의 문서.
// DOM을 건드리지 않는 순수 함수다(tests/briefing.test.mjs로 확인). 문장 안의 숫자는 모두 엔진 출력에서 온다.
// 판단 규칙(분류·놓칠 확률·추천 시각)은 derive.js를 그대로 쓰고, 여기서는 받는 사람에 맞춰 말투와 순서만 바꾼다.
import {
  classifyCargo, verbOf, statusView, sortedCargo, sameIfEarly, nodeState, STATE_WORD, isHold, driverAdvice,
} from "./derive.js?v=20261109a";
import {
  NODE_NAME, SCENARIO_CHIP, CARGO_SHORT, clock, clock10, clock10ceil, fmtDur, fmtP, fmtWon, fmtRd, fmtCap, nextUpdateText,
} from "./format.js?v=20261109a";

export const DOC_KINDS = [
  { key: "shipper", label: "화주 안내", scope: "cargo" },
  { key: "driver", label: "배차 요청", scope: "cargo" },
  { key: "internal", label: "사내 보고", scope: "day" },
  { key: "port", label: "선사·터미널 협의", scope: "day" },
];

// 시연용 화물 정보(가상). 포워더가 맡은 고객(화주) 화물 4건이다. 컨테이너 번호의 RDLU는 R-Delay 시연용 접두어다.
// customer는 화물판에 쓰는 짧은 화주 이름, we는 포워더가 그 화물을 위해 하는 일(있을 때만)이다.
export const CARGO_META = {
  import_factory: {
    customer: "양산 ○○산업", shipper: "양산 ○○산업 생산관리팀", container: "RDLU 100001-1", origin: "부산신항", dest: "양산 공장",
    work: "신항 수입 컨테이너 반출 → 양산 공장 야간 라인", ask: "야간 라인 투입 순서를 바꾸거나 대체 원자재를 쓸 수 있는지 검토해 주십시오.",
  },
  ulsan_parts: {
    customer: "울산 ○○오토", shipper: "울산 ○○오토 부품물류팀", container: "RDLU 100002-7", origin: "부산신항", dest: "울산 완성차 공장",
    work: "신항 수입 컨테이너 반출 → 울산 완성차 라인", ask: "라인 투입 순서 조정이나 안전재고 투입을 검토해 주십시오.",
  },
  sea_air: {
    customer: "○○전자", shipper: "○○전자 해외물류팀", container: "RDLU 100003-2", origin: "부산신항", dest: "인천공항 화물터미널",
    work: "신항 환적 컨테이너 반출 → 인천공항 화물터미널 반입", ask: "다음 항공편으로 넘어가도 최종 도착 일정에 문제가 없는지 알려 주십시오.",
    we: "다음 항공편 예약 가능 여부를 항공사와 미리 확인해 두겠습니다.",
  },
  ulsan_export: {
    customer: "울산 ○○정공", shipper: "울산 ○○정공 수출팀", container: "RDLU 100004-8", origin: "울산 공장", dest: "부산신항",
    work: "울산 공장 상차 → 신항 수출 반입", ask: null,
  },
};

export const DEFAULT_SENDER = { company: "○○포워딩", name: "수출입 담당 ○○○", phone: "010-0000-0000" };
export const DEMO_TAG = "시연용 가상 데이터로 만든 초안입니다. 보내기 전에 내용을 확인하세요.";

const T = (t) => clock(t, false);
// 조사: 마지막 글자의 받침으로 고른다(끝의 괄호 설명은 떼고 본다). 「으로/로」는 ㄹ 받침이면 「로」.
export function josa(word, pair) {
  const base = String(word).replace(/\s*\([^()]*\)\s*$/, "");
  const ch = base.charAt(base.length - 1);
  let jong;
  if (/[가-힣]/.test(ch)) jong = (ch.charCodeAt(0) - 0xac00) % 28;
  else if (/[0-9]/.test(ch)) jong = "2459".includes(ch) ? 0 : ch === "1" || ch === "7" || ch === "8" ? 8 : 1; // 일·칠·팔은 ㄹ 받침
  else jong = ch === "%" ? 0 : 0;
  if (pair[0] === "으로") return word + (jong === 0 || jong === 8 ? "로" : "으로");
  return word + (jong ? pair[0] : pair[1]);
}
// 「약 19%」, 99.5% 이상은 「거의 확실」, 1% 미만은 「1% 미만」
const about = (p) => (p >= 0.995 || p < 0.01 ? fmtP(p) : `약 ${fmtP(p)}`);
const isH2 = (cand) => !!cand && cand.vehicle === "수소";
const stripPlan = (label) => label.replace(" (현행 계획)", "");

// 승인 필요한 「우선 양하 요청」 안 중 가장 덜 위험한 것(수입 화물만)
function priorityOption(c) {
  const xs = c.candidates.filter((x) => x.negotiation && x.p_if_approved != null);
  if (!xs.length) return null;
  return xs.reduce((a, b) => (b.p_if_approved < a.p_if_approved ? b : a));
}

// 같은 위험으로 더 늦게 가도 되는 가장 늦은 시각(수입 화물: 컨테이너가 늦게 나오면 일찍 가 봐야 기다리기만 한다)
function laterSame(c, best) {
  if (!best || c.kind !== "import") return null;
  const xs = c.candidates.filter((x) => !x.negotiation && !x.priority && x.feasible !== false && !(x.reasons || []).length &&
    x.vehicle === best.vehicle && x.time > best.time + 1e-9 && Math.abs(x.p_fail - best.p_fail) < 0.01);
  if (!xs.length) return null;
  const x = xs.reduce((a, b) => (b.time > a.time ? b : a));
  const saved = best.exp_turnaround_h - x.exp_turnaround_h;
  return saved >= 0.25 ? { cand: x, saved } : null;
}

// 지금 실행할 안: 변경을 적용했으면 추천안, 아니면 현행 계획
export function currentOption(run, key, applied = false) {
  const k = classifyCargo(run, key);
  if (applied && k.choice && !k.choice.is_plan) return k.choice;
  return k.plan;
}

// 화물 한 건의 판단을 문서들이 함께 쓰는 사실로 정리한다
export function cargoFacts(run, key, applied = false) {
  const k = classifyCargo(run, key);
  const { cls, cargo: c, plan, choice, least } = k;
  const cur = currentOption(run, key, applied);
  const best = least || choice || plan;
  const verb = verbOf(c);
  const changed = applied && choice && !choice.is_plan;
  return {
    key, cls, c, plan, choice, least, cur, best, verb, changed,
    meta: CARGO_META[key] || { customer: "화주", shipper: "화주 담당자", container: "-", origin: "", dest: "", work: c.label, ask: null },
    short: CARGO_SHORT[key] || c.label,
    pr: priorityOption(c),
    later: laterSame(c, least || choice || plan),
    // 문서에 쓸 위험: 변경을 적용했으면 그 안, 안내가 필요하면 가장 나은 안
    risk: changed ? choice.p_fail : (cls === "change" ? plan.p_fail : best.p_fail),
    riskUpper: changed ? choice.p_upper : best.p_upper,
  };
}

// 왜 늦어지는지: 엔진 원인 판정 + 운영 공지에서 온 사건 이름
const EVENT = {
  terminal_failure: "시스템 오류",
  typhoon_diversion: "북중국 태풍 우회 선박 집중",
  holiday_surge: "명절 전 반출입 물량 집중",
  epidemic: "항만·물류 인력 결근(출근율 75%)",
};
export function causePhrase(run) {
  const now = run.now;
  const ev = EVENT[run.meta.scenario];
  const cap = now.capacity;
  const minK = cap.indexOf(Math.min(...cap));
  if (typeof now.cause === "string" && now.cause.startsWith("처리능력형")) {
    const where = NODE_NAME[run.nodes[minK].id] || run.nodes[minK].name;
    return cap[minK] < 0.999 ? `${where} ${ev ? `${josa(ev, ["으로", "로"])} ` : ""}처리능력이 ${fmtCap(cap[minK])}로 떨어져` : josa(ev || "처리 지연", ["으로", "로"]);
  }
  if (typeof now.cause === "string" && now.cause.startsWith("물량 주도형")) {
    const xi = Math.max(...now.xi);
    return `${ev ? `${josa(ev, ["으로", "로"])} ` : ""}부산신항 물동량이 평소의 ${xi.toFixed(1)}배로 늘어`;
  }
  const jam = now.congested;
  if (jam.length) {
    const k = run.nodes.findIndex((n) => n.short === jam[0]);
    const where = k >= 0 ? (NODE_NAME[run.nodes[k].id] || run.nodes[k].name) : jam[0];
    return `${ev ? `${josa(ev, ["으로", "로"])} ` : ""}${josa(where, ["이", "가"])} 혼잡해`;
  }
  if (now.p_spread >= 0.35) return `${ev ? `${josa(ev, ["으로", "로"])} ` : ""}부산신항 일대에 지연이 번질 조짐이 있어`;
  return josa(ev || "운영 지연", ["으로", "로"]);
}

const pText = (f) => fmtP(f.risk);
const span = (lo, hi) => {
  const a = fmtP(lo), b = fmtP(hi);
  return a === b ? `약 ${a}` : lo < 0.01 ? `${b} 이하` : `약 ${a}~${b}`;
};

function senderLine(sender) {
  return `${sender.company} ${sender.name}${sender.phone ? ` (${sender.phone})` : ""}`;
}
function asOf(run) {
  const m = /(\d{1,2}:\d{2})/.exec(run.meta.now_label || "");
  return `오늘 ${m ? m[1] : "10:00"} 기준`;
}

// ---------- 1. 화주 안내 ----------
export function shipperDoc(run, key, opts = {}) {
  const sender = { ...DEFAULT_SENDER, ...(opts.sender || {}) };
  const f = cargoFacts(run, key, !!opts.applied);
  const { c, cls, plan, choice, best, verb, meta } = f;
  const to = opts.shipper || meta.shipper;
  const blocks = [];
  let title, kind;
  const hello = `${to} 담당자님, ${sender.company} ${sender.name}입니다.`;
  if (cls === "notify" || cls === "hold-conservative") {
    kind = cls === "notify" ? "사전 안내" : "확인 중 안내";
    title = `[${kind}] ${f.short} 마감 지연 가능성`;
    blocks.push({ p: hello });
    blocks.push({ p: `${causePhrase(run)} ${c.label} 화물이 ${josa(c.deadline_label, ["을", "를"])} 맞추지 못할 수 있어 미리 알려 드립니다.` });
    const kv = [];
    if (c.kind === "import" && c.ready) kv.push(["컨테이너 반출 예상", `${clock10(c.ready.q50)} (늦으면 ${clock10ceil(c.ready.q90)})`]);
    kv.push(["최종 도착 예상", `${clock10(best.arrival_q50)} 전후 (늦으면 ${clock10ceil(best.arrival_q90)})`]);
    kv.push(["마감", c.deadline_label]);
    kv.push(["마감을 놓칠 가능성", cls === "notify" ? about(best.p_fail) : span(best.p_fail, best.p_upper)]);
    blocks.push({ kv });
    const acts = [];
    if (sameIfEarly(run, key)) acts.push(`컨테이너가 나오는 대로 바로 싣도록 협력 운송사에 ${josa(verb === "도착" ? `터미널 ${T(best.time)} 도착` : `${T(best.time)} 출발`, ["으로", "로"])} 차량을 요청해 두었습니다.`);
    else acts.push(`가장 덜 위험한 ${josa(verb === "도착" ? `터미널 ${T(best.time)} 도착` : `${T(best.time)} 출발`, ["으로", "로"])} 협력 운송사 차량을 요청해 두었습니다.`);
    if (f.pr) acts.push(`선사·터미널에 우선 하역을 요청하겠습니다. 받아들여지면 놓칠 가능성이 ${josa(about(f.pr.p_if_approved), ["으로", "로"])} 낮아집니다.`);
    if (meta.we) acts.push(meta.we);
    blocks.push({ h: "저희가 하는 일", list: acts });
    if (meta.ask) blocks.push({ h: "검토 부탁드립니다", p: meta.ask });
    blocks.push({ p: `${nextUpdateText(run)}에 상황을 다시 계산해 바뀌면 바로 연락드리겠습니다.` });
  } else if (cls === "change") {
    const dir = choice.time < plan.time ? "앞당깁니다" : "늦춥니다";
    kind = "일정 변경 안내";
    title = `[${kind}] ${f.short} ${verb} ${T(plan.time)} → ${T(choice.time)}`;
    blocks.push({ p: hello });
    const why = c.gate_closed
      ? `터미널이 ${T(c.gate_closed[0])}~${T(c.gate_closed[1])} 수출 반입을 막는다고 공지해`
      : `${causePhrase(run)}`;
    blocks.push({ p: `${why} ${josa(c.deadline_label, ["을", "를"])} 지키기 위해 ${verb} 시각을 ${T(plan.time)}에서 ${josa(T(choice.time), ["으로", "로"])} ${dir}.` });
    const kv = [
      ["바뀐 " + verb + " 시각", `${T(choice.time)} (기존 ${T(plan.time)})`],
      [c.kind === "export" ? "반입 완료 예상" : "최종 도착 예상", `${clock10(choice.arrival_q50)} 전후 (늦으면 ${clock10ceil(choice.arrival_q90)})`],
      ["마감을 놓칠 가능성", `${fmtP(plan.p_fail)} → ${fmtP(choice.p_fail)}`],
    ];
    const extra = (choice.change_cost || 0) - (plan.change_cost || 0);
    if (extra > 0) kv.push(["추가 운송비(예상)", fmtWon(extra)]);
    blocks.push({ kv });
    if (c.kind === "export") {
      blocks.push({ h: "준비 부탁드립니다", p: `컨테이너 적입과 수출 서류 준비를 ${T(choice.time - 0.5)}까지 마쳐 주십시오. 운송사 차량은 ${T(choice.time)}에 출발합니다(배차 요청은 저희가 바꿔 두었습니다).` });
    } else {
      blocks.push({ h: "준비 부탁드립니다", p: `${T(choice.time)} 도착에 맞춰 하차 인력을 준비해 주십시오.` });
    }
    blocks.push({ p: `${nextUpdateText(run)}에 다시 확인하고 바뀌면 바로 알려 드리겠습니다.` });
  } else if (cls === "hold-uncertain") {
    kind = "확인 중 안내";
    title = `[${kind}] ${f.short} 일정 재확인 예정`;
    blocks.push({ p: hello });
    blocks.push({ p: `${causePhrase(run)} ${c.label} 화물의 일정 계산이 아직 불확실합니다. 지금 계획(${T(plan.time)} ${verb})은 유지하고 ${nextUpdateText(run)}에 다시 확인해 알려 드리겠습니다.` });
    blocks.push({ kv: [["마감", c.deadline_label], ["놓칠 가능성(지금 계산)", span(best.p_fail, best.p_upper)]] });
  } else {
    kind = "정상 진행";
    const use = f.cur;
    title = `[${kind}] ${f.short} 계획대로 진행`;
    blocks.push({ p: hello });
    const changedNote = f.changed ? `(${verb} 시각을 ${T(plan.time)}에서 ${josa(T(use.time), ["으로", "로"])} 바꿨습니다) ` : "";
    blocks.push({ p: `${c.label} 화물은 ${changedNote}${josa(`${T(use.time)} ${verb}`, ["으로", "로"])}${isH2(use) ? "(수소 트럭)" : ""} ${c.deadline_label} 안에 마칠 전망입니다.` });
    blocks.push({ kv: [
      ["최종 도착 예상", `${clock10(use.arrival_q50)} 전후 (늦으면 ${clock10ceil(use.arrival_q90)})`],
      ["마감을 놓칠 가능성", fmtP(use.p_fail)],
    ] });
    blocks.push({ p: "상황이 바뀌면 바로 알려 드리겠습니다." });
  }
  return {
    kind: "shipper", title, meta: [["받는 사람", to], ["보내는 사람", senderLine(sender)], ["기준", asOf(run)]],
    blocks, sign: `${sender.company} ${sender.name} 드림`, footer: DEMO_TAG, cls, short: f.short,
  };
}

// ---------- 2. 운송사 배차 요청(기사님 화면 링크 포함) ----------
export function driverDoc(run, key, opts = {}) {
  const sender = { ...DEFAULT_SENDER, ...(opts.sender || {}) };
  const f = cargoFacts(run, key, !!opts.applied);
  const { c, plan, cur, verb, meta } = f;
  const blocks = [];
  blocks.push({ p: f.changed ? `아래 화물의 ${verb} 시각이 바뀌어 배차를 다시 요청드립니다.` : "아래 화물의 배차를 요청드립니다." });
  const kv = [["화물", `${c.label}`], ["화주", meta.customer || meta.shipper], ["컨테이너", meta.container], ["작업", meta.work]];
  if (f.changed) kv.push(["시간 변경", `${verb} ${T(plan.time)} → ${T(cur.time)}`]);
  else if (f.cls === "change" && f.choice) kv.push(["변경 검토 중", `${verb} ${T(f.choice.time)} 권장 — 배차 확정 전입니다`]);
  if (c.kind === "export") {
    kv.push(["출발", `${T(cur.time)}${isH2(cur) ? " · 수소 트럭" : ""}`]);
    kv.push(["반입 완료 예상", `${clock10(cur.arrival_q50)} (늦으면 ${clock10ceil(cur.arrival_q90)})`]);
  } else {
    kv.push(["터미널 도착", `${T(cur.time)}${isH2(cur) ? " · 수소 트럭" : ""}`]);
    if (f.later && Math.abs(cur.time - f.best.time) < 1e-9) kv.push(["더 늦게 가도 됨", `${T(f.later.cand.time)} 도착도 위험이 같고 대기가 약 ${fmtDur(f.later.saved)} 줄어듭니다`]);
    if (c.ready) kv.push(["컨테이너 반출 예상", `${clock10(c.ready.q50)} (늦으면 ${clock10ceil(c.ready.q90)})`]);
    kv.push(["터미널에서 걸리는 시간", `약 ${fmtDur(cur.exp_turnaround_h)}`]);
  }
  kv.push(["마감", c.deadline_label]);
  blocks.push({ kv });
  const warn = [];
  if (c.gate_closed) warn.push(`${T(c.gate_closed[0])}~${T(c.gate_closed[1])} 수출 반입 금지(터미널 공지) — ${T(c.gate_closed[0])} 전에 반입해야 합니다.`);
  if (c.kind === "import" && c.ready && cur.time + 0.25 < c.ready.q50) warn.push(`컨테이너가 ${clock10(c.ready.q50)}쯤 나옵니다. 그 전에 도착하면 기다려야 합니다.`);
  const L = run.nodes.findIndex((n) => n.id === "L");
  if (L >= 0 && nodeState(run, L) !== "ok") warn.push(`게이트·도로(양산·울산) ${STATE_WORD[nodeState(run, L)]} — 출발 전에 길 상황을 확인하세요.`);
  const jam = run.now.congested.filter((s) => s !== "육상");
  if (jam.length && c.kind === "import") warn.push("터미널 혼잡 — 기사님이 도착하면 도착 시각을 저희에게도 알려 주세요.");
  if (isH2(cur) && c.h2_note) warn.push(c.h2_note);
  if (warn.length) blocks.push({ h: "주의", list: warn });
  const url = opts.driverUrl ? opts.driverUrl : null;
  if (url) blocks.push({ h: "기사님께 전달해 주세요", p: `도착 시각별 터미널 대기 시간 화면: ${url}` });
  return {
    kind: "driver", title: `[배차 요청] ${f.short}${f.changed ? " · 시간 변경" : ""}`,
    meta: [["받는 사람", opts.trucker || "협력 운송사 배차 담당자"], ["보내는 사람", senderLine(sender)], ["기준", asOf(run)]],
    blocks, sign: `문의: ${sender.company} ${sender.name} ${sender.phone}`, footer: DEMO_TAG, cls: f.cls, short: f.short,
  };
}

// ---------- 3. 사내 보고(하루 전체) ----------
const ACTION_WORD = {
  change: "시간 변경", notify: "화주 사전 안내", "hold-conservative": "안내 후 재확인", "hold-uncertain": "판단 보류",
  optional: "계획대로(변경 가능)", ok: "계획대로",
};
export function internalDoc(run, opts = {}) {
  const sender = { ...DEFAULT_SENDER, ...(opts.sender || {}) };
  const applied = opts.applied || {};
  const st = statusView(run);
  const keys = sortedCargo(run);
  const blocks = [];
  blocks.push({ h: "1. 지금 상황", kv: [
    ["상황", st.title],
    ["번짐 지수", `${fmtRd(run.now.rd_median)} (범위 ${fmtRd(run.now.rd_q10)}~${fmtRd(run.now.rd_q90)}) · 번지는 중일 확률 ${Math.round(run.now.p_spread * 100)}%`],
    ...(st.cause ? [[st.cause.label, st.cause.text]] : []),
    ["전망", st.outlook],
  ] });
  const rows = keys.map((key) => {
    const f = cargoFacts(run, key, !!applied[key]);
    const after = f.changed ? fmtP(f.choice.p_fail) : f.cls === "change" ? `${fmtP(f.choice.p_fail)}(변경 시)` : "-";
    const judge = f.cls === "optional" && f.changed ? "변경함" : ACTION_WORD[f.cls];
    const act = f.cls === "change"
      ? `${f.verb} ${T(f.plan.time)} → ${T(f.choice.time)}${isH2(f.choice) ? " 수소" : ""}${f.changed ? " (적용함)" : " (적용 필요)"}`
      : f.cls === "optional" && f.changed ? `${f.verb} ${T(f.plan.time)} → ${T(f.choice.time)}${isH2(f.choice) ? " 수소" : ""} (적용함)`
      : f.cls === "notify" || f.cls === "hold-conservative" ? `화주 안내 · ${f.verb} ${T(f.best.time)} 유지${f.pr ? " · 우선 하역 요청" : ""}`
      : `${f.verb} ${T(f.cur.time)}${isH2(f.cur) ? " 수소" : ""}`;
    return [f.short, f.c.deadline_label.replace(/ (야간 라인 투입|라인 투입|항공 반입 마감|선적 반입 마감)$/, ""), judge, act, `${fmtP(f.cls === "change" ? f.plan.p_fail : f.best.p_fail)}`, after];
  });
  blocks.push({ h: "2. 오늘 관리 화물 조치", table: { head: ["화물", "마감", "판단", "조치", "놓칠 확률", "조치 후"], rows } });
  const saved = keys.map((key) => {
    const f = cargoFacts(run, key, true);
    return f.cls === "change" && f.choice ? f.plan.exp_cost - f.choice.exp_cost : 0;
  }).reduce((a, b) => a + b, 0);
  const notify = keys.filter((key) => ["notify", "hold-conservative"].includes(classifyCargo(run, key).cls));
  const money = [];
  if (saved > 10000) money.push(`시간 변경으로 줄어드는 예상 손실: ${fmtWon(saved)} (마감 실패 비용 가정 포함 모형 추정)`);
  if (notify.length) {
    const ahead = Math.round(Math.min(...notify.map((key) => run.cargo[key].deadline)));
    money.push(`화주 사전 안내 ${notify.length}건 — 마감 ${ahead}시간 전에 알려 대체 수단을 준비할 시간을 확보`);
  }
  if (!money.length) money.push("오늘은 추가 비용이 생길 화물이 없습니다.");
  blocks.push({ h: "3. 비용·영향", list: money });
  blocks.push({ h: "4. 다음 확인", p: `${nextUpdateText(run)} 관측이 들어오면 다시 계산합니다. 터미널 공지가 바뀌면 즉시 다시 봅니다.` });
  return {
    kind: "internal", title: `신항 지연 브리핑 — ${asOf(run)}`,
    meta: [["받는 사람", opts.boss || "운영팀장·대표"], ["작성", senderLine(sender)], ["상황", SCENARIO_CHIP[run.meta.scenario] || run.meta.scenario_label]],
    blocks, sign: `${sender.company} ${sender.name}`, footer: `R-Delay 모형 추정 · ${DEMO_TAG}`, cls: null, short: "오늘 전체",
  };
}

// ---------- 4. 선사·터미널 협의(선사·터미널 운영사·항만공사) ----------
export function portDoc(run, opts = {}) {
  const sender = { ...DEFAULT_SENDER, ...(opts.sender || {}) };
  const now = run.now;
  const st = statusView(run);
  const F = Object.fromEntries((now.factors || []).map((x) => [x.key, x]));
  const blocks = [];
  blocks.push({ p: `${josa(sender.company, ["은", "는"])} 고객 화물의 부산신항 반출입 일정을 R-Delay로 관리하는 포워더입니다. ${asOf(run)} 계산 결과를 공유드리며, 대응 우선순위 협의를 요청드립니다.` });
  blocks.push({ h: "1. 지연 번짐 현황", kv: [
    ["경보", `${now.alarm}${isHold(run) ? " (판단 경계)" : ""}`],
    ["번짐 지수", `${fmtRd(now.rd_median)} — 지연 1건이 풀리기 전에 새 지연을 평균 ${fmtRd(now.rd_median)}건 만듭니다(1보다 크면 번짐)`],
    ["막힌 곳", now.congested.length ? now.congested.map((s) => { const k = run.nodes.findIndex((n) => n.short === s); return k >= 0 ? (NODE_NAME[run.nodes[k].id] || s) : s; }).join(", ") : "없음"],
    ["전망", st.outlook],
  ] });
  if (F.now && F.volume && F.capacity && F.beta) {
    const rows = [
      ["지금", fmtRd(F.now.median), `${Math.round(F.now.p_gt1 * 100)}%`],
      ["물량을 평소로 되돌리면(반입 분산)", fmtRd(F.volume.median), `${Math.round(F.volume.p_gt1 * 100)}%`],
      ["처리능력을 평소로 되돌리면(장비·인력 보강)", fmtRd(F.capacity.median), `${Math.round(F.capacity.p_gt1 * 100)}%`],
      ["날씨·장비 영향을 빼면", fmtRd(F.beta.median), `${Math.round(F.beta.p_gt1 * 100)}%`],
    ];
    blocks.push({ h: "2. 어떤 대응이 번짐을 멈추나(모형 반사실)", table: { head: ["가정", "번짐 지수", "번질 확률"], rows } });
    const best = [["물량 분산", F.volume], ["처리능력 복구", F.capacity], ["날씨·장비 영향 제거", F.beta]].sort((a, b) => a[1].median - b[1].median)[0];
    const lead = best[1].median < 1 && F.now.median >= 1
      ? `${best[0]}만으로 번짐 지수가 1 아래(${fmtRd(best[1].median)})로 내려갑니다. 이 대응을 먼저 검토해 주시기 바랍니다.`
      : F.now.median < 1
        ? (now.congested.length ? "번짐 지수는 1 아래지만 막힌 곳이 있습니다. 막힌 곳의 적체 해소가 우선입니다." : "번짐 지수가 1 아래이고 막힌 곳이 없습니다. 평소 운영을 유지하면 됩니다.")
        : "한 가지 대응만으로는 번짐이 멈추지 않습니다. 여러 대응을 함께 검토해야 합니다.";
    blocks.push({ p: lead });
  }
  blocks.push({ h: "3. 고객 화물 영향", list: sortedCargo(run).map((key) => {
    const f = cargoFacts(run, key, false);
    if (f.cls === "change") return `${f.short}: 시간 변경 필요 — 현행 계획이면 놓칠 확률 ${fmtP(f.plan.p_fail)}, 바꾸면 ${fmtP(f.choice.p_fail)}`;
    return `${f.short}: ${ACTION_WORD[f.cls]} — 놓칠 확률 ${fmtP(f.best.p_fail)}`;
  }) });
  // 요청 사항: 우선 하역이 받아들여지면 위험이 줄어드는 수입 화물, 반입 제한 공지에 맞춰 시각을 바꾼 수출 화물
  const asks = sortedCargo(run).map((key) => {
    const f = cargoFacts(run, key, false);
    if (f.pr && ["notify", "hold-conservative", "change"].includes(f.cls)) {
      const base = f.cls === "change" ? f.plan.p_fail : f.best.p_fail;
      return `${f.short}(${f.meta.container}): 우선 하역 요청 — 받아들여지면 놓칠 확률 ${fmtP(base)} → ${fmtP(f.pr.p_if_approved)}`;
    }
    if (f.c.kind === "export" && f.c.gate_closed && f.cls === "change" && f.choice) {
      return `${f.short}(${f.meta.container}): 반입 제한(${T(f.c.gate_closed[0])}~${T(f.c.gate_closed[1])}) 전에 들어가도록 ${josa(`${T(f.choice.time)} 출발`, ["으로", "로"])} 바꿉니다. 제한 시간이 바뀌면 바로 알려 주십시오.`;
    }
    return null;
  }).filter(Boolean);
  if (asks.length) blocks.push({ h: "4. 요청드립니다", list: asks });
  blocks.push({ note: "원인 판정은 모형상 배분이며 책임 소재 판단에 쓰지 않습니다." });
  return {
    kind: "port", title: `부산신항 지연 번짐 공유 — ${asOf(run)}`,
    meta: [["받는 사람", opts.port || "선사·터미널 운영사·부산항만공사 담당자"], ["보내는 사람", senderLine(sender)], ["상황", SCENARIO_CHIP[run.meta.scenario] || run.meta.scenario_label]],
    blocks, sign: `${sender.company} ${sender.name}`, footer: `R-Delay 모형 추정 · ${DEMO_TAG}`, cls: null, short: "오늘 전체",
  };
}

export function buildDoc(kind, run, key, opts = {}) {
  if (kind === "shipper") return shipperDoc(run, key, { ...opts, applied: !!(opts.applied || {})[key] });
  if (kind === "driver") return driverDoc(run, key, { ...opts, applied: !!(opts.applied || {})[key] });
  if (kind === "internal") return internalDoc(run, opts);
  return portDoc(run, opts);
}

// ---------- 출력 형식 ----------
export function docToText(doc) {
  const out = [doc.title, ""];
  for (const [k, v] of doc.meta) out.push(`${k}: ${v}`);
  out.push("");
  for (const b of doc.blocks) {
    if (b.h) out.push(`■ ${b.h}`);
    if (b.p) out.push(b.p);
    if (b.kv) for (const [k, v] of b.kv) out.push(`- ${k}: ${v}`);
    if (b.list) for (const x of b.list) out.push(`• ${x}`);
    if (b.table) {
      out.push(b.table.head.join(" | "));
      for (const r of b.table.rows) out.push(r.join(" | "));
    }
    if (b.note) out.push(`※ ${b.note}`);
    out.push("");
  }
  out.push(doc.sign);
  out.push("");
  out.push(`※ ${doc.footer}`);
  return out.join("\n").replace(/\n{3,}/g, "\n\n");
}

const esc = (s) => String(s).replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
export function docToHTML(doc) {
  const parts = [`<h3 class="doc-title">${esc(doc.title)}</h3>`, `<dl class="doc-meta">`];
  for (const [k, v] of doc.meta) parts.push(`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`);
  parts.push(`</dl>`);
  for (const b of doc.blocks) {
    parts.push(`<section class="doc-block">`);
    if (b.h) parts.push(`<h4>${esc(b.h)}</h4>`);
    if (b.p) parts.push(`<p>${esc(b.p)}</p>`);
    if (b.kv) parts.push(`<dl class="doc-kv">${b.kv.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`);
    if (b.list) parts.push(`<ul>${b.list.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`);
    if (b.table) {
      parts.push(`<table class="doc-table"><thead><tr>${b.table.head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>`);
      for (const r of b.table.rows) parts.push(`<tr>${r.map((x) => `<td>${esc(x)}</td>`).join("")}</tr>`);
      parts.push(`</tbody></table>`);
    }
    if (b.note) parts.push(`<p class="doc-note">※ ${esc(b.note)}</p>`);
    parts.push(`</section>`);
  }
  parts.push(`<p class="doc-sign">${esc(doc.sign)}</p>`, `<p class="doc-foot">※ ${esc(doc.footer)}</p>`);
  return parts.join("");
}

// 기사님 휴대폰 화면 주소(포워더가 배차 요청에 넣어 운송사를 거쳐 기사님께 가는 링크)
export function driverLink(base, run, key, applied = false) {
  const u = new URL("driver.html", base);
  u.searchParams.set("s", run.meta.scenario);
  u.searchParams.set("c", key);
  if (applied) u.searchParams.set("a", "1");
  return u.toString();
}

// 기사님 화면의 핵심 한 줄(driver.html과 문서가 같은 값을 쓴다)
export function driverHeadline(run, key, applied = false) {
  const f = cargoFacts(run, key, applied);
  const adv = driverAdvice(run);
  const { c, cur } = f;
  if (c.kind === "export") {
    const closed = c.gate_closed;
    return {
      big: closed && cur.time < closed[0] ? `${T(closed[0])} 전 반입` : `${T(cur.time)} 출발`,
      sub: `출발 ${T(cur.time)} · 반입 완료 예상 ${clock10(cur.arrival_q50)}`,
      warn: closed ? `${T(closed[0])}~${T(closed[1])} 수출 반입 금지(터미널 공지)` : null, adv,
    };
  }
  const ready = c.ready ? c.ready.q50 : null;
  const start = ready != null ? Math.max(ready, cur.time) : cur.time;
  return {
    big: `${clock10(start, false)} 이후 도착`,
    sub: ready != null && ready > cur.time + 0.25
      ? `컨테이너 반출 예상 ${clock10(ready)} · 그 전엔 기다리기만 합니다`
      : `터미널에서 약 ${fmtDur(cur.exp_turnaround_h)}`,
    warn: null, adv,
  };
}
