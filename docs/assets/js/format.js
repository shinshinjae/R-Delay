// 화면 표기 규칙: 이름 상수와 시각·시간·확률·금액 형식. 엔진 결과는 바꾸지 않고 읽기 쉬운 말로만 옮긴다.

export const NODE_NAME = { AT: "터미널 A(가상)", PN: "신항 다른 터미널", L: "게이트·도로(양산·울산)", A: "항공 반입(인천)" };
export const NODE_GROUP = { AT: "바다(신항)", PN: "바다(신항)", L: "육상", A: "항공" };

export const SCENARIO_CHIP = {
  base: "평시", terminal_failure: "장비 고장", typhoon_diversion: "태풍 우회 물량",
  holiday_surge: "명절 물량", epidemic: "감염병·결근", h2_trucks: "수소 트럭 배차",
};
export const SCENARIO_LINE = {
  base: "특별한 사건이 없는 평일 · 배경 지연만 있음",
  terminal_failure: "터미널 A(가상) 시스템 오류 · 16:00까지 처리능력 절반 · 18:00~23:30 수출 반입 제한 공지",
  typhoon_diversion: "북중국 항만 폐쇄로 우회 선박이 몰려 신항 물동량 1.6배",
  holiday_surge: "명절 전 반출입 집중 · 게이트·도로 물동량 1.8배, 신항 1.25배",
  epidemic: "항만·물류 근로자 집단감염 · 출근율 75%",
  h2_trucks: "평시 조건에서 디젤과 수소 트럭 배차안 비교(충전 제약 포함)",
};
export const CARGO_SHORT = {
  import_factory: "양산 공장 원자재", ulsan_parts: "울산 완성차 부품", sea_air: "Sea&Air 인천", ulsan_export: "울산 부품 수출",
};

export const pct = (v, d = 0) => (v == null || !isFinite(v) ? "–" : `${(v * 100).toFixed(d)}%`);
export const num = (v, d = 2) => (v == null || !isFinite(v) ? "–" : Number(v).toFixed(d));

// t는 판단 시각(10:00) 기준 시간. 날짜가 넘어가면 「어제 」「내일 」을 붙인다.
function clockMinutes(totalMin, withDay) {
  const day = totalMin < 0 ? "어제 " : totalMin >= 1440 ? "내일 " : "";
  const m = ((Math.round(totalMin) % 1440) + 1440) % 1440;
  return `${withDay ? day : ""}${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}
export function clock(t, withDay = true) {
  if (t == null || !isFinite(t)) return "예측 범위 밖";
  return clockMinutes(Math.round((10 + t) * 60), withDay);
}
// 10분 단위 반올림 / 올림. 「늦으면」 쪽 시각은 늘 올림으로 쓴다.
export function clock10(t, withDay = true) {
  if (t == null || !isFinite(t)) return "예측 범위 밖";
  return clockMinutes(Math.round(((10 + t) * 60) / 10) * 10, withDay);
}
export function clock10ceil(t, withDay = true) {
  if (t == null || !isFinite(t)) return "예측 범위 밖";
  return clockMinutes(Math.ceil(((10 + t) * 60) / 10 - 1e-9) * 10, withDay);
}
export const hourLabel = (t) => {
  const total = 10 + t;
  const h = ((total % 24) + 24) % 24;
  return `${total >= 24 && h === 0 ? 24 : Math.round(h)}시`;
};
export function tickLabel(t) {
  const total = 10 + t;
  const h = ((total % 24) + 24) % 24;
  const day = total < 0 ? "어제 " : total >= 24 ? "내일 " : "";
  return `${day}${Math.round(h)}시`;
}

// 시간(h) → 「N분」「H시간」「H시간 M분」. 5분 단위 반올림 / 올림.
function durText(min) {
  if (min < 60) return `${min}분`;
  const h = Math.floor(min / 60), m = min % 60;
  return m ? `${h}시간 ${m}분` : `${h}시간`;
}
export function fmtDur(h) {
  if (h == null || !isFinite(h)) return "–";
  return durText(Math.max(5, Math.round((h * 60) / 5) * 5));
}
export function fmtDurCeil(h) {
  if (h == null || !isFinite(h)) return "–";
  return durText(Math.max(5, Math.ceil((h * 60) / 5 - 1e-9) * 5));
}
// 두 소요의 범위: 「약 45~50분」「약 55분~1시간」「약 1시간 10~15분」
export function fmtDurRange(a, b) {
  const x = fmtDur(a), y = fmtDur(b);
  if (x === y) return `약 ${x}`;
  const ma = Math.max(5, Math.round((a * 60) / 5) * 5), mb = Math.max(5, Math.round((b * 60) / 5) * 5);
  if (ma < 60 && mb < 60) return `약 ${ma}~${mb}분`;
  if (ma >= 60 && mb >= 60 && Math.floor(ma / 60) === Math.floor(mb / 60) && ma % 60 && mb % 60) return `약 ${Math.floor(ma / 60)}시간 ${ma % 60}~${mb % 60}분`;
  return `약 ${x}~${y}`;
}

// "2:15"처럼 띠 칸에 넣는 짧은 소요(5분 반올림)
export function fmtDurShort(h) {
  if (h == null || !isFinite(h)) return "";
  const min = Math.max(5, Math.round((h * 60) / 5) * 5);
  return `${Math.floor(min / 60)}:${String(min % 60).padStart(2, "0")}`;
}

// 확률: 99.5% 이상은 「거의 확실」, 1% 미만은 「1% 미만」, 나머지는 정수 %.
export function fmtP(p) {
  if (p == null || !isFinite(p)) return "–";
  if (p >= 0.995) return "거의 확실";
  if (p < 0.01) return "1% 미만";
  return `${Math.round(p * 100)}%`;
}
export function fmtWon(v) {
  if (v == null || !isFinite(v)) return "–";
  const k = Math.round(v / 1000); // 천 원 단위로 먼저 반올림한 뒤 단위를 고른다(9,600원 → 약 1만 원)
  if (Math.abs(k) >= 10) return `약 ${Math.round(v / 10000).toLocaleString("ko-KR")}만 원`;
  if (Math.abs(k) >= 1) return `약 ${k}천 원`;
  return `${Math.round(v)}원`;
}
export const fmtRd = (v) => num(v, 2);
export const fmtRatio = (x) => `${num(x, 1)}배`;
export const fmtCap = (c) => `평소의 ${Math.round(c * 100)}%`;
export const asOfText = (run) => {
  const m = /(\d{1,2}:\d{2})/.exec(run.meta.now_label || "");
  return m ? m[1] : "10:00";
};
export const nextUpdateText = (run) => clock((run.meta.params && run.meta.params.obs_every_h) || 2);
