; MidiPult for Windows - Inno Setup installer script.
; Build: build-installer.ps1 (uses ISCC.exe from Inno Setup 7).
;
; Per-user install (no administrator rights required):
; settings stay in %APPDATA%\MidiPult, program in %LOCALAPPDATA%\Programs\MidiPult.

#define MyAppName "MidiPult"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "RoBiN19666"
#define MyAppURL "https://github.com/RoBiN19666/midipult"
#define MyAppExeName "MidiPult.exe"
; Fixed AppId: keeps upgrades/uninstall of previous versions working.
#define MyAppId "{{861BFEF4-88FB-4EAD-AB49-EEE6C231D351}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
OutputDir=..\dist
OutputBaseFilename=MidiPult-{#MyAppVersion}-setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
VersionInfoVersion={#MyAppVersion}.0
VersionInfoProductVersion={#MyAppVersion}
VersionInfoProductTextVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} - web panel client for Yandex Station Midi
VersionInfoProductName={#MyAppName} for Windows
VersionInfoCompany={#MyAppPublisher}
VersionInfoCopyright=MIT License
LicenseFile=..\LICENSE
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Keep %APPDATA%\MidiPult (stations list and log) after uninstall.
Type: filesandordirs; Name: "{app}\webview"
