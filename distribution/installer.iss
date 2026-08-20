; Inno Setup Script for Local AI Hub V6 Win64 Desktop Release
; Generated for deterministic, safe Windows desktop installation
; Preserves machine-local data (Models, Environments, runtime, Output, Config, Backups, Reports)

#define MyAppName "Local AI Hub"
#define MyAppVersion "7.0.0"
#define MyAppPublisher "Local AI Hub Project"
#define MyAppURL "https://github.com/letam10/local-ai-hub"
#define DefaultInstallDir "D:\LocalAIHub"

[Setup]
AppId={{D37E84B1-2F16-4E89-9B21-085781E738C4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={#DefaultInstallDir}
DisableDirPage=no
DirExistsWarning=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=LocalAIHub-Setup-Win64-v7.0.0
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableWelcomePage=no
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"; LicenseFile: "..\LICENSES.md"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Core application source and distribution files (NEVER includes Models, Environments, runtime, or Output)
Source: "..\src\*"; DestDir: "{app}\src"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\scripts\*"; DestDir: "{app}\scripts"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\distribution\*"; DestDir: "{app}\distribution"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\architecture\*"; DestDir: "{app}\architecture"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\requirements-hub.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dependencies.lock.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\Config\*.example.json"; DestDir: "{app}\Config"; Flags: ignoreversion
Source: "..\LocalAIHub.vbs"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LocalAIHub.cmd"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\LocalAIHub.vbs"""; WorkingDir: "{app}"; Comment: "Local AI Hub Desktop Application"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\LocalAIHub.vbs"""; WorkingDir: "{app}"; Tasks: desktopicon; Comment: "Local AI Hub Desktop Application"

[Run]
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\scripts\update_managed_shortcuts.ps1"" -Apply"; Flags: runhidden; Description: "Register Windows shortcuts"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\src"
Type: filesandordirs; Name: "{app}\distribution"
Type: filesandordirs; Name: "{app}\docs"
Type: files; Name: "{app}\requirements-hub.txt"
Type: files; Name: "{app}\dependencies.lock.json"
Type: files; Name: "{app}\LocalAIHub.vbs"
Type: files; Name: "{app}\LocalAIHub.cmd"
