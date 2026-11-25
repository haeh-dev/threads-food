# threads-food 작업 규칙 (Claude용)

사용자가 맛집 사진을 주면 이 순서로 쓰레드 글을 만들어 `queue/` 에 넣는다. 게시는 매일
`daily-post.yml` 이 1편씩 한다. 사용자 안내는 한국어로 한다.

## 1. 묶기
```
python scripts/prepare_photos.py [--env-file <KAKAO_REST_KEY 가 든 .env>] scan <사진 폴더나 파일들>
```
- 촬영 시각 간격 120분 기준으로 가게별로 묶인다. 결과를 사용자에게 보여 주고 묶음이 맞는지 확인한다.
- 위치 정보가 있으면 주변 가게 후보가 나온다. **후보는 추정이다.** 상호는 사용자에게 확인받는다.
- HEIC 사진은 `pip install pillow-heif` 가 있어야 읽힌다.

## 2. 메모 받기 — 지어내지 않는다
묶음마다 최소한 **가게 이름(또는 지역), 먹은 메뉴, 한 줄 소감(좋았던 점·아쉬운 점)** 을 받는다.
메모가 없으면 묻는다. 사진만 보고 메뉴명·가격·맛을 단정하지 않는다(공정위 2026-06-01 개정:
하지 않은 체험을 쓰면 부당광고). 상호는 카카오 로컬 API 등록 상호를 쓴다.

## 3. 큐에 넣기
```
python scripts/prepare_photos.py build --group N [--pick 3,1,2]
```
`queue/NNNN/` 에 사진과 `post.json` 뼈대가 생긴다. 사진은 최대 10장, 첫 장이 대표 사진이다.

## 4. 쿠팡 링크
```
gh workflow run coupang.yml -f mode=search -f query="검색어"
gh run list --workflow=coupang.yml -L 1      # 실행 ID 확인
gh run view <ID> --log                        # ===COUPANG_RESULT_BEGIN=== 사이 JSON
```
- 후보를 보여 주고 사용자가 고르면 `mode=link -f query=<상품ID>` 로 `link.coupang.com/a/...` 를 만든다.
- 검색 API 는 호출 제한이 빡빡하다. 글 한 편에 한두 번만.
- 맛집 글에 어울리는 순서: 그 가게가 파는 밀키트·택배 > 비슷한 메뉴 밀키트 > 지역 특산물 > 사용자가 실제로 쓰는 주방도구.
- 써 보지 않은 상품을 후기처럼 쓰지 않는다.

## 5. post.json 쓰기
```json
{
  "store": "카카오 등록 상호",
  "area": "성수동",
  "text": "[성수] 가게명 메뉴 가격\n\n소감 3~5줄\n아쉬운 점: …\n\n(댓글에 쿠팡 파트너스 링크가 있어요)",
  "images": ["01.jpg", "02.jpg"],
  "reply": "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.\n집에서 비슷하게 먹고 싶을 때 → https://link.coupang.com/a/xxxx",
  "topic": "홍대맛집",
  "approved": false,
  "note": ""
}
```
- 본문 500자 이내. 이모지는 4자로 센다(문서상 UTF-8 바이트). 사람 말투로 짧게. 반대 의견(아쉬운 점)을 지우지 않는다.
- `topic` 은 쓰레드 주제 태그다. 글당 하나, `#` 없이 1~50자, 마침표·앰퍼샌드 불가. 붙이면 그 주제 피드에 노출된다.
  사람들이 실제로 찾아볼 말을 고른다: `지역+맛집`(홍대맛집, 성수맛집) 또는 `메뉴+맛집`(방어맛집). 계정 이름 같은
  브랜드 태그(노랑리본_홍대)는 주제로 쓰지 않는다. 고른 주제는 사용자에게 미리보기와 함께 확인받는다.
- 주제를 `topic` 으로 줄 때는 본문에 해시태그를 넣지 않는다. 본문의 첫 해시태그도 주제로 잡힐 수 있어 둘이 엇갈린다.
- 말투는 `tistory-food-post` 스킬의 `references/voice.md` 를 참고한다.
- 쿠팡 링크가 없으면 `reply` 는 null, 본문의 쿠팡 안내 줄도 뺀다.

## 6. 검사 → 미리보기 → 승인 → push
```
python scripts/validate_queue.py --show NNNN
```
미리보기를 사용자에게 보여 주고 **승인받은 글만** `"approved": true` 로 바꿔 커밋·push 한다.
승인 없이 approved 를 true 로 두지 않는다(게시는 사용자 이름으로 공개되는 일이다).
push 하면 `validate.yml` 이 같은 검사를 돌린다. `validate_queue.py` 로 남은 일수를 알려 준다.

## 하지 말 것
- 키·토큰을 파일에 쓰지 않는다. 이 저장소는 공개다. 시크릿 등록은 사용자가 `gh secret set` 으로 직접 한다.
- EXIF 를 지우지 않은 원본 사진을 커밋하지 않는다(`build` 를 거친 사진만 넣는다).
- 하루 여러 편을 몰아 올리지 않는다. 메타 스팸 규정은 고빈도·반복 게시를 막는다.
