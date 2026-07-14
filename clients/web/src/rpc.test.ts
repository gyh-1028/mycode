import { beforeEach, describe, expect, it } from "vitest";
import { resolveWebToken } from "./rpc";

describe("Web authentication token", () => {
  beforeEach(() => {
    sessionStorage.clear();
    history.replaceState(null, "", "/");
  });

  it("exchanges the URL fragment for a tab-scoped token that survives reload", () => {
    history.replaceState(null, "", "/#token=temporary-token");

    expect(resolveWebToken()).toBe("temporary-token");
    expect(window.location.hash).toBe("");
    expect(localStorage.getItem("mycode-web-token")).toBeNull();
    expect(resolveWebToken()).toBe("temporary-token");
  });
});
