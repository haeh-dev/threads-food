"""
coupang_api.py — 쿠팡파트너스 오픈 API(상품 검색, 딥링크).

    python scripts/coupang_api.py search "곱창 밀키트" --limit 5
    python scripts/coupang_api.py link 1234567890 https://www.coupang.com/vp/products/987

키는 환경변수 COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY 에서 읽는다.
GitHub Actions 에서는 coupang.yml 워크플로가 시크릿을 넣어 실행한다.

결과는 사람이 읽는 표와 함께, 기계가 읽을 JSON 을 아래 표시 사이에 찍는다.
    ===COUPANG_RESULT_BEGIN===
    {...}
    ===COUPANG_RESULT_END===

subId 를 붙이면 쿠팡파트너스 리포트에서 채널별(쓰레드/블로그) 실적을 나눠 볼 수 있다.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse

import requests

DOMAIN = "https://api-gateway.coupang.com"
DEEPLINK_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink"
SEARCH_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/search"
DEFAULT_SUB_ID = "threads"


class CoupangError(RuntimeError):
    pass


def _keys() -> tuple[str, str]:
    ak = os.environ.get("COUPANG_ACCESS_KEY", "").strip()
    sk = os.environ.get("COUPANG_SECRET_KEY", "").strip()
    if not ak or not sk:
        raise CoupangError("COUPANG_ACCESS_KEY / COUPANG_SECRET_KEY 가 없다. 저장소 시크릿을 확인할 것.")
    return ak, sk


def _authorization(method: str, path: str, query: str, ak: str, sk: str) -> str:
    """쿠팡 오픈 API HMAC 서명. 메시지 = 서명시각 + 메서드 + 경로 + 쿼리(물음표 제외)."""
    signed = time.strftime("%y%m%dT%H%M%SZ", time.gmtime())
    message = signed + method + path + query
    sig = hmac.new(sk.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"CEA algorithm=HmacSHA256, access-key={ak}, signed-date={signed}, signature={sig}"


def _request(method: str, path: str, query: str = "", body: dict | None = None) -> dict:
    ak, sk = _keys()
    url = DOMAIN + path + (f"?{query}" if query else "")
    headers = {
        "Authorization": _authorization(method, path, query, ak, sk),
        "Content-Type": "application/json;charset=UTF-8",
    }
    r = requests.request(method, url, headers=headers,
                         data=json.dumps(body) if body is not None else None, timeout=20)
    try:
        data = r.json()
    except ValueError:
        raise CoupangError(f"{path} → HTTP {r.status_code}: JSON 이 아닌 응답")
    if r.status_code >= 400 or str(data.get("rCode", "0")) != "0":
        raise CoupangError(f"{path} → HTTP {r.status_code}: {data.get('rMessage') or data.get('message') or data}")
    return data


def product_url(pid_or_url: str) -> str:
    s = pid_or_url.strip()
    if s.isdigit():
        return f"https://www.coupang.com/vp/products/{s}"
    if "coupang.com" not in s:
        raise CoupangError(f"쿠팡 URL 이나 상품 ID 가 아니다: {s}")
    return s


def deeplink(items: list[str], sub_id: str = DEFAULT_SUB_ID) -> list[dict]:
    """쿠팡 URL(또는 상품 ID)을 link.coupang.com/a/... 파트너스 링크로 바꾼다."""
    body = {"coupangUrls": [product_url(x) for x in items]}
    if sub_id:
        body["subId"] = sub_id
    return _request("POST", DEEPLINK_PATH, body=body).get("data", [])


def search(keyword: str, limit: int = 5, sub_id: str = DEFAULT_SUB_ID) -> list[dict]:
    """키워드로 상품을 찾는다. 검색 API 는 호출 제한이 빡빡하니 글 한 편에 한두 번만 쓴다."""
    params = {"keyword": keyword, "limit": str(limit)}
    if sub_id:
        params["subId"] = sub_id
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    data = _request("GET", SEARCH_PATH, query=query).get("data", {})
    out = []
    for p in data.get("productData", []):
        out.append({
            "productId": p.get("productId"),
            "productName": p.get("productName"),
            "productPrice": p.get("productPrice"),
            "isRocket": p.get("isRocket"),
            "categoryName": p.get("categoryName"),
            "productUrl": p.get("productUrl"),
        })
    return out


def _emit(result: dict) -> None:
    print("===COUPANG_RESULT_BEGIN===")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("===COUPANG_RESULT_END===")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("```json\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n```\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("keyword")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--sub-id", default=DEFAULT_SUB_ID)
    l = sub.add_parser("link")
    l.add_argument("items", nargs="+", help="쿠팡 상품 ID 또는 coupang.com URL (쉼표로 이어도 됨)")
    l.add_argument("--sub-id", default=DEFAULT_SUB_ID)
    a = ap.parse_args()

    try:
        if a.cmd == "search":
            rows = search(a.keyword, a.limit, a.sub_id)
            for i, p in enumerate(rows):
                rocket = " 로켓" if p["isRocket"] else ""
                print(f"[{i}] {p['productId']}  {p['productPrice']:>8}원{rocket}  {p['productName']}")
            _emit({"mode": "search", "keyword": a.keyword, "products": rows})
        else:
            items = [x for raw in a.items for x in raw.split(",") if x.strip()]
            rows = deeplink(items, a.sub_id)
            for r in rows:
                print(f"{r.get('shortenUrl')}  ← {r.get('originalUrl')}")
            _emit({"mode": "link", "links": rows})
    except CoupangError as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
