[Setup]
AppName=黃金跟單系統
AppVersion=1.0.0
AppPublisher=Gold Copy Trader
DefaultDirName={autopf}\黃金跟單系統
DefaultGroupName=黃金跟單系統
OutputDir=dist
OutputBaseFilename=黃金跟單系統_安裝檔
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
DisableProgramGroupPage=yes
UninstallDisplayName=黃金跟單系統
PrivilegesRequired=lowest
; SetupIconFile=
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "dist\黃金跟單系統\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\黃金跟單系統"; Filename: "{app}\黃金跟單系統.exe"
Name: "{autodesktop}\黃金跟單系統"; Filename: "{app}\黃金跟單系統.exe"

[Run]
Filename: "{app}\黃金跟單系統.exe"; Description: "啟動黃金跟單系統"; Flags: nowait postinstall skipifsilent
