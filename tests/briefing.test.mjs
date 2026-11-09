// 브리핑 초안 규칙(docs/assets/js/briefing.js) 점검: node --test tests/*.test.mjs
// 6개 장면 × 화물 4종 × 문서 4종이 빈칸 없이 만들어지고, 숫자가 엔진 출력·화면 판단과 어긋나지 않는지 본다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  DOC_KINDS, buildDoc, docToText, docToHTML, shipperDoc, driverDoc, internalDoc, portDoc, josa, cargoFacts, DEMO_TAG, driverHeadline,
} from "../docs/assets/js/briefing.js";
import { classifyCargo } from "../docs/assets/js/derive.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "docs", "data");
const PRE = JSON.parse(readFileSync(join(root, "precomputed.json"), "utf8"));
const R = PRE.runs;

test("조사: 받침에 맞춰 고른다", () => {
  assert.equal(josa("출발", ["으로", "로"]), "출발로");
  assert.equal(josa("도착", ["으로", "로"]), "도착으로");
  assert.equal(josa("16:00", ["으로", "로"]), "16:00으로");
  assert.equal(josa("○○운송", ["은", "는"]), "○○운송은");
  assert.equal(josa("게이트·도로(양산·울산)", ["이", "가"]), "게이트·도로(양산·울산)가");
  assert.equal(josa("시스템 오류", ["으로", "로"]), "시스템 오류로");
});

test("모든 장면·화물·문서가 빈칸 없이 만들어진다", () => {
  for (const [s, run] of Object.entries(R)) {
    for (const key of run.cargo_order) {
      for (const k of DOC_KINDS) {
        for (const applied of [false, true]) {
          const doc = buildDoc(k.key, run, key, { applied: { [key]: applied }, driverUrl: "https://example.org/driver.html" });
          const text = docToText(doc);
          const html = docToHTML(doc);
          assert.ok(doc.title.length > 4, `${s}/${key}/${k.key}: 제목`);
          assert.doesNotMatch(text, /undefined|NaN|null|\[object/, `${s}/${key}/${k.key}: ${text}`);
          assert.doesNotMatch(html, /undefined|NaN/, `${s}/${key}/${k.key}: html`);
          assert.ok(text.includes(DEMO_TAG), `${s}/${key}/${k.key}: 가상 데이터 표시`);
        }
      }
    }
  }
});

test("장비 고장 · 울산 수출: 일정 변경 안내와 바뀐 기사 지시", () => {
  const run = R.terminal_failure;
  const sh = docToText(shipperDoc(run, "ulsan_export"));
  assert.match(sh, /\[일정 변경 안내\]/);
  assert.match(sh, /출발 시각을 20:00에서 16:00으로 앞당깁니다/);
  assert.match(sh, /마감을 놓칠 가능성: 거의 확실 → 1% 미만/);
  assert.match(sh, /18:00~23:30 수출 반입을 막는다고 공지/);
  const before = docToText(driverDoc(run, "ulsan_export"));
  assert.match(before, /변경 검토 중: 출발 16:00 권장/);
  const after = docToText(driverDoc(run, "ulsan_export", { applied: true }));
  assert.match(after, /시간 변경: 출발 20:00 → 16:00/);
  assert.match(after, /출발: 16:00/);
});

test("화주 사전 안내: 놓칠 확률은 화면 판단과 같은 값", () => {
  const run = R.terminal_failure;
  for (const key of ["import_factory", "ulsan_parts", "sea_air"]) {
    const k = classifyCargo(run, key);
    assert.equal(k.cls, "notify");
    const t = docToText(shipperDoc(run, key));
    assert.match(t, /\[사전 안내\]/);
    assert.ok(t.includes(`약 ${Math.round(k.least.p_fail * 100)}%`), `${key}: ${t}`);
    assert.match(t, /우선 하역을 요청/);
  }
});

test("사내 보고: 화물 4건 표와 변경 적용 여부", () => {
  const run = R.terminal_failure;
  const d0 = internalDoc(run);
  const table = d0.blocks.find((b) => b.table).table;
  assert.equal(table.rows.length, 4);
  assert.match(docToText(d0), /출발 20:00 → 16:00 \(적용 필요\)/);
  const d1 = internalDoc(run, { applied: { ulsan_export: true } });
  assert.match(docToText(d1), /출발 20:00 → 16:00 \(적용함\)/);
  assert.match(docToText(d1), /줄어드는 예상 손실: 약 250만 원/);
});

test("항만 협의: 번짐을 멈추는 대응을 반사실로 고른다", () => {
  assert.match(docToText(portDoc(R.terminal_failure)), /처리능력 복구만으로 번짐 지수가 1 아래\(0\.72\)/);
  assert.match(docToText(portDoc(R.typhoon_diversion)), /물량 분산만으로 번짐 지수가 1 아래/);
  assert.match(docToText(portDoc(R.base)), /평소 운영을 유지하면 됩니다/);
  assert.match(docToText(portDoc(R.epidemic)), /막힌 곳의 적체 해소가 우선/);
  assert.match(docToText(portDoc(R.terminal_failure)), /책임 소재 판단에 쓰지 않습니다/);
});

test("포워더 문서: 운송사 배차 요청과 선사·터미널 요청 사항", () => {
  const run = R.terminal_failure;
  const d = driverDoc(run, "ulsan_export", { applied: true });
  assert.match(d.title, /^\[배차 요청\] 울산 부품 수출 · 시간 변경$/);
  assert.equal(d.meta[0][1], "협력 운송사 배차 담당자");
  const t = docToText(d);
  assert.match(t, /출발 시각이 바뀌어 배차를 다시 요청드립니다/);
  assert.match(t, /화주: 울산 ○○정공/);
  assert.match(docToText(driverDoc(run, "import_factory", { driverUrl: "https://example.org/driver.html" })), /기사님께 전달해 주세요/);
  const p = docToText(portDoc(run));
  assert.match(p, /R-Delay로 관리하는 포워더입니다/);
  assert.match(p, /■ 3\. 고객 화물 영향/);
  assert.match(p, /■ 4\. 요청드립니다/);
  assert.match(p, /우선 하역 요청 — 받아들여지면 놓칠 확률 52% → 26%/);
  assert.ok(!portDoc(R.base).blocks.some((b) => b.h === "4. 요청드립니다"), "평시에는 요청 사항이 없다");
  // 화주 안내에는 포워더가 하는 일(운송사 차량 요청·항공편 확인)이 들어간다
  const sh = docToText(shipperDoc(run, "sea_air"));
  assert.match(sh, /협력 운송사에 터미널 \d{2}:\d{2} 도착으로 차량을 요청해 두었습니다/);
  assert.match(sh, /다음 항공편 예약 가능 여부를 항공사와 미리 확인해 두겠습니다/);
});

test("기사님 화면 한 줄: 수입은 반출 뒤 도착, 수출은 반입 금지 전", () => {
  const run = R.terminal_failure;
  assert.equal(driverHeadline(run, "import_factory").big, "21:40 이후 도착");
  assert.equal(driverHeadline(run, "ulsan_export", true).big, "18:00 전 반입");
  assert.equal(cargoFacts(run, "ulsan_export", true).changed, true);
});
