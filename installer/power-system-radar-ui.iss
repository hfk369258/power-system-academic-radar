; 电力系统文献雷达 — Inno Setup 安装包脚本
; 构建方式（先运行 scripts/build_exe.ps1 生成 dist\_release）：
;   ISCC.exe /DAppVersion=0.3.5 installer\power-system-radar-ui.iss
; 升级安装时沿用旧目录（UsePreviousAppDir），且安装包不含用户数据，
; 因此 profiles/、work/、logs/、凭据文件全部原样保留。

#define AppName "电力系统文献雷达"
#define AppExeName "power-system-radar-ui.exe"

#ifndef AppVersion
#define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7C1E2F4A-9B3D-4E58-A6C0-52D34F1B90AD}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher=Power System Academic Radar
DefaultDirName={localappdata}\PowerSystemRadar
UsePreviousAppDir=yes
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=power-system-radar-ui_v{#AppVersion}_setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务："

[Files]
Source: "..\dist\_release\power-system-radar-ui\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "立即打开文献雷达控制台"; Flags: nowait postinstall skipifsilent

; 新版本可能删除了旧版 _internal 里的文件，安装前整体清掉再重装，避免残留
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  // 关闭正在运行的旧控制台，避免 exe 被占用导致覆盖失败
  Exec(ExpandConstant('{cmd}'), '/C taskkill /IM {#AppExeName} /F >nul 2>&1', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(1500);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // 刻意不清理：profiles/、work/、logs/ 与凭据文件属于用户数据，卸载后保留，
  // 重装或升级后自动继续使用。
end;
