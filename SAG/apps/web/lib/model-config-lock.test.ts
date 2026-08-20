import { describe, expect, it } from "vitest";

import { isLlmConfigLocked } from "./model-config-lock";

describe("isLlmConfigLocked", () => {
  it("recognizes an LLM deployment lock from the effective configuration", () => {
    expect(isLlmConfigLocked({ locked_fields: ["llm_model"] })).toBe(true);
  });

  it("keeps the UI editable when no LLM field is locked", () => {
    expect(isLlmConfigLocked({ locked_fields: ["embedding_model"] })).toBe(false);
    expect(isLlmConfigLocked({ locked_fields: [] })).toBe(false);
  });
});
