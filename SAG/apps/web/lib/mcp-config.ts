export type McpConnectionMode = "http" | "stdio";

export interface ParsedMcpServer {
  name: string;
  mode: McpConnectionMode;
  config: Record<string, unknown>;
}

export interface ParsedMcpConfig {
  servers: ParsedMcpServer[];
  skipped: string[];
}

export type McpConfigErrorCode =
  | "fieldObject"
  | "fieldScalar"
  | "argsType"
  | "argsScalar"
  | "entryObject"
  | "rootObject"
  | "serversObject"
  | "serverObject"
  | "noServerShape"
  | "nameRequired"
  | "urlRequired"
  | "invalidUrl"
  | "httpOnly"
  | "commandRequired"
  | "invalidJson"
  | "duplicateName"
  | "allDisabled"
  | "noMountableServer";

export class McpConfigError extends Error {
  readonly code: McpConfigErrorCode;
  readonly values: Record<string, string | number>;

  constructor(code: McpConfigErrorCode, values: Record<string, string | number> = {}) {
    super(code);
    this.name = "McpConfigError";
    this.code = code;
    this.values = values;
  }
}

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stripCodeFence(value: string): string {
  const trimmed = value.trim().replace(/^\uFEFF/, "");
  const match = trimmed.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  return match ? match[1] : trimmed;
}

function looksLikeServer(value: unknown): value is JsonRecord {
  return isRecord(value) && ["url", "command"].some((key) => typeof value[key] === "string");
}

function stringMap(value: unknown, field: string, name: string): Record<string, string> | undefined {
  if (value == null) return undefined;
  if (!isRecord(value)) {
    throw new McpConfigError("fieldObject", { name, field });
  }
  const entries = Object.entries(value).map(([key, item]) => {
    if (!["string", "number", "boolean"].includes(typeof item)) {
      throw new McpConfigError("fieldScalar", { name, field, key });
    }
    return [key, String(item)] as const;
  });
  return entries.length ? Object.fromEntries(entries) : undefined;
}

function splitArgs(value: string): string[] {
  const tokens = value.match(/(?:[^\s"']+|"(?:\\.|[^"])*"|'[^']*')+/g) ?? [];
  return tokens.map((token) => {
    const quoted =
      (token.startsWith('"') && token.endsWith('"')) ||
      (token.startsWith("'") && token.endsWith("'"));
    const unwrapped = quoted ? token.slice(1, -1) : token;
    return unwrapped.replace(/\\([\\"])/g, "$1");
  });
}

function argsList(value: unknown, name: string): string[] | undefined {
  if (value == null || value === "") return undefined;
  if (typeof value === "string") {
    const parsed = splitArgs(value);
    return parsed.length ? parsed : undefined;
  }
  if (!Array.isArray(value)) {
    throw new McpConfigError("argsType", { name });
  }
  const parsed = value.map((item) => {
    if (!["string", "number", "boolean"].includes(typeof item)) {
      throw new McpConfigError("argsScalar", { name });
    }
    return String(item);
  });
  return parsed.length ? parsed : undefined;
}

function serverEntries(root: unknown): Array<[string, JsonRecord]> {
  if (Array.isArray(root)) {
    return root.map((item, index) => {
      if (!isRecord(item)) {
        throw new McpConfigError("entryObject", { index: index + 1 });
      }
      const name = typeof item.name === "string" ? item.name.trim() : "";
      return [name || `MCP ${index + 1}`, item];
    });
  }
  if (!isRecord(root)) {
    throw new McpConfigError("rootObject");
  }

  const container = root.mcpServers ?? root.servers;
  if (container !== undefined) {
    if (Array.isArray(container)) return serverEntries(container);
    if (!isRecord(container)) {
      throw new McpConfigError("serversObject");
    }
    return Object.entries(container).map(([name, config]) => {
      if (!isRecord(config)) {
        throw new McpConfigError("serverObject", { name });
      }
      return [name, config];
    });
  }

  if (looksLikeServer(root)) {
    const name = typeof root.name === "string" ? root.name.trim() : "";
    return [[name || "MCP", root]];
  }

  const entries = Object.entries(root);
  if (entries.length && entries.every(([, value]) => isRecord(value))) {
    return entries.map(([name, value]) => [name, value as JsonRecord]);
  }
  throw new McpConfigError("noServerShape");
}

function normalizeServer(rawName: string, raw: JsonRecord): ParsedMcpServer | null {
  const name = rawName.trim() || (typeof raw.name === "string" ? raw.name.trim() : "");
  if (!name) throw new McpConfigError("nameRequired");
  if (raw.disabled === true || raw.enabled === false) return null;

  const transport = String(raw.type ?? raw.transport ?? "").toLowerCase();
  const url = typeof raw.url === "string" ? raw.url.trim() : "";
  const command = typeof raw.command === "string" ? raw.command.trim() : "";
  const prefersStdio = transport.includes("stdio") || transport.includes("local");
  const prefersHttp = transport.includes("http") || transport.includes("sse");
  const mode: McpConnectionMode = prefersStdio ? "stdio" : prefersHttp || url ? "http" : "stdio";

  if (mode === "http") {
    if (!url) throw new McpConfigError("urlRequired", { name });
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      throw new McpConfigError("invalidUrl", { name });
    }
    if (!["http:", "https:"].includes(parsed.protocol)) {
      throw new McpConfigError("httpOnly", { name });
    }
    const headers = stringMap(raw.headers, "headers", name);
    return {
      name,
      mode,
      config: { name, url, ...(headers ? { headers } : {}) },
    };
  }

  if (!command) throw new McpConfigError("commandRequired", { name });
  const args = argsList(raw.args, name);
  const env = stringMap(raw.env, "env", name);
  return {
    name,
    mode,
    config: { name, command, ...(args ? { args } : {}), ...(env ? { env } : {}) },
  };
}

/** Phân tích MCP JSON dạng Claude Desktop、Cursor、VS Code và dạng một dịch vụ đơn. */
export function parseMcpConfig(value: string): ParsedMcpConfig {
  const source = stripCodeFence(value);
  if (!source) return { servers: [], skipped: [] };

  let root: unknown;
  try {
    root = JSON.parse(source);
  } catch {
    throw new McpConfigError("invalidJson");
  }

  const skipped: string[] = [];
  const servers = serverEntries(root).flatMap(([name, raw]) => {
    const normalized = normalizeServer(name, raw);
    if (normalized) return [normalized];
    skipped.push(name);
    return [];
  });
  const duplicate = servers.find(
    (server, index) =>
      servers.findIndex((candidate) => candidate.name.toLowerCase() === server.name.toLowerCase()) !==
      index,
  );
  if (duplicate) throw new McpConfigError("duplicateName", { name: duplicate.name });
  if (!servers.length && skipped.length) {
    throw new McpConfigError("allDisabled");
  }
  if (!servers.length) throw new McpConfigError("noMountableServer");
  return { servers, skipped };
}
