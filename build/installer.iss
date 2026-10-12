; Установщик «СКЛАД РЕКВИЗИТА 2.4» для Inno Setup 6.
; Компиляция:  ISCC.exe build\installer.iss
; Результат:   dist\SkladRekvizita_Setup_2.4.exe

#define AppName "СКЛАД РЕКВИЗИТА 2.4"
#define AppVersion "2.4"
#define AppExe "SkladRekvizita.exe"

[Setup]
AppId={{9F2C6A41-6B7E-4C2B-9E4D-3D5A1B7C8E10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Склад Реквизита
DefaultDirName={autopf}\SkladRekvizita
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=SkladRekvizita_Setup_{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\app.ico
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"

[Files]
; Папочная сборка PyInstaller целиком — Python и все библиотеки внутри
Source: "..\dist\SkladRekvizita\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Запустить {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Удаляется только программа. Рабочая база, фотографии, документы и резервные копии
; остаются в %LOCALAPPDATA%\SkladRekvizita и при переустановке подхватываются заново.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir: String;
  BackupDir: String;
begin
  // Перед обновлением автоматически откладываем копию базы
  if CurStep = ssInstall then
  begin
    DataDir := ExpandConstant('{localappdata}\SkladRekvizita');
    BackupDir := DataDir + '\backups';
    if FileExists(DataDir + '\data.db') then
    begin
      ForceDirectories(BackupDir);
      FileCopy(DataDir + '\data.db',
               BackupDir + '\before_update_' + GetDateTimeString('yyyy-mm-dd_hh-nn-ss', #0, #0) + '.db',
               False);
    end;
  end;
end;
