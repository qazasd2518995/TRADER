; 版本由 build-release.py 用 /DMyAppVersion=x.y.z 帶進來；
; 直接用 ISCC 跑的話走下面的預設值。
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppName=黃金跟單會員端
AppVersion={#MyAppVersion}
AppPublisher=Gold Copy Trader
DefaultDirName={autopf}\黃金跟單會員端
DefaultGroupName=黃金跟單會員端
OutputDir=..\..\dist\installers
OutputBaseFilename=黃金跟單會員端_{#MyAppVersion}_Windows
Compression=lzma2/ultra64
SolidCompression=yes
DisableProgramGroupPage=yes
UninstallDisplayName=黃金跟單會員端
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Messages]
WelcomeLabel2=這會把「%1」安裝到你的電腦。%n%n本程式未經數位簽章，Windows 可能顯示「已保護您的電腦」的藍色警告——按「其他資訊」→「仍要執行」即可繼續。

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

[UninstallDelete]
; 只清程式目錄，%APPDATA%\黃金跟單系統 底下的設定與跟單狀態刻意保留 ——
; 使用者多半是重裝而不是永久移除，砍掉他要重設一次全部參數。
Type: filesandordirs; Name: "{app}\_internal"
