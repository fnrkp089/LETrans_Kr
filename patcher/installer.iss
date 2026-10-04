; 패처 설치 프로그램 (Inno Setup 6)
; build_package.py가 만든 폴더(서명된 Python 런타임 + 패처 소스)를 그대로 설치하고 바로가기를 만듦.
; 이 파일은 한글 때문에 UTF-8 BOM으로 저장해야 함.
;
;   ISCC /DAppVersion=0.8.0 /DSourceDir=dist\LastEpoch_KR_Patcher /Odist installer.iss

#ifndef AppVersion
  #error AppVersion 필요 (/DAppVersion=x.y.z)
#endif
#ifndef SourceDir
  #error SourceDir 필요 (/DSourceDir=패키지 폴더)
#endif

#define AppName "Last Epoch 한국어 패치"
; [Icons]/[Run]의 Parameters 값 안에 들어가므로 따옴표를 두 번 씀
#define Launch '-B ""{app}\app\patcher.py""'

[Setup]
AppId={{6C0E1B5E-6E0C-4B7F-9D4B-2F1E7A53C0A4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Kemmochi
AppPublisherURL=https://github.com/fnrkp089/LETrans_Kr
AppSupportURL=https://github.com/fnrkp089/LETrans_Kr/issues
AppUpdatesURL=https://github.com/fnrkp089/LETrans_Kr/releases
; 사용자 폴더에 설치: 관리자 권한을 묻지 않고, 패처가 app 폴더를 스스로 업데이트할 수 있음
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\LETransKr
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputBaseFilename=LastEpoch_KR_Patcher-v{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\runtime\pythonw.exe
VersionInfoVersion={#AppVersion}
CloseApplications=no

[Languages]
#if FileExists(AddBackslash(CompilerPath) + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#else
Name: "english"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "바탕 화면에 바로가기 만들기"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "{#Launch}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "{#Launch}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: "{#Launch}"; WorkingDir: "{app}"; Description: "{#AppName} 실행"; Flags: postinstall nowait skipifsilent

[InstallDelete]
; 자체 업데이트로 들어온 파일이 새 설치본과 섞이지 않게 함
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\app.old"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\app.old"
