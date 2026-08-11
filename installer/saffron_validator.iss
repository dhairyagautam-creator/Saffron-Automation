; Inno Setup script for Saffron Automation.
;
; Bundles the PyInstaller onedir build (dist\Saffron Automation\) as-is --
; the .exe plus its _internal\ support folder -- into a standard Windows
; installer with Desktop + Start Menu shortcuts, proper Add/Remove Programs
; registration, and an optional "launch after install" step.
;
; Bump MyAppVersion here to match app/version.py's APP_VERSION for every
; release -- see README.md's "Releasing a new version" section for the
; full build steps.
;
; MyChannel must match app/version.py's CHANNEL ("development" or
; "production"). It drives MyAppName/MyAppId below, which in turn drive
; the install directory, Start Menu group, and Add/Remove Programs entry
; -- so a Development build installs completely side-by-side with a
; Production install on the same machine, under a different name, with a
; different App ID, and Windows never treats one as an upgrade of the
; other. For a new Development build: only MyAppVersion changes below;
; MyChannel stays "development".
;
; Build with: iscc installer\saffron_validator.iss
; (or the full path to ISCC.exe if it isn't on PATH yet after a fresh
; Inno Setup install)
; Output: installer_output\Saffron Automation Setup v{MyAppVersion}.exe

#define MyChannel "production"
#define MyAppVersion "2.3.0"
#define MyAppPublisher "Saffron Formulations"
#define MyAppExeName "Saffron Automation.exe"

#if MyChannel == "development"
  #define MyAppName "Saffron Automation (Development)"
  ; Distinct GUID from Production's -- generated once for the Development
  ; channel, never changes between Development releases (same reasoning
  ; as Production's: change it and Windows stops treating consecutive
  ; Development builds as upgrades of each other).
  #define MyAppId "{{A87FC953-B157-43B7-A75A-D870F1FDD8A3}"
#else
  #define MyAppName "Saffron Automation"
  ; Fixed GUID -- never change this between releases, or Windows will treat
  ; each version as a completely different application instead of an upgrade.
  #define MyAppId "{{3DF5DFE8-4CE9-4E88-BF6C-7BA966CC22CC}"
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Per-user install under the current user's own LocalAppData\Programs --
; no admin rights needed to install, matching the app's runtime data (which
; also lives entirely under the user's profile, see app/config.py). This is
; what lets the same Setup .exe be handed to anyone on any Windows PC and
; just work, without an administrator having to be involved at all.
DefaultDirName={localappdata}\Programs\{#MyAppName}
PrivilegesRequired=lowest
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\installer_output
OutputBaseFilename=Saffron Automation Setup v{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; The entire PyInstaller onedir output -- the .exe and its _internal\
; support folder must travel together, never one without the other.
Source: "..\dist\Saffron Automation\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Unchecked by default -- "optional but preferred" per the founder's spec:
; available on the finish page, not forced.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent unchecked
