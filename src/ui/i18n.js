const LANGUAGE_STORAGE_KEY = "local-ai-hub-language";

export const LANGUAGE_OPTIONS = Object.freeze([
  { id: "vi", label: "Tiếng Việt" },
  { id: "en", label: "English" },
  { id: "zh", label: "简体中文" },
  { id: "ja", label: "日本語" },
  { id: "ko", label: "한국어" },
]);

const LANGUAGE_IDS = new Set(LANGUAGE_OPTIONS.map((item) => item.id));

// These are intentionally UI phrases rather than model/provider names.  The
// server remains the source of truth for status, while the client translates
// only safe labels and helper copy after rendering.
const DICTIONARIES = Object.freeze({
  vi: Object.freeze({
    "Dashboard": "Bảng điều khiển", "Bảng điều khiển": "Bảng điều khiển",
    "Settings": "Cài đặt", "Jobs": "Tác vụ", "Models & Storage": "Mô hình & Lưu trữ",
    "Projects & Recipes": "Dự án & Công thức", "Image AI": "Hình ảnh AI", "Media": "Phương tiện",
    "Video Creative": "Sáng tạo video", "Vision Studio": "Studio thị giác", "Reason & next action": "Lý do & hành động tiếp theo",
    "Recovery reason": "Lý do khôi phục", "Next action": "Hành động tiếp theo", "Module preflight": "Kiểm tra module",
    "Resource preflight": "Kiểm tra tài nguyên", "Storage constraints": "Ràng buộc lưu trữ", "Server-owned snapshot": "Ảnh chụp do máy chủ sở hữu",
    "Operational": "Đang hoạt động", "Healthy": "Khỏe mạnh", "Ready": "Sẵn sàng", "Partial": "Một phần",
    "Unavailable": "Chưa khả dụng", "Not published": "Chưa công bố", "Not run": "Chưa chạy", "Error": "Lỗi",
    "Available": "Có sẵn", "Installed": "Đã cài", "Missing": "Thiếu", "Unknown": "Không rõ", "Queued": "Đang xếp hàng",
    "Starting": "Đang khởi động", "Running": "Đang chạy", "Failed": "Thất bại", "Completed": "Hoàn tất",
    "No recovery reason was published in this snapshot.": "Snapshot này không công bố lý do khôi phục.",
    "No additional reason was published in this server snapshot.": "Snapshot máy chủ này không có lý do bổ sung.",
    "Review the server-owned evidence before runtime work.": "Xem bằng chứng do máy chủ sở hữu trước khi chạy runtime.",
    "Close navigation": "Đóng điều hướng", "Open navigation": "Mở điều hướng", "Expand navigation": "Mở rộng điều hướng",
    "Collapse navigation": "Thu gọn điều hướng", "Reason & next action": "Lý do & hành động tiếp theo",
    "active": "đang hoạt động", "records": "bản ghi", "Refresh snapshot": "Làm mới snapshot",
    "Language": "Ngôn ngữ", "Ngôn ngữ": "Ngôn ngữ", "Change theme": "Đổi giao diện", "Đổi giao diện": "Đổi giao diện",
    "Module navigation": "Điều hướng module", "Điều hướng module": "Điều hướng module", "Local AI center": "Trung tâm AI cục bộ", "Trung tâm AI cục bộ": "Trung tâm AI cục bộ",
    "Loopback only · no CDN": "Chỉ loopback · không CDN", "Chỉ loopback · không CDN": "Chỉ loopback · không CDN",
  }),
  en: Object.freeze({
    "Bảng điều khiển": "Dashboard", "Cài đặt": "Settings", "Tác vụ": "Jobs", "Mô hình & Lưu trữ": "Models & Storage",
    "Dự án & Công thức": "Projects & Recipes", "Hình ảnh AI": "Image AI", "Phương tiện": "Media", "Sáng tạo video": "Video Creative",
    "Studio thị giác": "Vision Studio", "Lý do & hành động tiếp theo": "Reason & next action", "Lý do khôi phục": "Recovery reason",
    "Hành động tiếp theo": "Next action", "Kiểm tra module": "Module preflight", "Kiểm tra tài nguyên": "Resource preflight",
    "Ràng buộc lưu trữ": "Storage constraints", "Ảnh chụp do máy chủ sở hữu": "Server-owned snapshot", "Đang hoạt động": "Operational",
    "Khỏe mạnh": "Healthy", "Sẵn sàng": "Ready", "Một phần": "Partial", "Chưa khả dụng": "Unavailable", "Chưa công bố": "Not published",
    "Chưa chạy": "Not run", "Lỗi": "Error", "Có sẵn": "Available", "Đã cài": "Installed", "Thiếu": "Missing", "Không rõ": "Unknown",
    "Đang xếp hàng": "Queued", "Đang khởi động": "Starting", "Đang chạy": "Running", "Thất bại": "Failed", "Hoàn tất": "Completed",
    "Snapshot này không công bố lý do khôi phục.": "No recovery reason was published in this snapshot.",
    "Snapshot máy chủ này không có lý do bổ sung.": "No additional reason was published in this server snapshot.",
    "Xem bằng chứng do máy chủ sở hữu trước khi chạy runtime.": "Review the server-owned evidence before runtime work.",
    "Đóng điều hướng": "Close navigation", "Mở điều hướng": "Open navigation", "Mở rộng điều hướng": "Expand navigation",
    "Thu gọn điều hướng": "Collapse navigation", "đang hoạt động": "active", "bản ghi": "records", "Làm mới snapshot": "Refresh snapshot",
    "Ngôn ngữ": "Language", "Đổi giao diện": "Change theme", "Điều hướng module": "Module navigation", "Trung tâm AI cục bộ": "Local AI center", "Chỉ loopback · không CDN": "Loopback only · no CDN",
  }),
  zh: Object.freeze({
    "Dashboard": "控制面板", "Bảng điều khiển": "控制面板", "Settings": "设置", "Cài đặt": "设置", "Jobs": "任务", "Tác vụ": "任务",
    "Models & Storage": "模型与存储", "Mô hình & Lưu trữ": "模型与存储", "Projects & Recipes": "项目与配方", "Dự án & Công thức": "项目与配方",
    "Image AI": "图像 AI", "Hình ảnh AI": "图像 AI", "Media": "媒体", "Phương tiện": "媒体", "Video Creative": "视频创作", "Sáng tạo video": "视频创作",
    "Vision Studio": "视觉工作室", "Studio thị giác": "视觉工作室", "Reason & next action": "原因与下一步", "Lý do & hành động tiếp theo": "原因与下一步",
    "Recovery reason": "恢复原因", "Lý do khôi phục": "恢复原因", "Next action": "下一步", "Hành động tiếp theo": "下一步",
    "Operational": "可运行", "Đang hoạt động": "可运行", "Healthy": "健康", "Khỏe mạnh": "健康", "Ready": "就绪", "Sẵn sàng": "就绪",
    "Partial": "部分可用", "Một phần": "部分可用", "Unavailable": "不可用", "Chưa khả dụng": "不可用", "Not run": "未运行", "Chưa chạy": "未运行",
    "Installed": "已安装", "Đã cài": "已安装", "Missing": "缺失", "Thiếu": "缺失", "Failed": "失败", "Thất bại": "失败", "Completed": "已完成", "Hoàn tất": "已完成",
    "Close navigation": "关闭导航", "Đóng điều hướng": "关闭导航", "Open navigation": "打开导航", "Mở điều hướng": "打开导航",
    "Refresh snapshot": "刷新快照", "Làm mới snapshot": "刷新快照", "active": "活动", "đang hoạt động": "活动", "records": "条记录", "bản ghi": "条记录",
    "Ngôn ngữ": "语言", "Đổi giao diện": "切换主题", "Điều hướng module": "模块导航", "Trung tâm AI cục bộ": "本地 AI 中心", "Chỉ loopback · không CDN": "仅本地回环 · 无 CDN",
  }),
  ja: Object.freeze({
    "Dashboard": "ダッシュボード", "Bảng điều khiển": "ダッシュボード", "Settings": "設定", "Cài đặt": "設定", "Jobs": "ジョブ", "Tác vụ": "ジョブ",
    "Models & Storage": "モデルとストレージ", "Mô hình & Lưu trữ": "モデルとストレージ", "Projects & Recipes": "プロジェクトとレシピ", "Dự án & Công thức": "プロジェクトとレシピ",
    "Image AI": "画像 AI", "Hình ảnh AI": "画像 AI", "Media": "メディア", "Phương tiện": "メディア", "Video Creative": "動画クリエイティブ", "Sáng tạo video": "動画クリエイティブ",
    "Vision Studio": "ビジョンスタジオ", "Studio thị giác": "ビジョンスタジオ", "Reason & next action": "理由と次のアクション", "Lý do & hành động tiếp theo": "理由と次のアクション",
    "Recovery reason": "復旧理由", "Lý do khôi phục": "復旧理由", "Next action": "次のアクション", "Hành động tiếp theo": "次のアクション",
    "Operational": "稼働中", "Đang hoạt động": "稼働中", "Healthy": "正常", "Khỏe mạnh": "正常", "Ready": "準備完了", "Sẵn sàng": "準備完了",
    "Partial": "一部利用可", "Một phần": "一部利用可", "Unavailable": "利用不可", "Chưa khả dụng": "利用不可", "Not run": "未実行", "Chưa chạy": "未実行",
    "Installed": "インストール済み", "Đã cài": "インストール済み", "Missing": "不足", "Thiếu": "不足", "Failed": "失敗", "Thất bại": "失敗", "Completed": "完了", "Hoàn tất": "完了",
    "Close navigation": "ナビゲーションを閉じる", "Đóng điều hướng": "ナビゲーションを閉じる", "Open navigation": "ナビゲーションを開く", "Mở điều hướng": "ナビゲーションを開く",
    "Refresh snapshot": "スナップショットを更新", "Làm mới snapshot": "スナップショットを更新", "active": "アクティブ", "đang hoạt động": "アクティブ", "records": "件", "bản ghi": "件",
    "Ngôn ngữ": "言語", "Đổi giao diện": "テーマを切り替える", "Điều hướng module": "モジュールナビゲーション", "Trung tâm AI cục bộ": "ローカル AI センター", "Chỉ loopback · không CDN": "ループバックのみ・CDN なし",
  }),
  ko: Object.freeze({
    "Dashboard": "대시보드", "Bảng điều khiển": "대시보드", "Settings": "설정", "Cài đặt": "설정", "Jobs": "작업", "Tác vụ": "작업",
    "Models & Storage": "모델 및 저장소", "Mô hình & Lưu trữ": "모델 및 저장소", "Projects & Recipes": "프로젝트 및 레시피", "Dự án & Công thức": "프로젝트 및 레시피",
    "Image AI": "이미지 AI", "Hình ảnh AI": "이미지 AI", "Media": "미디어", "Phương tiện": "미디어", "Video Creative": "비디오 크리에이티브", "Sáng tạo video": "비디오 크리에이티브",
    "Vision Studio": "비전 스튜디오", "Studio thị giác": "비전 스튜디오", "Reason & next action": "이유 및 다음 작업", "Lý do & hành động tiếp theo": "이유 및 다음 작업",
    "Recovery reason": "복구 이유", "Lý do khôi phục": "복구 이유", "Next action": "다음 작업", "Hành động tiếp theo": "다음 작업",
    "Operational": "작동 중", "Đang hoạt động": "작동 중", "Healthy": "정상", "Khỏe mạnh": "정상", "Ready": "준비됨", "Sẵn sàng": "준비됨",
    "Partial": "부분 사용 가능", "Một phần": "부분 사용 가능", "Unavailable": "사용 불가", "Chưa khả dụng": "사용 불가", "Not run": "실행 안 함", "Chưa chạy": "실행 안 함",
    "Installed": "설치됨", "Đã cài": "설치됨", "Missing": "누락", "Thiếu": "누락", "Failed": "실패", "Thất bại": "실패", "Completed": "완료", "Hoàn tất": "완료",
    "Close navigation": "탐색 닫기", "Đóng điều hướng": "탐색 닫기", "Open navigation": "탐색 열기", "Mở điều hướng": "탐색 열기",
    "Refresh snapshot": "스냅샷 새로 고침", "Làm mới snapshot": "스냅샷 새로 고침", "active": "활성", "đang hoạt động": "활성", "records": "개 기록", "bản ghi": "개 기록",
    "Ngôn ngữ": "언어", "Đổi giao diện": "테마 변경", "Điều hướng module": "모듈 탐색", "Trung tâm AI cục bộ": "로컬 AI 허브", "Chỉ loopback · không CDN": "루프백 전용 · CDN 없음",
  }),
});

// High-frequency headings and guidance copy emitted by pages.js.  Keeping
// these phrases in one supplemental table makes the five choices useful even
// when a page mixes legacy English labels with Vietnamese server explanations.
const EXTRA_DICTIONARIES = Object.freeze({
  vi: Object.freeze({
    "CONTROL PLANE": "BẢNG ĐIỀU KHIỂN", "MEDIA CAPABILITY EVIDENCE": "BẰNG CHỨNG KHẢ NĂNG MEDIA",
    "Exact media operation scope": "Phạm vi thao tác media chính xác", "Server snapshot only; the UI does not execute media operations.": "Chỉ là snapshot máy chủ; giao diện không thực thi thao tác media.",
    "WORKFLOW LIBRARY": "THƯ VIỆN WORKFLOW", "Module plan": "Kế hoạch module", "Static readiness snapshot": "Snapshot sẵn sàng tĩnh",
    "Preflight is read-only; install/download is not_run": "Preflight chỉ đọc; cài đặt/tải xuống chưa chạy", "STORAGE PROJECTION": "TỔNG QUAN LƯU TRỮ",
    "JOB RECOVERY": "KHÔI PHỤC TÁC VỤ", "Recovery attention": "Cần chú ý khôi phục", "Open focused Jobs": "Mở Tác vụ cần xem",
    "Check readiness": "Kiểm tra mức sẵn sàng", "Choose a route": "Chọn khu vực làm việc", "Run from Jobs": "Chạy từ Tác vụ",
    "Review module health and attention.": "Xem tình trạng module và các mục cần chú ý.", "Open an existing Hub workspace.": "Mở khu vực làm việc có sẵn trong Hub.",
    "Keep progress and artifacts in Hub.": "Giữ tiến trình và artifact trong Hub.", "Reason": "Lý do", "Outcome": "Kết quả", "Execution": "Thực thi",
    "Cleanup": "Dọn dẹp", "Source overwrite": "Ghi đè nguồn", "Total": "Tổng", "Free": "Trống", "Used": "Đã dùng",
    "Artifact preview unavailable": "Không có bản xem trước artifact", "Preview unavailable": "Bản xem trước không khả dụng",
    "No module rows published": "Snapshot chưa công bố module nào", "The server snapshot contains no safe module projection.": "Snapshot máy chủ không có projection module an toàn.",
    "Open Media / Node Studio": "Mở Media / Node Studio", "Review the server snapshot": "Xem snapshot máy chủ", "Readiness & Module Plan": "Mức sẵn sàng & Kế hoạch module",
    "Low space": "Sắp hết dung lượng", "SERVER-OWNED VOLUME": "Ổ ĐĨA DO MÁY CHỦ SỞ HỮU", "RESOURCE PREFLIGHT": "PREFLIGHT TÀI NGUYÊN",
    "STORAGE CONSTRAINTS": "RÀNG BUỘC LƯU TRỮ", "Allowlisted volume constraints": "Ràng buộc ổ đĩa theo allowlist", "Dry-run resource fit": "Độ phù hợp tài nguyên mô phỏng",
    "Mode": "Chế độ", "Target": "Đích", "Source": "Nguồn", "Active": "Đang hoạt động", "Attention": "Cần chú ý", "Interrupted": "Bị gián đoạn", "Recoverable": "Có thể khôi phục",
    "LIFECYCLE": "VÒNG ĐỜI", "Created": "Tạo lúc", "Started": "Bắt đầu lúc", "Finished": "Kết thúc lúc", "Hot": "Tác vụ nóng", "Durable": "Bền vững",
    "Artifact preview unavailable": "Không có bản xem trước artifact", "Preview unavailable in this snapshot.": "Snapshot này không có bản xem trước.",
    "No recovery action is available from this server snapshot.": "Snapshot máy chủ này không có hành động khôi phục.", "No jobs in this filter": "Không có tác vụ trong bộ lọc này", "Choose All to see the complete server snapshot.": "Chọn Tất cả để xem toàn bộ snapshot máy chủ.",
    "Direct workflow": "Workflow trực tiếp", "Workflow": "Workflow", "rows": "dòng", "Source:": "Nguồn:", "execution:": "thực thi:", "dry_run:": "chạy thử:",
    "MODULE": "MÔ-ĐUN", "VISION & DOCUMENT": "THỊ GIÁC & TÀI LIỆU", "SPEECH & VOICE": "GIỌNG NÓI & ÂM THANH", "IMAGE & VIDEO": "HÌNH ẢNH & VIDEO", "CREATIVE WORKSPACE": "KHÔNG GIAN SÁNG TẠO", "MODULE HEALTH": "TÌNH TRẠNG MÔ-ĐUN",
    "server-owned": "do máy chủ sở hữu", "Server-owned": "Do máy chủ sở hữu", "allowlisted": "được cho phép", "not_run": "chưa chạy", "Clean": "Sạch", "preserved": "được bảo toàn", "attention": "cần chú ý", "completed": "đã hoàn tất", "queue": "hàng đợi",
    "A bounded exact scope is published for this fixture state.": "Snapshot hiện tại đã công bố phạm vi chính xác có giới hạn.", "Use only the listed operations with opaque artifacts.": "Chỉ dùng thao tác được liệt kê với artifact opaque.", "Review detailed evidence": "Xem bằng chứng chi tiết", "Open server snapshot in": "Mở snapshot máy chủ trong",
    "Review output, cache, and temporary data before new writes.": "Kiểm tra output, bộ nhớ đệm và dữ liệu tạm trước khi ghi mới.", "Verify that the volume is mounted and readable, then refresh storage.": "Xác nhận ổ đĩa đã gắn và đọc được, sau đó làm mới lưu trữ.", "Low-space warning": "Cảnh báo sắp hết dung lượng",
    "Review the plan; no install, repair or uninstall action is available.": "Xem kế hoạch; không có thao tác cài đặt, sửa chữa hoặc gỡ cài đặt.", "Open Tác vụ to review recovery actions and opaque artifact availability.": "Mở Tác vụ để xem hành động khôi phục và artifact opaque.",
    "Workflow bền vững, local-first": "Workflow bền vững, ưu tiên cục bộ", "Workflow Library server-owned adapter chưa được V5-D wire.": "Bộ thư viện Workflow do máy chủ sở hữu chưa được kết nối trong V5-D.", "Tiếp tục local draft; xác nhận endpoint typed trước khi đồng bộ.": "Tiếp tục bản nháp cục bộ; xác nhận endpoint có kiểu trước khi đồng bộ.", "Graph chỉ là declarative metadata; không tự chạy node hoặc GPU khi chỉnh sửa.": "Graph chỉ là metadata khai báo; không tự chạy node hoặc GPU khi chỉnh sửa.",
    "Trống space is below the 20 GiB low-space threshold.": "Dung lượng trống thấp hơn ngưỡng cảnh báo 20 GiB.", "Volume statistics are unavailable; no figures are shown.": "Không có thống kê ổ đĩa; không hiển thị số liệu.", "C: review storage before new writes.": "C: kiểm tra lưu trữ trước khi ghi mới.", "The server recovery snapshot mixes đang hoạt động, cần chú ý and đã hoàn tất bản ghi.": "Snapshot khôi phục máy chủ gồm bản ghi đang hoạt động, cần chú ý và đã hoàn tất.", "Open Tác vụ to review recovery actions and opaque artifact availability.": "Mở Tác vụ để xem hành động khôi phục và artifact opaque.", "Kiểm tra module is do máy chủ sở hữu static metadata.": "Kiểm tra module là metadata tĩnh do máy chủ sở hữu.",
    "Compose và chỉnh sửa ảnh": "Soạn và chỉnh sửa ảnh", "Transform media trong Hub": "Biến đổi media trong Hub", "Theo dõi queue và artifact": "Theo dõi hàng đợi và artifact", "Kiểm tra inventory": "Kiểm tra danh mục",
  }),
  en: Object.freeze({
    "BẢNG ĐIỀU KHIỂN": "CONTROL PLANE", "BẰNG CHỨNG KHẢ NĂNG MEDIA": "MEDIA CAPABILITY EVIDENCE", "Phạm vi thao tác media chính xác": "Exact media operation scope",
    "Chỉ là snapshot máy chủ; giao diện không thực thi thao tác media.": "Server snapshot only; the UI does not execute media operations.", "THƯ VIỆN WORKFLOW": "WORKFLOW LIBRARY", "Kế hoạch module": "Module plan",
    "Snapshot sẵn sàng tĩnh": "Static readiness snapshot", "Preflight chỉ đọc; cài đặt/tải xuống chưa chạy": "Preflight is read-only; install/download is not_run", "TỔNG QUAN LƯU TRỮ": "STORAGE PROJECTION",
    "KHÔI PHỤC TÁC VỤ": "JOB RECOVERY", "Cần chú ý khôi phục": "Recovery attention", "Mở Tác vụ cần xem": "Open focused Jobs", "Kiểm tra mức sẵn sàng": "Check readiness", "Chọn khu vực làm việc": "Choose a route", "Chạy từ Tác vụ": "Run from Jobs",
    "Lý do": "Reason", "Kết quả": "Outcome", "Thực thi": "Execution", "Dọn dẹp": "Cleanup", "Ghi đè nguồn": "Source overwrite", "Tổng": "Total", "Trống": "Free", "Đã dùng": "Used",
    "Không có bản xem trước artifact": "Artifact preview unavailable", "Bản xem trước không khả dụng": "Preview unavailable", "Mở Media / Node Studio": "Open Media / Node Studio", "Xem snapshot máy chủ": "Review the server snapshot", "Mức sẵn sàng & Kế hoạch module": "Readiness & Module Plan",
    "Sắp hết dung lượng": "Low space", "Chế độ": "Mode", "Đích": "Target", "Nguồn": "Source", "Đang hoạt động": "Active", "Cần chú ý": "Attention", "Bị gián đoạn": "Interrupted", "Có thể khôi phục": "Recoverable",
    "VÒNG ĐỜI": "LIFECYCLE", "Tạo lúc": "Created", "Bắt đầu lúc": "Started", "Kết thúc lúc": "Finished", "Tác vụ nóng": "Hot", "Bền vững": "Durable", "Snapshot này không có bản xem trước.": "Preview unavailable in this snapshot.",
    "Snapshot máy chủ này không có hành động khôi phục.": "No recovery action is available from this server snapshot.", "Không có tác vụ trong bộ lọc này": "No jobs in this filter", "Chọn Tất cả để xem toàn bộ snapshot máy chủ.": "Choose All to see the complete server snapshot.",
    "Workflow trực tiếp": "Direct workflow", "dòng": "rows", "Nguồn:": "Source:", "thực thi:": "execution:", "chạy thử:": "dry_run:",
    "MÔ-ĐUN": "MODULE", "THỊ GIÁC & TÀI LIỆU": "VISION & DOCUMENT", "GIỌNG NÓI & ÂM THANH": "SPEECH & VOICE", "HÌNH ẢNH & VIDEO": "IMAGE & VIDEO", "KHÔNG GIAN SÁNG TẠO": "CREATIVE WORKSPACE", "TÌNH TRẠNG MÔ-ĐUN": "MODULE HEALTH", "Do máy chủ sở hữu": "Server-owned", "được cho phép": "allowlisted", "chưa chạy": "not_run", "Sạch": "Clean", "được bảo toàn": "preserved", "cần chú ý": "attention", "đã hoàn tất": "completed", "hàng đợi": "queue",
    "Snapshot hiện tại đã công bố phạm vi chính xác có giới hạn.": "A bounded exact scope is published for this fixture state.", "Chỉ dùng thao tác được liệt kê với artifact opaque.": "Use only the listed operations with opaque artifacts.", "Xem bằng chứng chi tiết": "Review detailed evidence", "Mở snapshot máy chủ trong": "Open server snapshot in", "Cảnh báo sắp hết dung lượng": "Low-space warning",
  }),
  zh: Object.freeze({
    "CONTROL PLANE": "控制平面", "MEDIA CAPABILITY EVIDENCE": "媒体能力证据", "Exact media operation scope": "精确媒体操作范围", "Server snapshot only; the UI does not execute media operations.": "仅显示服务器快照；界面不会执行媒体操作。",
    "WORKFLOW LIBRARY": "工作流库", "Module plan": "模块计划", "Static readiness snapshot": "静态就绪快照", "Preflight is read-only; install/download is not_run": "预检为只读；未运行安装或下载", "STORAGE PROJECTION": "存储概览", "JOB RECOVERY": "任务恢复", "Recovery attention": "恢复注意事项", "Open focused Jobs": "打开重点任务",
    "Check readiness": "检查就绪状态", "Choose a route": "选择工作区", "Run from Jobs": "从任务运行", "Reason": "原因", "Outcome": "结果", "Execution": "执行", "Cleanup": "清理", "Source overwrite": "源覆盖", "Total": "总计", "Free": "可用", "Used": "已用",
    "Artifact preview unavailable": "无法预览工件", "Preview unavailable": "预览不可用", "Open Media / Node Studio": "打开媒体 / 节点工作室", "Review the server snapshot": "查看服务器快照", "Readiness & Module Plan": "就绪状态与模块计划", "Low space": "空间不足", "Mode": "模式", "Target": "目标", "Source": "来源", "Active": "活动", "Attention": "注意", "Interrupted": "已中断", "Recoverable": "可恢复",
    "LIFECYCLE": "生命周期", "Created": "创建时间", "Started": "开始时间", "Finished": "结束时间", "Hot": "即时任务", "Durable": "持久任务", "Preview unavailable in this snapshot.": "此快照无法预览。", "No recovery action is available from this server snapshot.": "此服务器快照没有可用的恢复操作。", "No jobs in this filter": "此筛选器中没有任务", "Choose All to see the complete server snapshot.": "选择全部以查看完整服务器快照。", "Direct workflow": "直接工作流", "rows": "行", "Source:": "来源：", "execution:": "执行：", "dry_run:": "试运行：",
    "MODULE": "模块", "VISION & DOCUMENT": "视觉与文档", "SPEECH & VOICE": "语音与声音", "IMAGE & VIDEO": "图像与视频", "CREATIVE WORKSPACE": "创作工作区", "MODULE HEALTH": "模块状态", "Server-owned": "服务器拥有", "allowlisted": "已允许", "not_run": "未运行", "Clean": "干净", "preserved": "已保留", "attention": "注意", "completed": "已完成", "queue": "队列", "A bounded exact scope is published for this fixture state.": "此快照已发布有限的精确范围。", "Use only the listed operations with opaque artifacts.": "仅使用列出的操作和不透明工件。", "Review detailed evidence": "查看详细证据", "Open server snapshot in": "在设置中打开服务器快照", "Low-space warning": "空间不足警告",
  }),
  ja: Object.freeze({
    "CONTROL PLANE": "コントロールプレーン", "MEDIA CAPABILITY EVIDENCE": "メディア機能の証拠", "Exact media operation scope": "正確なメディア操作範囲", "Server snapshot only; the UI does not execute media operations.": "サーバースナップショットのみ表示し、UIはメディア操作を実行しません。",
    "WORKFLOW LIBRARY": "ワークフローライブラリ", "Module plan": "モジュール計画", "Static readiness snapshot": "静的な準備状態スナップショット", "Preflight is read-only; install/download is not_run": "プレフライトは読み取り専用です。インストール・ダウンロードは未実行です", "STORAGE PROJECTION": "ストレージ概要", "JOB RECOVERY": "ジョブ復旧", "Recovery attention": "復旧に関する注意", "Open focused Jobs": "要確認ジョブを開く",
    "Check readiness": "準備状態を確認", "Choose a route": "作業領域を選択", "Run from Jobs": "ジョブから実行", "Reason": "理由", "Outcome": "結果", "Execution": "実行", "Cleanup": "クリーンアップ", "Source overwrite": "ソース上書き", "Total": "合計", "Free": "空き", "Used": "使用済み",
    "Artifact preview unavailable": "アーティファクトのプレビューは利用できません", "Preview unavailable": "プレビューを利用できません", "Open Media / Node Studio": "メディア / ノードスタジオを開く", "Review the server snapshot": "サーバースナップショットを確認", "Readiness & Module Plan": "準備状態とモジュール計画", "Low space": "空き容量不足", "Mode": "モード", "Target": "対象", "Source": "ソース", "Active": "アクティブ", "Attention": "要注意", "Interrupted": "中断", "Recoverable": "復旧可能",
    "LIFECYCLE": "ライフサイクル", "Created": "作成", "Started": "開始", "Finished": "完了", "Hot": "即時ジョブ", "Durable": "永続ジョブ", "Preview unavailable in this snapshot.": "このスナップショットではプレビューを利用できません。", "No recovery action is available from this server snapshot.": "このサーバースナップショットには復旧操作がありません。", "No jobs in this filter": "このフィルターにジョブはありません", "Choose All to see the complete server snapshot.": "すべてを選択すると完全なサーバースナップショットを表示します。", "Direct workflow": "直接ワークフロー", "rows": "行", "Source:": "ソース：", "execution:": "実行：", "dry_run:": "ドライラン：",
    "MODULE": "モジュール", "VISION & DOCUMENT": "ビジョンとドキュメント", "SPEECH & VOICE": "音声とボイス", "IMAGE & VIDEO": "画像と動画", "CREATIVE WORKSPACE": "クリエイティブワークスペース", "MODULE HEALTH": "モジュール状態", "Server-owned": "サーバー所有", "allowlisted": "許可済み", "not_run": "未実行", "Clean": "クリーン", "preserved": "保持済み", "attention": "要注意", "completed": "完了", "queue": "キュー", "A bounded exact scope is published for this fixture state.": "このスナップショットには限定された正確な範囲が公開されています。", "Use only the listed operations with opaque artifacts.": "一覧の操作と不透明なアーティファクトのみ使用してください。", "Review detailed evidence": "詳細な証拠を確認", "Open server snapshot in": "設定でサーバースナップショットを開く", "Low-space warning": "空き容量不足の警告",
  }),
  ko: Object.freeze({
    "CONTROL PLANE": "컨트롤 플레인", "MEDIA CAPABILITY EVIDENCE": "미디어 기능 증거", "Exact media operation scope": "정확한 미디어 작업 범위", "Server snapshot only; the UI does not execute media operations.": "서버 스냅샷만 표시하며 UI는 미디어 작업을 실행하지 않습니다.",
    "WORKFLOW LIBRARY": "워크플로 라이브러리", "Module plan": "모듈 계획", "Static readiness snapshot": "정적 준비 상태 스냅샷", "Preflight is read-only; install/download is not_run": "프리플라이트는 읽기 전용이며 설치/다운로드는 실행되지 않았습니다", "STORAGE PROJECTION": "저장소 개요", "JOB RECOVERY": "작업 복구", "Recovery attention": "복구 주의 사항", "Open focused Jobs": "확인할 작업 열기",
    "Check readiness": "준비 상태 확인", "Choose a route": "작업 공간 선택", "Run from Jobs": "작업에서 실행", "Reason": "이유", "Outcome": "결과", "Execution": "실행", "Cleanup": "정리", "Source overwrite": "원본 덮어쓰기", "Total": "전체", "Free": "여유", "Used": "사용됨",
    "Artifact preview unavailable": "아티팩트 미리보기를 사용할 수 없음", "Preview unavailable": "미리보기 사용 불가", "Open Media / Node Studio": "미디어 / 노드 스튜디오 열기", "Review the server snapshot": "서버 스냅샷 검토", "Readiness & Module Plan": "준비 상태 및 모듈 계획", "Low space": "공간 부족", "Mode": "모드", "Target": "대상", "Source": "소스", "Active": "활성", "Attention": "주의", "Interrupted": "중단됨", "Recoverable": "복구 가능",
    "LIFECYCLE": "수명 주기", "Created": "생성", "Started": "시작", "Finished": "완료", "Hot": "실시간 작업", "Durable": "지속 작업", "Preview unavailable in this snapshot.": "이 스냅샷에서는 미리보기를 사용할 수 없습니다.", "No recovery action is available from this server snapshot.": "이 서버 스냅샷에는 복구 작업이 없습니다.", "No jobs in this filter": "이 필터에 작업이 없습니다", "Choose All to see the complete server snapshot.": "전체를 선택하면 서버 스냅샷 전체를 볼 수 있습니다.", "Direct workflow": "직접 워크플로", "rows": "행", "Source:": "소스:", "execution:": "실행:", "dry_run:": "시험 실행:",
    "MODULE": "모듈", "VISION & DOCUMENT": "비전 및 문서", "SPEECH & VOICE": "음성 및 보이스", "IMAGE & VIDEO": "이미지 및 비디오", "CREATIVE WORKSPACE": "크리에이티브 작업 공간", "MODULE HEALTH": "모듈 상태", "Server-owned": "서버 소유", "allowlisted": "허용 목록", "not_run": "실행 안 함", "Clean": "정상", "preserved": "보존됨", "attention": "주의", "completed": "완료됨", "queue": "대기열", "A bounded exact scope is published for this fixture state.": "이 스냅샷에 제한된 정확한 범위가 게시되었습니다.", "Use only the listed operations with opaque artifacts.": "나열된 작업과 불투명 아티팩트만 사용하세요.", "Review detailed evidence": "상세 증거 검토", "Open server snapshot in": "설정에서 서버 스냅샷 열기", "Low-space warning": "공간 부족 경고",
  }),
});

// Explicit labels used by the page renderer.  These keys are template-owned
// copy only; server-projected reasons, names, IDs and artifact text never pass
// through this table.
const PAGE_LABEL_DICTIONARIES = Object.freeze({
  vi: Object.freeze({
    "Tình trạng module": "Tình trạng module", "Jobs hoạt động": "Tác vụ đang hoạt động", "Dung lượng trống": "Dung lượng trống",
    "C:/ & D:/ dung lượng": "Dung lượng C:/ & D:/", "Server-owned, allowlisted volume snapshot": "Snapshot ổ đĩa do máy chủ sở hữu, theo allowlist",
    "Bước tiếp theo": "Bước tiếp theo", "BACKEND CONTRACT": "HỢP ĐỒNG BACKEND", "Quick": "Nhanh", "Nodes": "Node",
    "Chỉnh sửa & Mask": "Chỉnh sửa & Mask", "Hub Nodes": "Node Hub", "ComfyUI Advanced": "ComfyUI nâng cao", "Open focused Jobs": "Mở Tác vụ cần xem",
    "Reason & next action": "Lý do & hành động tiếp theo", "Module preflight": "Kiểm tra module", "Recovery attention": "Cần chú ý khôi phục",
    "Active": "Đang hoạt động", "Attention": "Cần chú ý", "Interrupted": "Bị gián đoạn", "Recoverable": "Có thể khôi phục", "Next action": "Hành động tiếp theo",
    "Static readiness snapshot": "Snapshot sẵn sàng tĩnh", "Preflight is read-only; install/download is not_run": "Preflight chỉ đọc; cài đặt/tải xuống chưa chạy",
    "Server-owned": "Do máy chủ sở hữu", "Lifecycle": "Vòng đời", "Recovery reason": "Lý do khôi phục", "No jobs in this filter": "Không có tác vụ trong bộ lọc này",
    "Choose All to see the complete server snapshot.": "Chọn Tất cả để xem toàn bộ snapshot máy chủ.", "Dashboard": "Bảng điều khiển", "Jobs": "Tác vụ", "Settings": "Cài đặt", "Models & Storage": "Mô hình & Lưu trữ",
    "RECOVERY SNAPSHOT": "SNAPSHOT KHÔI PHỤC", "Jobs recovery": "Khôi phục tác vụ", "Artifact preview": "Xem trước artifact", "Artifact preview unavailable": "Không có bản xem trước artifact", "Preview unavailable in this snapshot.": "Snapshot này không có bản xem trước.", "available": "có sẵn", "Source:": "Nguồn:", "execution:": "thực thi:", "dry_run:": "chạy thử:", "Lifecycle timestamps not published in this snapshot.": "Snapshot này không công bố mốc thời gian vòng đời.", "SERVER SNAPSHOT": "SNAPSHOT MÁY CHỦ", "Readiness & Module Plan": "Mức sẵn sàng & Kế hoạch module", "Overall readiness": "Mức sẵn sàng tổng thể", "Modules": "Module", "Bootstrap product-surface evidence is shown as received; fast refresh never promotes it to execution.": "Bằng chứng sản phẩm được hiển thị đúng như nhận được; làm mới nhanh không biến nó thành thực thi.", "Install / repair / uninstall: explanatory only": "Cài đặt / sửa / gỡ cài đặt: chỉ giải thích", "MODULE EVIDENCE": "BẰNG CHỨNG MODULE", "Safe module projection": "Projection module an toàn", "Rows are limited to server-projected id, provider, component, status, version, reason and next action.": "Các dòng chỉ gồm id, provider, component, trạng thái, phiên bản, lý do và hành động tiếp theo do máy chủ projection.",
  }),
  en: Object.freeze({
    "Tình trạng module": "Module health", "Jobs hoạt động": "Active jobs", "Dung lượng trống": "Free space", "C:/ & D:/ dung lượng": "C:/ & D:/ storage",
    "Server-owned, allowlisted volume snapshot": "Server-owned, allowlisted volume snapshot", "Bước tiếp theo": "Next action", "BACKEND CONTRACT": "BACKEND CONTRACT",
    "Quick": "Quick", "Nodes": "Nodes", "Chỉnh sửa & Mask": "Edit & Mask", "Hub Nodes": "Hub Nodes", "ComfyUI Advanced": "ComfyUI Advanced", "Open focused Jobs": "Open focused Jobs",
    "Reason & next action": "Reason & next action", "Module preflight": "Module preflight", "Recovery attention": "Recovery attention", "Active": "Active", "Attention": "Attention",
    "Interrupted": "Interrupted", "Recoverable": "Recoverable", "Next action": "Next action", "Static readiness snapshot": "Static readiness snapshot", "Preflight is read-only; install/download is not_run": "Preflight is read-only; install/download is not_run",
    "Server-owned": "Server-owned", "Lifecycle": "Lifecycle", "Recovery reason": "Recovery reason", "No jobs in this filter": "No jobs in this filter", "Choose All to see the complete server snapshot.": "Choose All to see the complete server snapshot.",
    "Dashboard": "Dashboard", "Jobs": "Jobs", "Settings": "Settings", "Models & Storage": "Models & Storage",
    "RECOVERY SNAPSHOT": "RECOVERY SNAPSHOT", "Jobs recovery": "Jobs recovery", "Artifact preview": "Artifact preview", "Artifact preview unavailable": "Artifact preview unavailable", "Preview unavailable in this snapshot.": "Preview unavailable in this snapshot.", "available": "available", "Source:": "Source:", "execution:": "execution:", "dry_run:": "dry_run:", "Lifecycle timestamps not published in this snapshot.": "Lifecycle timestamps not published in this snapshot.", "SERVER SNAPSHOT": "SERVER SNAPSHOT", "Readiness & Module Plan": "Readiness & Module Plan", "Overall readiness": "Overall readiness", "Modules": "Modules", "Bootstrap product-surface evidence is shown as received; fast refresh never promotes it to execution.": "Bootstrap product-surface evidence is shown as received; fast refresh never promotes it to execution.", "Install / repair / uninstall: explanatory only": "Install / repair / uninstall: explanatory only", "MODULE EVIDENCE": "MODULE EVIDENCE", "Safe module projection": "Safe module projection", "Rows are limited to server-projected id, provider, component, status, version, reason and next action.": "Rows are limited to server-projected id, provider, component, status, version, reason and next action.",
  }),
  zh: Object.freeze({
    "Tình trạng module": "模块状态", "Jobs hoạt động": "活动任务", "Dung lượng trống": "可用空间", "C:/ & D:/ dung lượng": "C:/ 与 D:/ 存储",
    "Server-owned, allowlisted volume snapshot": "服务器拥有的允许列表卷快照", "Bước tiếp theo": "下一步", "BACKEND CONTRACT": "后端契约", "Quick": "快速", "Nodes": "节点",
    "Chỉnh sửa & Mask": "编辑与蒙版", "Hub Nodes": "Hub 节点", "ComfyUI Advanced": "ComfyUI 高级", "Open focused Jobs": "打开重点任务", "Reason & next action": "原因与下一步",
    "Module preflight": "模块预检", "Recovery attention": "恢复注意事项", "Active": "活动", "Attention": "注意", "Interrupted": "已中断", "Recoverable": "可恢复", "Next action": "下一步",
    "Static readiness snapshot": "静态就绪快照", "Preflight is read-only; install/download is not_run": "预检为只读；未运行安装或下载", "Server-owned": "服务器拥有", "Lifecycle": "生命周期", "Recovery reason": "恢复原因",
    "No jobs in this filter": "此筛选器中没有任务", "Choose All to see the complete server snapshot.": "选择全部以查看完整服务器快照。", "Dashboard": "控制面板", "Jobs": "任务", "Settings": "设置", "Models & Storage": "模型与存储",
    "RECOVERY SNAPSHOT": "恢复快照", "Jobs recovery": "任务恢复", "Artifact preview": "工件预览", "Artifact preview unavailable": "工件预览不可用", "Preview unavailable in this snapshot.": "此快照无法预览。", "available": "可用", "Source:": "来源：", "execution:": "执行：", "dry_run:": "试运行：", "Lifecycle timestamps not published in this snapshot.": "此快照未发布生命周期时间。", "SERVER SNAPSHOT": "服务器快照", "Readiness & Module Plan": "就绪状态与模块计划", "Overall readiness": "总体就绪状态", "Modules": "模块", "Bootstrap product-surface evidence is shown as received; fast refresh never promotes it to execution.": "产品界面证据按收到的内容显示；快速刷新不会将其提升为执行。", "Install / repair / uninstall: explanatory only": "安装 / 修复 / 卸载：仅作说明", "MODULE EVIDENCE": "模块证据", "Safe module projection": "安全模块投影", "Rows are limited to server-projected id, provider, component, status, version, reason and next action.": "行仅包含服务器投影的 id、provider、component、状态、版本、原因和下一步。",
  }),
  ja: Object.freeze({
    "Tình trạng module": "モジュール状態", "Jobs hoạt động": "アクティブなジョブ", "Dung lượng trống": "空き容量", "C:/ & D:/ dung lượng": "C:/ と D:/ ストレージ",
    "Server-owned, allowlisted volume snapshot": "サーバー所有の許可リスト ボリュームスナップショット", "Bước tiếp theo": "次のアクション", "BACKEND CONTRACT": "バックエンド契約", "Quick": "クイック", "Nodes": "ノード",
    "Chỉnh sửa & Mask": "編集とマスク", "Hub Nodes": "Hub ノード", "ComfyUI Advanced": "ComfyUI 詳細", "Open focused Jobs": "要確認ジョブを開く", "Reason & next action": "理由と次のアクション",
    "Module preflight": "モジュールのプレフライト", "Recovery attention": "復旧に関する注意", "Active": "アクティブ", "Attention": "要注意", "Interrupted": "中断", "Recoverable": "復旧可能", "Next action": "次のアクション",
    "Static readiness snapshot": "静的な準備状態スナップショット", "Preflight is read-only; install/download is not_run": "プレフライトは読み取り専用です。インストール・ダウンロードは未実行です", "Server-owned": "サーバー所有", "Lifecycle": "ライフサイクル", "Recovery reason": "復旧理由",
    "No jobs in this filter": "このフィルターにジョブはありません", "Choose All to see the complete server snapshot.": "すべてを選択すると完全なサーバースナップショットを表示します。", "Dashboard": "ダッシュボード", "Jobs": "ジョブ", "Settings": "設定", "Models & Storage": "モデルとストレージ",
    "RECOVERY SNAPSHOT": "復旧スナップショット", "Jobs recovery": "ジョブ復旧", "Artifact preview": "アーティファクトのプレビュー", "Artifact preview unavailable": "アーティファクトのプレビューは利用できません", "Preview unavailable in this snapshot.": "このスナップショットではプレビューを利用できません。", "available": "利用可能", "Source:": "ソース：", "execution:": "実行：", "dry_run:": "ドライラン：", "Lifecycle timestamps not published in this snapshot.": "このスナップショットにはライフサイクル時刻がありません。", "SERVER SNAPSHOT": "サーバースナップショット", "Readiness & Module Plan": "準備状態とモジュール計画", "Overall readiness": "全体の準備状態", "Modules": "モジュール", "Bootstrap product-surface evidence is shown as received; fast refresh never promotes it to execution.": "製品画面の証拠は受信したまま表示し、クイック更新で実行状態に昇格させません。", "Install / repair / uninstall: explanatory only": "インストール / 修復 / アンインストール：説明のみ", "MODULE EVIDENCE": "モジュールの証拠", "Safe module projection": "安全なモジュール投影", "Rows are limited to server-projected id, provider, component, status, version, reason and next action.": "行にはサーバー投影の id、provider、component、状態、バージョン、理由、次のアクションのみを表示します。",
  }),
  ko: Object.freeze({
    "Tình trạng module": "모듈 상태", "Jobs hoạt động": "활성 작업", "Dung lượng trống": "여유 공간", "C:/ & D:/ dung lượng": "C:/ 및 D:/ 저장소",
    "Server-owned, allowlisted volume snapshot": "서버 소유 허용 목록 볼륨 스냅샷", "Bước tiếp theo": "다음 작업", "BACKEND CONTRACT": "백엔드 계약", "Quick": "빠른 작업", "Nodes": "노드",
    "Chỉnh sửa & Mask": "편집 및 마스크", "Hub Nodes": "Hub 노드", "ComfyUI Advanced": "ComfyUI 고급", "Open focused Jobs": "확인할 작업 열기", "Reason & next action": "이유 및 다음 작업",
    "Module preflight": "모듈 프리플라이트", "Recovery attention": "복구 주의 사항", "Active": "활성", "Attention": "주의", "Interrupted": "중단됨", "Recoverable": "복구 가능", "Next action": "다음 작업",
    "Static readiness snapshot": "정적 준비 상태 스냅샷", "Preflight is read-only; install/download is not_run": "프리플라이트는 읽기 전용이며 설치/다운로드는 실행되지 않았습니다", "Server-owned": "서버 소유", "Lifecycle": "수명 주기", "Recovery reason": "복구 이유",
    "No jobs in this filter": "이 필터에는 작업이 없습니다", "Choose All to see the complete server snapshot.": "전체를 선택하면 서버 스냅샷 전체를 볼 수 있습니다.", "Dashboard": "대시보드", "Jobs": "작업", "Settings": "설정", "Models & Storage": "모델 및 저장소",
    "RECOVERY SNAPSHOT": "복구 스냅샷", "Jobs recovery": "작업 복구", "Artifact preview": "아티팩트 미리보기", "Artifact preview unavailable": "아티팩트 미리보기 사용 불가", "Preview unavailable in this snapshot.": "이 스냅샷에서는 미리보기를 사용할 수 없습니다.", "available": "사용 가능", "Source:": "소스:", "execution:": "실행:", "dry_run:": "시험 실행:", "Lifecycle timestamps not published in this snapshot.": "이 스냅샷에는 수명 주기 시간이 게시되지 않았습니다.", "SERVER SNAPSHOT": "서버 스냅샷", "Readiness & Module Plan": "준비 상태 및 모듈 계획", "Overall readiness": "전체 준비 상태", "Modules": "모듈", "Bootstrap product-surface evidence is shown as received; fast refresh never promotes it to execution.": "제품 화면 증거는 받은 그대로 표시하며 빠른 새로 고침으로 실행 상태로 승격하지 않습니다.", "Install / repair / uninstall: explanatory only": "설치 / 복구 / 제거: 설명 전용", "MODULE EVIDENCE": "모듈 증거", "Safe module projection": "안전한 모듈 프로젝션", "Rows are limited to server-projected id, provider, component, status, version, reason and next action.": "행에는 서버가 프로젝션한 id, provider, component, 상태, 버전, 이유, 다음 작업만 포함됩니다.",
  }),
});

export const currentLanguage = () => {
  try {
    const value = window.localStorage?.getItem(LANGUAGE_STORAGE_KEY) || "vi";
    return LANGUAGE_IDS.has(value) ? value : "vi";
  } catch {
    return "vi";
  }
};

export const setLanguage = (value) => {
  const language = LANGUAGE_IDS.has(value) ? value : "vi";
  try { window.localStorage?.setItem(LANGUAGE_STORAGE_KEY, language); } catch { /* local storage may be unavailable */ }
  document.documentElement.lang = language;
  return language;
};

export const translateText = (value, language = currentLanguage()) => {
  let result = String(value ?? "");
  const dictionary = { ...(DICTIONARIES[language] || DICTIONARIES.vi), ...(EXTRA_DICTIONARIES[language] || {}), ...(PAGE_LABEL_DICTIONARIES[language] || {}) };
  for (const [source, target] of Object.entries(dictionary).sort((a, b) => b[0].length - a[0].length)) {
    if (source.length < 8 && /^[A-Za-z ]+$/.test(source)) {
      const escaped = source.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      result = result.replace(new RegExp(`\\b${escaped}\\b`, "g"), target);
    } else result = result.split(source).join(target);
  }
  return result;
};

export const localizeDocument = (root = document, language = currentLanguage()) => {
  // Only localize fixed markup that explicitly opts in.  Snapshot, job, and
  // artifact text is server-owned data and must never be traversed or changed
  // by a client-side dictionary pass.
  const nodes = (selector) => [
    ...(root?.matches?.(selector) ? [root] : []),
    ...(root?.querySelectorAll?.(selector) || []),
  ];
  nodes("[data-i18n]").forEach((node) => {
    const source = node.getAttribute("data-i18n");
    if (source !== null) node.textContent = translateText(source, language);
  });
  // A small container form keeps legacy semantic markup (for example
  // `<span>Attention</span><strong>4</strong>`) intact while translating only
  // its first fixed child.  The numeric/server-owned sibling is never touched.
  nodes("[data-i18n-container]").forEach((node) => {
    const source = node.getAttribute("data-i18n-container");
    const target = node.querySelector?.("[data-i18n-slot]") || node.firstElementChild;
    if (source !== null && target) target.textContent = translateText(source, language);
  });
  // Recovery count cards have a stable semantic hook and a numeric sibling;
  // translate only their first label span so the existing count markup and
  // server-projected number remain untouched.
  nodes("[data-recovery-count]").forEach((node) => {
    const target = node.querySelector?.(":scope > span");
    if (target) target.textContent = translateText(target.textContent, language);
  });
  [
    ["data-i18n-aria-label", "aria-label"],
    ["data-i18n-title", "title"],
  ].forEach(([sourceAttribute, targetAttribute]) => {
    nodes(`[${sourceAttribute}]`).forEach((node) => {
      const source = node.getAttribute(sourceAttribute);
      if (source !== null) node.setAttribute(targetAttribute, translateText(source, language));
    });
  });
  return language;
};
