"""
publish_next.py — 큐에서 가장 앞선 승인 글 1편을 쓰레드에 올린다. daily-post.yml 이 매일 실행한다.

    python scripts/publish_next.py --dry-run   # 올리지 않고 점검만(토큰, 사진 주소, 글 검사)
    python scripts/publish_next.py             # 실제 게시

사진 주소는 https://raw.githubusercontent.com/{저장소}/{커밋}/queue/{번호}/{파일} 이다.
커밋 SHA 를 박아 두므로 캐시 때문에 엉뚱한 사진이 올라갈 일이 없다.

중간에 실패해도 같은 글이 두 번 올라가지 않게, 단계마다 queue/{번호}/state.json 에 기록한다.
  - 본문 게시 성공 → media_id 기록
  - 댓글 게시 성공 → reply_id 기록
다음 실행은 기록된 단계를 건너뛰고 남은 단계만 한다. 워크플로는 실패해도 이 기록을 커밋한다.

모두 끝나면 글을 posted/{번호}.json 으로 옮기고 queue/{번호}/ 사진은 지운다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys

import requests

from queue_lib import POSTED, ROOT, load_queue
from threads_api import Threads, ThreadsError

KST = dt.timezone(dt.timedelta(hours=9))


def summary(line: str) -> None:
    print(line)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def image_urls(post, repo: str, ref: str) -> list[str]:
    return [f"https://raw.githubusercontent.com/{repo}/{ref}/queue/{post.id}/{name}" for name in post.images]


def save_state(post) -> None:
    (post.dir / "state.json").write_text(json.dumps(post.state, ensure_ascii=False, indent=2), encoding="utf-8")


def archive(post) -> None:
    POSTED.mkdir(exist_ok=True)
    record = dict(post.data)
    record.update(post.state)
    record["id"] = post.id
    (POSTED / f"{post.id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(post.dir)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--ref", default=os.environ.get("GITHUB_SHA", ""))
    a = ap.parse_args()

    posts = load_queue()
    ready = [p for p in posts if p.approved]
    if not ready:
        summary(f"게시할 글이 없다(큐 {len(posts)}편, 승인된 글 0편). 오늘은 건너뛴다.")
        return

    post = ready[0]
    if post.errors:
        summary(f"맨 앞 글 {post.label()} 에 오류가 있어 멈춘다:")
        for e in post.errors:
            summary(f"- {e}")
        sys.exit(1)

    if not a.repo or not a.ref:
        sys.exit("--repo / --ref 가 필요하다(Actions 에서는 자동으로 채워진다).")
    urls = image_urls(post, a.repo, a.ref)
    token = os.environ.get("THREADS_ACCESS_TOKEN", "")

    summary(f"오늘의 글: {post.label()} / 사진 {len(urls)}장 / 남은 승인 글 {len(ready) - 1}편")

    if a.dry_run:
        ok = True
        for u in urls:
            try:
                r = requests.head(u, timeout=15, allow_redirects=True)
                ctype = r.headers.get("Content-Type", "")
                good = r.status_code == 200 and ctype.startswith("image/")
            except requests.RequestException as e:
                good, ctype = False, str(e)
            ok &= good
            summary(f"- 사진 {'OK' if good else '실패'}: {u.rsplit('/', 1)[-1]} ({ctype})")
        if token:
            try:
                me = Threads(token).me()
                summary(f"- Threads 토큰 OK: @{me.get('username')}")
            except ThreadsError as e:
                ok = False
                summary(f"- Threads 토큰 실패: {e}")
        else:
            summary("- Threads 토큰 없음(THREADS_ACCESS_TOKEN 시크릿을 등록할 것)")
        summary("점검 통과" if ok and token else "점검에서 문제가 나왔다")
        sys.exit(0 if ok and token else 1)

    api = Threads(token)
    try:
        if not post.state.get("media_id"):
            post.state["media_id"] = api.post_photos(urls, post.text)
            post.state["posted_at"] = dt.datetime.now(KST).isoformat(timespec="seconds")
            save_state(post)
            summary(f"- 본문 게시 완료: {post.state['media_id']}")
        else:
            summary(f"- 본문은 이미 게시됨({post.state['media_id']}), 남은 단계만 한다")

        if post.reply and not post.state.get("reply_id"):
            post.state["reply_id"] = api.reply(post.state["media_id"], post.reply)
            save_state(post)
            summary(f"- 댓글 게시 완료: {post.state['reply_id']}")

        post.state["permalink"] = api.permalink(post.state["media_id"])
    except ThreadsError as e:
        summary(f"게시 실패: {e}")
        sys.exit(1)

    archive(post)
    (ROOT / ".last_posted").write_text(post.label(), encoding="utf-8")
    summary(f"완료: {post.state.get('permalink') or post.state['media_id']}")


if __name__ == "__main__":
    main()
