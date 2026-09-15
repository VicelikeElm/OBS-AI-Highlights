#define MyAppName "OBS AI Highlights"
#define MyAppVersion "0.4.1"
#define MyAppPublisher "OBS AI Highlights"
#define MyAppExeName "OBSAIHighlights.exe"

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
OutputBaseFilename=OBSAIHighlights-Setup-v{#MyAppVersion}
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
Source: "..\dist\OBSAIHighlights\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\OBS AI Highlights"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{%USERPROFILE}"
Name: "{group}\Uninstall OBS AI Highlights"; Filename: "{uninstallexe}"
Name: "{autodesktop}\OBS AI Highlights"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{%USERPROFILE}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{%USERPROFILE}"; Description: "Launch OBS AI Highlights"; Flags: nowait postinstall skipifsilent
