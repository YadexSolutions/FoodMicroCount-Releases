#if VER != 0x06070400
#error Use Inno Setup 6.7.4 for this release candidate.
#endif

#define Product "FoodMicro Count"
#define Publisher "Yaw Agyei Gyasi"

[Setup]
AppId=FoodMicroCount.YawAgyeiGyasi
AppName={#Product}
AppVersion={#AppVersion}
AppVerName={#Product} Version {#DisplayVersion}
AppPublisher={#Publisher}
DefaultDirName={localappdata}\Programs\FoodMicro Count
DefaultGroupName={#Product}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
DisableProgramGroupPage=yes
SetupIconFile=..\foodmicro\assets\icon.ico
UninstallDisplayIcon={app}\FoodMicroCount.exe
LicenseFile=..\LICENSE
InfoBeforeFile=..\WINDOWS_READ_ME_FIRST.txt
InfoAfterFile=..\WINDOWS_FIRST_RUN.txt
OutputDir=..\dist
OutputBaseFilename=FoodMicroCount-{#AppVersion}-Windows-x64-Setup
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#Publisher}
VersionInfoDescription=FoodMicro Count Windows Installer
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\dist\FoodMicroCount\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\FoodMicro Count"; Filename: "{app}\FoodMicroCount.exe"; WorkingDir: "{app}"

Name: "{group}\FoodMicro Count Beginner Guide"; Filename: "{app}\docs\FoodMicroCount-1.0-Beginners-Guide.pdf"

[Run]
Filename: "{app}\FoodMicroCount.exe"; Description: "Open FoodMicro Count"; Flags: nowait postinstall skipifsilent
Filename: "{app}\docs\FoodMicroCount-1.0-Beginners-Guide.pdf"; Description: "Open the illustrated Beginner Guide"; Flags: shellexec postinstall skipifsilent unchecked
