; Inno Setup 6 script -> dist\installer\SwarLekhak-Setup.exe
; Build:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\SwarLekhak.iss
; UNSIGNED unless you sign it: do not claim SmartScreen approval.

#define AppName "SwarLekhak"
#define AppVersion "0.2.0"

[Setup]
AppId={{6C1F3A52-8E0B-4E59-9B7A-5A11E0B5C0DE}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SwarLekhak project
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist\installer
OutputBaseFilename=SwarLekhak-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\SwarLekhak.exe
LicenseFile=..\docs\THIRD_PARTY_NOTICES.md
InfoBeforeFile=..\docs\PRIVACY.md

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Start SwarLekhak when I sign in (opt-in)"; Flags: unchecked

[Files]
Source: "..\dist\SwarLekhak\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\SwarLekhak"; Filename: "{app}\SwarLekhak.exe"
Name: "{group}\SwarLekhak (demo fixture mode)"; Filename: "{app}\SwarLekhak.exe"; Parameters: "--fixture"
Name: "{group}\SwarLekhak examiner review"; Filename: "{app}\SwarLekhak.exe"; Parameters: "--examiner"
Name: "{group}\Uninstall SwarLekhak"; Filename: "{uninstallexe}"
Name: "{autodesktop}\SwarLekhak"; Filename: "{app}\SwarLekhak.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "SwarLekhak"; \
  ValueData: """{app}\SwarLekhak.exe"""; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\SwarLekhak.exe"; Parameters: "--demo-setup"; Description: "Install the demo examination package"; \
  Flags: postinstall runhidden unchecked skipifsilent
Filename: "{app}\SwarLekhak.exe"; Description: "Launch SwarLekhak"; Flags: postinstall nowait skipifsilent

[Code]
function FlowInstalled(): Boolean;
begin
  Result := DirExists(ExpandConstant('{localappdata}\WisprFlow')) or
            DirExists(ExpandConstant('{localappdata}\Programs\Wispr Flow')) or
            DirExists(ExpandConstant('{localappdata}\Programs\wispr-flow'));
end;

procedure InitializeWizard();
begin
  if not FlowInstalled() then
    SuppressibleMsgBox('Wispr Flow was not found (heuristic check). SwarLekhak uses Wispr Flow to write answers. ' +
           'Install it separately from wisprflow.ai - it is not included in this installer.', mbInformation, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\SwarLekhak');
    if DirExists(DataDir) then
      { silent uninstall keeps the data (IDNO) - deleting evidence must be an explicit choice }
      if SuppressibleMsgBox('Delete your SwarLekhak data (answers, encrypted evidence audio, submissions, settings) in ' +
                DataDir + '? Choose No to keep it.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
