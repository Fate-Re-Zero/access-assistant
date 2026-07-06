import { describe, expect, it } from "vitest";

import { formatArgValue, formatToolArgs } from "./formatToolArgs";

describe("formatToolArgs", () => {
  it("serializes nested object args instead of [object Object]", () => {
    const summary = formatToolArgs({
      request: {
        merchantName: "gpop_20180608",
        returnCode: -10250017,
        returnMessage: "Server reject, no authority",
        url: "https://hps.sdo.com/mobilegame/queryThirdAccByAppMid",
      },
    });

    expect(summary).toContain("merchantName=gpop_20180608");
    expect(summary).toContain("returnCode=-10250017");
    expect(summary).not.toContain("[object Object]");
  });

  it("formats primitive args directly", () => {
    expect(formatArgValue("abc")).toBe("abc");
    expect(formatArgValue(42)).toBe("42");
    expect(formatToolArgs({ url: "https://example.com", merchantName: "demo" })).toBe(
      "url=https://example.com, merchantName=demo",
    );
  });
});
