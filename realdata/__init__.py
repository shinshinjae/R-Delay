"""R-Delay 실자료 후향 검증(부산신항 공개 자료, 2023-01~).

원자료는 저장소 밖(.env의 RDELAY_DATA_ROOT)에 두고, 읽은 결과는 realdata/cache/*.parquet에 둔다(gitignore).
Clarksons를 쓰는 계산·결과는 local_only/에만 둔다. 실행: python -m realdata.io --refresh
"""
from .paths import console_safe

console_safe()
