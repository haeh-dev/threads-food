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
REPORT_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/reports"   # 시간당 500회, 매일 15:00 갱신
REPORT_KINDS = ("clicks", "orders", "cancels", "commission")
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


def report(kind: str, start: str, end: str, sub_id: str | None = None) -> list[dict]:
    """일별 실적(clicks/orders/cancels/commission). 날짜는 yyyyMMdd. 실적은 매일 15:00 에 갱신된다."""
    if kind not in REPORT_KINDS:
        raise CoupangError(f"실적 종류는 {', '.join(REPORT_KINDS)} 중 하나다: {kind}")
    params = {"startDate": start, "endDate": end}
    if sub_id:
        params["subId"] = sub_id
    query = urllib.parse.urlencode(params)
    data = _request("GET", f"{REPORT_PATH}/{kind}", query=query).get("data", [])
    return data if isinstance(data, list) else [data]


def load_keys_file(path: str) -> None:
    """키 파일(첫 줄 Access Key, 둘째 줄 Secret Key)을 환경변수로 읽는다. 값은 출력하지 않는다."""
    lines = [x.strip() for x in open(path, encoding="utf-8-sig").read().splitlines() if x.strip()]
    if len(lines) < 2:
        raise CoupangError(f"{path} 에 키가 두 줄 있어야 한다(Access Key, Secret Key).")
    os.environ.setdefault("COUPANG_ACCESS_KEY", lines[0])
    os.environ.setdefault("COUPANG_SECRET_KEY", lines[1])


def _summarize(kind: str, rows: list[dict]) -> None:
    """응답 행을 날짜·subId 별로 묶어 숫자 칸을 더해 보여 준다(필드 이름은 응답에 있는 그대로)."""
    if not rows:
        print(f"  {kind}: 기록 없음")
        return
    skip = {"date", "trackingCode", "subId", "addtag", "ctag", "orderId", "productId", "productName"}
    num_keys = sorted({k for r in rows for k, v in r.items()
                       if k not in skip and isinstance(v, (int, float)) and not isinstance(v, bool)})
    by_day: dict[tuple, dict] = {}
    for r in rows:
        key = (str(r.get("date", "")), str(r.get("subId") or "-"))
        acc = by_day.setdefault(key, {k: 0 for k in num_keys})
        for k in num_keys:
            acc[k] += r.get(k) or 0
    print(f"  {kind}: {len(rows)}행  ({', '.join(num_keys) or '숫자 칸 없음'})")
    for (day, sub), acc in sorted(by_day.items()):
        vals = "  ".join(f"{k}={acc[k]:,.0f}" if isinstance(acc[k], (int, float)) else f"{k}={acc[k]}"
                         for k in num_keys)
        print(f"    {day}  subId={sub:<10} {vals}")
    if kind == "orders":
        for r in rows:
            name = (r.get("productName") or "")[:40]
            print(f"      주문 {r.get('date')} {r.get('subId') or '-'} {name}")


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
    r = sub.add_parser("report", help="일별 실적 조회. 공개 저장소라 Actions 가 아니라 이 PC 에서만 실행한다")
    r.add_argument("--days", type=int, default=7, help="오늘 포함 최근 며칠(기본 7)")
    r.add_argument("--start", help="시작일 yyyyMMdd (주면 --days 무시)")
    r.add_argument("--end", help="종료일 yyyyMMdd")
    r.add_argument("--sub-id", default=None, help="이 subId 만(기본: 전체)")
    r.add_argument("--kinds", default=",".join(REPORT_KINDS))
    r.add_argument("--keys-file", help="키 파일(첫 줄 Access, 둘째 줄 Secret). 예: cupang.txt")
    r.add_argument("--json", action="store_true", help="원본 응답을 JSON 으로 출력")
    a = ap.parse_args()

    try:
        if a.cmd == "report":
            if os.environ.get("GITHUB_ACTIONS"):
                raise CoupangError("실적은 공개 로그에 남지 않도록 Actions 에서 조회하지 않는다.")
            if a.keys_file:
                load_keys_file(a.keys_file)
            import datetime as dt
            kst = dt.timezone(dt.timedelta(hours=9))
            end = a.end or dt.datetime.now(kst).strftime("%Y%m%d")
            start = a.start or (dt.datetime.strptime(end, "%Y%m%d") - dt.timedelta(days=a.days - 1)).strftime("%Y%m%d")
            print(f"쿠팡파트너스 실적 {start} ~ {end}" + (f" (subId={a.sub_id})" if a.sub_id else "")
                  + "  ※ 매일 15:00 갱신")
            raw = {}
            for kind in [k.strip() for k in a.kinds.split(",") if k.strip()]:
                rows = report(kind, start, end, a.sub_id)
                raw[kind] = rows
                if not a.json:
                    _summarize(kind, rows)
            if a.json:
                print(json.dumps(raw, ensure_ascii=False, indent=2))
            return
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
