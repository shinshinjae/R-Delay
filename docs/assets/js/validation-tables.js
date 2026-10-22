// 검증 표 4개와 AI 보조 모듈 표. method.html(작동 원리·검증)이 쓰며, DOM 요소를 인자로 받으므로 다른 화면에서도 다시 쓸 수 있다.
// 데이터는 validation.json · ai_model.json · precomputed.json의 presets만 읽는다(장면별 실행 결과는 쓰지 않음).
// 머리글은 쉬운 말로 쓰고, 원래 전문 용어 머리글은 th의 title에 남긴다.

// ---------- 작은 형식 함수(이 파일 전용) ----------
const pct = (v, d = 0) => (v == null || !isFinite(v) ? "–" : `${(v * 100).toFixed(d)}%`);
const num = (v, d = 2) => (v == null || !isFinite(v) ? "–" : Number(v).toFixed(d));

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function cell(tr, content, cls, tag = "td") {
  const c = document.createElement(tag);
  if (cls) c.className = cls;
  if (content instanceof Node) c.appendChild(content);
  else c.textContent = content;
  tr.appendChild(c);
  return c;
}

// head: [쉬운 말, 원래 머리글] 쌍 또는 문자열. 본문 첫 칸은 행 머리(th scope=row)로 그린다.
function table(tbl, head, rows, numCols = []) {
  tbl.replaceChildren();
  const thead = tbl.createTHead().insertRow();
  head.forEach((h, i) => {
    const [plain, orig] = Array.isArray(h) ? h : [h, null];
    const th = cell(thead, plain, [numCols.includes(i) ? "num" : null, plain.length > 28 ? "long" : null].filter(Boolean).join(" ") || null, "th");
    th.scope = "col";
    if (orig && orig !== plain) th.title = orig;
  });
  const body = tbl.createTBody();
  for (const r of rows) {
    const tr = body.insertRow();
    if (r.cls) tr.className = r.cls;
    r.cells.forEach((v, i) => {
      const c = cell(tr, v, [numCols.includes(i) ? "num" : null, r.wrap?.includes(i) ? "wrap" : null].filter(Boolean).join(" ") || null, i === 0 ? "th" : "td");
      if (i === 0) c.scope = "row";
    });
  }
  return body;
}

// ---------- 이름(상황판과 같은 화면 이름) ----------
export const SCENARIO_NAME = {
  base: "평시", terminal_failure: "장비 고장", typhoon_diversion: "태풍 우회 물량",
  holiday_surge: "명절 물량", epidemic: "감염병·결근", h2_trucks: "수소 트럭 배차",
};
// ai_model.json의 coef 행 순서는 엔진 거점 순서(AT, PN, L, A)와 같다.
export const NODE_ORDER = ["AT", "PN", "L", "A"];
export const NODE_NAME = { AT: "터미널 A(가상)", PN: "신항 다른 터미널", L: "게이트·도로(양산·울산)", A: "항공 반입(인천)" };
const CARGO_FALLBACK = {
  import_factory: "수입 원자재 → 양산 공장 야간 라인", ulsan_parts: "수입 부품 → 울산 완성차 라인",
  sea_air: "Sea&Air 환적 → 인천공항 항공 반입", ulsan_export: "울산 자동차부품 수출 → 신항 선적 반입",
};

const scenarioName = (key, v) => SCENARIO_NAME[key] || v.label;
const lead = (x) => (x == null || x.median == null ? "–" : `${num(x.median, 1)}h (${num(x.q25, 1)}~${num(x.q75, 1)})`);
const lab = (x) => (x ? `${x[0]}/${x[1]}` : "–");

// ---------- 표 1. 경보·원인·확률 ----------
export const VAL_HEAD = [
  ["상황", "시나리오"],
  ["번짐 경보 맞힘·헛경보·놓침 / 시험에서 번진 횟수", "확산 경보 적중·오경보·미탐 / 참 확산"],
  ["첫 경보가 적체보다 앞선 시간(보통, 가운데 절반 범위)", "첫 경보 선행시간 중앙값(사분위)"],
  ["놓친 적체 / 적체 횟수", "적체 놓침"],
  ["경보 뒤 12시간 안에 적체·번짐 모두 없음 / 경보 횟수", "경보 뒤 12h 안 사건 없음"],
  ["원인 판정 정확도(단정한 경우, '번지지 않음' 일치 포함) · 보류(번짐 경계/원인)", "원인 정확도(단정, 비확산 일치 포함) · 보류(확산/원인)"],
  ["시험의 정답 원인별로 맞힌 횟수(물량 · 처리능력)", "참 원인별 재현(물량 · 처리능력)"],
  ["확률 예측 점수(0보다 크면 단순 기준보다 나음)", "Brier 기술점수"],
  ["80% 범위가 정답을 담은 비율(목표 80%): 24시간 / 판단 시각", "80% 포함률 24h / 판단 시각"],
  ["판단 시각 치우침(추정 − 정답)", "판단 시각 편향"],
  ["AI 보조 끔→켬 오차", "AI 끔→켬 RMSE"],
];

export function drawValTable(tbl, VALID) {
  const entries = Object.entries(VALID.scenarios);
  return table(tbl, VAL_HEAD, entries.map(([key, v]) => {
    const a = v.spread_alarm;
    return { cells: [scenarioName(key, v), `${a.tp} · ${a.fp} · ${a.fn} / ${a.tp + a.fn}`, lead(v.lead_time_h), `${v.missed}/${v.events}`,
      `${v.false_alarms}/${v.alarms}`,
      `${v.cause_accuracy_decided == null ? "–" : pct(v.cause_accuracy_decided)} · ${v.hold_spread}/${v.hold_cause}`,
      `${lab(v.label_recall["물량 주도형"])} · ${lab(v.label_recall["처리능력형"])}`,
      key === "base" ? `${num(v.brier_skill, 2)}(불안정)` : num(v.brier_skill, 2), `${pct(v.coverage_80_mean)} / ${pct(v.coverage_80_now)}`,
      `${v.bias_now_mean >= 0 ? "+" : ""}${num(v.bias_now_mean, 2)}`,
      `${num(v.rmse_filter_ai_off_mean, 3)} → ${num(v.rmse_filter_ai_on_same_worlds, 3)}`] };
  }), [7]);
}

// 표 1 아래 설명(기존 #val-note 문장 그대로)
export function valNoteText(VALID) {
  return `세계 ${VALID.worlds}개(난수 ${VALID.world_seeds[0]}~${VALID.world_seeds[1]}). 선행시간은 첫 경보(확산 또는 적체)가 참 적체 시작보다 몇 시간 앞섰는지입니다(음수는 늦음). ` +
      "Brier 기술점수는 시나리오별 기후값 대비이며 평시는 참 확산 비율이 약 0.5%라 값이 불안정합니다. 판단 시각 편향은 R_d 추정 중앙값 − 참값의 평균입니다. 원인 정확도에는 ‘비확산’끼리 맞은 경우가 포함되므로 정답 원인별로 맞힌 횟수(참 원인별 재현)를 함께 보십시오. " +
      "참 R_d가 1 근처에 머무는 태풍·명절 장면은 판단 시각의 확산 경보가 자주 틀리므로 보류 구간을 둡니다. 장비 고장 장면은 판단 시각 구간이 참값을 덜 포함하고(하향 편향), 다른 장면은 목표 80%보다 넓습니다.";
}

// ---------- 표 2. 경보는 무엇 덕분인가 ----------
export const NOTICE_HEAD = [
  ["상황", "시나리오"],
  ["R‑Delay 첫 경보가 앞선 시간", "R-Delay 첫 경보 선행시간"],
  ["공지만 보고 바로 경보하는 규칙", "공지 즉시 경보 규칙"],
  ["공지를 가리고 관측만 쓴 경우(경보가 울린 세계만) · 경보 없음", "공지를 가린 관측 단독(경보가 울린 세계만)"],
  ["판단 시각에 관측만 쓴 번짐 경보 맞힘 · 놓침", "관측 단독 판단 시각 확산 경보 적중·미탐"],
  ["확률 예측 점수: R‑Delay / 공지 규칙 / 공지 가림", "P(R_d>1) Brier 기술점수: R-Delay / 공지 지시 규칙 / 공지 가림"],
];

export function drawNoticeTable(tbl, VALID) {
  const rows = Object.entries(VALID.scenarios).filter(([, v]) => v.lead_time_notice_rule_h).map(([key, v]) => ({
    cells: [scenarioName(key, v), lead(v.lead_time_h), lead(v.lead_time_notice_rule_h),
      `${lead(v.lead_time_masked_h)} · 경보 없음 ${v.masked_no_alarm}/${v.masked_worlds}`,
      v.masked_alarm_tp_fn ? `${v.masked_alarm_tp_fn[0]} · ${v.masked_alarm_tp_fn[1]}` : "–",
      `${num(v.brier_skill, 2)} / ${num(v.brier_skill_notice_rule, 2)} / ${num(v.brier_skill_masked, 2)}`],
  }));
  return table(tbl, NOTICE_HEAD, rows);
}

// ---------- 표 3. 기준 전파율을 틀리게 주면 ----------
export const MISSPEC_HEAD = [
  ["상황", "시나리오"],
  ["기준 전파율 1.3배: 맞힘·헛경보·놓침 | 범위 포함률 24시간 / 판단 시각", "β̄ ×1.3: 적중·오경보·미탐 | 포함률 24h / 판단 시각"],
  ["기준 전파율 0.7배: 맞힘·헛경보·놓침 | 범위 포함률 24시간 / 판단 시각", "β̄ ×0.7: 적중·오경보·미탐 | 포함률 24h / 판단 시각"],
];

export function drawMisspecTable(tbl, VALID) {
  const rows = Object.entries(VALID.scenarios).map(([key, v]) => {
    const m13 = v.misspec["beta_bar_x1.3"], m07 = v.misspec["beta_bar_x0.7"];
    const f = (m) => (m ? `${m.alarm_tp_fp_fn.join(" · ")} | ${pct(m.coverage_80)} / ${pct(m.inside_now)}` : "–");
    return { cells: [scenarioName(key, v), f(m13), f(m07)] };
  });
  return table(tbl, MISSPEC_HEAD, rows);
}

// ---------- 표 4. 화물 ----------
export const CARGO_HEAD = [
  ["상황", "시나리오"],
  ["화물", "화물"],
  ["지금 계획대로면: 예측한 놓칠 확률 / 시험에서 놓친 비율", "현행 계획 실패 예측 / 실현"],
  ["예측 점수: 같은 상황 평균 대비", "기술점수: 기후값 대비"],
  ["예측과 시험 결과의 상관", "예측·실현 상관"],
  ["예측 점수: '항상 0' / 공지 반영 도착예정 대비", "기술점수: 항상 0 / 공지 반영 ETA 대비"],
  ["R‑Delay 판단대로 했을 때 시험에서 놓친 비율(추천 변경 비율, 체류 변화)", "R-Delay 추천 따른 실현 실패"],
  ["화주 안내 비율 · 안내가 맞은 비율 · 실패 중 안내한 비율", "화주 통보 비율 · 정밀도 · 재현율"],
];

// cargoPresets: precomputed.json의 presets.cargo(화물 순서와 이름). 없으면 이 파일의 기본 이름을 쓴다.
export function drawCargoTable(tbl, VALID, cargoPresets) {
  const entries = Object.entries(VALID.scenarios);
  const names = cargoPresets ? Object.keys(cargoPresets) : Object.keys(CARGO_FALLBACK);
  const label = (n) => cargoPresets?.[n]?.label || CARGO_FALLBACK[n] || n;
  const crow = [];
  for (const [key, v] of entries) {
    if (key === "base") continue;
    for (const n of names) {
      const c = v.cargo[n];
      if (!c) continue;
      crow.push({ wrap: [1], cells: [scenarioName(key, v), label(n), `${pct(c.predicted_fail_plan, 1)} / ${pct(c.realized_fail_plan, 1)}`,
        c.skill_vs_climatology == null ? "–" : num(c.skill_vs_climatology, 2), c.corr_pred_realized == null ? "–" : num(c.corr_pred_realized, 2),
        `${c.skill_vs_always_zero == null ? "–" : num(c.skill_vs_always_zero, 2)} / ${c.skill_vs_notice_eta == null ? (c.brier_notice_eta === 0 && c.realized_fail_plan > 0 ? "규칙도 정답" : "–") : num(c.skill_vs_notice_eta, 2)}`,
        `${pct(c.realized_fail_rdelay, 1)} (추천 변경 ${pct(c.changed_share)}${c.realized_stay_change_h == null ? "" : `, 체류 ${c.realized_stay_change_h >= 0 ? "+" : ""}${num(c.realized_stay_change_h, 1)}h`})`,
        `${pct(c.notice_share)} · ${c.notice_precision == null ? "–" : pct(c.notice_precision)} · ${c.notice_recall == null ? "–" : pct(c.notice_recall)}`] });
    }
  }
  return table(tbl, CARGO_HEAD, crow, [3, 4]);
}

// 표 4 아래 설명. 같은 상황 안의 판별력 범위는 표에 보이는 값(소수 2자리)에서 직접 계산한다.
function cargoRange(VALID, field) {
  const vals = [];
  for (const [key, v] of Object.entries(VALID?.scenarios || {})) {
    if (key === "base") continue;
    for (const c of Object.values(v.cargo || {})) if (c[field] != null && isFinite(c[field])) vals.push(Number(num(c[field], 2)));
  }
  if (!vals.length) return null;
  const f = (x) => x.toFixed(2).replace("-", "−");
  return `${f(Math.min(...vals))}~${f(Math.max(...vals))}`;
}
export function valCargoNoteText(VALID) {
  const sk = cargoRange(VALID, "skill_vs_climatology"), co = cargoRange(VALID, "corr_pred_realized");
  const span = sk && co ? `(이 표 16행 전체 기준 예측 점수 ${sk}, 상관 ${co})` : "";
  return "‘항상 0’은 공지를 반영하지 않는 현행 방식(도착예정시각 + 고정 여유 2시간)이 이 장면들에서 모두 실패 0으로 예측했다는 뜻입니다. 공지를 반영한 도착예정시각 규칙은 수입 화물에서는 같은 결과였고, 반입 제한이 공지된 수출 화물에서는 실패를 정확히 맞혔습니다(‘규칙도 정답’). " +
      `같은 상황 평균 대비 예측 점수와 예측·시험 결과 상관이 작다는 것은${span}, 사건 수준의 위험은 맞히지만 같은 사건 안에서 세계별로 누가 놓칠지는 아직 잘 가르지 못한다는 뜻입니다. ` +
      "충격 장면에서는 모든 세계에 안내해 실패 중 안내한 비율이 100%, 안내가 맞은 비율 = 그 장면의 놓친 비율이 되고, 공지가 없는 명절 장면에서는 안내가 선택적입니다. " +
      "‘판단대로 했을 때’는 ‘추정 불확실’·‘추천 없음’이면 지금 계획을 유지한 것으로 셉니다. 수입 화물은 컨테이너가 늦게 나와 어느 시각에 가도 예측 위험이 같아 R-Delay가 대부분 시간 변경을 권하지 않았으므로(명절 Sea&Air 제외) 그 칸은 지금 계획의 결과와 같습니다 — 쓸모는 화주에게 미리 알리는 데 있습니다. " +
      "수출 화물의 개선(장비 고장 100% → 11.7%, 그 안에 ‘추정 불확실’로 지금 계획을 유지한 세계 10% 포함)은 공지된 반입 제한을 출발 시각으로 옮긴 결과로 ‘공지 규칙 + 출발 앞당기기’로도 얻을 수 있습니다. 명절 Sea&Air의 앞당김은 시험에서 놓친 비율을 줄이지 못하고 체류만 늘렸습니다.";
}

// ---------- AI 보조 모듈(기존 #ai-info 그대로) ----------
export const AI_HEAD = [
  ["거점", "거점"],
  ["풍속", "로그 전파율 평균에 대한 풍속 계수"],
  ["파고", "로그 전파율 평균에 대한 파고 계수"],
  ["주말", "로그 전파율 평균에 대한 주말 계수"],
  ["장비 고장", "로그 전파율 평균에 대한 장비 고장 계수"],
  ["장비 계수/참값", "학습한 장비 고장 계수 ÷ 합성 참값 계수"],
  ["z̄ 오차 AI", "AI가 예측한 로그 전파율 평균과 참값의 오차(RMSE, 평가 18일)"],
  ["z̄ 오차 AI 없음", "AI 없이 0으로 둘 때의 오차(RMSE, 평가 18일)"],
  ["R²(평활 레이블)", "평활 레이블 대비 설명력"],
];

export function drawAiInfo(box, MODEL) {
  box.replaceChildren();
  if (!MODEL) {
    box.appendChild(el("p", "m-error", "AI 모듈 학습 정보(ai_model.json)를 불러오지 못했습니다."));
    return;
  }
  box.appendChild(el("p", null, `${MODEL.description} 자료: ${MODEL.data}.`));
  const t = document.createElement("table");
  t.className = "m-table";
  t.setAttribute("aria-label", "AI 전파율 모듈 거점별 계수와 평가 지표");
  table(t, AI_HEAD,
    NODE_ORDER.map((id, k) => {
      const m = MODEL.metrics_test?.[id] || {};
      const coef = MODEL.coef?.[k] || [];
      return { cells: [NODE_NAME[id], ...coef.map((c) => num(c, 3)), num(m.equip_coef_ratio, 2), num(m.rmse_vs_true_mean, 3), num(m.rmse_zero_model, 3), num(m.r2_vs_smoothed_label, 2)] };
    }), [1, 2, 3, 4, 5, 6, 7, 8]);
  const w = el("div", "table-wrap m-table-wrap");
  w.tabIndex = 0;
  w.setAttribute("role", "region");
  w.setAttribute("aria-label", "AI 모듈 표(옆으로 밀어 볼 수 있음)");
  w.appendChild(t);
  box.appendChild(w);
  box.appendChild(el("p", null, "학습형 모듈은 기상·장비 상태로 전파율의 사전평균을 맞추는 보조 역할입니다. 평활 레이블이 사전분포 쪽으로 수축해 장비 고장 계수가 참값의 29~60%로 작게 추정되므로, 장비 고장·출근율처럼 운영자가 아는 처리능력 정보는 AI가 아니라 해소율 γ에 직접 입력합니다. ‘z̄ 오차’는 참값을 아는 합성 자료에서만 잴 수 있습니다."));
}
