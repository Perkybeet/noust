import { renderHook, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { readLocalActiveTeam, useLocalActiveTeam, writeLocalActiveTeam } from "./activeTeam";

afterEach(() => {
  writeLocalActiveTeam(null, null);
  writeLocalActiveTeam("web-2", null);
});

describe("the team a browser remembers", () => {
  it("is none until one is chosen", () => {
    expect(readLocalActiveTeam(null)).toBeNull();
  });

  it("is kept per server: a node's teams are not this server's", () => {
    writeLocalActiveTeam(null, "acme");
    writeLocalActiveTeam("web-2", "globex");

    expect(readLocalActiveTeam(null)).toBe("acme");
    expect(readLocalActiveTeam("web-2")).toBe("globex");
  });

  it("is forgotten with null", () => {
    writeLocalActiveTeam(null, "acme");
    writeLocalActiveTeam(null, null);

    expect(readLocalActiveTeam(null)).toBeNull();
  });

  it("follows a change in the page that shows it", () => {
    const { result } = renderHook(() => useLocalActiveTeam(null));
    expect(result.current).toBeNull();

    act(() => writeLocalActiveTeam(null, "acme"));

    expect(result.current).toBe("acme");
  });

  it("holds the choice until the page is closed when the browser will not store it", () => {
    const failing = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage is disabled");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage is disabled");
    });

    writeLocalActiveTeam(null, "acme");

    expect(readLocalActiveTeam(null)).toBe("acme");
    failing.mockRestore();
    vi.restoreAllMocks();
  });
});
