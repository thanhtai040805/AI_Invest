const LLM_FIELDS = new Set([
  "llm_provider",
  "llm_base_url",
  "llm_api_key",
  "llm_model",
  "llm_temperature",
  "llm_max_tokens",
  "llm_context_window",
  "llm_timeout_ms",
  "llm_max_retries",
]);

export function isLlmConfigLocked(config: { locked_fields: string[] }) {
  return config.locked_fields.some((field) => LLM_FIELDS.has(field));
}
