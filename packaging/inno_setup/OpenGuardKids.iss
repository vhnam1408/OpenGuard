; Inno Setup 6.3+ Unicode. Build from the repository; see packaging/README.md.
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define RepoRoot SourcePath + "..\.."

[Setup]
AppId={{5E701DEF-89D3-4D53-AB87-7CA63CE05478}
AppName=OpenGuard Kids
AppVersion={#AppVersion}
AppPublisher=OpenGuard Kids
DefaultDirName={autopf}\OpenGuardKids
DisableDirPage=yes
UsePreviousAppDir=no
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0
AllowNetworkDrive=no
AllowUNCPath=no
OutputDir={#RepoRoot}\dist\installer
OutputBaseFilename=OpenGuardKidsSetup
Compression=lzma2
SolidCompression=yes
DiskSpanning=no
WizardStyle=modern
SetupLogging=yes
SetupMutex=Global\OpenGuardKids.Setup
UninstallDisplayIcon={app}\ui\ogk_ui.exe
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
; Do not allow replacement of a loaded SYSTEM executable on a later reboot.
; PrepareToInstall stops the service before files can be replaced.

[Files]
Source: "{#RepoRoot}\dist\ogk_agent\*"; DestDir: "{app}\agent"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#RepoRoot}\dist\ogk_ui\*"; DestDir: "{app}\ui"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "install-support.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "install-support.ps1"; Flags: dontcopy

[Icons]
; Common Startup is evaluated in EACH interactive user's token, never SYSTEM.
; No HKCU writes from the elevated account (which may be a different admin).
Name: "{commonstartup}\OpenGuard Kids"; Filename: "{app}\ui\ogk_ui.exe"; Parameters: "--autostart"; WorkingDir: "{app}\ui"; Comment: "OpenGuard Kids visible User Tray"
Name: "{commonprograms}\OpenGuard Kids\OpenGuard Kids"; Filename: "{app}\ui\ogk_ui.exe"; WorkingDir: "{app}\ui"

[Run]
Filename: "{app}\ui\ogk_ui.exe"; Parameters: "--autostart"; Description: "Start the visible OpenGuard Kids Tray (User)"; Flags: postinstall nowait skipifsilent runasoriginaluser

[Messages]
FinishedHeadingLabel=OpenGuard Kids installed - waiting for enrollment
FinishedLabel=The Agent is installed. The visible Tray runs as User. Pair the intended Windows account before any controls are enabled. If Setup was launched already elevated, open OpenGuard Kids from a normal User session or sign out and sign in again.

[Code]
var
  Prepared: Boolean;
  RestorePreviousService: Boolean;
  Configured: Boolean;

function SupportScript(Uninstalling: Boolean): String;
begin
  if Uninstalling then
    Result := ExpandConstant('{app}\installer\install-support.ps1')
  else
    Result := ExpandConstant('{tmp}\install-support.ps1');
end;

function RunSupport(Action: String; Uninstalling: Boolean; var Code: Integer): Boolean;
var
  Params, LogName: String;
begin
  LogName := ExpandConstant('{tmp}\OpenGuardKids-install-support.log');
  Params := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    SupportScript(Uninstalling) + '" -Action ' + Action +
    ' -InstallDir "' + ExpandConstant('{app}') + '" -LogPath "' + LogName + '"';
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Params, '', SW_HIDE, ewWaitUntilTerminated, Code);
  Log(Format('OpenGuard Kids action %s: launched=%d, exit=%d, log=%s', [
    Action, Ord(Result), Code, LogName]));
end;

function FailureMessage: String;
var
  Details: AnsiString;
begin
  Result := 'OpenGuard Kids installation action failed. See Setup log and ' +
    ExpandConstant('{tmp}\OpenGuardKids-install-support.log');
  if LoadStringFromFile(ExpandConstant('{tmp}\OpenGuardKids-install-support.log'), Details) then
    Result := Result + #13#10 + String(Details);
end;

function InitializeSetup(): Boolean;
begin
  // PrivilegesRequired enforces elevation; the helper also verifies its token.
  Result := IsAdminInstallMode;
  if not Result then
    MsgBox('Administrator permission and UAC approval are required.', mbError, MB_OK);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Result := '';
  if Prepared then exit;
  ExtractTemporaryFile('install-support.ps1');
  if not RunSupport('Prepare', False, Code) then begin
    Result := FailureMessage;
    exit;
  end;
  if (Code <> 0) and (Code <> 10) then begin
    Result := FailureMessage;
    exit;
  end;
  RestorePreviousService := Code = 10;
  Prepared := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
begin
  if CurStep = ssPostInstall then begin
    if not RunSupport('Configure', False, Code) then
      RaiseException(FailureMessage);
    if Code <> 0 then
      RaiseException(FailureMessage);
    Configured := True;
  end;
end;

procedure DeinitializeSetup();
var
  Code: Integer;
begin
  // Restore a previously running service if preparation was cancelled/failed.
  if Prepared and RestorePreviousService and not Configured then
    RunSupport('Restore', False, Code);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  // Run only AFTER the user confirmed removal, BEFORE any file deletion.
  // An exception at usUninstall is fatal in Inno; do not ignore SCM failures.
  if CurUninstallStep = usUninstall then begin
    if not RunSupport('Remove', True, Code) then
      RaiseException(FailureMessage);
    if Code <> 0 then
      RaiseException(FailureMessage);
  end;
end;
