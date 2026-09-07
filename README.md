# LETrans_Kr — Last Epoch 한국어 번역패치

게임용 한국어 번역패치 배포 저장소. 번역 작업과 패키지 생성은 [작업장](https://github.com/fnrkp089/lastepoch-kr-patch_workbench)에서 진행함.

## 사용

릴리즈의 `LastEpoch_KR_Patcher.exe`를 실행하고 게임 경로를 확인한 뒤 패치 적용.
게임을 종료한 상태에서 실행해야 함. 한국어 번들이 없다면 게임에서 한국어를 한 번 선택해야 함.

소스 실행은 Python 3.10 이상에서:

```powershell
py patcher/patcher.py --cli
py patcher/patcher.py --path "D:\Steam\steamapps\common\Last Epoch"
py patcher/patcher.py --status
py patcher/patcher.py --force
py patcher/patcher.py --restore
```

## 패처 0.6.1 변경

이 설명은 저장소의 Python 소스 기준임. 이미 배포한 exe는 새 소스를 빌드해 배포해야 변경됨.

- Steam 설치 경로와 게임 buildid 확인.
- 최신 `kr-patch-*.zip`과 해당 `SHA256SUMS` 항목을 확인하고 해시가 일치할 때만 진행.
- 압축 경로 이탈, Windows 특수 경로, 심볼릭 링크, 중복 경로 차단.
- 임시 bundle/catalog에 `LELocalePatch`로 적용하고 재추출한 번역값을 비교한 뒤 실제 파일 교체.
- 같은 게임 빌드의 첫 백업 보존. 백업 해시·파일 쌍 확인, 다른 빌드 복원 거부.
- 같은 버전이라도 현재 번들 해시가 달라지면 재적용.
- 검증 가능한 델타 기준 정보가 없으므로 전체 ZIP 사용.

체크섬은 다운로드 무결성을 확인하며 배포자 서명을 대신하지 않음.
LELocalePatch는 실제 번들에 존재하는 키만 수정하며 게임 내 문맥·화면 검수는 별도임.

## 번역 릴리즈 준비

[시즌 작업 가이드](https://github.com/fnrkp089/lastepoch-kr-patch_workbench/blob/main/docs/SEASON5_WORKFLOW.md)의 검증과 패키징 절차 사용.
가이드는 작업장 개선 PR 병합 후 main에서 확인 가능함.
작업장의 `release/<버전>/`에서 아래 에셋을 가져와 릴리즈에 업로드:

- `kr-patch-<버전>.zip`: LELocalePatch.exe와 활성 `*_ko.json` 테이블.
- `SHA256SUMS`: ZIP 파일의 SHA256 항목 필수.
- `release_manifest.json`, `RELEASE_NOTES.md`: 생성 기록과 릴리즈 노트 초안.

## 패처 빌드

`patcher.py`, `locale_runner.py`, `workbench_common.py`는 같은 `patcher/` 폴더에 있어야 함.
PyInstaller가 공통 모듈을 자동으로 포함함.

```powershell
cd patcher
py -m pip install pyinstaller certifi
py -m PyInstaller --onefile --windowed --name LastEpoch_KR_Patcher --hidden-import certifi --collect-data certifi --clean patcher.py
```

기존 `Build Patcher EXE` 워크플로는 수동 실행 시 지정 릴리즈에 exe를 첨부함.
새 `Test patcher` 워크플로는 테스트만 실행하며 빌드나 릴리즈 업로드를 하지 않음.

```powershell
py -m unittest discover -s tests -v
```

자동 테스트는 임시 파일과 외부 도구 모의 응답 사용. 실제 Windows exe/게임 파일 적용은 배포 전 별도 확인 대상임.

## 원본 도구와 라이선스

- [LELocalePatch](https://github.com/aianlinb/LELocalePatch): Unity 번들 추출/적용 도구.
- 이 저장소의 라이선스: MIT.
