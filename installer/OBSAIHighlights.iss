#define MyAppName "OBS AI Highlights"
#define MyAppVersion "1.1.1"
#define MyAppPublisher "OBS AI Highlights"
#define MyAppExeName "OBSAIHighlights.exe"
#ifdef AppOnlyUpdate
  #define OutputBaseFilename "OBSAIHighlights-Update-v" + MyAppVersion
#else
  #define OutputBaseFilename "OBSAIHighlights-Setup-v" + MyAppVersion
#endif

[Setup]
AppId={{9F3E9B7A-6C2D-4E1A-9D3F-2B6C7A1E5F40}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\OBS AI Highlights
DefaultGroupName=OBS AI Highlights
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\installer-output
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile=..\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoDescription=OBS AI Highlights Installer
VersionInfoCompany={#MyAppPublisher}

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
#ifdef AppOnlyUpdate
Source: "..\dist\OBSAIHighlights\OBSAIHighlights.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\OBSAIHighlights\_internal\assets\*"; DestDir: "{app}\_internal\assets"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\dist\OBSAIHighlights\_internal\profiles\*"; DestDir: "{app}\_internal\profiles"; Flags: ignoreversion recursesubdirs createallsubdirs
#else
Source: "..\dist\OBSAIHighlights\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#endif
Source: "..\vendor\tesseract\tesseract-ocr-w64-setup-5.4.0.20240606.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\OBS AI Highlights"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{%USERPROFILE}"
Name: "{group}\Uninstall OBS AI Highlights"; Filename: "{uninstallexe}"
Name: "{autodesktop}\OBS AI Highlights"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{%USERPROFILE}"; Tasks: desktopicon

[Run]
; No "skipifsilent" - the app's own in-app updater runs this installer
; silently (see app.py's _launch_installer_and_exit()) and relies on
; this entry to relaunch it afterward, same as an interactive install's
; "Launch..." checkbox already does.
; Keep Tesseract outside {app}: upgrades uninstall the previous app version.
Filename: "{tmp}\tesseract-ocr-w64-setup-5.4.0.20240606.exe"; Parameters: "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=""{localappdata}\OBS AI Highlights\tesseract"""; StatusMsg: "Installing Tesseract OCR (first install only)..."; Flags: runhidden waituntilterminated; Check: not (FileExists(ExpandConstant('{localappdata}\OBS AI Highlights\tesseract\tesseract.exe')) and FileExists(ExpandConstant('{localappdata}\OBS AI Highlights\tesseract\tessdata\eng.traineddata')))
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{%USERPROFILE}"; Description: "Launch OBS AI Highlights"; Flags: nowait postinstall
