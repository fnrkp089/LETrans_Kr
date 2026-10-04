# LETrans_Kr — Last Epoch 한국어 번역패치

게임용 한국어 번역패치 배포 저장소. 번역 작업과 패키지 생성은 [작업장](https://github.com/fnrkp089/lastepoch-kr-patch_workbench)에서 진행함.

## 사용

1. [릴리즈](https://github.com/fnrkp089/LETrans_Kr/releases)에서 `LastEpoch_KR_Patcher-v<버전>-setup.exe`를 받아 설치. 사용자 폴더에 설치하므로 관리자 권한을 묻지 않음.
2. 바탕 화면이나 시작 메뉴의 `Last Epoch 한국어 패치` 실행.
3. 게임 경로를 확인한 뒤 패치 적용.

게임을 종료한 상태에서 실행해야 함. 한국어 번들이 없다면 게임에서 한국어를 한 번 선택해야 함.
새 패처가 나오면 패처가 알려주고 스스로 업데이트하므로 다시 받을 필요 없음.

설치 없이 쓰려면 `LastEpoch_KR_Patcher-v<버전>.zip`을 받아 압축을 풀고 `LastEpoch_KR_Patcher.cmd` 실행.

소스 실행은 Python 3.10 이상에서:

```powershell
py patcher/patcher.py --cli
py patcher/patcher.py --path "D:\Steam\steamapps\common\Last Epoch"
py patcher/patcher.py --status
py patcher/patcher.py --force
py patcher/patcher.py --restore
```

## 패처에 exe가 없는 이유

0.7.4까지는 PyInstaller로 만든 단일 exe를 배포했는데, 서명 없는 PyInstaller exe는 백신의 머신러닝 추정에 자주 걸림(`Trojan:Win32/Wacatac.H!ml` 등 오탐).
0.8.0부터 패처 본체에는 직접 만든 실행 파일을 넣지 않음. 설치 프로그램은 아래 구성을 사용자 폴더에 풀고 바로가기를 만드는 [Inno Setup](https://jrsoftware.org/isinfo.php) 설치본임(`patcher/installer.iss`).

| 구성 | 내용 |
|---|---|
| `LastEpoch_KR_Patcher.cmd` | 실행용 배치 파일. 메모장으로 열어볼 수 있음 |
| `app\*.py` | 이 저장소 `patcher/`의 소스 그대로. 메모장으로 열어볼 수 있음 |
| `runtime\` | [python.org](https://www.python.org/downloads/windows/) 공식 배포본. exe/dll/pyd 전부 Python Software Foundation·Microsoft 서명본 |

`runtime\`의 파일은 우클릭 → 속성 → 디지털 서명에서 서명을 확인할 수 있음.
패키지는 GitHub Actions(`Build Patcher Package`)가 이 저장소 소스에서 빌드하며, 빌드 출처 증명이 함께 기록됨:

```powershell
gh attestation verify LastEpoch_KR_Patcher-v<버전>.zip -R fnrkp089/LETrans_Kr
```

패처가 적용 중에 받아 쓰는 `LELocalePatch.exe`, `LEFontPatch.exe`는 별도 도구이며 SHA256을 확인한 뒤 실행함(아래 원본 도구 참고).

## 패처 동작

- Steam 설치 경로와 게임 buildid 확인.
- 최신 `kr-patch-*.zip`과 해당 `SHA256SUMS` 항목을 확인하고 해시가 일치할 때만 진행.
- 압축 경로 이탈, Windows 특수 경로, 심볼릭 링크, 중복 경로 차단.
- 임시 bundle/catalog에 `LELocalePatch`로 적용하고 재추출한 번역값을 비교한 뒤 실제 파일 교체.
- 같은 게임 빌드의 첫 백업 보존. 백업 해시·파일 쌍 확인, 다른 빌드 복원 거부.
- 같은 버전이라도 현재 번들 해시가 달라지면 재적용.
- 한국어 폰트 변경(선택): `LEFontPatch`로 `resources.assets`와 `PermaLoad.bundle`을 원본에서 다시 패치. 원본은 게임 폴더 `kr_font_backup\`에 백업.
- 검증 가능한 델타 기준 정보가 없으므로 전체 ZIP 사용.

체크섬은 다운로드 무결성을 확인하며 배포자 서명을 대신하지 않음.
LELocalePatch는 실제 번들에 존재하는 키만 수정하며 게임 내 문맥·화면 검수는 별도임.

## 릴리즈 구성

- 번역 릴리즈 `v<버전>`: `kr-patch-<버전>.zip`(LELocalePatch.exe와 `*_ko.json` 테이블), `LEFontPatch.exe`, `SHA256SUMS`, `release_manifest.json`. 패처는 최신 번역 릴리즈를 받아 적용함.
- 패처 릴리즈 `patcher-v<버전>`: `LastEpoch_KR_Patcher-v<버전>-setup.exe`(설치 프로그램), `LastEpoch_KR_Patcher-v<버전>.zip`(설치 없이 쓰는 압축본), `LastEpoch_KR_Patcher-app-v<버전>.zip`(자체 업데이트용, `app\`만), `SHA256SUMS`. 패처 코드가 바뀔 때만 새로 올림. 번역 릴리즈가 계속 Latest여야 하므로 Latest로 지정하지 않음.

## 패처 자체 업데이트

패처는 시작할 때 `patcher-v*` 릴리즈를 확인하고, 새 버전이 있으면 물어본 뒤 스스로 업데이트하고 다시 시작함.

- `LastEpoch_KR_Patcher-app-v<버전>.zip`을 받아 `SHA256SUMS`와 대조.
- 새 소스가 지금 런타임에서 뜨는지 확인한 뒤 `app\` 폴더를 통째로 교체. 실패하면 지금 버전 그대로 둠.
- `runtime\`은 건드리지 않음. Python 버전이 바뀐 업데이트는 자동으로 못 하므로 새 패키지를 받으라고 안내함.
- 소스로 직접 실행한 경우에는 알림만 표시.

번역 릴리즈 준비는 [시즌 작업 가이드](https://github.com/fnrkp089/lastepoch-kr-patch_workbench/blob/main/docs/SEASON5_WORKFLOW.md)의 검증과 패키징 절차 사용.

## 패처 빌드

`patcher.py`, `locale_runner.py`, `workbench_common.py`는 같은 `patcher/` 폴더에 있어야 함.
추가 패키지 설치 없이 Windows의 Python 3.10 이상에서:

```powershell
py patcher/build_package.py --out dist
```

`build_package.py`는 python.org에서 Python 배포본을 받아 SHA256을 확인하고, 쓰지 않는 구성요소를 뺀 뒤 패처 소스와 함께 묶음.
묶은 뒤 exe/dll/pyd의 서명이 전부 유효한지 확인하며 하나라도 아니면 실패함.

`Build Patcher Package` 워크플로는 수동 실행 시 테스트 → 패키지 빌드 → 설치 프로그램 빌드·설치 확인 → 출처 증명 → 아티팩트 업로드까지 함.
`Test patcher` 워크플로는 테스트만 실행함.

```powershell
py -m unittest discover -s tests -v
```

자동 테스트는 임시 파일과 외부 도구 모의 응답 사용. 실제 게임 파일 적용은 배포 전 별도 확인 대상임.

## 원본 도구와 라이선스

- [LELocalePatch](https://github.com/aianlinb/LELocalePatch): Unity 번들 추출/적용 도구.
- [LEFontPatch](https://github.com/fnrkp089/LEFontPatch): 폰트 교체 도구(원작 [aianlinb/LEFontPatch](https://github.com/aianlinb/LEFontPatch)의 포크).
- Python: [PSF License](https://docs.python.org/3/license.html). 패키지의 `runtime\LICENSE.txt` 참고.
- 이 저장소의 라이선스: MIT.
