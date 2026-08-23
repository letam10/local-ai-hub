; Inno Setup Script for Local AI Hub V8.0.1 Win64 Desktop Release
; Generated for deterministic, safe Windows desktop installation
; Preserves machine-local data (Models, Environments, runtime, Output, Config, Backups, Reports)

#define MyAppName "Local AI Hub"
#ifndef MyAppVersion
#define MyAppVersion "8.0.1"
#endif
#define MyAppPublisher "Local AI Hub Project"
#define MyAppURL "https://github.com/letam10/local-ai-hub"
#define DefaultInstallDir "{autopf}\Local AI Hub"

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
OutputBaseFilename=LocalAIHub-Setup-Win64-v{#MyAppVersion}
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
; The installer consumes a task-built stable product candidate. Source code is
; inside the versioned payload; user Models/Output/Config never enter {app}.
Source: "..\dist\stable-product\LocalAIHub.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\stable-product\local-ai-hub.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\stable-product\product.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\stable-product\current.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\stable-product\versions\*"; DestDir: "{app}\versions"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\LocalAIHub.exe"; WorkingDir: "{app}"; IconFilename: "{app}\LocalAIHub.exe"; Comment: "Local AI Hub stable installed product"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\LocalAIHub.exe"; WorkingDir: "{app}"; IconFilename: "{app}\LocalAIHub.exe"; Tasks: desktopicon; Comment: "Local AI Hub stable installed product"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\versions"
Type: filesandordirs; Name: "{app}\staging"
Type: files; Name: "{app}\LocalAIHub.exe"
Type: files; Name: "{app}\local-ai-hub.ico"
Type: files; Name: "{app}\product.json"
Type: files; Name: "{app}\installation.json"
Type: files; Name: "{app}\current.json"
Type: files; Name: "{app}\requirements-hub.txt"
Type: files; Name: "{app}\dependencies.lock.json"

[Code]
function JsonPath(const Value: string): string;
begin
  Result := Value;
  StringChangeEx(Result, '\\', '\\\\', True);
  StringChangeEx(Result, '"', '\\"', True);
end;

procedure WriteInstallationConfig;
var
  AppRoot, DataRoot, Payload: string;
  Manifest: string;
begin
  AppRoot := ExpandConstant('{app}');
  DataRoot := GetEnv('LOCALAIHUB_DATA_ROOT');
  if DataRoot = '' then
    DataRoot := ExpandConstant('{userappdata}\LocalAIHub\Data');
  if not ForceDirectories(DataRoot) then
    RaiseException('LOCALAIHUB_DATA_ROOT could not be created.');
  Payload := '{' +
    '"schema_version":"v8.0.1-installation.v1",' +
    '"product_id":"LocalAIHub",' +
    '"app_root":"' + JsonPath(AppRoot) + '",' +
    '"data_root":"' + JsonPath(DataRoot) + '",' +
    '"app_user_model_id":"LocalAIHub.Desktop",' +
    '"launcher":"LocalAIHub.exe"}' + #13#10;
  if not SaveStringToFile(AddBackslash(AppRoot) + 'installation.json', Payload, False) then
    RaiseException('LOCALAIHUB_INSTALLATION_CONFIG_WRITE_FAILED.');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    WriteInstallationConfig;
end;
