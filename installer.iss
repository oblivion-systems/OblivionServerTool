; ─────────────────────────────────────────────────────────────────────────────
; Oblivion Server Tool — Inno Setup installer script
;
; Prerequisites:
;   1. Build the exe first:  build.bat
;   2. Compile this script:  ISCC installer.iss
;      (or open in Inno Setup IDE and press F9)
;
; Output: dist\OblivionServerToolSetup-v<version>.exe
; ─────────────────────────────────────────────────────────────────────────────

#define MyAppName      "Oblivion Server Tool"
#define MyAppVersion   "0.11.4"
#define MyAppPublisher "Oblivion"
#define MyAppURL       "https://github.com/jacquesvniekerk-eng/OblivionServerTool"
#define MyAppExeName   "OblivionServerTool.exe"

[Setup]
; Unique GUID — do not change after first release or existing installs lose their
; Add/Remove Programs entry and won't be cleanly updated.
AppId={{3D7A1F2E-9B4C-4D6E-A8F0-12C3E5D7B9A1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases

; Install location
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes

; Appearance & behavior
WizardStyle=modern
AllowNoIcons=yes
PrivilegesRequired=admin
; HKCU is used intentionally for the optional startup run key (per-user, not system-wide)
UsedUserAreasWarning=no

; x64 Windows only (matches PyInstaller target)
ArchitecturesInstallIn64BitMode=x64compatible

; Output
OutputDir=dist
OutputBaseFilename=OblivionServerToolSetup-v{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes

SetupIconFile=emblem.ico
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startupitem"; Description: "Launch {#MyAppName} when &Windows starts"; GroupDescription: "Startup:"

[Files]
; Main executable produced by build.bat → PyInstaller
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; WebView2 bootstrapper — installs the Edge WebView2 runtime if missing.
; Edge WebView2 ships with Windows 11 by default but is NOT preinstalled on
; clean Windows 10.  Without it, the pywebview window will fail to render
; on first launch.  To activate this:
;   1. Download MicrosoftEdgeWebview2Setup.exe (~2 MB) from
;      https://developer.microsoft.com/en-us/microsoft-edge/webview2/
;   2. Place it in the repo root (alongside installer.iss).
;   3. Uncomment the two lines below.
;   4. Re-run ISCC installer.iss.
; The Microsoft bootstrapper is itself idempotent — running /silent /install on
; a system that already has WebView2 is a no-op, so no Check: function needed.
; Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
; Run: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft Edge WebView2 Runtime..."

[Icons]
; IconFilename explicitly set so the Start Menu / Desktop shortcuts always show
; emblem.ico, even if Windows decides to use a generic icon for the .exe
; (happens occasionally on fresh installs before the shell icon cache warms).
Name: "{group}\{#MyAppName}";           Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";     Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; HKCU so no UAC prompt appears when Windows starts and launches the app.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#MyAppName}"; \
  ValueData: """{app}\{#MyAppExeName}"""; \
  Flags: uninsdeletevalue; Tasks: startupitem

[Run]
Filename: "{app}\{#MyAppExeName}"; \
  Description: "Launch {#StringChange(MyAppName, '&', '&&')} now"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Config lives in %APPDATA% — leave it alone on uninstall so settings survive reinstalls.
; Uncomment the lines below only if you want a full wipe on uninstall:
; Type: files;     Name: "{userappdata}\Oblivion Server Tool\oblivion_config.json"
; Type: files;     Name: "{userappdata}\Oblivion Server Tool\oblivion_plugins.json"
; Type: dirifempty; Name: "{userappdata}\Oblivion Server Tool"
