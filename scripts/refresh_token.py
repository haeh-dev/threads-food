"""
refresh_token.py — Threads 장기 토큰을 갱신해 새 토큰을 표준 출력으로만 내보낸다.

refresh-token.yml 이 매주 실행하고, 받은 토큰을 마스킹한 뒤 저장소 시크릿에 다시 저장한다.
토큰은 로그에 찍히지 않도록 표준 출력 한 줄로만 나간다(만료까지 남은 일수는 표준 오류로).
"""
from __future__ import annotations

import os
import sys

from threads_api import ThreadsError, refresh_long_lived


def main() -> None:
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not token:
        sys.exit("THREADS_ACCESS_TOKEN 이 없다")
    try:
        new, expires_in = refresh_long_lived(token)
    except ThreadsError as e:
        sys.exit(str(e))
    print(f"새 토큰 유효기간: 약 {expires_in // 86400}일", file=sys.stderr)
    sys.stdout.write(new)


if __name__ == "__main__":
    main()
