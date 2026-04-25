[Setup]
AppName=黃金跟單會員端
AppVersion=1.0.0
AppPublisher=Gold Copy Trader
DefaultDirName={autopf}\黃金跟單會員端
DefaultGroupName=黃金跟單會員端
OutputDir=..\..\dist\installers
OutputBaseFilename=黃金跟單會員端_安裝檔
Compression=lzma2/ultra64
SolidCompression=yes
DisableProgramGroupPage=yes
UninstallDisplayName=黃金跟單會員端
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "捷徑："; Flags: checkedonce
Name: "startupicon"; Description: "開機後自動啟動"; GroupDescription: "自動啟動："; Flags: unchecked

[Files]
Source: "..\..\dist\黃金跟單會員端\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\黃金跟單會員端"; Filename: "{app}\黃金跟單會員端.exe"
Name: "{autodesktop}\黃金跟單會員端"; Filename: "{app}\黃金跟單會員端.exe"; Tasks: desktopicon
Name: "{userstartup}\黃金跟單會員端"; Filename: "{app}\黃金跟單會員端.exe"; Tasks: startupicon

[Run]
Filename: "{app}\黃金跟單會員端.exe"; Description: "啟動黃金跟單會員端"; Flags: nowait postinstall skipifsilent
