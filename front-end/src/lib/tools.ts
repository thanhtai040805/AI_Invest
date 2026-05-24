/**
 * Single source of truth for tool name → i18n key.
 *
 * Values are i18n keys (looked up in `useI18n().t`), not final labels.
 * The map name "TOOL_I18N_KEY" reflects this: it maps a raw tool identifier
 * to the i18n bundle key whose value is the user-facing label.
 *
 * Consumers that need a final localized string should:
 *   1. Call `localizeToolName(tool, fallback)` to get the i18n key (or fallback).
 *   2. Resolve that key against the active i18n table via `t[key]`.
 */
export const TOOL_I18N_KEY: Record<string, string> = {
  load_skill: "toolLoadSkill",
  write_file: "toolWriteFile",
  edit_file: "toolEditFile",
  read_file: "toolReadFile",
  run_backtest: "toolRunBacktest",
  bash: "toolBash",
  read_url: "toolReadUrl",
  read_document: "toolReadDocument",
  compact: "toolCompact",
  create_task: "toolCreateTask",
  update_task: "toolUpdateTask",
  spawn_subagent: "toolSpawnSubagent",
};

/**
 * Map tool names to Vietnamese display labels.
 * Used for the VN analyst role system.
 */
const vnToolMap: Record<string, string> = {
  load_skill: "Tải kiến thức chiến lược",
  write_file: "Viết mã",
  edit_file: "Sửa mã",
  read_file: "Đọc tệp",
  run_backtest: "Chạy backtest",
  bash: "Chạy lệnh",
  read_url: "Đọc trang web",
  read_document: "Đọc tài liệu",
  compact: "Nén ngữ cảnh",
  create_task: "Tạo tác vụ",
  update_task: "Cập nhật tác vụ",
  spawn_subagent: "Triển khai phụ tác vụ",
  market_analyst: "Phân tích thị trường",
  fund_analyst: "Phân tích cơ bản",
  bull_researcher: "Nhà nghiên cứu lạc quan",
  bear_researcher: "Nhà nghiên cứu bi quan",
  portfolio_manager: "Quản lý danh mục",
  risk_gate: "Kiểm soát rủi ro",
};

/**
 * Returns the i18n key (or fallback) for a tool name.
 *
 * - If `tool` is mapped in `TOOL_I18N_KEY`, returns the mapped key.
 * - Else if `fallback` is provided, returns `fallback`.
 * - Else returns `tool` unchanged.
 *
 * Centralizes the previously-duplicated lookup pattern.
 */
export function localizeToolName(tool: string, fallback?: string): string {
  if (tool in TOOL_I18N_KEY) {
    return TOOL_I18N_KEY[tool];
  }
  if (fallback !== undefined) {
    return fallback;
  }
  return tool;
}

/** @deprecated Use `localizeToolName(tool, fallback)` instead. */
export function getVnToolName(tool: string): string {
  return vnToolMap[tool] || tool;
}
