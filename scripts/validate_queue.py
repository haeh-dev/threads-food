"""
validate_queue.py — 큐에 있는 글을 모두 검사하고 순서대로 보여준다.

    python scripts/validate_queue.py            # 전체 검사(오류가 있으면 종료 코드 1)
    python scripts/validate_queue.py --show 0003  # 한 편을 게시될 모습 그대로 출력

push 할 때마다 validate.yml 워크플로가 같은 검사를 돌린다.
"""
from __future__ import annotations

import argparse
import sys

from queue_lib import load_queue, reply_topic
from threads_api import threads_len


def show(post) -> None:
    print(f"── {post.label()}  (사진 {len(post.images)}장: {', '.join(post.images)})")
    print(post.text)
    if post.reply:
        print("\n  └ 댓글")
        for line in post.reply.splitlines():
            print(f"    {line}")
    topic = reply_topic(post.reply) or "없음"
    print(f"\n  본문 {threads_len(post.text)}자(주제 태그 없음) / 댓글 {threads_len(post.reply)}자(주제 태그: {topic})"
          f" / 승인 {'예' if post.approved else '아니오'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", help="이 번호의 글을 미리보기로 출력")
    a = ap.parse_args()

    posts = load_queue()
    if a.show:
        hit = [p for p in posts if p.id == a.show]
        if not hit:
            sys.exit(f"queue/{a.show} 가 없다")
        show(hit[0])
        for e in hit[0].errors:
            print(f"  ! {e}")
        sys.exit(1 if hit[0].errors else 0)

    if not posts:
        print("큐가 비어 있다.")
        return

    bad = 0
    approved = 0
    for p in posts:
        mark = "승인" if p.approved else "대기"
        partial = "  (게시됨, 댓글 재시도 대기)" if p.state.get("media_id") else ""
        print(f"[{mark}] {p.label()}  사진 {len(p.images)}장{partial}")
        for e in p.errors:
            print(f"    ! {e}")
        bad += bool(p.errors)
        approved += p.approved and not p.errors
    print(f"\n총 {len(posts)}편, 게시 가능 {approved}편, 오류 {bad}편 → 하루 1편이면 약 {approved}일치")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
