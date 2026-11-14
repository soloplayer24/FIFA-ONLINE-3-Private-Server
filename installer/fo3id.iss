; setup.exe for the FO3ID private server (Inno Setup 6). Built by installer/build.py, which stages the
; files (the repo's own, the launcher, a Python of its own) and passes AppVersion and Stage.
; No game files: the FO3 ID client must be installed already; setup asks for its folder, then
; installer/setup_pc.py makes the certificate, adds the hosts entry, extracts the player database
; from the client and patches the client. Uninstall puts the client's original files back.

#ifndef AppVersion
  #define AppVersion "dev"
#endif
#ifndef Stage
  #define Stage "build\app"
#endif
#define EnvKey "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

[Setup]
AppId=FO3ID-Private-Server
AppName=FO3ID Private Server
AppVersion={#AppVersion}
AppPublisher=soloplayer24
AppPublisherURL=https://github.com/soloplayer24/FIFA-ONLINE-3-Private-Server
DefaultDirName={sd}\FO3ID Server
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesEnvironment=yes
LicenseFile={#Stage}\LICENSE
OutputDir=..\dist
OutputBaseFilename=FO3ID-Server-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=FO3ID Private Server

[Dirs]
; the server writes its state, logs and caches next to itself
Name: "{app}"; Permissions: users-modify

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Registry]
; the game's folder for play.bat, the server, its tools and the launcher
Root: HKLM; Subkey: "{#EnvKey}"; ValueType: string; ValueName: "FO3_GAME"; ValueData: "{code:GameDir}"; Flags: uninsdeletevalue

[Icons]
Name: "{autodesktop}\FO3ID"; Filename: "{app}\play.bat"; WorkingDir: "{app}"; Comment: "Start the server and the game"
Name: "{autoprograms}\FO3ID\FO3ID"; Filename: "{app}\play.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\FO3ID\Set up this PC again"; Filename: "{app}\python\python.exe"; Parameters: """{app}\installer\setup_pc.py"""; WorkingDir: "{app}"

[Run]
Filename: "{app}\python\python.exe"; Parameters: """{app}\installer\setup_pc.py"" --game ""{code:GameDir}"""; WorkingDir: "{app}"; StatusMsg: "Setting up this PC and the game (a few minutes)..."; Flags: runhidden waituntilterminated
Filename: "{app}\installer\setup.log"; Description: "Show what setup did"; Flags: postinstall shellexec skipifsilent unchecked
Filename: "{app}\play.bat"; WorkingDir: "{app}"; Description: "Play now"; Flags: postinstall shellexec skipifsilent nowait runasoriginaluser

[UninstallRun]
; the game's original files back (tools/fo4_season.py install kept them in {app}\backup)
Filename: "{app}\python\python.exe"; Parameters: """{app}\tools\fo4_season.py"" restore --game ""{reg:HKLM\{#EnvKey},FO3_GAME|D:\FO3ID\32795}"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "RestoreClient"
Filename: "{app}\python\python.exe"; Parameters: """{app}\tools\control_presets.py"" --remove --game ""{reg:HKLM\{#EnvKey},FO3_GAME|D:\FO3ID\32795}"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "RestoreKontrol"

[UninstallDelete]
; made by setup and the server, not by the installer
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\tools\__pycache__"
Type: filesandordirs; Name: "{app}\tools\re\__pycache__"

[Code]
var
  GamePage: TInputDirWizardPage;

procedure InitializeWizard;
begin
  GamePage := CreateInputDirPage(wpSelectDir, 'FIFA Online 3 Indonesia',
    'Where is the game installed?',
    'Select the FO3 ID game folder (the one with fifazf.exe). Close the game before you continue: setup patches it.',
    False, '');
  GamePage.Add('');
  GamePage.Values[0] := GetEnv('FO3_GAME');
  if GamePage.Values[0] = '' then
    GamePage.Values[0] := 'D:\FO3ID\32795';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = GamePage.ID) and not FileExists(AddBackslash(GamePage.Values[0]) + 'fifazf.exe') then
  begin
    MsgBox('fifazf.exe is not in that folder. Select the FO3 ID game folder.', mbError, MB_OK);
    Result := False;
  end;
end;

function GameDir(Param: String): String;
begin
  Result := GamePage.Values[0];
end;
