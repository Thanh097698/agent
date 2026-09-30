#ifndef AgentVersion
  #define AgentVersion "2.0.0"
#endif
#ifndef AgentBits
  #define AgentBits "32"
#endif
#ifndef AgentDist
  #error AgentDist must point to the architecture-specific dist folder
#endif

[Setup]
AppId={{2A7B08EB-CE46-4A3D-BD55-A6DC3091EB9C}
AppName=ViDental Imaging Agent
AppVersion={#AgentVersion}
AppPublisher=ViDental
DefaultDirName={localappdata}\Programs\ViDental\ImagingAgent
DefaultGroupName=ViDental Imaging Agent
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
MinVersion=10.0
OutputDir={#AgentDist}
OutputBaseFilename=ViDentalImagingAgent-{#AgentVersion}-{#AgentBits}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ViDentalImagingAgent.exe
CloseApplications=yes
CloseApplicationsFilter=ViDentalImagingAgent.exe
RestartApplications=no
#if AgentBits == "64"
ArchitecturesAllowed=x64compatible
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Tự khởi động ViDental Imaging Agent khi đăng nhập Windows"
Name: "desktopicon"; Description: "Tạo biểu tượng trên màn hình Desktop"; Flags: unchecked

[Files]
Source: "{#AgentDist}\ViDentalImagingAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\ViDental Imaging Agent"; Filename: "{app}\ViDentalImagingAgent.exe"
Name: "{userstartup}\ViDental Imaging Agent"; Filename: "{app}\ViDentalImagingAgent.exe"; Parameters: "--quiet"; Tasks: startup
Name: "{userdesktop}\ViDental Imaging Agent"; Filename: "{app}\ViDentalImagingAgent.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ViDentalImagingAgent.exe"; Description: "Mở ViDental Imaging Agent"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{userstartup}\ViDental Imaging Agent.lnk"

[Messages]
SetupWindowTitle=Cài đặt - %1
ButtonBack=< Quay lại
ButtonNext=Tiếp tục >
ButtonInstall=Cài đặt
ButtonCancel=Hủy
ButtonYes=Có
ButtonNo=Không
ButtonFinish=Hoàn tất
WelcomeLabel1=Cài đặt [name]
WelcomeLabel2=Bộ cài sẽ cài [name/ver] trên máy này.%n%nHãy thoát bản ViDental Imaging Agent đang chạy trước khi tiếp tục.
WizardSelectTasks=Tùy chọn khởi động
SelectTasksDesc=Chọn các tùy chọn bạn muốn sử dụng.
SelectTasksLabel2=Chọn tùy chọn cho [name], rồi bấm Tiếp tục.
WizardReady=Sẵn sàng cài đặt
ReadyLabel1=Bộ cài đã sẵn sàng cài [name] trên máy này.
ReadyLabel2a=Bấm Cài đặt để bắt đầu, hoặc Quay lại để thay đổi tùy chọn.
ReadyLabel2b=Bấm Cài đặt để bắt đầu.
WizardInstalling=Đang cài đặt
InstallingLabel=Vui lòng đợi trong khi cài [name].
FinishedHeadingLabel=Đã cài đặt [name]
FinishedLabel=Đã cài [name]. Mở bộ kết nối để tạo mã, sau đó vào web phòng khám và chọn Thiết lập máy này.
FinishedLabelNoIcons=Đã cài [name] trên máy này.
ReadyMemoDir=Thư mục cài đặt:
ReadyMemoTasks=Tùy chọn:
StatusClosingApplications=Đang đóng ứng dụng...
StatusCreateDirs=Đang tạo thư mục...
StatusExtractFiles=Đang giải nén...
StatusCreateIcons=Đang tạo biểu tượng...
StatusSavingUninstall=Đang lưu thông tin gỡ cài đặt...
StatusRunProgram=Đang hoàn tất...
ExitSetupTitle=Thoát cài đặt
ExitSetupMessage=Cài đặt chưa hoàn tất. Bạn muốn thoát không?
