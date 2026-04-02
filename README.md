# LETrans_Kr — Last Epoch 한국어 번역패치

> Steam 경로 자동 감지 → GitHub에서 최신 패치 다운 → LELocalePatch로 번들 패치 → 완료!

**GitHub**: https://github.com/fnrkp089/LETrans_Kr

## 기능

| 기능 | 설명 |
|------|------|
| **Steam 자동 감지** | 레지스트리 + `libraryfolders.vdf` + `appmanifest_899770.acf` |
| **LELocalePatch 연동** | Unity 에셋 번들 직접 패치 (catalog.bin CRC 자동 처리) |
| **SHA256 검증** | 다운로드 파일 무결성 검증 |
| **버전 관리** | `kr_patch_state.json`으로 중복 적용 방지 |
| **게임 업데이트 감지** | Steam buildid 추적 → 자동 재적용 안내 |
| **델타 패칭** | `detools` (bsdiff) 기반 차분 패치 지원 |
| **패처 자동 업데이트** | 새 버전 감지 시 자동 교체 |
| **백업 / 복원** | 원본 번들 + catalog.bin 백업 |
| **GUI + CLI** | tkinter GUI (기본) / 커맨드라인 모드 |

## 사용법

### GUI (기본)

`LastEpoch_KR_Patcher.exe` 실행 → 🚀 패치 적용 클릭

### CLI

```bash
# 자동 감지 + 적용
python patcher.py --cli

# 경로 지정
python patcher.py --path "D:\Steam\steamapps\common\Last Epoch"

# 현재 상태 확인
python patcher.py --status

# 강제 재적용
python patcher.py --force

# 백업 복원
python patcher.py --restore

# 패처 자기 업데이트
python patcher.py --self-update
```

## 릴리즈 (관리자용)

### 번역만 업데이트 (대부분)

```bash
# 1. 릴리즈 에셋 생성
python prepare_release.py ko_fix_origin LELocalePatch.exe -v v0.4.3

# 2. release/ 폴더의 zip + SHA256SUMS를 GitHub Release에 업로드
#    exe는 올리지 않음 (기존 exe가 자동으로 최신 번역을 다운받음)
```

### 패처 코드도 바꿨을 때

```bash
# 1. patcher.py 수정 + PATCHER_VERSION 올리기
# 2. 레포에 push
# 3. GitHub에 릴리즈 만들기 (zip + SHA256SUMS)
# 4. Actions 탭 → "Build Patcher EXE" → Run workflow → tag 입력
#    → exe가 해당 릴리즈에 자동 첨부됨
```

### 릴리즈 에셋 구조

```
kr-patch-v0.4.3.zip           # 메인 패치 번들
├── LELocalePatch.exe
├── Skills_ko.json
├── Properties_ko.json
├── Abilities_ko.json
├── UI_ko.json
└── ...                        # 총 22개 JSON (플랫 구조)

SHA256SUMS                     # 체크섬
LastEpoch_KR_Patcher.exe       # 패처 (수동 빌드 시에만 첨부)
```

## 패치 플로우

```
[시작]
  │
  ├─ GitHub API → 최신 릴리즈 확인
  │
  ├─ 이미 최신? ─── YES ──→ 게임 업데이트 감지? ─── NO ──→ [스킵]
  │      │                         │
  │      NO                       YES
  │      │                         │
  │      ▼                         ▼
  ├─ 에셋 분류 (bundle / delta / checksums)
  │
  ├─ 델타 패치 가능? ─── YES ──→ 델타만 다운 + 적용
  │      │
  │      NO
  │      │
  ├─ 전체 zip 다운로드
  ├─ SHA256 검증
  ├─ 백업 (번들 + catalog.bin)
  ├─ LELocalePatch.exe import 실행
  ├─ 상태 저장 (버전, buildid, hash)
  │
  [완료]
```

## 빌드

```bash
pip install pyinstaller detools certifi
pyinstaller --onefile --windowed --name "LastEpoch_KR_Patcher" \
    --hidden-import certifi --collect-data certifi --clean patcher.py
```

또는 GitHub Actions에서 수동 트리거로 빌드 (패처 코드 변경 시에만).

## 선행 조건

- **한국어 번들 필요**: 게임에서 한번 언어를 한국어로 설정해야 번들 파일이 다운로드됨
- **LELocalePatch.exe**: 릴리즈 zip에 포함 (별도 설치 불필요)

## 라이선스

MIT

## Library

- **LELocalePatch** : https://github.com/aianlinb/LELocalePatch
