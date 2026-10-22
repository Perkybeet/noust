import { renderHook, act } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { QueryClient } from "@tanstack/react-query";

import { clearNotices, recordNotice, RECENT_NOTICES, useRecentNotices } from "./notices";
import { applyServerEvent } from "./events";

afterEach(() => {
  clearNotices();
});

describe("the notices kept for the inbox", () => {
  it("keeps what the server said, newest first, with the server it came from", () => {
    const { result } = renderHook(() => useRecentNotices());
    act(() => {
      recordNotice("Deployed shop", "active", null, 1_000);
      recordNotice("Backup failed", "failed", "web-2", 2_000);
    });
    expect(result.current.map((notice) => [notice.text, notice.state, notice.node, notice.at])).toEqual([
      ["Backup failed", "failed", "web-2", 2_000],
      ["Deployed shop", "active", null, 1_000],
    ]);
  });

  it("keeps the last thirty and lets the oldest go", () => {
    const { result } = renderHook(() => useRecentNotices());
    act(() => {
      for (let index = 0; index < RECENT_NOTICES + 5; index += 1) recordNotice(`notice ${String(index)}`, "idle");
    });
    expect(result.current).toHaveLength(RECENT_NOTICES);
    expect(result.current[0]?.text).toBe(`notice ${String(RECENT_NOTICES + 4)}`);
    expect(result.current.at(-1)?.text).toBe("notice 5");
  });

  it("takes in what the event stream says, and a state it does not know as a plain notice", () => {
    const { result } = renderHook(() => useRecentNotices());
    const client = new QueryClient();
    act(() => {
      applyServerEvent(client, "notice", { text: "Something odd", state: "mystery" });
      applyServerEvent(client, "notice", { text: "Restarted shop", state: "active" });
      // Without text there is nothing to say.
      applyServerEvent(client, "notice", { state: "failed" });
    });
    expect(result.current.map((notice) => [notice.text, notice.state])).toEqual([
      ["Restarted shop", "active"],
      ["Something odd", "idle"],
    ]);
  });
});
