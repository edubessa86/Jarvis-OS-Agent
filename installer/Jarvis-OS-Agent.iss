#define MyAppName "Jarvis OS Agent"
#define MyAppVersion "1.0.0"
#define MyAppId "{{A5C6D7E8-1B2C-4D5E-8F90-123456789ABC}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=UltimateJarvis
DefaultDirName={localappdata}\JarvisOSBootstrap
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=Jarvis-OS-Agent-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
Uninstallable=no

[Files]
Source: "install.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\Instalar Jarvis OS Agent"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"""; WorkingDir: "{app}"
Name: "{userprograms}\Instalar Jarvis OS Agent"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"""; WorkingDir: "{app}"

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"""; WorkingDir: "{app}"; Flags: postinstall waituntilterminated