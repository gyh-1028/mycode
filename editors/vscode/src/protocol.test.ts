import { describe, expect, it } from "vitest";
import { isNotification, parseProtocolLine } from "./protocol.js";

describe("protocol", () => {
  it("parses notifications", () => {
    const value = parseProtocolLine('{"jsonrpc":"2.0","method":"agent/event","params":{"type":"run.started"}}');
    expect(isNotification(value)).toBe(true);
  });

  it("rejects non JSON-RPC payloads", () => {
    expect(() => parseProtocolLine('{"message":"bad"}')).toThrow(/Invalid JSON-RPC/);
  });
});
