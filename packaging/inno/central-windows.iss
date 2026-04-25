[Setup]
AppName=黃金訊號中心
AppVersion=1.0.0
AppPublisher=Gold Copy Trader
DefaultDirName={autopf}\黃金訊號中心
DefaultGroupName=黃金訊號中心
OutputDir=..\..\dist\installers
OutputBaseFilename=黃金訊號中心_安裝檔
Compression=lzma2/ultra64
SolidCompression=yes
DisableProgramGroupPage=yes
UninstallDisplayName=黃金訊號中心
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "捷徑："; Flags: checkedonce
Name: "startupicon"; Description: "開機後自動啟動"; GroupDescription: "自動啟動："; Flags: unchecked

[Files]
Source: "..\..\dist\黃金訊號中心\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\黃金訊號中心"; Filename: "{app}\黃金訊號中心.exe"
Name: "{autodesktop}\黃金訊號中心"; Filename: "{app}\黃金訊號中心.exe"; Tasks: desktopicon
Name: "{userstartup}\黃金訊號中心"; Filename: "{app}\黃金訊號中心.exe"; Tasks: startupicon

[Run]
Filename: "{app}\黃金訊號中心.exe"; Description: "啟動黃金訊號中心"; Flags: nowait postinstall skipifsilent
