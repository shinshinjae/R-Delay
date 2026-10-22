// 엔진 결과(run) → 화면 문장. DOM을 건드리지 않는 순수 함수다(tests/derive.test.mjs로 확인).
// 이 규칙은 엔진이 검증한 출력이 아니라 표시 방식이며, method.html §4에 같은 내용을 공개한다.
// 용어: 허용 위험(ε), 오차 감안(몬테카를로 오차 상한 p̂⁺), 지금 번지는 중일 확률(P(R_d>1)), 가상 시험 결과는 「시험에서」.
import {
  NODE_NAME, SCENARIO_CHIP, CARGO_SHORT, clock, clock10, clock10ceil, fmtDur, fmtDurCeil, fmtDurRange, fmtP, fmtWon, fmtRd,
  fmtRatio, fmtCap, nextUpdateText, hourLabel,
} from "./format.js?v=20261023c";

export const LEVEL_OF_ALARM = { "정상": "good", "적체 경보": "warning", "확산 경보": "serious", "확산·적체 경보": "critical" };

// ---------- 공통 ----------
export const nodeIdx = (run, short) => run.nodes.findIndex((n) => n.short === short);
export const nodeLabel = (run, k) => NODE_NAME[run.nodes[k].id] || run.nodes[k].name;
const congestedIdx = (run) => run.now.congested.map((s) => nodeIdx(run, s)).filter((k) => k >= 0);
const startsWith = (s, p) => typeof s === "string" && s.startsWith(p);

// 은/는: 끝의 괄호 설명을 떼고 마지막 글자로 고른다(한글 받침 유무, 로마자는 받침 없음으로 본다).
export function topic(word) {
  const base = word.replace(/\s*\([^()]*\)\s*$/, "");
  const ch = base.charAt(base.length - 1);
  if (/[가-힣]/.test(ch)) return `${word}${(ch.charCodeAt(0) - 0xac00) % 28 ? "은" : "는"}`;
  if (/[0-9]/.test(ch)) return `${word}${"013678".includes(ch) ? "은" : "는"}`;
  return `${word}는`;
}
function firstStay(n, ok, start = 0) {
  if (n === 0 || !ok(n - 1)) return null;
  let i = n - 1;
  while (i - 1 >= start && ok(i - 1)) i -= 1;
  return i;
}
export const isHold = (run) => run.now.p_spread >= run.now.hold_band[0] && run.now.p_spread <= run.now.hold_band[1];
const pInt = (p) => Math.round(p * 100);
const maxForeP = (run) => Math.max(...run.rd.fore_q.map((q) => q[3]));
// 허용 위험과 같은 값으로 반올림되면 소수 한 자리(9.8% / 10.4%처럼 기준을 넘는지 보이게)
export function fmtPvs(p, eps) {
  const a = fmtP(p);
  if (a === fmtP(eps) && Math.abs(p - eps) > 1e-6 && p >= 0.01 && p < 0.995) return `${(p * 100).toFixed(1)}%`;
  return a;
}

// ---------- 거점 상태 ----------
export function nodeState(run, k) {
  if (congestedIdx(run).includes(k)) return "jam";
  const thr = run.x.threshold[k];
  if (run.now.x_mean[k] >= 0.75 * thr) return "watch";
  for (let i = 0; i < run.t_fore.length; i++) {
    if (run.t_fore[i] <= 12 && run.x.fore_q50[i][k] >= thr) return "watch";
  }
  return "ok";
}
export const STATE_LEVEL = { jam: "critical", watch: "warning", ok: "good" };
export const STATE_WORD = { jam: "막힘", watch: "주의", ok: "보통" };

// ---------- 상태 배너 ----------
export function statusView(run) {
  const now = run.now;
  const jamNames = congestedIdx(run).map((k) => nodeLabel(run, k)).join(", ");
  const hold = isHold(run);
  let level = LEVEL_OF_ALARM[now.alarm] || "warning";
  let head;
  if (hold) {
    level = "warning";
    head = [`지연이 번질 수 있음(${pInt(now.p_spread)}% · 판단 경계)`, jamNames ? `막힌 곳: ${jamNames}` : "아직 막힌 곳 없음"];
  } else {
    head = {
      critical: ["지연이 번지는 중", `막힌 곳: ${jamNames}`],
      serious: ["지연이 번지기 시작했습니다", "아직 막힌 곳 없음"],
      warning: [`막힌 곳: ${jamNames}`, "번짐 신호 약함"],
      good: ["평소 수준입니다", "막힌 곳 없음"],
    }[level];
  }
  return {
    level, icon: hold ? "hold" : level, titleParts: head, title: head.join(" · "), hold,
    cause: causeLine(run), outlook: outlookText(run), index: indexLine(run),
  };
}

function speedNote(now) {
  return now.cofactors.includes("기상·장비") ? " · 전파 속도도 평소보다 빠름(날씨·장비·우연 변동)" : "";
}
const MIXED_NOTE = " · 거점 규모 가정에 따라 판정이 달라질 수 있음";

export function causeLine(run) {
  const now = run.now;
  const next = nextUpdateText(run);
  const cap = now.capacity;
  const minK = cap.indexOf(Math.min(...cap));
  const mixed = typeof now.cause === "string" && now.cause.includes("혼합") ? MIXED_NOTE : "";
  if (startsWith(now.cause, "처리능력형")) {
    const text = cap[minK] < 0.999
      ? `처리능력 저하(${nodeLabel(run, minK)} ${fmtCap(cap[minK])}) — 물량 탓 아님`
      : "물량 탓 아님 — 처리 속도·날씨·장비 쪽 요인";
    return { label: "원인", text: text + speedNote(now) + mixed, hold: false };
  }
  if (startsWith(now.cause, "물량 주도형")) {
    const k = now.xi.indexOf(Math.max(...now.xi));
    const seaBoth = run.nodes[k].domain === "sea" && run.nodes.every((n, j) => n.domain !== "sea" || now.xi[j] >= now.xi[k] - 0.05);
    const where = seaBoth ? "신항" : nodeLabel(run, k);
    return { label: "원인", text: `물량 증가(${where} 물동량 추정 ${fmtRatio(now.xi[k])})${speedNote(now)}${mixed}`, hold: false };
  }
  if (startsWith(now.cause, "확산 경계")) {
    return {
      label: "원인", hold: true, text: `아직 단정하기 어렵습니다 — 다음 관측(${next}) 후 다시 판단`,
      sub: `지금 번지는 중일 확률이 ${pInt(now.p_spread)}%로 판단 경계(${pInt(now.hold_band[0])}~${pInt(now.hold_band[1])}%)라 원인 판정을 미룹니다`,
    };
  }
  if (startsWith(now.cause, "원인 보류")) {
    return { label: "원인", hold: true, text: `물량 탓인지 처리능력 탓인지 아직 가르기 어렵습니다 — 다음 관측(${next}) 후 다시 판단` };
  }
  const jam = congestedIdx(run);
  if (jam.length && now.congestion_factor) {
    const c = Math.min(...jam.map((k) => cap[k]));
    const text = {
      "처리능력 저하": c < 0.999 ? `처리능력 저하(출근율·장비 등, ${fmtCap(c)})` : "처리능력 저하",
      "물량": "물량 증가",
      "기상·장비": "날씨·장비 영향",
    }[now.congestion_factor] || now.congestion_factor;
    return { label: "막힌 이유", text, hold: false };
  }
  return null;
}

export function indexLine(run) {
  const n = run.now;
  return { rd: fmtRd(n.rd_median), lo: fmtRd(n.rd_q10), hi: fmtRd(n.rd_q90), p: pInt(n.p_spread) };
}

// ---------- 전망 ----------
function jamOutlook(run, k) {
  const tf = run.t_fore, q50 = run.x.fore_q50, q90 = run.x.fore_q90, thr = run.x.threshold;
  const name = nodeLabel(run, k);
  // 0번 칸(지금)과 마지막 칸(차트에서도 빼는 예측 끝점)은 시각 판단에 쓰지 않는다
  const n = tf.length - 1;
  const nearEnd = (i) => (i != null && tf[i] > tf[n - 1] - 2 ? null : i); // 끝 2시간 안에서만 내려가는 것은 시각으로 말하지 않는다
  const end = nearEnd(firstStay(n, (i) => q50[i][k] < thr[k], 1));
  const end90 = nearEnd(firstStay(n, (i) => q90[i][k] < thr[k], 1));
  let peak = 0;
  for (let i = 1; i < tf.length; i++) if (q50[i][k] > q50[peak][k]) peak = i;
  const worse = q50[peak][k] >= 1.2 * run.now.x_mean[k];
  if (worse && tf[peak] >= tf[tf.length - 1] - 2) return `${name} 막힘은 24시간 끝까지 계속 심해질 전망`;
  const late = end90 != null && end90 !== end ? `(길면 ${clock(tf[end90])})` : end != null && end90 == null ? "(길면 24시간 넘게)" : "";
  if (end != null && worse) return `${name} 막힘은 ${clock(tf[peak])}쯤 가장 심했다가 ${clock(tf[end])}쯤${late} 풀릴 전망`;
  if (end != null) return `${name} 막힘은 ${clock(tf[end])}쯤${late} 풀릴 전망`;
  if (worse) return `${name} 막힘은 더 심해질 전망(가장 나쁜 때 ${clock(tf[peak])}쯤)`;
  return `${name} 막힘은 24시간 안에 풀릴 신호가 없습니다`;
}

function spreadOutlook(run) {
  const ft = run.rd.fore_t, fq = run.rd.fore_q;
  if (run.now.p_spread >= 0.5) {
    const n = ft.length - 1; // 마지막 예측 점은 메인 차트처럼 빼고 본다
    const nearEnd = (i) => (i != null && ft[i] > ft[n - 1] - 2 ? null : i); // 끝 2시간 안의 시각은 쓰지 않는다
    // 90% 상한이 외딴 한 점에서만 1을 살짝(1.05 미만) 넘은 것은 무시한다(앞뒤가 모두 1 아래)
    const below = (i) => fq[i][2] < 1 || (fq[i][2] < 1.05 && i > 0 && i < n - 1 && fq[i - 1][2] < 1 && fq[i + 1][2] < 1);
    const stable = nearEnd(firstStay(n, below));
    const soft = nearEnd(firstStay(n, (i) => fq[i][3] < 0.5, 1));
    if (stable != null && (soft == null || ft[stable] - ft[soft] <= 6)) return `새 지연이 늘어나는 건 ${clock(ft[stable])} 이후 멈출 전망`;
    if (soft != null) return `새 지연 증가는 ${clock(ft[soft])}쯤부터 누그러질 전망${stable != null ? `(늦으면 ${clock(ft[stable])} 이후 멈춤)` : ""}`;
    return "번짐이 24시간 동안 이어질 수 있습니다";
  }
  const i = fq.findIndex((q) => q[3] >= 0.5);
  return i >= 0 ? `${clock(ft[i])}쯤부터 번질 수 있음` : null;
}

export function outlookText(run) {
  const parts = [];
  const sp = spreadOutlook(run);
  if (sp) parts.push(sp);
  const jam = congestedIdx(run);
  for (const k of jam) parts.push(jamOutlook(run, k));
  const tf = run.t_fore, q50 = run.x.fore_q50, thr = run.x.threshold;
  const fresh = [];
  run.nodes.forEach((_, k) => {
    if (jam.includes(k)) return;
    const i = tf.findIndex((t, j) => t > 0 && t <= 12 && q50[j][k] >= thr[k]);
    if (i >= 0) fresh.push([k, tf[i]]);
  });
  if (fresh.length) {
    fresh.sort((a, b) => a[1] - b[1]);
    parts.push(fresh.map(([k, t], j) => `${topic(nodeLabel(run, k))} ${clock(t)}쯤${j === fresh.length - 1 ? "부터 막힐 수 있음" : ""}`).join(", "));
  }
  return parts.length ? parts.join(" · ") : "앞으로 24시간 큰 변화 없을 전망";
}

// 24시간 카드의 한 줄
export function trendLine(run) {
  const maxP = pInt(maxForeP(run));
  if (run.now.p_spread >= 0.5) return `${spreadOutlook(run)}${spreadOutlook(run).endsWith("있습니다") ? "" : "입니다"}`;
  const jam = congestedIdx(run);
  if (jam.length) return `${jamOutlook(run, jam[0])} · 번짐 지수는 1 근처(번질 확률 최대 ${maxP}%)`;
  if (run.rd.fore_q.every((q) => q[2] < 1)) return "24시간 내내 1 아래에 머물 전망입니다";
  return `1 근처를 오갈 전망입니다(번질 확률 최대 ${maxP}%)`;
}

// ---------- 화물 ----------
export const CLASS_ORDER = ["change", "notify", "hold-conservative", "hold-uncertain", "optional", "ok"];
export const CLASS_PILL = {
  change: ["warning", "시간 변경 권장"],
  notify: ["critical", "화주 안내 필요"],
  "hold-conservative": ["hold", "기준 근처 · 다시 확인"],
  "hold-uncertain": ["hold", "판단 보류"],
  optional: ["good", "바꿔도 됨(선택)"],
  ok: ["good", "계획대로 OK"],
};

export function classifyCargo(run, key) {
  const c = run.cargo[key];
  const rk = run.ranking.find((r) => r.cargo === key);
  const byLabel = (l) => c.candidates.find((x) => x.label === l);
  const plan = c.candidates.find((x) => x.is_plan);
  const dec = c.decision;
  let cls, choice = null, least = null;
  if (dec.status === "uncertain") {
    cls = "hold-uncertain";
    least = byLabel(dec.least) || byLabel(dec.choice) || plan;
  } else if (dec.status === "no_acceptable_action") {
    cls = dec.conservative ? "hold-conservative" : "notify";
    least = byLabel(dec.least) || plan;
  } else {
    choice = byLabel(dec.choice) || plan;
    if (choice.is_plan) cls = "ok";
    else if (rk.plan_p_upper > c.epsilon) cls = "change";
    else cls = "optional";
  }
  // 지금 계획과 놓칠 확률·오차 감안 값이 화면 표기로 같으면 다른 시각을 내세우지 않고 지금 계획으로 말한다
  const same = (a, b) => fmtPvs(a.p_fail, c.epsilon) === fmtPvs(b.p_fail, c.epsilon) && fmtPvs(a.p_upper, c.epsilon) === fmtPvs(b.p_upper, c.epsilon);
  if (least && plan && !least.is_plan && same(least, plan)) least = plan;
  return { cls, cargo: c, plan, choice, least, rk };
}

export const verbOf = (c) => (c.kind === "export" ? "출발" : "도착");
const riskWords = (p, eps = null) => (p >= 0.995 ? "거의 확실히 놓침" : `놓칠 확률 ${eps == null ? fmtP(p) : fmtPvs(p, eps)}`);
const timeOf = (cand) => clock(cand.time, false);
// 「16:00 출발」「21:30 도착(수소 트럭)」
const optName = (cand, verb) => `${timeOf(cand)} ${verb}${isH2(cand) ? "(수소 트럭)" : ""}`;
// 「가능성이 높습니다」「지연 가능성은 낮지만」은 오차를 감안해도 10% 미만일 때만 쓴다
const lowRisk = (cand) => cand.p_upper < 0.1;
const isH2 = (cand) => !!cand && cand.vehicle === "수소";

// 컨테이너가 늦게 나와서, 더 일찍 도착해도 놓칠 확률이 같은지
export function sameIfEarly(run, key) {
  const { cargo: c, least, plan } = classifyCargo(run, key);
  if (c.kind !== "import" || !c.ready || !least) return false;
  // 지금 계획의 위험이 가장 나은 안과 같을 때만 「시각을 바꿔도 같다」고 말한다(아니면 더 나은 시각을 숨기게 된다)
  if (plan && Math.abs(plan.p_fail - least.p_fail) >= 0.01) return false;
  const same = c.candidates.filter((x) => !x.negotiation && !(x.reasons || []).length && x.time <= least.time && Math.abs(x.p_fail - least.p_fail) < 0.01);
  return same.length >= 2;
}

// 수소 트럭: 가능한 안이 있는지, 없으면 그 이유
function h2Info(c) {
  const h2 = c.candidates.filter((x) => x.vehicle === "수소");
  if (!h2.length) return null;
  const ok = h2.filter((x) => x.feasible !== false && !startsWith(x.status, "실행 불가"));
  const bad = h2.filter((x) => x.feasible === false || startsWith(x.status, "실행 불가"));
  const reason = bad.length ? ((bad[0].reasons && bad[0].reasons[0]) || bad[0].status.replace("실행 불가: ", "")) : "";
  return { possible: ok.length > 0, reason };
}

function scenarioCargoValid(valid, run, key) {
  const S = valid && valid.scenarios && valid.scenarios[run.meta.scenario];
  return S && S.cargo ? S.cargo[key] : null;
}

export function cargoView(run, key, valid = null) {
  const k = classifyCargo(run, key);
  const { cls, cargo: c, plan, choice, least } = k;
  const verb = verbOf(c);
  const [level, pill] = CLASS_PILL[cls];
  const eps = fmtP(c.epsilon);
  let action = "", reason = "", note = null;
  if (cls === "change") {
    const dir = choice.time < plan.time ? "앞당기세요" : "늦추세요";
    action = c.kind === "export" ? `${timeOf(choice)} 출발로 ${dir}` : `터미널 도착을 ${timeOf(choice)}으로 ${dir}`;
    if (isH2(choice)) action += " · 수소 트럭";
    if (plan.p_fail > c.epsilon) {
      reason = `지금 계획(${timeOf(plan)} ${verb})대로면 ${riskWords(plan.p_fail)} → 바꾸면 놓칠 확률 ${fmtPvs(choice.p_fail, c.epsilon)}`;
    } else {
      reason = `지금 계획은 오차를 감안하면 ${fmtPvs(plan.p_upper, c.epsilon)}로 허용 위험 ${eps}를 넘을 수 있음 → ${timeOf(choice)}이면 ${fmtPvs(choice.p_upper, c.epsilon)} 이내`;
    }
    const v = scenarioCargoValid(valid, run, key);
    if (v && v.changed_share >= 0.5 && v.realized_fail_rdelay >= v.realized_fail_plan - 0.01) {
      note = `가상 시험에서는 바꿔도 놓친 비율이 같았습니다(${pInt(v.realized_fail_plan)}%→${pInt(v.realized_fail_rdelay)}%)`;
    }
  } else if (cls === "notify") {
    action = least.p_fail >= 0.5
      ? "어느 시각에 가도 절반 넘게 놓칠 전망 — 화주에게 미리 알리세요"
      : `어느 시각에 가도 놓칠 확률이 허용 위험 ${eps}를 넘습니다(가장 낮아도 ${fmtPvs(least.p_fail, c.epsilon)}) — 화주에게 가능성을 미리 알리세요`;
    reason = sameIfEarly(run, key)
      ? `컨테이너가 ${clock10(c.ready.q50)}쯤 나와서 더 일찍 가도 위험이 같습니다`
      : `가장 나은 안: ${optName(least, verb)} · ${riskWords(least.p_fail, c.epsilon)}${least.is_plan ? "" : `(지금 계획 ${optName(plan, verb)}이면 ${fmtPvs(plan.p_fail, c.epsilon)})`}`;
  } else if (cls === "hold-conservative") {
    const next = nextUpdateText(run);
    if (!least.is_plan) {
      // 가장 덜 위험한 안이 지금 계획이 아니면 「계획대로」라고 쓰지 않고, 계획의 위험과 그 안의 시각을 함께 밝힌다
      action = `지금 계획(${optName(plan, verb)})대로면 ${riskWords(plan.p_fail, c.epsilon)} → ${optName(least, verb)}이면 놓칠 확률 ${fmtPvs(least.p_fail, c.epsilon)} — 다만 계산이 충분히 확실하지 않아 다음 관측(${next}) 후 다시 확인하세요`;
    } else if (lowRisk(least)) {
      action = `계획대로 진행해도 될 가능성이 높습니다(놓칠 확률 ${fmtPvs(least.p_fail, c.epsilon)}) — 다만 계산이 충분히 확실하지 않아 다음 관측(${next}) 후 다시 확인하세요`;
    } else if (least.p_fail < c.epsilon / 2) {
      action = `지금 계획대로면 놓칠 확률 ${fmtPvs(least.p_fail, c.epsilon)}이지만 계산 오차가 커서 다음 관측(${next}) 후 다시 확인하세요`;
    } else if (least.p_fail <= 1.25 * c.epsilon) {
      action = `지금 계획대로면 놓칠 확률 ${fmtPvs(least.p_fail, c.epsilon)} — 허용 위험 ${eps} 근처라 다음 관측(${next}) 후 다시 확인하세요`;
    } else {
      action = `지금 계획대로면 놓칠 확률 ${fmtPvs(least.p_fail, c.epsilon)}로 허용 위험 ${eps}를 넘을 수 있습니다 — 계산이 충분히 확실하지 않아 다음 관측(${next}) 후 다시 확인하세요`;
    }
    reason = `${least.is_plan ? "" : `${optName(least, verb)} 기준 · `}오차를 감안하면 ${fmtPvs(least.p_upper, c.epsilon)} · 허용 위험 ${eps}`;
  } else if (cls === "hold-uncertain") {
    const maxed = (run.meta.params && run.meta.params.particles) >= 1024;
    const tail = maxed ? `표본을 최대로 늘려도 계산 오차가 커서 판단을 미룹니다 — 다음 관측(${nextUpdateText(run)}) 후 다시 확인하세요` : "계산이 아직 불확실합니다 — 표본을 늘려 다시 계산해 보세요";
    action = !least.is_plan && plan.p_fail > c.epsilon
      ? `지금 계획(${optName(plan, verb)})대로면 ${riskWords(plan.p_fail, c.epsilon)} · ${optName(least, verb)} 쪽이 낮아 보이지만 ${tail}`
      : tail;
    reason = `${least.is_plan ? "" : `${optName(least, verb)}이면 `}놓칠 확률 ${fmtPvs(least.p_fail, c.epsilon)} · 오차 감안 ${fmtPvs(least.p_upper, c.epsilon)} · 허용 위험 ${eps}`;
  } else if (cls === "optional") {
    const diff = plan.exp_cost - choice.exp_cost;
    const cost = diff >= 10000 ? `비용 ${fmtWon(diff)} 적음` : "비용 비슷";
    if (isH2(choice)) {
      const planH2 = c.candidates.find((x) => x.vehicle === "수소" && Math.abs(x.time - plan.time) < 1e-9);
      const why = planH2 && (planH2.feasible === false || startsWith(planH2.status, "실행 불가")) ? `(${timeOf(plan)} 불가 — 충전·이동 제약)` : "";
      action = `수소 트럭으로 바꿀 수 있음 — ${timeOf(choice)} ${verb}${why} · ${cost}`;
    } else {
      action = `${timeOf(choice)} ${verb === "도착" ? "도착으로" : "출발로"} 바꿔도 됩니다 · ${cost}`;
    }
    reason = `지금 계획도 허용 위험 안 · 놓칠 확률 ${fmtP(plan.p_fail)}` + (fmtP(choice.p_fail) !== fmtP(plan.p_fail) ? ` → 바꾸면 ${fmtP(choice.p_fail)}` : "");
  } else {
    action = `지금 계획(${timeOf(plan)} ${verb}) 그대로 진행하세요`;
    reason = `놓칠 확률 ${fmtP(plan.p_fail)} · 허용 위험 ${eps} 안`;
    if (run.meta.h2) {
      const h = h2Info(c);
      if (h && !h.possible) note = `수소 트럭은 불가(${h.reason})`;
    }
  }
  const canCopy = cls === "notify" || cls === "hold-conservative";
  return {
    key, cls, level, pill, action, reason, note, label: c.label, deadline: c.deadline_label, kind: c.kind,
    notice: canCopy ? noticeText(run, key) : null, canCopy, copyPrimary: cls === "notify",
    p: (least || choice || plan).p_fail,
  };
}

export function sortedCargo(run) {
  const keys = run.ranking.map((r) => r.cargo);
  const cl = Object.fromEntries(keys.map((k) => [k, classifyCargo(run, k)]));
  const p = (k) => (cl[k].least || cl[k].choice || cl[k].plan).p_fail;
  return keys.slice().sort((a, b) => {
    const d = CLASS_ORDER.indexOf(cl[a].cls) - CLASS_ORDER.indexOf(cl[b].cls);
    if (d) return d;
    return cl[a].cls === "notify" ? p(b) - p(a) : 0;
  });
}

export function todoView(run) {
  const counts = Object.fromEntries(CLASS_ORDER.map((c) => [c, 0]));
  const keys = run.ranking.map((r) => r.cargo);
  for (const k of keys) counts[classifyCargo(run, k).cls] += 1;
  const n = keys.length;
  const okLike = counts.ok + counts.optional;
  let text;
  if (okLike === n) {
    text = `오늘 화물 ${n}건 모두 계획대로 진행 가능`;
  } else {
    const items = [["change", "시간 변경"], ["notify", "화주 안내"], ["hold-conservative", "다시 확인"], ["hold-uncertain", "판단 보류"]]
      .filter(([c]) => counts[c]).map(([c, w]) => `${w} ${counts[c]}`);
    if (okLike) items.push(`계획대로 ${okLike}`);
    text = `오늘 화물 ${n}건 — ${items.join(" · ")}`;
  }
  if (run.meta.h2) {
    const info = keys.map((k) => h2Info(run.cargo[k])).filter(Boolean);
    const yes = info.filter((h) => h.possible).length, no = info.length - yes;
    if (info.length) text += ` · 수소 트럭 가능 ${yes}건${no ? ` · 불가 ${no}건` : ""}`;
  } else if (counts.optional) {
    text += ` · ${counts.optional}건은 바꿔도 됨`;
  }
  return { text, counts, n };
}

// ---------- 펼친 카드 ----------
const STATUS_TAG = {
  "추천": "추천", "기준 충족": "허용 위험 안", "위험 기준 초과": "위험 높음",
  "위험 최소(기준 초과)": "위험 높음", "추정 불확실": "계산 불확실",
};
export function cargoDetail(run, key) {
  const k = classifyCargo(run, key);
  const { cargo: c, plan, cls } = k;
  const imp = c.kind === "import";
  const why = [];
  if (imp && c.ready) {
    const q = run.queue;
    why.push(`배 ${clock(q.ship_arrival, false)} 입항 → 부두 자리 기다림 ${fmtDur(q.wait_median_h)}(길면 ${q.wait_q90_h == null ? "예측 범위 밖" : fmtDurCeil(q.wait_q90_h)}) → 컨테이너 ${clock10(c.ready.q50)}쯤 나옴(늦으면 ${clock10ceil(c.ready.q90)})`);
  }
  if (c.gate_closed) {
    const [a, b] = c.gate_closed;
    const t = c.plan.t_plan;
    const pushed = t != null && plan.time < b && t >= b - 1e-9;
    why.push(pushed
      ? `지금 계획(${timeOf(plan)} 출발)은 공지된 반입 금지(${clock(a, false)}~${clock(b, false)})에 걸려 ${clock(t)}에야 반입됩니다`
      : `터미널 공지: 수출 반입 금지 ${clock(a, false)}~${clock(b, false)}`);
  }
  const ref = k.choice || k.least || plan;
  const basis = ref.is_plan ? "" : `${optName(ref, verbOf(c))} 기준 `;
  why.push(`${basis}최종 도착 예상 ${clock10(ref.arrival_q50)}(늦으면 ${clock10ceil(ref.arrival_q90)}) · 마감 ${c.deadline_label}`);
  if (sameIfEarly(run, key)) why.push(`컨테이너가 ${clock10(c.ready.q50)}쯤 나와서 더 일찍 가도 위험이 같습니다`);

  // 같은 위험(반올림 같음)인 연속 시각은 한 줄로 묶는다
  const rows = [];
  for (const x of c.candidates) {
    const infeasible = startsWith(x.status, "실행 불가") || x.feasible === false;
    const name = x.label.replace(" (현행 계획)", "").replace(" + 우선 양하 요청", "");
    const last = rows[rows.length - 1];
    const sameRisk = last && !last.infeasible && !infeasible && !x.negotiation && !last.negotiation && !x.priority && !last.priority &&
      last.vehicle === x.vehicle && fmtPvs(last.p, c.epsilon) === fmtPvs(x.p_fail, c.epsilon) && fmtPvs(last.upper, c.epsilon) === fmtPvs(x.p_upper, c.epsilon) &&
      x.status !== "추천" && last.status !== "추천";
    if (sameRisk) {
      last.toName = name;
      last.members.push(x);
      if (x.is_plan) last.isPlan = true;
      continue;
    }
    rows.push({ name, toName: null, members: [x], vehicle: x.vehicle, p: x.p_fail, upper: x.p_upper, status: x.status, infeasible,
      negotiation: !!x.negotiation, priority: !!x.priority, isPlan: !!x.is_plan, approved: x.p_if_approved });
  }
  const options = rows.map((r) => {
    const tags = [];
    if (r.isPlan) tags.push("지금 계획");
    if (r.priority) tags.push("우선 하역 요청");
    let name = r.toName ? `${r.name}~${r.toName.replace(/^(도착|출발) /, "")}` : r.name;
    if (imp) name = name.replace(/^도착 /, "터미널 도착 ");
    let status = STATUS_TAG[r.status] || r.status;
    let text = `놓칠 확률 ${fmtPvs(r.p, c.epsilon)}${r.members.length > 1 ? "(이 시간대 어느 때나)" : ""} · 오차 감안 ${fmtPvs(r.upper, c.epsilon)}`;
    if (r.negotiation && r.approved != null) {
      const no = Math.min(1, Math.max(0, 2 * r.p - r.approved));
      status = "터미널 승인 필요";
      text = `승인되면 ${fmtP(r.approved)} · 안 되면 ${fmtP(no)}(반반 가정 ${fmtP(r.p)})`;
    }
    if (r.infeasible) {
      const x = r.members[0];
      status = `불가: ${(x.reasons && x.reasons[0]) || x.status.replace("실행 불가: ", "")}`;
      text = "";
    }
    const x = r.members[0];
    const turn = r.members.length > 1
      ? `${c.kind === "export" ? "반입 처리" : "터미널에서"} ${fmtDurRange(Math.min(...r.members.map((m) => m.exp_turnaround_h)), Math.max(...r.members.map((m) => m.exp_turnaround_h)))}`
      : `${c.kind === "export" ? "반입 처리" : "터미널에서"} ${fmtDur(x.exp_turnaround_h)}`;
    return {
      name, tags, status, infeasible: r.infeasible, chosen: r.status === "추천", p: r.p, upper: r.upper, eps: c.epsilon,
      text, sub: r.infeasible ? "" : `${turn} · 예상 비용 ${fmtWon(x.exp_cost)}${r.members.length > 1 ? " 안팎" : ""}`,
      danger: r.upper > c.epsilon,
    };
  });
  return {
    why, options, h2Note: c.h2_note || null,
    rule: `판단은 계산 오차를 감안한 확률로 합니다(이 화물${ref.is_plan ? "" : ` ${optName(ref, verbOf(c))} 기준`}: ${fmtPvs(ref.p_upper, c.epsilon)}) · 허용 위험 ${fmtP(c.epsilon)}`,
    notice: cls === "notify" || cls === "hold-conservative" ? noticeText(run, key) : null,
    raw: [c.decision.message, c.notice].filter(Boolean),
  };
}

// ---------- 화주 안내 문구 ----------
function noticeCause(run, key) {
  if (sameIfEarly(run, key)) return "부산신항 터미널 하역 지연";
  const jam = congestedIdx(run);
  if (jam.length) {
    const id = run.nodes[jam[0]].id;
    if (id === "AT" || id === "PN") return "부산신항 터미널 혼잡";
    if (id === "L") return "양산·울산 방면 게이트·도로 혼잡";
    return "항공 화물터미널 반입 지연";
  }
  return run.now.p_spread >= 0.5 ? "부산신항 일대 지연 확산" : "운영 지연";
}
export function noticeText(run, key) {
  const { cargo: c, cls, least, plan } = classifyCargo(run, key);
  const ref = least || plan;
  const basis = ref.is_plan ? "" : `${c.kind === "export" ? `${timeOf(ref)} 출발` : `터미널 ${timeOf(ref)} 도착`}${isH2(ref) ? "(수소 트럭)" : ""} 기준, `;
  const head = `[시연용 가상 데이터 예시] ${c.label} 건 사전 안내드립니다. ${noticeCause(run, key)} 때문에 도착이 늦어질 수 있습니다. ` +
    `예상 도착은 ${clock10(ref.arrival_q50)} 전후, 늦으면 ${clock10ceil(ref.arrival_q90)}입니다(${basis}마감 ${c.deadline_label}). `;
  const unsure = "계산이 충분히 확실하지 않아 미리 알려 드립니다.";
  // 「약 3%~6%」, 1% 미만이면 「6% 이하」, 둘이 같게 표기되면 한 값
  const lo = fmtPvs(ref.p_fail, c.epsilon), hi = fmtPvs(ref.p_upper, c.epsilon);
  const span = lo === hi ? `약 ${hi}` : ref.p_fail < 0.01 ? `${hi} 이하` : `약 ${lo}~${hi}`;
  const tail = cls === "hold-conservative"
    ? (lowRisk(ref)
      ? `지연 가능성은 낮지만(${span}) ${unsure} 상황이 바뀌면 바로 다시 알려 드리겠습니다.`
      : `마감을 맞추지 못할 가능성은 ${span}입니다. ${unsure} 상황이 바뀌면 바로 다시 알려 드리겠습니다.`)
    : `마감을 맞추지 못할 가능성은 약 ${fmtPvs(ref.p_fail, c.epsilon)}입니다. 상황이 바뀌면 바로 다시 알려 드리겠습니다.`;
  return head + tail;
}

// ---------- 기사님 안내 ----------
function interp(g, t) {
  if (t <= g[0].t) return g[0].q50;
  for (let i = 1; i < g.length; i++) {
    if (t <= g[i].t) {
      const a = g[i - 1], b = g[i];
      return a.q50 + ((b.q50 - a.q50) * (t - a.t)) / (b.t - a.t || 1);
    }
  }
  return null;
}
const minutes5 = (h) => Math.max(5, Math.round((h * 60) / 5) * 5);
export const cellLevel = (q50) => { const m = minutes5(q50); return m <= 60 ? "good" : m <= 120 ? "warning" : "critical"; };
// 연속 칸 구간 → 「20:00~24:00」(마지막 칸은 그 시각 이후)
function rangeText(cells, lastT) {
  const ranges = [];
  for (const x of cells) {
    const r = ranges[ranges.length - 1];
    if (r && Math.abs(x.t - r[1] - 1) < 1e-9) r[1] = x.t; else ranges.push([x.t, x.t]);
  }
  return ranges.map(([s, e]) => {
    if (s === e) return e === lastT ? `${clock(s, false)} 이후` : clock(s, false);
    return e === lastT ? `${clock(s, false)} 이후` : `${clock(s, false)}~${clock(e, false)}`;
  }).join(", ");
}
// 칸 묶음 중 가장 짧은 소요 + 10분 이내 칸들
function bestWindow(cells, lastT) {
  if (!cells.length) return null;
  const min = Math.min(...cells.map((x) => x.q50));
  const best = cells.filter((x) => x.q50 <= min + 10 / 60);
  return { cells: best, range: rangeText(best, lastT), dur: fmtDurRange(Math.min(...best.map((x) => x.q50)), Math.max(...best.map((x) => x.q50))) };
}

export function driverAdvice(run) {
  const g = run.gate;
  const lastT = g[g.length - 1].t;
  const flat = !!run.gate_flat;
  const imp = run.cargo_order.map((k) => run.cargo[k]).find((c) => c.kind === "import" && c.ready);
  const exKey = run.cargo_order.find((k) => run.cargo[k].kind === "export");
  const ex = exKey ? run.cargo[exKey] : null;
  const closed = ex && ex.gate_closed ? ex.gate_closed : null;
  const readyT = imp ? imp.ready.q50 : null;
  const cells = g.map((x) => {
    const inClosed = !!closed && x.t >= closed[0] && x.t < closed[1];
    const early = readyT != null && x.t + 0.5 < readyT;
    return {
      t: x.t, q50: x.q50, q90: x.q90, level: flat ? "flat" : cellLevel(x.q50), label: hourLabel(x.t), closed: inClosed, early,
      readyHere: readyT != null && readyT >= x.t - 0.5 && readyT < x.t + 0.5, best: false,
      aria: `${hourLabel(x.t)} 도착: 약 ${fmtDur(x.q50)}, 길면 ${fmtDurCeil(x.q90)}${early ? " · 컨테이너 나오기 전" : ""}${inClosed ? " · 수출 반입 금지" : ""}`,
    };
  });
  const out = { flat, cells, rows: [], readyLine: null, ready: readyT != null ? { t: readyT, label: clock10(readyT, false) } : null, closed };
  // 수입 컨테이너 찾으러 갈 때
  if (imp) {
    const w = bestWindow(cells.filter((x) => !x.early), lastT);
    if (w) {
      for (const x of w.cells) x.best = true;
      // 컨테이너가 나오는 시각에 가도 가장 짧은 소요와 10분 안이면 그 시각부터, 아니면 가장 덜 걸리는 칸부터
      const min = Math.min(...w.cells.map((x) => x.q50));
      const atReady = interp(g, readyT);
      const start = atReady != null && atReady <= min + 10 / 60 ? readyT : Math.max(readyT, w.cells[0].t);
      const dur = interp(g, start);
      const later = !flat && w.cells[0].t > start + 0.5;
      out.rows.push({
        key: "import", label: "수입 컨테이너 찾으러 갈 때",
        value: `${clock10(start, false)} 이후 도착`, sub: `약 ${fmtDur(dur ?? w.cells[0].q50)}${later ? ` · 가장 덜 걸리는 때 ${w.range}` : ""}`,
      });
    } else {
      out.rows.push({ key: "import", label: "수입 컨테이너 찾으러 갈 때", value: `컨테이너 ${clock10(readyT)}쯤 나옴`, sub: "그 뒤 도착 · 소요는 예측 범위 밖" });
    }
    out.readyLine = `오늘 ${clock(run.queue.ship_arrival, false)} 입항 배 컨테이너는 ${clock10(readyT)}쯤 나옵니다(늦으면 ${clock10ceil(imp.ready.q90)}) — 그 전에 가면 기다리기만 합니다`;
  }
  // 수출 넣으러 갈 때
  if (ex) {
    if (closed) {
      const before = cells.filter((x) => x.t < closed[0] && (ex.deadline == null || x.t + x.q90 <= ex.deadline));
      const w = bestWindow(before, lastT);
      out.rows.push({
        key: "export", label: "수출 넣으러 갈 때",
        value: w ? `${clock(closed[0], false)} 전 도착` : "마감 전 반입 어려움",
        sub: `${clock(closed[0], false)}~${clock(closed[1], false)} 반입 금지(터미널 공지)${w ? ` · ${w.range} 도착이면 약 ${w.dur.replace(/^약 /, "")}` : ` · 마감 ${ex.deadline_label}`}`,
        warn: true,
      });
    } else if (flat) {
      // 하루 내내 비슷해도 늦은 칸이 마감에 걸리면 그 전까지로 알려 준다
      const ok = cells.filter((x) => ex.deadline == null || x.t + x.q90 <= ex.deadline);
      if (!ok.length) out.rows.push({ key: "export", label: "수출 넣으러 갈 때", value: "마감 전 반입 어려움", sub: `마감 ${ex.deadline_label}`, warn: true });
      else if (ok.length < cells.length) out.rows.push({ key: "export", label: "수출 넣으러 갈 때", value: `${clock(ok[ok.length - 1].t, false)}까지 도착`, sub: `그보다 늦으면 마감(${clock(ex.deadline)})에 늦을 수 있음` });
    } else {
      // 반입 처리가 늦어도(길면) 마감 안에 끝나는 칸만 권한다
      const ok = cells.filter((x) => ex.deadline == null || x.t + x.q90 <= ex.deadline);
      const w = bestWindow(ok, null);
      if (w) {
        // 마감 안에 끝나는 칸이 모두 가장 짧은 소요와 10분 안이면 「언제 가도 비슷」, 늦은 칸이 마감에 걸려 빠졌으면 마지막으로 가능한 시각까지로 말한다
        const flatish = w.cells.length === ok.length;
        const value = !flatish ? w.range : ok.length === cells.length ? "언제 가도 비슷" : `${clock(ok[ok.length - 1].t, false)}까지 아무 때나`;
        const late = flatish && ok.length < cells.length ? ` · 그보다 늦으면 마감(${clock(ex.deadline)})에 늦을 수 있음` : "";
        out.rows.push({ key: "export", label: "수출 넣으러 갈 때", value, sub: `약 ${w.dur.replace(/^약 /, "")}${late}` });
      } else {
        out.rows.push({ key: "export", label: "수출 넣으러 갈 때", value: "마감 전 반입 어려움", sub: `마감 ${ex.deadline_label}`, warn: true });
      }
    }
  }
  if (flat) {
    const qs = g.map((x) => x.q50).sort((a, b) => a - b);
    out.flatText = `터미널 처리 시간은 하루 내내 비슷합니다 · 약 ${fmtDur(qs[Math.floor(qs.length / 2)])}`;
  }
  const L = run.nodes.findIndex((n) => n.id === "L");
  if (L >= 0 && nodeState(run, L) !== "ok") {
    out.roadWarn = `${nodeLabel(run, L)} ${STATE_WORD[nodeState(run, L)]} — 도로·게이트 앞 대기는 아래 시간에 빠져 있습니다`;
  }
  return out;
}

// ---------- 어디가 막혔나 ----------
// 기준과 같은 값으로 반올림되면 소수 한 자리까지 보여 준다
function xText(x, thr) {
  const a = Math.round(x * 100), b = Math.round(thr * 100);
  return a === b && Math.abs(x - thr) > 1e-9 ? (x * 100).toFixed(1) : String(a);
}
export function nodeView(run, k) {
  const now = run.now;
  const state = nodeState(run, k);
  const x = now.x_mean[k], thr = run.x.threshold[k];
  const lo = run.x.filter_q10[run.x.filter_q10.length - 1][k], hi = run.x.filter_q90[run.x.filter_q90.length - 1][k];
  const borderline = Math.abs(x - thr) < 0.01;
  let word = STATE_WORD[state];
  if (state === "jam" && borderline) word = "막힘(기준 바로 위)";
  const notes = [];
  if (state === "watch") {
    const i = run.t_fore.findIndex((t, j) => t > 0 && t <= 12 && run.x.fore_q50[j][k] >= thr);
    notes.push(i >= 0 && x < thr ? `${clock(run.t_fore[i])}쯤 막힘 기준을 넘을 전망` : "막힘 기준에 가까움");
  }
  if (now.capacity[k] < 0.999) notes.push(`처리능력 ${fmtCap(now.capacity[k])}`);
  if (now.xi[k] >= 1.15) notes.push(`물동량 추정 ${fmtRatio(now.xi[k])}`);
  const share = run.contrib.node_share[k];
  const origin = share >= 0.5 && now.p_spread >= 0.5;
  return {
    id: run.nodes[k].id, name: nodeLabel(run, k), state, level: STATE_LEVEL[state], word,
    x: xText(x, thr), thr: Math.round(thr * 100), range: borderline ? `${Math.round(lo * 100)}~${Math.round(hi * 100)}%` : null, notes,
    origin: origin ? `번짐 기여 ${Math.round(share * 100)}%(모형 추정)` : null,
  };
}

// 자세히 보기 › 경로에만 쓴다
export function whereNote(run) {
  if (run.now.p_spread < 0.5) return null;
  const c = run.contrib;
  const k = c.node_share.indexOf(Math.max(...c.node_share));
  if (run.nodes[k].id === "A") return null;
  return `${nodeLabel(run, k)} 지연 1건은 지금 상태가 이어진다면 약 ${Math.round(6 * c.generation_hours)}시간 안에 항공 반입 지연 약 ${c.amplification_to_air[k].toFixed(1)}건으로 이어집니다`;
}

// ---------- 믿을 수 있나요? ----------
const range = (arr) => {
  const a = Math.min(...arr), b = Math.max(...arr);
  return a === b ? `${a}` : `${a}~${b}`;
};
export function trustLines(valid, run) {
  const s = run.meta.scenario;
  const chip = SCENARIO_CHIP[s] || run.meta.scenario_label;
  const S = valid && valid.scenarios && valid.scenarios[s];
  const good = [], limits = [];
  if (!S) {
    limits.push(`이 장면(${chip})은 따로 검증하지 않았습니다 · 평시 조건 검증을 참고하세요`);
  } else {
    const a = S.spread_alarm, n = a.tp + a.fn, worlds = S.worlds || valid.worlds;
    const rule = S.lead_time_notice_rule_h && S.lead_time_notice_rule_h.median;
    const ours = S.lead_time_h && S.lead_time_h.median;
    const noticeDriven = rule != null && ours != null && Math.abs(rule - ours) <= 0.25;
    if (n === 0) good.push(`가상 ${chip} 상황 ${worlds}회 시험: 지연이 번진 적 없음 · 헛경보 ${a.fp}회`);
    else if (noticeDriven && S.masked_alarm_tp_fn) {
      const [mtp] = S.masked_alarm_tp_fn;
      limits.push(`가상 ${chip} 상황 ${worlds}회 시험: 번진 ${n}회 중 ${a.tp}회 경보, 약 ${ours.toFixed(1)}시간 먼저 — 운영사 공지 덕분입니다(공지를 가리면 판단 시각 번짐 경보는 ${S.masked_worlds}회 중 ${mtp}회뿐, 공지만 보는 단순 규칙도 같은 시점)`);
    } else if (a.fn / n < 0.3) good.push(`가상 ${chip} 상황 ${worlds}회 시험: 번진 ${n}회 중 ${a.tp}회 경보 · 헛경보 ${a.fp}회`);
    else limits.push(`가상 ${chip} 상황 ${worlds}회 시험: 번진 ${n}회 중 ${a.tp}회만 경보(놓침 ${a.fn}회) · 헛경보 ${a.fp}회`);
    if (rule != null && ours != null && !noticeDriven) {
      if (rule - ours > 0.25) limits.push(`운영사 공지만 보는 단순 규칙이 더 일찍 경보했습니다(${rule.toFixed(1)}시간 대 ${ours.toFixed(1)}시간 먼저)`);
      else if (ours - rule > 0.25) good.push(`운영사 공지만 보는 단순 규칙보다 ${(ours - rule).toFixed(1)}시간 먼저 경보했습니다`);
    }
    if (S.coverage_80_now < 0.75) {
      limits.push(`이 상황에서는 지연 번짐 지수를 ${S.bias_now_mean < 0 ? "낮게" : "높게"} 잡는 편입니다(예상 범위가 정답을 담은 비율 ${pInt(S.coverage_80_now)}%, 목표 80%)`);
    }
    const keys = Object.keys(S.cargo || {});
    const kind = (k) => (run.cargo[k] ? run.cargo[k].kind : null);
    const imps = keys.filter((k) => kind(k) === "import");
    const warned = imps.filter((k) => S.cargo[k].notice_share >= 0.5 && S.cargo[k].realized_fail_plan >= 0.15);
    if (warned.length) {
      good.push(`현행 방식(도착예정시각+여유 2시간)은 수입 화물 ${warned.length}건을 모두 '문제없음'으로 봤지만, R-Delay는 오전 10시에 놓칠 위험 ${range(warned.map((k) => pInt(S.cargo[k].predicted_fail_plan)))}%를 알렸습니다(시험에서 놓친 비율 ${range(warned.map((k) => pInt(S.cargo[k].realized_fail_plan)))}%)`);
    }
    if (imps.some((k) => S.cargo[k].notice_share >= 0.5 && S.cargo[k].changed_share < 0.05)) {
      limits.push("수입 화물은 컨테이너가 늦게 나와 어느 시각에 가도 예측 위험이 같아 시간 변경을 권하지 않았습니다 — 쓸모는 화주에게 미리 알리는 데 있습니다");
    }
    for (const k of keys) {
      const c = S.cargo[k];
      if (c.changed_share < 0.5) continue;
      const nm = CARGO_SHORT[k] || k;
      const p = pInt(c.realized_fail_plan), r = pInt(c.realized_fail_rdelay);
      if (kind(k) === "export" && run.cargo[k] && run.cargo[k].gate_closed && c.realized_fail_rdelay < c.realized_fail_plan - 0.01) {
        limits.push(`${nm}: 출발을 앞당겨 시험에서 놓친 비율 ${p}% → ${r}% — 공지된 반입 금지만 읽어도 얻는 효과입니다`);
      } else if (c.realized_fail_rdelay < c.realized_fail_plan - 0.01) {
        good.push(`${nm}: 추천대로 바꾸면 시험에서 놓친 비율 ${p}% → ${r}%`);
      } else {
        limits.push(`${nm}: 추천대로 바꿔도 시험에서 놓친 비율은 ${p}%로 같았습니다`);
      }
    }
    const under = imps.filter((k) => S.cargo[k].notice_share >= 0.5 && S.cargo[k].realized_fail_plan - S.cargo[k].predicted_fail_plan >= 0.05);
    if (under.length) {
      const gaps = under.map((k) => Math.round((S.cargo[k].realized_fail_plan - S.cargo[k].predicted_fail_plan) * 100));
      const who = under.length === imps.length ? `수입 화물 ${under.length}건 모두` : under.map((k) => CARGO_SHORT[k] || k).join(", ");
      limits.push(`${who} 시험 결과가 예측보다 ${range(gaps)}%p 높았습니다(놓칠 확률을 낮게 잡는 편)`);
    }
  }
  limits.push("모두 가상 세계 시험이며 실제 항만 자료로는 아직 검증 전입니다.");
  const lim = limits.length > 6 ? [...limits.slice(0, 5), limits[limits.length - 1]] : limits;
  return { good: good.slice(0, 3), limits: lim };
}
