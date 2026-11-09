"""한국도로공사 '노선별 방향별 일별 1시간 구간통행속도'(15043737)에서 신항 배후 구간만 골라 작게 만든다.

입력: data/manual/ex_speed_1h/ 아래 ZIP·CSV(압축 풀지 않아도 됨, CP949/UTF-8 자동 판별)
출력: data/derived/ex_speed_busan.csv   (원본 열 그대로, 고른 구간의 행만)
      화면에 고른 콘존명 목록 → 빠진 구간이 있으면 --pattern에 더하기

기본 패턴은 남해제2지선(가락·서부산·냉정·서김해)과 남해선·부산외곽순환 김해권, 신항·녹산 이름을 잡습니다.
콘존명 표기(예: 'IC'·'JC' 붙는 방식)는 받은 파일에서 확인하세요.
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile

import pandas as pd

from common import DATA

DEFAULT = r"가락|서부산|냉정|서김해|김해|진례|장유|진영|부산신항|신항|녹산|대저|북부산|사상|양산|노포|금정"
NAME_COLS = [r"콘존명", r"구간명", r"CONZONE_?NM", r"conzone_?name"]


def sources(folder):
    """(이름, 여는 함수)를 돌려준다. 큰 ZIP도 통째로 메모리에 올리지 않고 흘려 읽는다."""
    for p in sorted(folder.rglob("*")):
        if p.name.startswith("_") or not p.is_file():
            continue
        if p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as z:
                members = [n for n in z.namelist() if n.lower().endswith((".csv", ".txt"))]
            for n in members:
                yield f"{p.name}:{n}", (lambda p=p, n=n: _zip_open(p, n))
        elif p.suffix.lower() in (".csv", ".txt"):
            yield p.name, (lambda p=p: p.open("rb"))


def _zip_open(path, member):
    z = zipfile.ZipFile(path)
    f = z.open(member)
    f._rdelay_zip = z  # 파일을 닫을 때까지 ZIP 핸들 유지
    return f


def frames(opener, chunksize: int):
    with opener() as f:
        head = f.read(200_000)
    enc = None
    for cand in ("utf-8-sig", "cp949"):
        try:
            head[:-4].decode(cand)  # 끝이 글자 중간에서 잘렸을 수 있음
            enc = cand
            break
        except UnicodeDecodeError:
            continue
    if enc is None:
        raise ValueError("인코딩을 알 수 없음")
    first = head[:4000]
    sep = "|" if first.count(b"|") > first.count(b",") else ","
    with opener() as f:
        yield from pd.read_csv(f, dtype=str, encoding=enc, sep=sep, chunksize=chunksize,
                               keep_default_na=False, encoding_errors="replace")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pattern", default=DEFAULT, help="콘존명에서 찾을 정규식")
    ap.add_argument("--folder", default=str(DATA / "manual" / "ex_speed_1h"))
    ap.add_argument("--chunksize", type=int, default=300_000)
    a = ap.parse_args()
    from pathlib import Path
    folder = Path(a.folder)
    if not folder.exists():
        sys.exit(f"[중단] {folder}가 없습니다. 도로공사 1시간 구간통행속도 파일을 여기에 넣으세요(MANUAL_DOWNLOADS.md).")
    out = DATA / "derived" / "ex_speed_busan.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    pat = re.compile(a.pattern)
    kept, seen, header = 0, {}, None
    with out.open("w", newline="", encoding="utf-8-sig") as fp:
        for name, opener in sources(folder):
            ncol = None
            for df in frames(opener, a.chunksize):
                if ncol is None:
                    ncol = next((c for c in df.columns for p in NAME_COLS if re.search(p, str(c), re.I)), None)
                    if ncol is None:
                        print(f"  ! {name}: 콘존명 열을 찾지 못해 건너뜀 (열: {list(df.columns)[:8]})")
                        break
                sub = df[df[ncol].astype(str).str.contains(pat, na=False)]
                if sub.empty:
                    continue
                if header is None:
                    header = list(sub.columns)
                    fp.write(",".join(header) + "\n")
                sub.reindex(columns=header).to_csv(fp, header=False, index=False)
                kept += len(sub)
                for z, n in sub[ncol].value_counts().items():
                    seen[z] = seen.get(z, 0) + int(n)
            print(f"{name}: 누적 {kept:,}행")
    if not kept:
        sys.exit("[주의] 고른 행이 없습니다. 콘존명 표기를 확인해 --pattern을 고치세요.")
    print(f"\n고른 콘존 {len(seen)}개 (행 수 많은 순, 상위 40)")
    for z, n in sorted(seen.items(), key=lambda x: -x[1])[:40]:
        print(f"  {n:8,}  {z}")
    print(f"\n→ {out} ({kept:,}행)")


if __name__ == "__main__":
    main()
