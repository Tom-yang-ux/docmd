; DocMD per-user installer.  It intentionally keeps user tasks/models outside
; the app folder so a 2.0/3.0 upgrade can replace the program safely.
#define AppName "DocMD 高可信 Markdown 工具"
#define AppVersion "1.0.0"
#define AppExeName "DocMD.exe"

[Setup]
AppId={{B8D337C1-5B36-4D16-9C8F-2C59B60F63B7}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=DocMD
DefaultDirName={localappdata}\DocMD\app
DefaultGroupName=DocMD
OutputDir=..\outputs
OutputBaseFilename=DocMD-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
CloseApplications=yes

[Files]
Source: "..\dist\DocMD\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Dirs]
Name: "{userappdata}\docmd"; Flags: uninsneveruninstall
Name: "{userappdata}\docmd\output"; Flags: uninsneveruninstall

[Icons]
Name: "{autoprograms}\DocMD 高可信 Markdown 工具"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\DocMD 高可信 Markdown 工具"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项："; Flags: unchecked

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 DocMD"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
