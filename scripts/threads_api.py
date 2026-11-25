"""
threads_api.py — Threads API 최소 래퍼.

게시 순서는 Meta 공식 문서를 따른다.
  1) 미디어 컨테이너 생성  POST /{user-id}/threads
  2) 처리 완료 대기        GET  /{container-id}?fields=status
  3) 게시                  POST /{user-id}/threads_publish

사진은 공개 URL 로만 넘길 수 있다. Meta 서버가 그 URL 에서 사진을 한 번 받아 가므로
게시가 끝나면 원본 URL 은 지워도 된다.
"""
from __future__ import annotations

import time

import requests

BASE = "https://graph.threads.net/v1.0"
TEXT_LIMIT = 500        # 게시물 본문 최대 글자 수
CAROUSEL_LIMIT = 10     # 이 저장소에서 쓰는 캐러셀 최대 장수(문서상 20장, 보수적으로 잡음)
TOPIC_MAX = 50          # 주제 태그 1~50자, 마침표(.)·앰퍼샌드(&) 불가


class ThreadsError(RuntimeError):
    pass


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF or cp in (0x200D, 0xFE0F) or 0x2B00 <= cp <= 0x2BFF


def threads_len(text: str) -> int:
    """Threads 글자 수. 문서상 이모지는 UTF-8 바이트 수로 센다(🐟 = 4자)."""
    return sum(len(ch.encode("utf-8")) if _is_emoji(ch) else 1 for ch in text)


def topic_errors(topic: str) -> list[str]:
    errs = []
    if not 1 <= len(topic) <= TOPIC_MAX:
        errs.append(f"주제 태그는 1~{TOPIC_MAX}자다(지금 {len(topic)}자)")
    if "." in topic or "&" in topic:
        errs.append("주제 태그에는 마침표(.)와 앰퍼샌드(&)를 쓸 수 없다")
    if topic.startswith("#"):
        errs.append("주제 태그에는 # 을 붙이지 않는다")
    return errs


class Threads:
    def __init__(self, token: str, timeout: int = 30):
        if not token:
            raise ThreadsError("THREADS_ACCESS_TOKEN 이 비어 있다.")
        self._token = token
        self._timeout = timeout
        self._user_id: str | None = None

    # ── 저수준 ───────────────────────────────────────────────
    def _call(self, method: str, path: str, **params) -> dict:
        params["access_token"] = self._token
        url = f"{BASE}/{path.lstrip('/')}"
        if method == "GET":
            r = requests.get(url, params=params, timeout=self._timeout)
        else:
            r = requests.post(url, data=params, timeout=self._timeout)
        try:
            data = r.json()
        except ValueError:
            data = {"error": {"message": r.text[:300]}}
        if r.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            msg = err.get("message", err) if isinstance(err, dict) else err
            code = err.get("code", "") if isinstance(err, dict) else ""
            # 토큰은 params 에만 있고 메시지에는 넣지 않는다.
            raise ThreadsError(f"{method} {path.split('?')[0]} → HTTP {r.status_code} {code}: {msg}")
        return data

    # ── 계정 ────────────────────────────────────────────────
    def me(self) -> dict:
        return self._call("GET", "me", fields="id,username")

    @property
    def user_id(self) -> str:
        if self._user_id is None:
            self._user_id = self.me()["id"]
        return self._user_id

    # ── 컨테이너 ─────────────────────────────────────────────
    def _create(self, **params) -> str:
        return self._call("POST", f"{self.user_id}/threads", **params)["id"]

    def wait_ready(self, container_id: str, max_wait: int = 180) -> None:
        """컨테이너가 FINISHED 가 될 때까지 기다린다. 사진은 보통 수 초면 끝난다."""
        waited, delay = 0, 3
        while waited <= max_wait:
            st = self._call("GET", container_id, fields="status,error_message")
            status = st.get("status")
            if status in ("FINISHED", "PUBLISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                raise ThreadsError(f"컨테이너 {container_id} 상태 {status}: {st.get('error_message', '')}")
            time.sleep(delay)
            waited += delay
            delay = min(delay * 2, 15)
        raise ThreadsError(f"컨테이너 {container_id} 가 {max_wait}초 안에 준비되지 않았다.")

    def _publish(self, container_id: str) -> str:
        return self._call("POST", f"{self.user_id}/threads_publish", creation_id=container_id)["id"]

    # ── 고수준 ──────────────────────────────────────────────
    def post_photos(self, image_urls: list[str], text: str, topic_tag: str | None = None) -> str:
        """사진 1장이면 단일 이미지, 2장 이상이면 캐러셀로 올리고 게시물 ID 를 돌려준다.

        topic_tag 를 주면 그 주제 피드에 글이 노출된다. 글당 하나이고, 본문·캐러셀 부모 컨테이너에 붙인다.
        """
        if not image_urls:
            raise ThreadsError("사진이 없다.")
        if len(image_urls) > CAROUSEL_LIMIT:
            raise ThreadsError(f"사진은 최대 {CAROUSEL_LIMIT}장이다(지금 {len(image_urls)}장).")
        extra = {}
        if topic_tag:
            if topic_errors(topic_tag):
                raise ThreadsError("; ".join(topic_errors(topic_tag)))
            extra["topic_tag"] = topic_tag

        if len(image_urls) == 1:
            cid = self._create(media_type="IMAGE", image_url=image_urls[0], text=text, **extra)
        else:
            children = []
            for url in image_urls:
                item = self._create(media_type="IMAGE", image_url=url, is_carousel_item="true")
                self.wait_ready(item)
                children.append(item)
            cid = self._create(media_type="CAROUSEL", children=",".join(children), text=text, **extra)
        self.wait_ready(cid)
        return self._publish(cid)

    def reply(self, media_id: str, text: str, topic_tag: str | None = None) -> str:
        """내 게시물에 텍스트 답글을 단다. threads_manage_replies 권한이 필요하다.

        topic_tag 를 주면 답글에 주제 태그를 붙인다. 문서에 답글의 주제 태그 규정이 없어서,
        주제 태그 때문에 거절되면 태그 없이 한 번 더 시도하고 경고를 남긴다.
        """
        if topic_tag:
            try:
                cid = self._create(media_type="TEXT", text=text, reply_to_id=media_id, topic_tag=topic_tag)
            except ThreadsError as e:
                print(f"경고: 주제 태그 '{topic_tag}' 를 붙인 답글이 거절돼 태그 없이 다시 시도한다: {e}")
                cid = self._create(media_type="TEXT", text=text, reply_to_id=media_id)
        else:
            cid = self._create(media_type="TEXT", text=text, reply_to_id=media_id)
        self.wait_ready(cid)
        return self._publish(cid)

    def my_threads(self, limit: int = 50, after: str | None = None) -> dict:
        """내 게시물 목록(최신순). paging.cursors.after 로 다음 쪽을 받는다."""
        params = {"fields": "id,shortcode,permalink,timestamp,text", "limit": str(limit)}
        if after:
            params["after"] = after
        return self._call("GET", f"{self.user_id}/threads", **params)

    def replies(self, media_id: str) -> list[dict]:
        """게시물에 달린 답글. threads_read_replies 권한이 필요하다."""
        return self._call("GET", f"{media_id}/replies", fields="id,text,username").get("data", [])

    def permalink(self, media_id: str) -> str:
        try:
            return self._call("GET", media_id, fields="permalink").get("permalink", "")
        except ThreadsError:
            return ""


def refresh_long_lived(token: str, timeout: int = 30) -> tuple[str, int]:
    """장기 토큰(60일)을 새로 늘린다. 발급 후 24시간이 지나야 갱신된다."""
    r = requests.get(
        "https://graph.threads.net/refresh_access_token",
        params={"grant_type": "th_refresh_token", "access_token": token},
        timeout=timeout,
    )
    data = r.json()
    if r.status_code >= 400 or "access_token" not in data:
        err = data.get("error", {})
        raise ThreadsError(f"토큰 갱신 실패 HTTP {r.status_code}: {err.get('message', err)}")
    return data["access_token"], int(data.get("expires_in", 0))
