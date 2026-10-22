// 화면 문구 규칙(docs/assets/js/derive.js) 점검: node --test tests/*.test.mjs
// 미리 계산된 6개 장면에서 배너·할 일·화물 분류·안내 문구가 엔진 출력과 어긋나지 않는지,
// 그리고 확률을 단정문으로 바꾸거나 시험 결과를 과장하지 않는지 본다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  statusView, todoView, classifyCargo, cargoView, cargoDetail, noticeText, driverAdvice, trustLines, sortedCargo, CLASS_ORDER,
  trendLine, nodeView, topic, sameIfEarly,
} from "../docs/assets/js/derive.js";
import { clock10, clock10ceil, fmtDur, fmtDurCeil, fmtP, fmtWon, fmtDurRange } from "../docs/assets/js/format.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "docs", "data");
const PRE = JSON.parse(readFileSync(join(root, "precomputed.json"), "utf8"));
const VALID = JSON.parse(readFileSync(join(root, "validation.json"), "utf8"));
const R = PRE.runs;
const allText = (run) => {
  const st = statusView(run);
  const parts = [st.title, st.cause ? st.cause.text : "", st.outlook, todoView(run).text, trendLine(run)];
  for (const k of Object.keys(run.cargo)) {
    const v = cargoView(run, k, VALID);
    parts.push(v.action, v.reason, v.note || "", v.notice || "");
  }
  const t = trustLines(VALID, run);
  return parts.concat(t.good, t.limits).join("\n");
};

test("배너 제목: 경보 단계, 판단 경계는 확률로", () => {
  assert.equal(statusView(R.base).title, "평소 수준입니다 · 막힌 곳 없음");
  assert.equal(statusView(R.terminal_failure).title, "지연이 번지는 중 · 막힌 곳: 터미널 A(가상)");
  assert.equal(statusView(R.typhoon_diversion).title, "지연이 번지기 시작했습니다 · 아직 막힌 곳 없음");
  assert.equal(statusView(R.epidemic).title, "막힌 곳: 게이트·도로(양산·울산) · 번짐 신호 약함");
  const h = statusView(R.holiday_surge);
  assert.equal(h.hold, true);
  assert.equal(h.icon, "hold");
  assert.equal(h.level, "warning", "판단 경계 장면이 확산 경보 장면보다 강한 색이면 안 된다");
  assert.equal(h.title, "지연이 번질 수 있음(60% · 판단 경계) · 막힌 곳: 게이트·도로(양산·울산)");
});

test("원인 줄: 처리능력·물량·보류·막힌 이유", () => {
  assert.match(statusView(R.terminal_failure).cause.text, /^처리능력 저하\(터미널 A\(가상\) 평소의 50%\) — 물량 탓 아님/);
  assert.match(statusView(R.typhoon_diversion).cause.text, /^물량 증가\(신항 물동량 추정 1\.5배\)/);
  const h = statusView(R.holiday_surge).cause;
  assert.equal(h.hold, true);
  assert.match(h.text, /아직 단정하기 어렵습니다 — 다음 관측\(12:00\) 후 다시 판단/);
  assert.equal(statusView(R.epidemic).cause.label, "막힌 이유");
  assert.equal(statusView(R.base).cause, null);
});

test("원인 라벨에 덧붙은 말(혼합 등)이 있어도 원인을 잃지 않는다", () => {
  const run = structuredClone(R.terminal_failure);
  run.now.cause = "처리능력형(θ에 따라 달라짐: 혼합)";
  const c = statusView(run).cause;
  assert.equal(c.label, "원인");
  assert.match(c.text, /거점 규모 가정에 따라 판정이 달라질 수 있음/);
});

test("전망: 멈춤은 90% 상한, 막힘은 길면 시각을 함께", () => {
  assert.equal(statusView(R.terminal_failure).outlook,
    "새 지연이 늘어나는 건 16:00 이후 멈출 전망 · 터미널 A(가상) 막힘은 16:00쯤 가장 심했다가 20:30쯤(길면 23:30) 풀릴 전망");
  assert.equal(statusView(R.base).outlook, "앞으로 24시간 큰 변화 없을 전망");
  assert.match(statusView(R.holiday_surge).outlook, /24시간 끝까지 계속 심해질 전망/);
  assert.match(statusView(R.typhoon_diversion).outlook, /터미널 A\(가상\)는 15:00쯤부터 막힐 수 있음/);
});

test("24시간 카드: 중앙값만으로 '1 아래에 머물'을 말하지 않는다", () => {
  assert.equal(trendLine(R.base), "24시간 내내 1 아래에 머물 전망입니다");
  const e = trendLine(R.epidemic);
  assert.ok(!e.includes("1 아래에 머물"), e);
  assert.match(e, /번질 확률 최대 \d+%/);
});

test("할 일 요약", () => {
  assert.equal(todoView(R.base).text, "오늘 화물 4건 모두 계획대로 진행 가능");
  assert.equal(todoView(R.terminal_failure).text, "오늘 화물 4건 — 시간 변경 1 · 화주 안내 3");
  assert.equal(todoView(R.holiday_surge).text, "오늘 화물 4건 — 시간 변경 1 · 다시 확인 1 · 계획대로 2");
  assert.equal(todoView(R.h2_trucks).text, "오늘 화물 4건 모두 계획대로 진행 가능 · 수소 트럭 가능 3건 · 불가 1건");
});

test("화물 분류는 엔진 판단과 맞는다(6장면 × 4화물)", () => {
  for (const [s, run] of Object.entries(R)) {
    for (const key of Object.keys(run.cargo)) {
      const { cls, cargo: c } = classifyCargo(run, key);
      const st = c.decision.status;
      if (st === "no_acceptable_action") assert.ok(cls === "notify" || cls === "hold-conservative", `${s}/${key}`);
      else if (st === "uncertain") assert.equal(cls, "hold-uncertain", `${s}/${key}`);
      else assert.ok(["ok", "change", "optional"].includes(cls), `${s}/${key}`);
      if (cls === "change") assert.ok(run.ranking.find((r) => r.cargo === key).plan_p_upper > c.epsilon, `${s}/${key}`);
    }
  }
  assert.equal(classifyCargo(R.terminal_failure, "ulsan_export").cls, "change");
  assert.equal(classifyCargo(R.holiday_surge, "ulsan_parts").cls, "hold-conservative");
  assert.equal(classifyCargo(R.h2_trucks, "import_factory").cls, "optional");
});

test("화물 카드: 확률을 단정문으로 바꾸지 않는다", () => {
  for (const run of Object.values(R)) {
    for (const key of Object.keys(run.cargo)) {
      const v = cargoView(run, key, VALID);
      assert.ok(!v.pill.includes("해결"), "「해결」이라고 쓰지 않는다");
      assert.ok(!/맞추기 어렵습니다/.test(v.action), v.action);
      if (v.cls === "notify" && v.p < 0.5) assert.match(v.action, /허용 위험 \d+%를 넘습니다\(가장 낮아도 [\d.]+%\)/);
    }
  }
  const sa = cargoView(R.holiday_surge, "sea_air", VALID);
  assert.equal(sa.cls, "change");
  assert.match(sa.reason, /오차를 감안하면 13%로 허용 위험 10%를 넘을 수 있음 → 17:30이면 9\.8% 이내/);
  assert.match(sa.note, /가상 시험에서는 바꿔도 놓친 비율이 같았습니다\(10%→10%\)/);
});

test("화물 순서: 급한 것부터, 화주 안내는 놓칠 확률이 큰 순서", () => {
  for (const run of Object.values(R)) {
    const keys = sortedCargo(run);
    const order = keys.map((k) => CLASS_ORDER.indexOf(classifyCargo(run, k).cls));
    assert.deepEqual(order, order.slice().sort((a, b) => a - b));
    const ps = keys.filter((k) => classifyCargo(run, k).cls === "notify").map((k) => classifyCargo(run, k).least.p_fail);
    assert.deepEqual(ps, ps.slice().sort((a, b) => b - a));
  }
});

test("화주 안내 문구: 가상 표시, 엔진과 같은 확률·시각, 중복·계산 용어 없음", () => {
  for (const [s, run] of Object.entries(R)) {
    for (const key of Object.keys(run.cargo)) {
      const v = cargoView(run, key, VALID);
      if (!v.canCopy) { assert.equal(v.notice, null); continue; }
      const t = noticeText(run, key);
      assert.ok(t.startsWith("[시연용 가상 데이터 예시]"), `${s}/${key}`);
      assert.ok(!t.includes("부산신항 신항") && !t.includes("넉넉히") && !t.includes("부산신항 게이트"), t);
      const { least, cargo: c } = classifyCargo(run, key);
      const engineP = /실패확률 (\d+)%/.exec(c.notice || "");
      if (engineP && v.cls === "notify") assert.ok(t.includes(`약 ${engineP[1]}%`), `${s}/${key}: ${t} vs ${c.notice}`);
      assert.ok(t.includes(`${clock10(least.arrival_q50)} 전후`) && t.includes(`늦으면 ${clock10ceil(least.arrival_q90)}`), `${s}/${key}`);
    }
  }
});

test("펼친 카드: 같은 위험 시각은 묶고, 우선 하역은 승인 여부로 나눈다", () => {
  const d = cargoDetail(R.terminal_failure, "import_factory");
  assert.equal(d.options[0].name, "터미널 도착 16:00~21:30");
  assert.ok(d.options[0].tags.includes("지금 계획"));
  const pr = d.options.find((o) => o.tags.includes("우선 하역 요청"));
  assert.match(pr.text, /^승인되면 19% · 안 되면 44%\(반반 가정 32%\)$/);
  assert.ok(!d.why.some((w) => /충분|여유 부족/.test(w)));
  assert.ok(cargoDetail(R.terminal_failure, "ulsan_export").why.some((w) => /반입 금지/.test(w)));
});

test("기사님 안내: 컨테이너 반출과 반입 금지를 반영한다", () => {
  const d = driverAdvice(R.terminal_failure);
  const imp = d.rows.find((r) => r.key === "import"), ex = d.rows.find((r) => r.key === "export");
  assert.equal(imp.value, "21:40 이후 도착");
  assert.equal(ex.value, "18:00 전 도착");
  for (const c of d.cells) {
    if (c.best) assert.ok(!c.early, `컨테이너 나오기 전 칸(${c.label})을 권하지 않는다`);
    const m = Math.round((c.q50 * 60) / 5) * 5;
    assert.equal(c.level, m <= 60 ? "good" : m <= 120 ? "warning" : "critical", c.label);
  }
  assert.equal(d.cells.length, 15);
  const t = driverAdvice(R.typhoon_diversion);
  assert.equal(t.rows.find((r) => r.key === "import").value, "18:30 이후 도착");
  assert.ok(driverAdvice(R.holiday_surge).roadWarn, "게이트·도로가 막히면 기사 카드에 경고");
  assert.equal(driverAdvice(R.base).flat, true);
});

test("믿을 수 있나요: 공지 덕분인 성과는 한계로, 마지막 줄 고정, '실제로' 쓰지 않음", () => {
  for (const run of Object.values(R)) {
    const t = trustLines(VALID, run);
    assert.equal(t.limits.at(-1), "모두 가상 세계 시험이며 실제 항만 자료로는 아직 검증 전입니다.");
    for (const line of [...t.good, ...t.limits.slice(0, -1)]) assert.ok(!line.includes("실제로"), line);
  }
  const tf = trustLines(VALID, R.terminal_failure);
  assert.ok(tf.limits.some((l) => /59회 중 53회 경보.*운영사 공지 덕분/.test(l)));
  assert.ok(tf.limits.some((l) => /100% → 12% — 공지된 반입 금지만 읽어도/.test(l)));
  assert.ok(tf.good.some((l) => /현행 방식\(도착예정시각\+여유 2시간\)/.test(l)));
  assert.match(trustLines(VALID, R.h2_trucks).limits[0], /따로 검증하지 않았습니다/);
});

test("거점: 기준 바로 위는 그렇게 표시", () => {
  const v = nodeView(R.holiday_surge, 2);
  assert.equal(v.word, "막힘(기준 바로 위)");
  assert.ok(v.range);
  assert.match(nodeView(R.terminal_failure, 0).origin, /번짐 기여 95%\(모형 추정\)/);
});

test("모든 장면 문장에 깨진 표현이 없다", () => {
  for (const [s, run] of Object.entries(R)) {
    const t = allText(run);
    assert.ok(!/있음입니다|천 원.*천 원|약 \d+~\d+시간|undefined|NaN|null/.test(t), `${s}: ${t.match(/.*(있음입니다|undefined|NaN|null).*/)?.[0]}`);
  }
});

test("표기 규칙", () => {
  assert.equal(fmtP(1), "거의 확실");
  assert.equal(fmtP(0.004), "1% 미만");
  assert.equal(fmtP(0.443), "44%");
  assert.equal(fmtDur(2.22), "2시간 15분");
  assert.equal(fmtDurCeil(2.8), "2시간 50분");
  assert.equal(clock10ceil(18.73), "내일 04:50");
  assert.equal(fmtWon(9600), "약 1만 원");
  assert.equal(fmtDurRange(0.92, 1.0), "약 55분~1시간");
  assert.equal(topic("터미널 A(가상)"), "터미널 A(가상)는");
  assert.equal(topic("게이트·도로(양산·울산)"), "게이트·도로(양산·울산)는");
  assert.equal(topic("신항 다른 터미널"), "신항 다른 터미널은");
});

// ---------- 출시 전 점검에서 찾은 경우들(미리 계산한 장면을 고쳐 재현) ----------
const clone = (s) => structuredClone(R[s]);
const cand = (run, key, label) => run.cargo[key].candidates.find((x) => x.label === label);

test("기준 근처: 가장 덜 위험한 안이 계획이 아니면 「계획대로」라고 쓰지 않는다", () => {
  const run = clone("terminal_failure");
  const c = run.cargo.ulsan_export;
  c.epsilon = 0.01;
  c.decision = { status: "no_acceptable_action", conservative: true, least: "출발 16:00", message: "" };
  const v = cargoView(run, "ulsan_export", VALID);
  assert.equal(v.cls, "hold-conservative");
  assert.ok(!v.action.includes("계획대로"), v.action);
  assert.match(v.action, /^지금 계획\(20:00 출발\)대로면 거의 확실히 놓침 → 16:00 출발이면 놓칠 확률 1% 미만/);
  assert.match(v.reason, /^16:00 출발 기준 · 오차를 감안하면/);
  assert.match(noticeText(run, "ulsan_export"), /\(16:00 출발 기준, 마감 /);
  assert.match(cargoDetail(run, "ulsan_export").why.at(-1), /^16:00 출발 기준 최종 도착 예상/);
});

test("기준 근처: 오차 감안 10% 이상이면 「가능성이 높습니다」「낮지만」을 쓰지 않는다", () => {
  assert.match(cargoView(R.holiday_surge, "ulsan_parts", VALID).action, /^계획대로 진행해도 될 가능성이 높습니다\(놓칠 확률 3%\)/);
  const run = clone("holiday_surge");
  const c = run.cargo.ulsan_parts;
  c.epsilon = 0.3;
  Object.assign(c.candidates.find((x) => x.is_plan), { p_fail: 0.27, p_upper: 0.34 });
  const v = cargoView(run, "ulsan_parts", VALID);
  assert.match(v.action, /^지금 계획대로면 놓칠 확률 27% — 허용 위험 30% 근처라/);
  const t = noticeText(run, "ulsan_parts");
  assert.ok(!t.includes("낮지만"), t);
  assert.match(t, /마감을 맞추지 못할 가능성은 약 27%~34%입니다/);
});

test("판단 보류: 다른 시각의 숫자면 그 시각과 계획의 위험을 밝히고, 표본 최대면 다시 계산을 권하지 않는다", () => {
  const run = clone("terminal_failure");
  run.cargo.ulsan_export.decision = { status: "uncertain", least: "출발 16:00", message: "" };
  let v = cargoView(run, "ulsan_export", VALID);
  assert.equal(v.cls, "hold-uncertain");
  assert.match(v.action, /^지금 계획\(20:00 출발\)대로면 거의 확실히 놓침 · 16:00 출발 쪽이 낮아 보이지만 계산이 아직 불확실합니다/);
  assert.match(v.reason, /^16:00 출발이면 놓칠 확률/);
  run.meta.params.particles = 1024;
  v = cargoView(run, "ulsan_export", VALID);
  assert.match(v.action, /표본을 최대로 늘려도 계산 오차가 커서 판단을 미룹니다/);
  assert.ok(!/허용 위험 근처라|화주 안내를 준비/.test(v.action), v.action);
});

test("화주 안내 필요: 허용 위험과 같게 반올림되면 소수 한 자리", () => {
  const run = clone("terminal_failure");
  const { least } = classifyCargo(run, "import_factory");
  run.cargo.import_factory.epsilon = 0.01;
  Object.assign(cand(run, "import_factory", least.label), { p_fail: 0.014 });
  assert.match(cargoView(run, "import_factory", VALID).action, /허용 위험 1%를 넘습니다\(가장 낮아도 1\.4%\)/);
});

test("바꿔도 됨: 조사와 수소 트럭 시각 표기", () => {
  const run = clone("holiday_surge");
  run.cargo.sea_air.epsilon = 0.3;
  run.ranking.find((r) => r.cargo === "sea_air").plan_p_upper = 0.133;
  const v = cargoView(run, "sea_air", VALID);
  assert.equal(v.cls, "optional");
  assert.equal(v.action, "17:30 도착으로 바꿔도 됩니다 · 비용 비슷");
  const h2 = Object.keys(R.h2_trucks.cargo).map((key) => cargoView(R.h2_trucks, key, VALID).action).filter((a) => a.startsWith("수소 트럭으로"));
  assert.ok(h2.length >= 1);
  for (const a of h2) assert.match(a, /^수소 트럭으로 바꿀 수 있음 — \d\d:\d\d (도착|출발)\(/, a);
});

test("전망: 차트에서 빼는 마지막 예측 점과 지금(0번 칸)으로 시각을 말하지 않는다", () => {
  const run = clone("terminal_failure");
  const fq = run.rd.fore_q;
  fq.forEach((q, i) => { q[2] = i === fq.length - 1 ? 0.9 : 1.05; q[3] = 0.6; });
  const o = statusView(run).outlook;
  assert.ok(!o.includes("내일 10:00 이후"), o);
  assert.match(o, /^번짐이 24시간 동안 이어질 수 있습니다/);
  const b = clone("base");
  const k = b.nodes.findIndex((n) => n.id === "A");
  b.x.fore_q50[0][k] = b.x.threshold[k] + 0.01;
  assert.ok(!statusView(b).outlook.includes("10:00쯤부터 막힐 수 있음"), statusView(b).outlook);
});

test("기사님 안내: 수출은 마감 안에 끝나는 칸만, 「이후」 없이", () => {
  const run = clone("holiday_surge");
  for (const g of run.gate) if (g.t >= 13) Object.assign(g, { q50: 0.3, q90: 0.5 });
  const ex = driverAdvice(run).rows.find((r) => r.key === "export");
  assert.equal(ex.value, "23:00");
});

test("믿을 수 있나요: 공지를 가린 경보 수의 뜻과 과소 예측 한계", () => {
  const t = trustLines(VALID, R.terminal_failure);
  assert.ok(t.limits.some((l) => l.includes("공지를 가리면 판단 시각 번짐 경보는 30회 중 4회뿐")), t.limits.join("\n"));
  assert.ok(t.limits.some((l) => /예측보다 \d+(~\d+)?%p 높았습니다/.test(l)), t.limits.join("\n"));
  assert.equal(t.limits.at(-1), "모두 가상 세계 시험이며 실제 항만 자료로는 아직 검증 전입니다.");
});

test("막힘 전망: 지금(0번 칸)으로 풀린다고 하지 않고, 끝 2시간 안에서만 풀리면 신호 없음으로 본다", () => {
  const run = clone("terminal_failure");
  const thr = run.x.threshold[0];
  run.x.fore_q50.forEach((q) => { q[0] = thr - 0.01; });
  run.x.fore_q90.forEach((q) => { q[0] = thr - 0.01; });
  assert.match(statusView(run).outlook, /터미널 A\(가상\) 막힘은 10:30쯤 풀릴 전망/);
  const late = clone("terminal_failure");
  const n = late.t_fore.length;
  late.x.fore_q50.forEach((q, i) => { q[0] = i >= n - 4 ? thr - 0.01 : thr + 0.01; });
  assert.match(statusView(late).outlook, /터미널 A\(가상\) 막힘은 24시간 안에 풀릴 신호가 없습니다/);
});

test("번짐 전망: 한 점만 튄 것은 무시하고, 누그러짐이 훨씬 이르면 그것을 앞세운다", () => {
  const run = clone("terminal_failure");
  run.rd.fore_q.forEach((q, i) => { q[2] = i < 4 ? 1.1 : i === 10 ? 1.02 : 0.9; q[3] = i < 4 ? 0.6 : 0.1; });
  assert.match(statusView(run).outlook, /^새 지연이 늘어나는 건 12:00 이후 멈출 전망/);
  const slow = clone("terminal_failure");
  slow.rd.fore_q.forEach((q, i) => { q[2] = i < 40 ? 1.1 : 0.9; q[3] = i < 2 ? 0.6 : 0.1; });
  assert.match(statusView(slow).outlook, /^새 지연 증가는 11:00쯤부터 누그러질 전망\(늦으면 내일 06:00 이후 멈춤\)/);
});

test("기사님 안내: 수출 마감 — 금지 공지·평평한 날·가능한 칸 없음", () => {
  assert.equal(driverAdvice(R.holiday_surge).rows.find((r) => r.key === "export").value, "22:00까지 아무 때나");
  assert.equal(driverAdvice(R.base).rows.find((r) => r.key === "export").value, "23:00까지 도착");
  const closed = clone("terminal_failure");
  for (const g of closed.gate) g.q90 = 15;
  assert.equal(driverAdvice(closed).rows.find((r) => r.key === "export").value, "마감 전 반입 어려움");
  const open = clone("typhoon_diversion");
  for (const g of open.gate) g.q90 = 15;
  assert.equal(driverAdvice(open).rows.find((r) => r.key === "export").value, "마감 전 반입 어려움");
});

test("판단 보류·기준 근처: 계획과 위험이 같으면 계획으로, 오차만 크면 「근처」라고 하지 않는다", () => {
  const run = clone("terminal_failure");
  const c = run.cargo.ulsan_export;
  const plan = c.candidates.find((x) => x.is_plan);
  Object.assign(cand(run, "ulsan_export", "출발 16:00"), { p_fail: plan.p_fail, p_upper: plan.p_upper });
  c.decision = { status: "uncertain", least: "출발 16:00", message: "" };
  assert.equal(classifyCargo(run, "ulsan_export").least.is_plan, true);
  const far = clone("holiday_surge");
  Object.assign(far.cargo.ulsan_parts.candidates.find((x) => x.is_plan), { p_fail: 0.01, p_upper: 0.3 });
  assert.match(cargoView(far, "ulsan_parts", VALID).action, /^지금 계획대로면 놓칠 확률 1%이지만 계산 오차가 커서/);
  const opt = cargoDetail(R.holiday_surge, "sea_air").options.find((o) => o.tags.includes("지금 계획"));
  assert.equal(opt.status, "계산 불확실");
});

test("화주 안내 필요: 근거 줄과 안내 문구도 허용 위험과 같게 반올림되면 소수 한 자리", () => {
  const run = clone("typhoon_diversion");
  const key = "import_factory";
  const { least } = classifyCargo(run, key);
  run.cargo[key].epsilon = 0.01;
  for (const x of run.cargo[key].candidates) if (x.p_fail < 0.014) x.p_fail = 0.5;
  Object.assign(cand(run, key, least.label), { p_fail: 0.013 });
  const v = cargoView(run, key, VALID);
  assert.match(v.action, /가장 낮아도 1\.3%/);
  if (!sameIfEarly(run, key)) assert.match(v.reason, /놓칠 확률 1\.3%/);
  assert.match(noticeText(run, key), /약 1\.3%입니다/);
});

test("화주 안내 필요: 지금 계획이 더 위험하면 「더 일찍 가도 같다」고 하지 않고 더 나은 시각을 밝힌다", () => {
  const run = clone("typhoon_diversion");
  const key = "import_factory";
  const c = run.cargo[key];
  const plan = c.candidates.find((x) => x.is_plan);
  const early = c.candidates.find((x) => !x.is_plan && !x.negotiation && x.time < plan.time);
  c.decision = { ...c.decision, least: early.label };
  plan.p_fail = Math.min(0.99, early.p_fail + 0.1);
  assert.equal(sameIfEarly(run, key), false);
  assert.match(cargoView(run, key, VALID).reason, /^가장 나은 안: \d\d:\d\d 도착 · 놓칠 확률 \d+%\(지금 계획 20:00 도착이면 \d+%\)$/);
  assert.equal(sameIfEarly(R.typhoon_diversion, key), true);
});

test("기준 근처: 허용 위험을 넘으면 「근처」라 하지 않고, 안내 범위는 1% 미만을 「이하」로", () => {
  const run = clone("holiday_surge");
  Object.assign(run.cargo.ulsan_parts.candidates.find((x) => x.is_plan), { p_fail: 0.09, p_upper: 0.2 });
  assert.match(cargoView(run, "ulsan_parts", VALID).action, /^지금 계획대로면 놓칠 확률 9%로 허용 위험 5%를 넘을 수 있습니다/);
  Object.assign(run.cargo.ulsan_parts.candidates.find((x) => x.is_plan), { p_fail: 0.004, p_upper: 0.06 });
  assert.match(noticeText(run, "ulsan_parts"), /지연 가능성은 낮지만\(6% 이하\)/);
});
