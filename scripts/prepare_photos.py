"""
prepare_photos.py — 사용자가 준 사진을 가게별로 묶고, 쓰레드용으로 손질해 큐에 넣는다.

1) 묶기
    python scripts/prepare_photos.py [--env-file <KAKAO_REST_KEY 가 든 .env>] scan <사진 폴더나 파일들>

   --env-file 은 선택이다(위치로 주변 가게 후보를 찾을 때 필요). 저장소의 .env 는 자동으로 읽는다.

   촬영 시각 순으로 정렬하고, 간격이 --gap 분(기본 120분)보다 벌어지면 다른 가게로 본다.
   사진에 위치 정보가 있으면 카카오 로컬 API 로 동네 이름과 반경 안의 음식점·카페 후보를 붙인다.
   결과는 work/scan.json 에 남는다(커밋하지 않음).

2) 큐에 넣기
    python scripts/prepare_photos.py build --group 2 [--pick 3,1,2] [--id 0007]

   queue/{번호}/01.jpg … 로 저장하고, 없으면 post.json 뼈대를 만든다.
   - EXIF 방향대로 회전한다(폰 사진은 가로로 저장되고 방향 태그로 세로를 표시한다).
   - 가로 1440px 이하로 줄이고 JPEG 로 저장한다.
   - 위치(GPS)를 포함한 EXIF 를 모두 지운다. 이 저장소는 공개라서 반드시 지워야 한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from queue_lib import QUEUE, ROOT, next_id  # noqa: E402

try:  # 아이폰 HEIC 사진용(선택)
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_OK = True
except ImportError:
    HEIC_OK = False

WORK = ROOT / "work"
SCAN_FILE = WORK / "scan.json"
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
MAX_WIDTH = 1440
QUALITY = 85
MAX_PHOTOS = 10
KAKAO = "https://dapi.kakao.com/v2/local"


# ── 환경 ────────────────────────────────────────────────────
def load_env(files: list[str]) -> None:
    for f in [str(ROOT / ".env"), *files]:
        p = Path(f)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ── EXIF ───────────────────────────────────────────────────
def _ratio(x) -> float:
    try:
        return float(x)
    except TypeError:
        return x[0] / x[1]


def read_meta(path: Path) -> dict:
    meta = {"file": str(path), "taken": None, "lat": None, "lng": None}
    with Image.open(path) as im:
        exif = im.getexif()
        raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)  # DateTimeOriginal, DateTime
        if raw:
            try:
                meta["taken"] = dt.datetime.strptime(str(raw).strip("\x00"), "%Y:%m:%d %H:%M:%S").isoformat()
            except ValueError:
                pass
        gps = exif.get_ifd(0x8825)
        if gps and 2 in gps and 4 in gps:
            lat = sum(_ratio(v) / 60 ** i for i, v in enumerate(gps[2]))
            lng = sum(_ratio(v) / 60 ** i for i, v in enumerate(gps[4]))
            if gps.get(1) == "S":
                lat = -lat
            if gps.get(3) == "W":
                lng = -lng
            if lat and lng:
                meta["lat"], meta["lng"] = round(lat, 6), round(lng, 6)
    if meta["taken"] is None:  # EXIF 가 없으면 파일 수정 시각으로 대신한다(정확하지 않음)
        meta["taken"] = dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        meta["taken_from_file"] = True
    return meta


# ── 카카오 ──────────────────────────────────────────────────
def kakao(path: str, **params) -> dict:
    key = os.environ.get("KAKAO_REST_KEY")
    if not key:
        return {}
    url = f"{KAKAO}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {key}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # 후보 찾기는 보조 기능이라 실패해도 계속한다
        print(f"  (카카오 조회 실패: {e})", file=sys.stderr)
        return {}


def nearby(lat: float, lng: float, radius: int) -> tuple[str, list[dict]]:
    region = kakao("geo/coord2regioncode.json", x=lng, y=lat).get("documents", [])
    area = ""
    for d in region:
        if d.get("region_type") == "H":  # 행정동
            area = f"{d.get('region_2depth_name', '')} {d.get('region_3depth_name', '')}".strip()
    cands = []
    for code in ("FD6", "CE7"):  # 음식점, 카페
        docs = kakao("search/category.json", category_group_code=code, x=lng, y=lat,
                     radius=radius, sort="distance", size=5).get("documents", [])
        for d in docs:
            cands.append({
                "name": d["place_name"],
                "distance_m": int(d.get("distance") or 0),
                "category": d.get("category_name", ""),
                "address": d.get("road_address_name") or d.get("address_name", ""),
                "place_url": d.get("place_url", ""),
            })
    cands.sort(key=lambda c: c["distance_m"])
    return area, cands[:6]


# ── 명령 ────────────────────────────────────────────────────
def collect(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            files += sorted(x for x in p.rglob("*") if x.suffix.lower() in EXTS)
        elif p.exists():
            files.append(p)
        else:
            print(f"  ! 없음: {p}", file=sys.stderr)
    heic = [f for f in files if f.suffix.lower() in {".heic", ".heif"}]
    if heic and not HEIC_OK:
        print(f"  ! HEIC 사진 {len(heic)}장은 건너뛴다. `pip install pillow-heif` 후 다시 실행할 것.",
              file=sys.stderr)
        files = [f for f in files if f not in heic]
    return files


def cmd_scan(a) -> None:
    files = collect(a.inputs)
    if not files:
        sys.exit("사진이 없다.")
    metas = sorted((read_meta(f) for f in files), key=lambda m: m["taken"])

    groups: list[list[dict]] = []
    for m in metas:
        t = dt.datetime.fromisoformat(m["taken"])
        if groups and (t - dt.datetime.fromisoformat(groups[-1][-1]["taken"])).total_seconds() <= a.gap * 60:
            groups[-1].append(m)
        else:
            groups.append([m])

    out = []
    for i, g in enumerate(groups, 1):
        pts = [(m["lat"], m["lng"]) for m in g if m["lat"]]
        entry = {
            "group": i,
            "start": g[0]["taken"], "end": g[-1]["taken"],
            "files": [m["file"] for m in g],
            "time_from_file": any(m.get("taken_from_file") for m in g),
            "lat": None, "lng": None, "area": "", "candidates": [],
        }
        if pts:
            entry["lat"] = statistics.median(p[0] for p in pts)
            entry["lng"] = statistics.median(p[1] for p in pts)
            entry["area"], entry["candidates"] = nearby(entry["lat"], entry["lng"], a.radius)
        out.append(entry)

        when = entry["start"].replace("T", " ")[:16]
        warn = "  (EXIF 시각 없음, 파일 시각으로 묶음)" if entry["time_from_file"] else ""
        print(f"\n[묶음 {i}] {when}  사진 {len(g)}장{warn}")
        for f in entry["files"]:
            print(f"    {Path(f).name}")
        if entry["lat"]:
            print(f"    위치 {entry['lat']:.5f},{entry['lng']:.5f}  {entry['area']}")
            for c in entry["candidates"]:
                print(f"      · {c['name']} ({c['distance_m']}m) {c['category'].split('>')[-1].strip()}")
        else:
            print("    위치 정보 없음 → 가게 이름을 사용자에게 물어볼 것")

    WORK.mkdir(exist_ok=True)
    SCAN_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(out)}묶음 → {SCAN_FILE.relative_to(ROOT)}")


def convert(src: Path, dst: Path) -> tuple[int, int, int]:
    with Image.open(src) as im:
        icc = im.info.get("icc_profile")
        im = ImageOps.exif_transpose(im).convert("RGB")
    w, h = im.size
    if w > MAX_WIDTH:
        im = im.resize((MAX_WIDTH, round(h * MAX_WIDTH / w)), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"quality": QUALITY, "optimize": True, "progressive": True}
    if icc:
        kwargs["icc_profile"] = icc  # 색 정보만 남기고 EXIF(위치 포함)는 넣지 않는다
    im.save(dst, "JPEG", **kwargs)
    return im.size[0], im.size[1], dst.stat().st_size


def cmd_build(a) -> None:
    if a.files:
        files = [Path(f) for f in a.files]
        area = ""
    else:
        if not SCAN_FILE.exists():
            sys.exit("먼저 scan 을 실행할 것.")
        groups = json.loads(SCAN_FILE.read_text(encoding="utf-8"))
        hit = [g for g in groups if g["group"] == a.group]
        if not hit:
            sys.exit(f"묶음 {a.group} 이 없다.")
        files = [Path(f) for f in hit[0]["files"]]
        area = hit[0].get("area", "")

    if a.pick:
        idx = [int(x) for x in a.pick.split(",")]
        files = [files[i - 1] for i in idx]
    if len(files) > MAX_PHOTOS:
        print(f"  ! {len(files)}장 중 앞 {MAX_PHOTOS}장만 쓴다(--pick 으로 고를 수 있음)", file=sys.stderr)
        files = files[:MAX_PHOTOS]

    pid = a.id or next_id()
    dest = QUEUE / pid
    if dest.exists() and any(dest.glob("*.jpg")) and not a.force:
        sys.exit(f"{dest.relative_to(ROOT)} 에 이미 사진이 있다(--force 로 덮어쓰기).")

    names = []
    for i, src in enumerate(files, 1):
        name = f"{i:02d}.jpg"
        w, h, size = convert(src, dest / name)
        names.append(name)
        print(f"  + {name}  {w}x{h}  {size // 1024}KB  ← {src.name}")

    pj = dest / "post.json"
    if not pj.exists():
        skeleton = {"store": "", "area": area, "text": "", "images": names,
                    "reply": None, "approved": False, "note": ""}
        pj.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        data = json.loads(pj.read_text(encoding="utf-8"))
        data["images"] = names
        pj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ {pj.relative_to(ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", action="append", default=[])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("inputs", nargs="+")
    s.add_argument("--gap", type=int, default=120, help="이 분 이상 벌어지면 다른 가게로 본다")
    s.add_argument("--radius", type=int, default=60, help="주변 가게 후보 반경(m)")
    b = sub.add_parser("build")
    b.add_argument("--group", type=int)
    b.add_argument("--files", nargs="+", help="scan 없이 파일을 직접 지정")
    b.add_argument("--pick", help="묶음 안에서 쓸 사진 순서(1부터), 예: 3,1,2")
    b.add_argument("--id", help="큐 번호(기본: 다음 번호)")
    b.add_argument("--force", action="store_true")
    a = ap.parse_args()
    load_env(a.env_file)
    if a.cmd == "scan":
        cmd_scan(a)
    else:
        if not a.group and not a.files:
            sys.exit("--group 이나 --files 중 하나가 필요하다.")
        cmd_build(a)


if __name__ == "__main__":
    main()
