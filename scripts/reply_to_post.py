"""
reply_to_post.py — 이미 올라간 내 쓰레드 글에 답글(쿠팡 링크 댓글 등)을 단다. reply.yml 이 실행한다.

    python scripts/reply_to_post.py --post https://www.threads.com/@계정/post/코드 --text "답글" [--dry-run]

--text 를 주지 않으면 환경변수 REPLY_TEXT 를 쓴다(워크플로에서 여러 줄 텍스트를 넘기기 위해).

- 글 주소의 /post/ 뒤 코드로 내 게시물 목록에서 해당 글을 찾는다(최근 500편까지).
- 쿠팡 링크가 있으면 첫 줄이 대가성 문구인지 검사한다.
- 같은 내용의 답글이 이미 있으면 다시 달지 않는다(답글 조회 권한이 없으면 이 검사는 건너뛴다).
"""
from __future__ import annotations

import argparse
import os
import re
import sys

from queue_lib import COUPANG_RE, DISCLOSURE, LINK_LIMIT, URL_RE, reply_topic
from threads_api import TEXT_LIMIT, Threads, ThreadsError, threads_len


def shortcode_of(s: str) -> str:
    m = re.search(r"/post/([A-Za-z0-9_-]+)", s)
    return m.group(1) if m else s.strip().strip("/")


def check_text(text: str) -> list[str]:
    errs = []
    if not text.strip():
        errs.append("답글이 비어 있다")
    if threads_len(text) > TEXT_LIMIT:
        errs.append(f"답글이 {threads_len(text)}자다(최대 {TEXT_LIMIT}, 이모지는 4자로 셈)")
    if len(URL_RE.findall(text)) > LINK_LIMIT:
        errs.append(f"링크가 {LINK_LIMIT}개를 넘는다")
    if COUPANG_RE.search(text) and text.strip().splitlines()[0].strip() != DISCLOSURE:
        errs.append("쿠팡 링크가 있으면 첫 줄은 대가성 문구여야 한다: " + DISCLOSURE)
    return errs


def find_media(api: Threads, code: str, max_pages: int = 10) -> dict | None:
    after = None
    for _ in range(max_pages):
        page = api.my_threads(after=after)
        for m in page.get("data", []):
            if m.get("shortcode") == code or f"/post/{code}" in (m.get("permalink") or ""):
                return m
        after = page.get("paging", {}).get("cursors", {}).get("after")
        if not after or not page.get("data"):
            break
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", required=True, help="내 쓰레드 글 주소 또는 /post/ 뒤 코드")
    ap.add_argument("--text", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--replace", action="store_true",
                    help="같은 내용의 내 답글이 있으면 새로 달고 옛것은 지운다(되돌릴 수 없음)")
    a = ap.parse_args()

    text = (a.text if a.text is not None else os.environ.get("REPLY_TEXT", "")).replace("\r\n", "\n").strip()
    errs = check_text(text)
    if errs:
        for e in errs:
            print(f"! {e}")
        sys.exit(1)

    api = Threads(os.environ.get("THREADS_ACCESS_TOKEN", ""))
    try:
        me = api.me()
        code = shortcode_of(a.post)
        media = find_media(api, code)
        if not media:
            sys.exit(f"@{me.get('username')} 의 최근 글에서 {code} 를 찾지 못했다. 다른 계정의 글이거나 주소가 틀렸다.")
        print(f"대상 글: @{me.get('username')} {media.get('permalink')} ({media.get('timestamp')})")
        print("  " + (media.get("text") or "").splitlines()[0][:60])
        topic = reply_topic(text)
        print(f"답글(주제 태그: {topic or '없음'}):")
        for line in text.splitlines():
            print(f"  {line}")

        same: list[dict] = []
        try:
            existing = api.replies(media["id"])
            same = [r for r in existing
                    if (r.get("text") or "").strip() == text and r.get("username") == me.get("username")]
        except ThreadsError as e:
            if a.replace:
                sys.exit(f"기존 답글을 확인할 수 없어 교체하지 않는다: {e}")
            print(f"(기존 답글 확인 건너뜀: {e})")

        if same and not a.replace:
            print("같은 내용의 답글이 이미 있어 다시 달지 않는다(지우고 다시 달려면 --replace).")
            return
        if a.dry_run:
            plan = f"새 답글을 달고 기존 답글 {len(same)}개를 지울" if same else "새 답글을 달"
            print(f"점검 모드라 게시하지 않았다({plan} 예정).")
            return

        # 새 답글을 먼저 달고 나서 옛 답글을 지운다. 중간에 실패해도 댓글이 사라지지 않게.
        reply_id = api.reply(media["id"], text, topic)
        print(f"답글 게시 완료: {reply_id} {api.permalink(reply_id)}")
        for r in same:
            if r.get("id") == reply_id:
                continue
            api.delete(r["id"])
            print(f"기존 답글 삭제: {r['id']}")
    except ThreadsError as e:
        sys.exit(f"실패: {e}")


if __name__ == "__main__":
    main()
