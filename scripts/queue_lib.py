"""
queue_lib.py — 예약 큐(queue/NNNN/post.json) 읽기와 검사.

post.json 형식:
{
  "store":  "가게 이름(카카오 로컬 등록 상호)",
  "area":   "지역(예: 성수동)",
  "text":   "쓰레드 본문, 500자 이내",
  "images": ["01.jpg", "02.jpg"],
  "reply":  "링크 댓글(없으면 null)",
  "approved": true,
  "note":   "내부 메모(게시되지 않음)"
}

규칙(게시 전에 반드시 통과해야 한다):
- 본문·댓글은 각각 500자 이내, 링크는 각각 5개 이하
- 사진은 1~10장, jpg/png, 장당 8MB 미만
- 쿠팡 링크는 본문이 아니라 댓글에만 넣는다
- 쿠팡 링크가 있는 댓글은 첫 줄이 대가성 문구여야 한다(공정위 심사지침, 첫 부분 표시)
- 그 경우 본문에도 "쿠팡 파트너스" 안내를 넣는다(댓글에만 고지하면 인정되지 않는다는 해설이 있음)
- approved 가 true 인 글만 게시한다
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from threads_api import CAROUSEL_LIMIT, TEXT_LIMIT

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "queue"
POSTED = ROOT / "posted"

DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
BODY_NOTICE_KEYWORD = "쿠팡 파트너스"
LINK_LIMIT = 5
IMAGE_EXT = {".jpg", ".jpeg", ".png"}
IMAGE_MAX_BYTES = 8 * 1024 * 1024

URL_RE = re.compile(r"https?://\S+")
COUPANG_RE = re.compile(r"https?://(?:[\w-]+\.)*coupang\.com\S*", re.I)


@dataclass
class Post:
    id: str
    dir: Path
    data: dict
    state: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.data.get("approved") is True

    @property
    def images(self) -> list[str]:
        return list(self.data.get("images") or [])

    @property
    def text(self) -> str:
        return self.data.get("text") or ""

    @property
    def reply(self) -> str:
        return self.data.get("reply") or ""

    def label(self) -> str:
        return f"{self.id} {self.data.get('area', '')} {self.data.get('store', '')}".strip()


def validate(post: Post) -> list[str]:
    errs: list[str] = []
    d = post.data
    text, reply = post.text, post.reply

    if not text.strip():
        errs.append("본문(text)이 비어 있다")
    if len(text) > TEXT_LIMIT:
        errs.append(f"본문이 {len(text)}자다(최대 {TEXT_LIMIT})")
    if len(reply) > TEXT_LIMIT:
        errs.append(f"댓글이 {len(reply)}자다(최대 {TEXT_LIMIT})")
    if len(URL_RE.findall(text)) > LINK_LIMIT:
        errs.append(f"본문 링크가 {LINK_LIMIT}개를 넘는다")
    if len(URL_RE.findall(reply)) > LINK_LIMIT:
        errs.append(f"댓글 링크가 {LINK_LIMIT}개를 넘는다")

    if COUPANG_RE.search(text):
        errs.append("쿠팡 링크는 본문이 아니라 댓글(reply)에 넣는다")
    if COUPANG_RE.search(reply):
        first_line = reply.strip().splitlines()[0].strip() if reply.strip() else ""
        if first_line != DISCLOSURE:
            errs.append("쿠팡 링크 댓글의 첫 줄은 대가성 문구여야 한다: " + DISCLOSURE)
        if BODY_NOTICE_KEYWORD not in text:
            errs.append(f"댓글에 쿠팡 링크가 있으면 본문에도 '{BODY_NOTICE_KEYWORD}' 안내를 넣는다")

    imgs = post.images
    if not imgs:
        errs.append("사진(images)이 없다")
    if len(imgs) > CAROUSEL_LIMIT:
        errs.append(f"사진이 {len(imgs)}장이다(최대 {CAROUSEL_LIMIT})")
    for name in imgs:
        p = post.dir / name
        if "/" in name or "\\" in name:
            errs.append(f"사진 이름에 경로를 넣지 않는다: {name}")
        elif not p.exists():
            errs.append(f"사진 파일이 없다: {name}")
        elif p.suffix.lower() not in IMAGE_EXT:
            errs.append(f"jpg/png 만 된다: {name}")
        elif p.stat().st_size >= IMAGE_MAX_BYTES:
            errs.append(f"8MB 이상이다: {name}")

    for key in ("store", "area"):
        if not str(d.get(key, "")).strip():
            errs.append(f"{key} 가 비어 있다")
    return errs


def load_post(d: Path) -> Post:
    pj = d / "post.json"
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Post(d.name, d, {}, errors=["post.json 이 없다"])
    except json.JSONDecodeError as e:
        return Post(d.name, d, {}, errors=[f"post.json 형식 오류: {e}"])
    state_file = d / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    post = Post(d.name, d, data, state)
    post.errors = validate(post)
    return post


def load_queue() -> list[Post]:
    if not QUEUE.exists():
        return []
    dirs = sorted(p for p in QUEUE.iterdir() if p.is_dir() and not p.name.startswith("."))
    return [load_post(d) for d in dirs]


def next_id() -> str:
    """queue 와 posted 를 통틀어 가장 큰 번호 + 1."""
    nums = []
    for base in (QUEUE, POSTED):
        if base.exists():
            for p in base.iterdir():
                stem = p.stem if p.is_file() else p.name
                if stem.isdigit():
                    nums.append(int(stem))
    return f"{(max(nums) + 1) if nums else 1:04d}"
