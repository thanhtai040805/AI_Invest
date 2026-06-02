/**
 * Simple i18n utility for Vietnamese translations
 */

const translations = {
  // --- Existing front-end keys (preserved Vietnamese) ---
  reconnecting: "Đang kết nối lại...",
  connected: "Đã kết nối",
  sendFailed: "Gửi tin nhắn thất bại",
  prompt: "Nhập câu hỏi của bạn...",
  toolProcessing: "Đang xử lý...",
  processing: "Đang xử lý",
  completed: "Hoàn thành",
  failed: "Thất bại",
  running: "Đang chạy",
  fullReport: "Xem báo cáo đầy đủ",
  thinkingRunning: "Đang phân tích",
  thinkingDone: "Hoàn thành {count} bước",
  toolRunning: "Đang chạy",
  describeStrategy: "Mô tả chiến lược đầu tư của bạn để nhận phân tích chuyên sâu",
  examples: "Ví dụ nhanh",

  // --- Navigation ---
  home: "Trang chủ",
  agent: "Tác vụ",
  runs: "Lịch sử chạy",
  settings: "Cài đặt",
  alphaZoo: "Alpha Zoo",
  sessions: "Phiên làm việc",
  newChat: "Tạo phiên mới",
  deleteConfirm: "Xoá?",
  noSessions: "Chưa có phiên nào",
  viewDetails: "Xem chi tiết",
  selectRun: "-- Chọn --",
  selectTwoRuns: "Chọn hai lần chạy để so sánh số liệu.",

  // --- Settings ---
  settingsDesc: "Cấu hình thông tin xác thực mô hình và token nguồn dữ liệu thị trường cho dự án này.",
  localApiAccess: "Truy cập API cục bộ",
  localApiAccessDesc: "Đối với triển khai Web UI từ xa, nhập khóa API máy chủ một lần trong trình duyệt này. Sử dụng localhost có thể để trống.",
  localApiKey: "Khóa API máy chủ",
  localApiKeyHint: "Chỉ lưu trong trình duyệt này. Để trống để xoá.",
  localApiKeySaved: "Đã lưu khóa API",
  localApiKeySave: "Lưu khóa",
  settingsUnavailable: "Không thể truy cập cài đặt",

  // --- LLM Settings ---
  llmSettings: "Cài đặt LLM",
  llmSettingsDesc: "Chọn mô hình sử dụng cho tác vụ và lưu vào tệp .env của dự án.",
  llmConnection: "Kết nối",
  llmGeneration: "Sinh",
  llmProvider: "Nhà cung cấp",
  llmModelName: "Mô hình",
  llmBaseUrl: "Base URL",
  llmApiKey: "Khóa API",
  llmApiKeyConfigured: "Đã cấu hình",
  llmApiKeyPlaceholder: "Để trống để giữ khóa hiện tại",
  llmClearApiKey: "Xoá khóa API đã lưu",
  llmNoApiKeyRequired: "Nhà cung cấp này không yêu cầu khóa API.",
  llmOauthRequired: "Nhà cung cấp này sử dụng OAuth. Chạy: {command}",
  llmTemperature: "Nhiệt độ",
  llmTimeoutSeconds: "Thời gian chờ (giây)",
  llmMaxRetries: "Số lần thử lại",
  llmReasoningEffort: "Mức độ suy luận",
  llmReasoningOff: "Tắt",
  llmSaveSettings: "Lưu cài đặt",
  llmSaving: "Đang lưu...",
  llmSettingsSaved: "Đã lưu cài đặt LLM",
  llmSettingsLoadFailed: "Không thể tải cài đặt LLM",
  llmSettingsSaveFailed: "Không thể lưu cài đặt LLM",
  llmEnvPath: "Đã lưu tại",
  llmProviderHint: "Thay đổi nhà cung cấp sẽ cập nhật mô hình và endpoint được đề xuất.",
  llmModelHint: "Sử dụng mã mô hình chính xác theo yêu cầu của nhà cung cấp.",
  llmUseProviderDefaults: "Sử dụng mặc định",

  // --- Data Source Settings ---
  dataSourceSettings: "Cài đặt nguồn dữ liệu",
  dataSourceSettingsDesc: "Cấu hình thông tin xác thực nguồn dữ liệu thị trường tùy chọn cho backtest.",

  baostockStatus: "BaoStock",
  baostockSupported: "Loader khả dụng",
  baostockNotSupported: "Không có loader",
  baostockPackageInstalled: "Gói Python đã cài",
  baostockPackageMissing: "Gói Python chưa cài",
  saveDataSourceSettings: "Lưu cài đặt",
  dataSourceSettingsSaved: "Đã lưu cài đặt nguồn dữ liệu",
  dataSourceSettingsLoadFailed: "Không thể tải cài đặt nguồn dữ liệu",
  dataSourceSettingsSaveFailed: "Không thể lưu cài đặt nguồn dữ liệu",

  // --- Connection ---
  online: "Trực tuyến",
  offline: "Ngoại tuyến",
  checking: "Đang kiểm tra…",
  checkConnection: "Kiểm tra kết nối",
  connection: "Kết nối",
  endpoints: "Endpoints",
  disconnected: "Mất kết nối",
  reconnectingN: "Mất kết nối, đang thử lại (lần {n})…",
  sessionCreated: "Phiên đã tạo",

  // --- Agent UI ---
  startResearch: "Bắt đầu nghiên cứu",
  send: "Gửi",
  loading: "Đang tải...",
  noRuns: "Chưa có lần chạy nào. Vào Agent để tạo.",
  runHistory: "Lịch sử chạy",
  status: "Trạng thái",
  elapsed: "Thời gian",
  stepN: "Bước {n}",
  unknownError: "Lỗi không xác định",
  stopGeneration: "Dừng tạo",
  newMessages: "Tin nhắn mới",
  cancelSent: "Đã gửi yêu cầu hủy",
  cancelFailed: "Hủy thất bại",
  executionFailed: "Thực thi thất bại",
  executionTimeout: "Thực thi quá thời gian, đã tự động dừng",
  exportChat: "Xuất chat",
  goBack: "Quay lại",
  rename: "Đổi tên",

  // --- Home/Hero ---
  heroTitle: "Nghiên cứu Chiến lược Định lượng với AI",
  heroDesc: "Mô tả chiến lược giao dịch bằng ngôn ngữ tự nhiên. Tác vụ AI sẽ tạo mã, chạy backtest và tối ưu hóa trong thời gian thực.",
  feat1: "AI Tác vụ",
  feat1d: "Tạo chiến lược bằng ngôn ngữ tự nhiên với suy luận ReAct",
  feat2: "Backtest Tích hợp",
  feat2d: "3 nguồn dữ liệu: Cổ phiếu A, US/HK, Crypto",
  feat3: "Phát trực tiếp Thời gian thực",
  feat3d: "Xem tác vụ AI suy nghĩ, gọi công cụ và lặp",
  feat4: "Phát lại Chiến lược",
  feat4d: "Trình phân tích nhật ký giao dịch + Shadow Account — trích xuất quy tắc, backtest, quy kết PnL delta",
  bye: "Tạm biệt",

  // --- Chart labels ---
  chart: "Biểu đồ",
  trades: "Giao dịch",
  code: "Mã",
  trace: "Theo dõi",
  noData: "Không có dữ liệu",
  noTrades: "Không có giao dịch nào.",
  noCode: "Không có tệp mã.",
  noTrace: "Không có dữ liệu theo dõi.",
  priceAndTrades: "Giá & Giao dịch",
  equityAndDrawdown: "Vốn & Sụt giảm",
  noChartData: "Không có dữ liệu biểu đồ",
  noChartDataHint: "Công cụ backtest có thể chưa tạo dữ liệu giá. Hãy kiểm tra thư mục artifacts/.",
  noPriceData: "Không có dữ liệu giá",
  noEquityData: "Không có dữ liệu vốn",
  equityDrawdown: "Vốn & Sụt giảm",

  // --- Tool names ---
  toolLoadSkill: "Tải kiến thức chiến lược",
  toolWriteFile: "Viết mã",
  toolEditFile: "Sửa mã",
  toolReadFile: "Đọc tệp",
  toolRunBacktest: "Chạy backtest",
  toolBash: "Chạy lệnh",
  toolReadUrl: "Đọc trang web",
  toolReadDocument: "Đọc tài liệu",
  toolCompact: "Nén ngữ cảnh",
  toolCreateTask: "Tạo tác vụ",
  toolUpdateTask: "Cập nhật tác vụ",
  toolSpawnSubagent: "Triển khai phụ tác vụ",

  // --- Metric labels ---
  metricTotalReturn: "Tổng lợi nhuận",
  metricAnnualReturn: "Hàng năm",
  metricSharpe: "Sharpe",
  metricMaxDrawdown: "Sụt giảm tối đa",
  metricWinRate: "Tỷ lệ thắng",
  metricTradeCount: "Giao dịch",
  metricFinalValue: "Giá trị cuối",
  metricCalmar: "Calmar",
  metricSortino: "Sortino",
  metricProfitLossRatio: "Tỷ lệ Lợi nhuận/Thua lỗ",
  metricMaxConsecutiveLoss: "Số lần thua liên tiếp tối đa",
  metricAvgHoldingDays: "Số ngày nắm giữ TB",
  metricBenchmarkReturn: "Benchmark",
  metricExcessReturn: "Lợi nhuận vượt trội",
  metricIR: "IR",

  // --- Example prompts ---
  example1: "MA kép 5/20 ngày trên 000001.SZ, backtest 2024",
  example2: "Chiến lược MA kép 5/20 trên 000001.SZ, backtest 2024",
  example3: "Bollinger band mean-reversion trên 600519.SH, backtest 3 năm",

  // --- Validation ---
  validation: "Kiểm định",
  score: "Điểm",
  passed: "Đạt",
  findings: "Phát hiện",
  recommendations: "Khuyến nghị",
  review: "Đánh giá",
  noReview: "Không có dữ liệu đánh giá.",
  overlayMA: "Đường TB",
  overlayChannel: "Kênh",
  overlayIndicators: "Chỉ báo",
  overlayClearAll: "Chỉ nến (xoá hết)",

  // --- Correlation ---
  correlation: "Ma trận tương quan",
  selectAssets: "Mã chứng khoán",
  windowLabel: "Cửa sổ (ngày)",
  computeBtn: "Tính toán",
  methodLabel: "Phương pháp",
  noCorrelationData: "Không có dữ liệu tương quan",

  // --- Strategy Comparison ---
  strategyComparison: "So sánh chiến lược",
  baseline: "Cơ sở",
  compareTo: "So sánh",
  delta: "Chênh lệch",
  metric: "Chỉ số",

  // --- Appearance ---
  darkMode: "Tối",
  lightMode: "Sáng",
  language: "Ngôn ngữ",
  appearance: "Giao diện",

  // --- Table columns ---
  colTime: "Thời gian",
  colCode: "Mã",
  colSide: "Chiều",
  colPrice: "Giá",
  colQty: "KL",
  colReason: "Lý do",

  // --- Filters ---
  filterLogs: "Lọc nhật ký...",

  // --- Confirm / Cancel ---
  confirmDelete: "Xác nhận",
  cancelDelete: "Huỷ",

  // --- Export ---
  exportTitle: "# Xuất Chat",
  exportTime: "Thời gian xuất",
  exportUser: "## Người dùng",
  exportAssistant: "## Trợ lý",
  exportError: "## Lỗi",
  exportToolCall: "> Gọi công cụ",
  exportRunComplete: "> Backtest hoàn tất",
  downloadTradesCsv: "Tải CSV giao dịch",
  downloadMetricsCsv: "Tải CSV chỉ số",
};

type I18nFn = ((key: keyof typeof translations) => string) & Record<string, string>;

export function useI18n(): { t: I18nFn } {
  const tFn = (key: keyof typeof translations) => translations[key] || key;
  const proxy = new Proxy(tFn, {
    get(target, prop: string) {
      if (prop in translations) return translations[prop as keyof typeof translations];
      return target(prop as keyof typeof translations);
    },
  });
  return { t: proxy as unknown as I18nFn };
}
