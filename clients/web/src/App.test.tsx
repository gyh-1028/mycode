import { beforeEach, describe, expect, it } from "vitest";
import { loadPermissionMode } from "./App";

describe("permission mode persistence", () => {
  beforeEach(() => localStorage.clear());

  it("restores standard and read-only modes", () => {
    expect(loadPermissionMode()).toBe("standard");
    localStorage.setItem("mycode-permission-mode", "read-only");
    expect(loadPermissionMode()).toBe("read-only");
  });

  it("never restores full access after a reload", () => {
    localStorage.setItem("mycode-permission-mode", "full-access");
    expect(loadPermissionMode()).toBe("standard");
  });
});
