"""
chat_post.py — 하루 한 번, 그날의 무작위 시각이 지나면 일상 대화 글(맛집·여행 질문) 1편을 올린다.
chat-post.yml 이 09~22시(KST) 매시 41분에 실행한다. 사용자 지시(2026-11-25): 매일 아무 때나 일상 대화 글 하나씩.

    python scripts/chat_post.py --check              # 목록 검사, 남은 글 수, 오늘 예정 시각
    python scripts/chat_post.py --dry-run            # 올리지 않고 오늘 고를 글과 토큰 점검
    python scripts/chat_post.py [--force]            # 예정 시각이 지났으면(또는 --force) 1편 게시

- 글 목록은 chat/pool.json. "approved": true 일 때만 올린다(목록 전체를 사용자에게 한 번 승인받는다).
- 그날의 예정 시각은 날짜로 정해지는 무작위 값이다(09:00~22:00, 맛집 글 시간대 11:30~13:30 제외).
  GitHub 예약 실행이 늦으면 예정 시각이 지난 뒤 첫 실행에서 올린다.
- 하루 1편. 같은 글은 두 번 올리지 않는다. 전날과 다른 종류(맛집/여행)를 먼저 고른다.
- 링크·해시태그·이모지·주제 태그는 넣지 않는다. 기록은 chat/posted.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queue_lib import HASHTAG_RE, URL_RE  # noqa: E402
from threads_api import TEXT_LIMIT, Threads, ThreadsError, _is_emoji, threads_len  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
POOL = ROOT / "chat" / "pool.json"
LOG = ROOT / "chat" / "posted.json"
KST = dt.timezone(dt.timedelta(hours=9))
WINDOW = (9 * 60, 22 * 60)              # 게시 시간대 09:00~22:00
AVOID = (11 * 60 + 30, 13 * 60 + 30)    # 맛집 글(12:23 예약) 시간대는 피한다
KINDS = ("맛집", "여행")   # 카페는 사용자 지시(2026-11-26)로 뺐다
LOW_LEFT = 7


def summary(line: str) -> None:
    print(line)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def target_minute(day: str) -> int:
    """날짜마다 정해지는 오늘의 게시 시각(자정부터 몇 분째인지)."""
    rnd = random.Random(hashlib.sha256(f"chat-{day}".encode()).hexdigest())
    while True:
        m = rnd.randrange(WINDOW[0], WINDOW[1])
        if not AVOID[0] <= m < AVOID[1]:
            return m


def item_errors(it: dict) -> list[str]:
    errs = []
    text = (it.get("text") or "").strip()
    if not it.get("id"):
        errs.append("id 가 없다")
    if it.get("kind") not in KINDS:
        errs.append(f"kind 는 {'/'.join(KINDS)} 중 하나다")
    if not text:
        errs.append("text 가 비어 있다")
    if threads_len(text) > TEXT_LIMIT:
        errs.append(f"{threads_len(text)}자다(최대 {TEXT_LIMIT})")
    if URL_RE.search(text):
        errs.append("링크를 넣지 않는다")
    if HASHTAG_RE.search(text):
        errs.append("해시태그를 넣지 않는다(첫 해시태그가 주제로 잡힌다)")
    if any(_is_emoji(ch) for ch in text):
        errs.append("이모지를 넣지 않는다")
    return errs


def load() -> tuple[dict, list[dict]]:
    pool = json.loads(POOL.read_text(encoding="utf-8")) if POOL.exists() else {"approved": False, "items": []}
    log = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
    return pool, log


def check_pool(pool: dict) -> list[str]:
    errs, seen = [], set()
    for it in pool.get("items", []):
        if it.get("id") in seen:
            errs.append(f"{it.get('id')}: id 가 겹친다")
        seen.add(it.get("id"))
        errs += [f"{it.get('id')}: {e}" for e in item_errors(it)]
    return errs


def pick(pool: dict, log: list[dict], day: str) -> tuple[dict | None, list[dict]]:
    used = {e.get("id") for e in log}
    left = [it for it in pool.get("items", []) if it.get("id") not in used]
    last_kind = log[-1].get("kind") if log else None
    cands = [it for it in left if it.get("kind") != last_kind] or left
    if not cands:
        return None, left
    return random.Random(f"pick-{day}").choice(cands), left


def hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="목록 검사와 남은 글 수만 보여 준다")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="예정 시각을 무시하고 지금 올린다")
    ap.add_argument("--now", help="시험용 현재 시각(KST, 예: 2026-11-26T15:00)")
    a = ap.parse_args()

    now = dt.datetime.fromisoformat(a.now).replace(tzinfo=KST) if a.now else dt.datetime.now(KST)
    day = now.date().isoformat()
    pool, log = load()
    errs = check_pool(pool)
    target = target_minute(day)

    if a.check:
        used = {e.get("id") for e in log}
        left = [it for it in pool.get("items", []) if it.get("id") not in used]
        by_kind = {k: sum(1 for it in left if it.get("kind") == k) for k in KINDS}
        print(f"목록 승인: {'예' if pool.get('approved') else '아니오'} / 전체 {len(pool.get('items', []))}편 / "
              f"남은 글 {len(left)}편 ({', '.join(f'{k} {v}' for k, v in by_kind.items())}) / 올린 글 {len(log)}편")
        print(f"오늘({day}) 예정 시각 {hhmm(target)} KST")
        for e in errs:
            print(f"  ! {e}")
        sys.exit(1 if errs else 0)

    if any((e.get("posted_at") or "").startswith(day) for e in log):
        summary(f"오늘({day}) 대화 글은 이미 올렸다.")
        return
    if not pool.get("approved"):
        summary("대화 글 목록(chat/pool.json)이 아직 승인되지 않아 올리지 않는다.")
        return
    if errs:
        summary("대화 글 목록에 오류가 있어 멈춘다:")
        for e in errs:
            summary(f"- {e}")
        sys.exit(1)

    minute_now = now.hour * 60 + now.minute
    if minute_now < target and not a.force:
        summary(f"오늘 예정 시각은 {hhmm(target)} KST, 아직 {hhmm(minute_now)} 이라 기다린다.")
        if not a.dry_run:
            return

    item, left = pick(pool, log, day)
    if not item:
        summary("::warning::대화 글이 다 떨어졌다. chat/pool.json 에 새 글을 채워야 한다.")
        return
    summary(f"오늘의 대화 글({item['kind']}, {item['id']}): {item['text']}")

    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if a.dry_run:
        try:
            me = Threads(token).me()
            summary(f"- Threads 토큰 OK: @{me.get('username')}")
        except ThreadsError as e:
            summary(f"- Threads 토큰 실패: {e}")
            sys.exit(1)
        summary("점검 모드라 올리지 않았다.")
        return

    api = Threads(token)
    try:
        media_id = api.post_text(item["text"])
        permalink = api.permalink(media_id)
    except ThreadsError as e:
        summary(f"게시 실패: {e}")
        sys.exit(1)
    log.append({"id": item["id"], "kind": item["kind"], "text": item["text"], "media_id": media_id,
                "permalink": permalink, "posted_at": now.isoformat(timespec="seconds")})
    LOG.parent.mkdir(exist_ok=True)
    LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    summary(f"게시 완료: {permalink or media_id}")
    remaining = len(left) - 1
    if remaining <= LOW_LEFT:
        summary(f"::warning::남은 대화 글이 {remaining}편이다. chat/pool.json 에 새 글을 채워야 한다.")


if __name__ == "__main__":
    main()
