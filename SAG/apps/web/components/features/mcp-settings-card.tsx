"use client";

import * as React from "react";
import {
  Braces,
  CheckCircle2,
  Globe2,
  Plug,
  RotateCw,
  Terminal,
  X,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useApp } from "@/components/features/app-shell";
import { SettingsRow, SettingsSection } from "@/components/features/settings-section";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Field,
  FieldDescription,
  FieldLabel,
  FieldSeparator,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api, ApiError } from "@/lib/api";
import { McpConfigError, parseMcpConfig } from "@/lib/mcp-config";
import type { ParsedMcpConfig, ParsedMcpServer } from "@/lib/mcp-config";
import type { Binding } from "@/lib/types";

function bindingLabel(binding: Binding): string {
  const config = (binding.config ?? {}) as { name?: string; url?: string; command?: string };
  return config.name || config.url || config.command || binding.target_id || "MCP";
}

function connectionIcon(mode: ParsedMcpServer["mode"]) {
  return mode === "http" ? <Globe2 className="size-3" /> : <Terminal className="size-3" />;
}

/** Các dịch vụ MCP ngoài được gắn vào trợ lý mặc định. */
export function McpSettingsCard() {
  const t = useTranslations("McpSettings");
  const { agent } = useApp();
  const [bindings, setBindings] = React.useState<Binding[] | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [mounting, setMounting] = React.useState(false);
  const [importing, setImporting] = React.useState(false);
  const [unmountingId, setUnmountingId] = React.useState<string | null>(null);
  const [mode, setMode] = React.useState<"http" | "stdio">("http");
  const [name, setName] = React.useState("");
  const [url, setUrl] = React.useState("");
  const [command, setCommand] = React.useState("");
  const [args, setArgs] = React.useState("");
  const [jsonText, setJsonText] = React.useState("");
  const [parsedJson, setParsedJson] = React.useState<ParsedMcpConfig | null>(null);
  const [jsonError, setJsonError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    if (!agent) return;
    setLoadError(null);
    try {
      const all = await api.listBindings(agent.id);
      setBindings(all.filter((binding) => binding.target_type === "mcp_server"));
    } catch (error) {
      setLoadError(error instanceof ApiError ? error.message : t("loadFailed"));
    }
  }, [agent, t]);

  React.useEffect(() => {
    setBindings(null);
    void load();
  }, [load]);

  const existingNames = React.useMemo(
    () =>
      new Set(
        (bindings ?? []).flatMap((binding) =>
          [binding.target_id, bindingLabel(binding)]
            .map((value) => value.trim().toLowerCase())
            .filter(Boolean),
        ),
      ),
    [bindings],
  );

  const pendingImported = React.useMemo(
    () =>
      (parsedJson?.servers ?? []).filter(
        (server) => !existingNames.has(server.name.toLowerCase()),
      ),
    [existingNames, parsedJson],
  );

  if (!agent) return null;

  function parseJson(value: string) {
    setJsonText(value);
    if (!value.trim()) {
      setParsedJson(null);
      setJsonError(null);
      return;
    }
    try {
      setParsedJson(parseMcpConfig(value));
      setJsonError(null);
    } catch (error) {
      setParsedJson(null);
      setJsonError(
        error instanceof McpConfigError
          ? t(`errors.${error.code}`, error.values)
          : t("invalidConfig"),
      );
    }
  }

  async function importServers() {
    if (!agent || !pendingImported.length) return;
    setImporting(true);
    let mounted = 0;
    const failed: string[] = [];
    for (const server of pendingImported) {
      try {
        await api.addBinding(agent.id, {
          target_type: "mcp_server",
          target_id: server.name,
          config: server.config,
        });
        mounted += 1;
      } catch (error) {
        const message = error instanceof ApiError ? error.message : t("mountFailed");
        failed.push(t("serverError", { name: server.name, error: message }));
      }
    }
    await load();
    setImporting(false);
    if (mounted) toast.success(t("mountedCount", { count: mounted }));
    if (failed.length) toast.error(failed.slice(0, 2).join(t("errorSeparator")));
    if (!failed.length) parseJson("");
  }

  async function mount() {
    if (!agent) return;
    const config: Record<string, unknown> = name.trim() ? { name: name.trim() } : {};
    if (mode === "http") {
      if (!url.trim()) return;
      config.url = url.trim();
    } else {
      if (!command.trim()) return;
      config.command = command.trim();
      const list = args.trim().split(/\s+/).filter(Boolean);
      if (list.length) config.args = list;
    }

    setMounting(true);
    try {
      await api.addBinding(agent.id, { target_type: "mcp_server", config });
      setName("");
      setUrl("");
      setCommand("");
      setArgs("");
      await load();
      toast.success(t("mounted"));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t("mountFailed"));
    } finally {
      setMounting(false);
    }
  }

  async function unmount(binding: Binding) {
    if (!agent) return;
    setUnmountingId(binding.id);
    try {
      await api.removeBinding(agent.id, binding.id);
      await load();
      toast.success(t("unmounted"));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : t("unmountFailed"));
    } finally {
      setUnmountingId(null);
    }
  }

  return (
    <SettingsSection title={t("title")} description={t("description")}>
      <SettingsRow title={t("mountedServices")} description={t("mountedDescription")}>
        {loadError ? (
          <Alert variant="destructive">
            <AlertTitle>{t("loadErrorTitle")}</AlertTitle>
            <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
              <span>{loadError}</span>
              <Button type="button" variant="outline" size="sm" onClick={() => void load()}>
                <RotateCw />
                {t("retry")}
              </Button>
            </AlertDescription>
          </Alert>
        ) : bindings === null ? (
          <div className="flex gap-2">
            <Skeleton className="h-7 w-24" />
            <Skeleton className="h-7 w-32" />
          </div>
        ) : bindings.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("empty")}</p>
        ) : (
          <div className="flex min-h-8 flex-wrap items-center gap-2">
            {bindings.map((binding) => {
              const label = bindingLabel(binding);
              const unmounting = unmountingId === binding.id;
              return (
                <Badge
                  key={binding.id}
                  variant="outline"
                  className="max-w-full gap-1.5 py-1 pl-2 pr-1"
                >
                  <Plug className="shrink-0" />
                  <span className="min-w-0 truncate">{label}</span>
                  <button
                    type="button"
                    onClick={() => void unmount(binding)}
                    disabled={unmounting}
                    className="rounded-full p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-destructive disabled:pointer-events-none disabled:opacity-50"
                    aria-label={t("unmountAria", { name: label })}
                    title={t("unmount")}
                  >
                    {unmounting ? <Spinner className="size-3" /> : <X className="size-3" />}
                  </button>
                </Badge>
              );
            })}
          </div>
        )}
      </SettingsRow>

      <SettingsRow title={t("addService")} description={t("addDescription")}>
        <div className="grid gap-5">
          <Field data-invalid={Boolean(jsonError)}>
            <FieldLabel htmlFor="mcp-json">
              <Braces className="size-4 text-muted-foreground" />
              MCP JSON
            </FieldLabel>
            <Textarea
              id="mcp-json"
              value={jsonText}
              onChange={(event) => parseJson(event.target.value)}
              placeholder={'{"mcpServers":{"example":{"url":"https://host/mcp"}}}'}
              className="min-h-28 resize-y font-mono text-xs leading-5"
              spellCheck={false}
              aria-invalid={Boolean(jsonError)}
            />
            {jsonError ? (
              <p role="alert" className="text-sm text-destructive">
                {jsonError}
              </p>
            ) : null}
          </Field>

          {parsedJson ? (
            <div className="grid gap-3 border-l-2 border-foreground/20 pl-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <CheckCircle2 className="size-4" />
                  {t("recognizedCount", { count: parsedJson.servers.length })}
                </div>
                <Button
                  type="button"
                  size="sm"
                  onClick={() => void importServers()}
                  disabled={importing || pendingImported.length === 0}
                >
                  {importing ? <Spinner /> : <Plug />}
                  {importing
                    ? t("mounting")
                    : pendingImported.length
                      ? t("mountCount", { count: pendingImported.length })
                      : t("allMounted")}
                </Button>
              </div>
              <div className="flex flex-wrap gap-2">
                {parsedJson.servers.map((server) => {
                  const exists = existingNames.has(server.name.toLowerCase());
                  return (
                    <Badge
                      key={server.name}
                      variant={exists ? "secondary" : "outline"}
                      className="max-w-full gap-1.5"
                    >
                      {connectionIcon(server.mode)}
                      <span className="truncate">{server.name}</span>
                      {exists ? t("mountedSuffix") : null}
                    </Badge>
                  );
                })}
              </div>
              {parsedJson.skipped.length ? (
                <p className="text-xs text-muted-foreground">
                  {t("skipped", { names: parsedJson.skipped.join(t("nameSeparator")) })}
                </p>
              ) : null}
            </div>
          ) : null}

          <FieldSeparator>{t("manualSeparator")}</FieldSeparator>

          <form
            onSubmit={(event) => {
              event.preventDefault();
              void mount();
            }}
            className="grid gap-4"
          >
            <div className="grid gap-4 sm:grid-cols-[auto_minmax(0,1fr)] sm:items-end">
              <Field>
                <FieldLabel>{t("connectionMode")}</FieldLabel>
                <ToggleGroup
                  type="single"
                  variant="outline"
                  size="sm"
                  value={mode}
                  onValueChange={(value) => value && setMode(value as typeof mode)}
                  aria-label={t("connectionModeAria")}
                  className="justify-start"
                >
                  <ToggleGroupItem value="http">
                    <Globe2 />
                    HTTP
                  </ToggleGroupItem>
                  <ToggleGroupItem value="stdio">
                    <Terminal />
                    {t("localCommand")}
                  </ToggleGroupItem>
                </ToggleGroup>
              </Field>
              <Field>
                <FieldLabel htmlFor="mcp-name">{t("serviceName")}</FieldLabel>
                <Input
                  id="mcp-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder={t("serviceNamePlaceholder")}
                />
              </Field>
            </div>

            {mode === "http" ? (
              <Field>
                <FieldLabel htmlFor="mcp-url">{t("httpEndpoint")}</FieldLabel>
                <Input
                  id="mcp-url"
                  value={url}
                  onChange={(event) => setUrl(event.target.value)}
                  placeholder="https://host/mcp"
                />
                <FieldDescription>{t("httpDescription")}</FieldDescription>
              </Field>
            ) : (
              <div className="grid gap-4 sm:grid-cols-[minmax(8rem,12rem)_minmax(0,1fr)]">
                <Field>
                  <FieldLabel htmlFor="mcp-command">{t("command")}</FieldLabel>
                  <Input
                    id="mcp-command"
                    value={command}
                    onChange={(event) => setCommand(event.target.value)}
                    placeholder="npx"
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="mcp-args">{t("arguments")}</FieldLabel>
                  <Input
                    id="mcp-args"
                    value={args}
                    onChange={(event) => setArgs(event.target.value)}
                    placeholder="-y @modelcontextprotocol/server-filesystem /data"
                  />
                </Field>
              </div>
            )}

            <div className="flex justify-end">
              <Button
                type="submit"
                size="sm"
                disabled={mounting || (mode === "http" ? !url.trim() : !command.trim())}
              >
                {mounting ? <Spinner /> : <Plug />}
                {mounting ? t("mounting") : t("mountService")}
              </Button>
            </div>
          </form>
        </div>
      </SettingsRow>

    </SettingsSection>
  );
}
