/**
 * Simple i18n utility for Vietnamese translations
 */

const translations = {
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
};

export function useI18n() {
  return {
    t: (key: keyof typeof translations) => translations[key] || key,
  };
}
