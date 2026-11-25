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
CAROUSEL_LIMIT = 10     # 이 저장소에서 쓰는 캐러셀 최대 장수(보수적으로 잡음)


class ThreadsError(RuntimeError):
    pass


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
    def post_photos(self, image_urls: list[str], text: str) -> str:
        """사진 1장이면 단일 이미지, 2장 이상이면 캐러셀로 올리고 게시물 ID 를 돌려준다."""
        if not image_urls:
            raise ThreadsError("사진이 없다.")
        if len(image_urls) > CAROUSEL_LIMIT:
            raise ThreadsError(f"사진은 최대 {CAROUSEL_LIMIT}장이다(지금 {len(image_urls)}장).")

        if len(image_urls) == 1:
            cid = self._create(media_type="IMAGE", image_url=image_urls[0], text=text)
        else:
            children = []
            for url in image_urls:
                item = self._create(media_type="IMAGE", image_url=url, is_carousel_item="true")
                self.wait_ready(item)
                children.append(item)
            cid = self._create(media_type="CAROUSEL", children=",".join(children), text=text)
        self.wait_ready(cid)
        return self._publish(cid)

    def reply(self, media_id: str, text: str) -> str:
        """내 게시물에 텍스트 답글을 단다. threads_manage_replies 권한이 필요하다."""
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
