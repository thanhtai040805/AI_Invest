/**
 * Hệ thống log chẩn đoán phía client.
 *
 * Bộ đệm vòng tự động ghi lại các sự kiện quan trọng của cấu hình mô hình,
 * thao tác kho tri thức và tương tác hỏi đáp, hỗ trợ xuất một lần ra file JSON
 * để kỹ sư tiện kiểm tra sự cố.
 *
 * Log không bao giờ chứa các thông tin nhạy cảm như API Key.
 */

export type DiagEventType =
  | "app.init"
  | "model.load"
  | "model.save"
  | "model.test"
  | "knowledge.upload"
  | "knowledge.create"
  | "knowledge.process"
  | "qa.ask"
  | "qa.event"
  | "qa.complete"
  | "qa.error"
  | "warn"
  | "error";

export interface DiagEntry {
  seq: number;
  ts: string;
  type: DiagEventType;
  data: Record<string, unknown>;
}

export interface DiagEnvironment {
  app: "web" | "desktop";
  desktop_version?: string;
  user_agent: string;
  language: string;
  timezone: string;
}

export interface DiagExport {
  version: 1;
  exported_at: string;
  environment: DiagEnvironment;
  model_config: Record<string, unknown> | null;
  capabilities: Record<string, unknown> | null;
  entries: DiagEntry[];
  /** Real runtime log files captured from disk (desktop only). */
  desktop_log_files?: DiagLogFile[];
}

export interface DiagLogFile {
  name: string;
  path: string;
  size_bytes: number;
  /** Tail of the file content (up to 5MB). */
  content: string;
  /** True when only the tail was captured because the file exceeded the cap. */
  truncated: boolean;
}

const SENSITIVE_KEY_PATTERNS = [
  /api[_-]?key/i,
  /secret/i,
  /token/i,
  /password/i,
  /credential/i,
];

function isSensitiveKey(key: string): boolean {
  return SENSITIVE_KEY_PATTERNS.some((pattern) => pattern.test(key));
}

function looksLikeKeyValue(value: unknown): boolean {
  // Chuỗi dài, có thể là khóa mã hóa base64/hex
  if (typeof value !== "string") return false;
  if (value.length < 20) return false;
  return /^[A-Za-z0-9+/=_-]{20,}$/.test(value) || /^[A-Fa-f0-9]{20,}$/.test(value);
}

const REDACTED = "[REDACTED]";

/**
 * Làm sạch đệ quy: thay thế mọi trường chứa thông tin nhạy cảm trong đối tượng.
 * Không sửa đổi đối tượng gốc.
 *
 * Quy tắc:
 * 1. Key nhạy cảm tường minh (api_key, secret, token, password, credential) + giá trị chuỗi → làm sạch
 * 2. Giá trị boolean (như llm_api_key_set) → không làm sạch
 * 3. Key chứa "key" + giá trị trông giống base64/hex → làm sạch (dự phòng)
 */
export function sanitize(value: unknown): unknown {
  if (value === null || value === undefined) return value;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return value;

  if (Array.isArray(value)) {
    return value.map(sanitize);
  }

  if (typeof value === "object") {
    const result: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(value as Record<string, unknown>)) {
      // Giá trị boolean/khác không thể là khóa, giữ nguyên
      if (typeof val === "boolean" || typeof val === "number" || val === null) {
        result[key] = val;
        continue;
      }
      // Key nhạy cảm tường minh + giá trị chuỗi → làm sạch
      if (isSensitiveKey(key) && typeof val === "string") {
        result[key] = REDACTED;
        continue;
      }
      // Dự phòng: key chứa "key" + giá trị trông giống base64/hex → làm sạch
      if (typeof val === "string" && key.toLowerCase().includes("key") && looksLikeKeyValue(val)) {
        result[key] = REDACTED;
        continue;
      }
      result[key] = sanitize(val);
    }
    return result;
  }

  return value;
}

const DEFAULT_MAX_ENTRIES = 500;

export class DiagnosticsStore {
  private buffer: DiagEntry[] = [];
  private seq = 0;
  private listeners = new Set<() => void>();
  private cachedSnapshot: DiagEntry[] | null = null;
  readonly maxEntries: number;

  constructor(maxEntries = DEFAULT_MAX_ENTRIES) {
    this.maxEntries = Math.max(1, Math.floor(maxEntries));
  }

  record(type: DiagEventType, data: Record<string, unknown> = {}): void {
    const sanitized = sanitize(data) as Record<string, unknown>;
    this.buffer.push({
      seq: ++this.seq,
      ts: new Date().toISOString(),
      type,
      data: sanitized,
    });
    while (this.buffer.length > this.maxEntries) {
      this.buffer.shift();
    }
    this.cachedSnapshot = null;
    this.notify();
  }

  snapshot(): DiagEntry[] {
    if (!this.cachedSnapshot) {
      this.cachedSnapshot = [...this.buffer];
    }
    return this.cachedSnapshot;
  }

  get count(): number {
    return this.buffer.length;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private notify(): void {
    this.listeners.forEach((listener) => listener());
  }

  export(
    environment: DiagEnvironment,
    modelConfig: Record<string, unknown> | null = null,
    capabilities: Record<string, unknown> | null = null,
    desktopLogFiles?: DiagLogFile[],
  ): DiagExport {
    return {
      version: 1,
      exported_at: new Date().toISOString(),
      environment,
      model_config: modelConfig ? (sanitize(modelConfig) as Record<string, unknown>) : null,
      capabilities: capabilities ? (sanitize(capabilities) as Record<string, unknown>) : null,
      entries: this.snapshot(),
      ...(desktopLogFiles?.length ? { desktop_log_files: desktopLogFiles } : {}),
    };
  }
}

/** Singleton toàn cục, đảm bảo mọi component ghi vào cùng một bộ đệm. */
let globalStore: DiagnosticsStore | null = null;

export function getDiagnosticsStore(): DiagnosticsStore {
  if (!globalStore) {
    globalStore = new DiagnosticsStore();
  }
  return globalStore;
}

/** Tải file JSON xuống máy local. */
export function downloadDiagnostics(export_: DiagExport): void {
  const json = JSON.stringify(export_, null, 2);
  const blob = new Blob([json], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `sag-diagnostics-${export_.exported_at.replace(/[:.]/g, "-")}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
