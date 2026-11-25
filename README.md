# threads-food

직접 다녀온 맛집 사진으로 쓰레드(Threads) 글을 만들어 **하루 1편씩 자동으로 올리는** 저장소입니다.
쿠팡파트너스 링크는 본문이 아니라 댓글에 붙입니다.

## 흐름

```
[사진을 줄 때 — Claude 세션에서]
사진 여러 장 + 짧은 메모
  → 촬영 시각·위치로 가게별로 묶기 (1가게 = 1글)
  → 회전 보정, 가로 1440px로 축소, 위치 정보(EXIF) 삭제, 가운데에 옅은 norang_ribbon 워터마크
  → 본문 + 쿠팡 링크 댓글 작성 → 미리보기 → 승인한 글만 queue/ 에 push

[매일 12:23 KST — GitHub Actions, PC 꺼져 있어도 됨]
queue/ 에서 가장 앞선 승인 글 1편
  → 쓰레드에 사진 + 본문 게시 → 그 글에 쿠팡 링크 댓글
  → posted/ 로 기록을 옮기고 사진 삭제. 큐가 비면 건너뜀
```

## 폴더

| 경로 | 내용 |
|---|---|
| `queue/NNNN/` | 게시 대기 글. `post.json` + 사진 `01.jpg` … |
| `posted/NNNN.json` | 게시가 끝난 글의 기록(게시물 ID, 주소, 시각) |
| `scripts/prepare_photos.py` | 사진 묶기(`scan`)와 큐 넣기(`build`) |
| `scripts/validate_queue.py` | 큐 검사·미리보기 |
| `scripts/publish_next.py` | 1편 게시(워크플로가 실행) |
| `scripts/coupang_api.py` | 쿠팡 상품 검색·딥링크 |
| `scripts/reply_to_post.py` | 이미 올라간 내 글에 답글 달기(`reply.yml` 이 실행) |
| `.github/workflows/` | 매일 게시, 규칙 검사, 쿠팡 링크, 지난 글 답글, 토큰 갱신 |

## 처음 한 번 할 설정

이 저장소는 **공개**입니다. 키와 토큰은 절대 파일에 넣지 말고 GitHub 시크릿에만 넣습니다.
`gh secret set 이름 --repo haeh-dev/threads-food` 를 실행하면 값을 붙여넣으라는 입력창이 나옵니다
(값이 화면과 기록에 남지 않습니다).

| 시크릿 | 용도 | 필수 |
|---|---|---|
| `THREADS_ACCESS_TOKEN` | 쓰레드 게시 | 필수 |
| `COUPANG_ACCESS_KEY` / `COUPANG_SECRET_KEY` | 쿠팡 검색·딥링크 | 쿠팡 링크를 쓸 때 |
| `GH_PAT` | Threads 토큰 자동 갱신 | 선택(없으면 60일마다 직접 갱신) |

### 1. Threads 토큰

1. [Meta for Developers](https://developers.facebook.com/apps/) → 앱 만들기 → 사용 사례에서 **Threads API 액세스** 선택
2. 사용 사례 맞춤 설정 → 권한에 `threads_basic`, `threads_content_publish`, `threads_manage_replies` 추가
   (`threads_manage_replies` 가 있어야 쿠팡 링크 댓글을 달 수 있습니다)
3. 앱 역할 → 역할 → **Threads 테스터**로 본인 쓰레드 계정 추가 →
   쓰레드(웹) 설정 → 계정 → 웹사이트 권한 → 초대 수락
4. 사용 사례 설정 화면의 토큰 생성기에서 본인 계정으로 액세스 토큰 발급
5. 받은 토큰이 단기 토큰(1시간)이면 장기 토큰(60일)으로 바꿉니다. PowerShell에서 앱 시크릿과 단기 토큰을
   환경변수로 넣고 실행합니다.
   ```powershell
   Invoke-RestMethod "https://graph.threads.net/access_token?grant_type=th_exchange_token&client_secret=$env:APP_SECRET&access_token=$env:SHORT_TOKEN"
   ```
6. 장기 토큰을 시크릿으로 등록합니다.
   ```powershell
   gh secret set THREADS_ACCESS_TOKEN --repo haeh-dev/threads-food
   ```

개발 모드 앱이라도 테스터로 등록한 본인 계정에는 게시할 수 있습니다.

### 2. 쿠팡파트너스 키

쿠팡파트너스 → 오픈 API 메뉴에서 Access Key / Secret Key 를 발급받아 등록합니다.
(계정 승인 상태에 따라 메뉴가 열리지 않을 수 있습니다.)

```powershell
gh secret set COUPANG_ACCESS_KEY --repo haeh-dev/threads-food
```
```powershell
gh secret set COUPANG_SECRET_KEY --repo haeh-dev/threads-food
```

쿠팡파트너스 **활동 채널에 쓰레드 프로필 주소를 등록**해 두세요. 등록하지 않은 채널의 홍보는
부정행위로 볼 수 있다는 안내가 있습니다.

### 3. (선택) 토큰 자동 갱신

GitHub → Settings → Developer settings → Fine-grained tokens → 이 저장소만, 권한 **Secrets: Read and write**
로 토큰을 만들어 `GH_PAT` 으로 등록하면 매주 Threads 토큰이 자동으로 갱신됩니다.

### 4. 점검

Actions → `daily-post` → Run workflow(점검 모드 기본값) 로 토큰과 사진 주소를 확인합니다.

```powershell
gh workflow run daily-post.yml --repo haeh-dev/threads-food
```

## 지키는 규칙

`validate_queue.py` 가 push 때마다 검사하고, 어긋나면 게시하지 않습니다.

- 본문·댓글 각 500자(이모지는 4자로 셈), 링크 각 5개 이하 / 사진 1~10장
- 주제 태그(토픽): 본문에는 달지 않고, 쿠팡 링크 댓글에만 `광고` 주제를 자동으로 붙입니다.
  본문의 첫 해시태그가 주제로 잡히므로 본문에는 해시태그를 넣지 않습니다(검사에서 걸립니다)
- 쿠팡 링크는 **댓글에만**, 댓글 **첫 줄은 대가성 문구**
  「이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.」
- 본문의 "쿠팡 파트너스" 안내 줄은 선택(본문은 짧게, 필수 고지는 댓글 첫 줄)
- 메뉴·가격·맛은 직접 다녀온 사람이 준 메모에 있는 것만 씁니다. 하지 않은 체험을 쓰면
  공정위 심사지침상 부당광고가 될 수 있습니다(2026-06-01 시행 개정).
- 써 보지 않은 쿠팡 상품은 후기처럼 쓰지 않습니다("집에서 비슷하게 먹고 싶다면" 정도로).

## 알아둘 것

- 큐에 있는 동안 사진과 글은 공개 저장소에서 보입니다. 위치 정보는 넣기 전에 지웁니다.
  게시 후 사진은 지우지만 git 기록에는 남습니다.
- GitHub 예약 실행은 정시보다 늦을 수 있습니다. 게시 시각은 `daily-post.yml` 의 cron 으로 바꿉니다.
- 공개 저장소에서 60일 동안 커밋이 없으면 예약 워크플로가 자동으로 꺼집니다. 큐가 오래 비어 있었다면
  Actions 에서 다시 켜야 합니다.
- 멈추려면 Actions → `daily-post` → Disable workflow.
