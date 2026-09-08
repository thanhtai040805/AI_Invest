import { describe, expect, it } from "vitest";

import {
  WORKSPACE_SECTIONS,
  isWorkspaceSection,
  workspaceSectionDefinition,
  workspaceSectionFromPathname,
} from "./workspace";

describe("workspace sections", () => {
  it("keeps normal and compact navigation on one ordered definition", () => {
    expect(WORKSPACE_SECTIONS.map((item) => item.id)).toEqual([
      "knowledge",
      "search",
    ]);
  });

  it.each([
    ["/search", "search"],
    ["/search/results", "search"],
    ["/chat", null],
    ["/chat/thread-1", null],
    ["/knowledge", "knowledge"],
    ["/knowledge/source-1", "knowledge"],
    ["/settings", null],
  ])("maps %s to %s", (pathname, expected) => {
    expect(workspaceSectionFromPathname(pathname)).toBe(expected);
  });

  it("validates persisted values and resolves section metadata", () => {
    expect(isWorkspaceSection("knowledge")).toBe(true);
    expect(isWorkspaceSection("answer")).toBe(false);
    expect(isWorkspaceSection("explore")).toBe(false);
    expect(workspaceSectionDefinition("knowledge")).toMatchObject({
      id: "knowledge",
      href: "/knowledge",
    });
  });
});
