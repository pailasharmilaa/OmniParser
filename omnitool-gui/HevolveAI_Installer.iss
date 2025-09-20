#define MyAppName "HevolveAI Agent Companion"
#define MyAppVersion "1.3"
#define MyAppPublisher "HevolveAI"
#define MyAppURL "https://hevolve.hertzai.com"
#define MyAppExeName "HevolveAiAgentCompanion.exe"
#define MyAppId "{{89DC2F8B-F634-4265-A23D-8BF6ACFEEB58}"

[Setup]
; Basic setup information
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppPublisher}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Require administrator rights for installation
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=HevolveAI_Agent_Companion_Setup
Compression=lzma
SolidCompression=yes
; Use the application icon for the setup
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
; Create the app in Program Files folder
DisableDirPage=no
DisableProgramGroupPage=yes
; Enable Windows 10 style
WizardStyle=modern
; Enable detailed logging
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Start automatically when Windows starts"; GroupDescription: "Windows startup"; Flags: unchecked

[Files]
; Main executable and all dependencies from cx_Freeze build
Source: "build\HevolveAiAgentCompanion\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Include icon file
Source: "app.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Normal shortcuts don't include the --background flag
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\app.ico"

[Run]
; Run without --background after installation to start normally
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent runascurrentuser shellexec

[Registry]
; Registry entry for auto-start - only use --background flag for startup
Root: HKCU; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "HevolveAiAgentCompanion"; ValueData: """{app}\{#MyAppExeName}"" --background"; Flags: uninsdeletevalue; Tasks: startupicon

[Dirs]
; Create the log directory in ProgramData
Name: "{commonappdata}\HevolveAi Agent Companion\logs"; Flags: uninsalwaysuninstall

[Code]
// Check if the .NET Framework 4.5 or higher is installed
function IsDotNetDetected(): boolean;
var
    success: boolean;
    release: cardinal;
    key: string;
begin
    // .NET 4.5+ release key
    key := 'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full';
    success := RegQueryDWordValue(HKLM, key, 'Release', release);
    
    if success then begin
        // Release values >= 378389 correspond to .NET 4.5+
        Result := (release >= 378389);
    end else begin
        Result := False;
    end;
end;

// Fix any duplicate or malformed registry entries
procedure SetupStartupRegistry();
var
    appExePath: string;
    regValue: string;
begin
    // Get the correctly quoted app path with background flag
    appExePath := ExpandConstant('"{app}\{#MyAppExeName}" --background');
    
    // Always clean up any existing registry entries first to avoid duplicates
    if RegValueExists(HKCU, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Run', 'HevolveAiAgentCompanion') then
    begin
        RegDeleteValue(HKCU, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Run', 'HevolveAiAgentCompanion');
        Log('Removed existing registry startup entry');
    end;
    
    // Add new registry entry if the task is selected
    if WizardIsTaskSelected('startupicon') then
    begin
        if RegWriteStringValue(HKCU, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Run', 
                           'HevolveAiAgentCompanion', appExePath) then
        begin
            Log('Successfully added registry startup entry: ' + appExePath);
            
            // Verify the entry was written correctly
            if RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Run', 
                           'HevolveAiAgentCompanion', regValue) then
            begin
                Log('Verified registry entry: ' + regValue);
            end
            else
            begin
                Log('Unable to verify registry entry');
            end;
        end
        else
        begin
            Log('Failed to add registry startup entry');
        end;
    end;
end;

procedure InitializeWizard;
begin
    // Check for requirements
    if not IsDotNetDetected() then
    begin
        MsgBox('This application requires .NET Framework 4.5 or higher. ' +
               'Please install it before continuing with the installation.', mbInformation, MB_OK);
    end;
end;

// Set up startup registry during installation
procedure CurStepChanged(CurStep: TSetupStep);
begin
    if CurStep = ssPostInstall then
    begin
        SetupStartupRegistry();
    end;
end;

// Prompt user before uninstallation
function InitializeUninstall(): Boolean;
begin
    Result := MsgBox('Do you want to uninstall {#MyAppName}? ' +
                    'All application files will be removed.', 
                    mbConfirmation, MB_YESNO) = IDYES;
end;

// Clean up temp files after uninstallation
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
    if CurUninstallStep = usPostUninstall then
    begin
        // Clean up ProgramData directory
        DelTree(ExpandConstant('{commonappdata}\HevolveAi Agent Companion'), True, True, True);
        
        // Make sure to remove the registry entry
        RegDeleteValue(HKCU, 'SOFTWARE\Microsoft\Windows\CurrentVersion\Run', 'HevolveAiAgentCompanion');
    end;
end;