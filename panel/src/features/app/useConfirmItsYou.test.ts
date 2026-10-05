import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement } from "react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { ElevationCancelledError } from "../../api/errors";
import { authKeys } from "../../api/queries/auth";
import type { SessionInfo } from "../../api/queries/auth";
import { createQueryClient } from "../../app/App";
import { SESSION, fakeBackend, json } from "../../test/fakes";
import { cancelElevation, useElevationRequested } from "../auth/elevation";
import { useConfirmItsYou } from "./useDeleteApp";

const MINUTE = 60_000;

function elevatedUntil(offsetMs: number): string {
  return new Date(Date.now() + offsetMs).toISOString();
}

function setup(cached: SessionInfo, server: SessionInfo) {
  const backend = fakeBackend({ "GET /api/auth/session": () => json(200, server) });
  const client = createQueryClient();
  client.setQueryData(authKeys.session, cached);
  const Wrapper = ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client }, children);
  const { result } = renderHook(() => useConfirmItsYou(), { wrapper: Wrapper });
  return { backend, confirm: result.current };
}

describe("useConfirmItsYou", () => {
  it("does not ask while the cached window is open, and does not even look at the server", async () => {
    const { backend, confirm } = setup({ ...SESSION, elevated_until: elevatedUntil(5 * MINUTE) }, SESSION);

    await act(async () => {
      await confirm();
    });

    expect(backend.callsTo("GET /api/auth/session")).toHaveLength(0);
  });

  it("does not ask when the cached deadline passed but the server moved it, as it does while the operator works", async () => {
    const { backend, confirm } = setup(
      { ...SESSION, elevated_until: elevatedUntil(-MINUTE) },
      { ...SESSION, elevated_until: elevatedUntil(10 * MINUTE) },
    );

    await act(async () => {
      await confirm();
    });

    expect(backend.callsTo("GET /api/auth/session")).toHaveLength(1);
  });

  it("asks only once the server also says the window closed", async () => {
    const { backend, confirm } = setup(
      { ...SESSION, elevated_until: elevatedUntil(-MINUTE) },
      { ...SESSION, elevated_until: elevatedUntil(-MINUTE) },
    );

    const dialog = renderHook(() => useElevationRequested());
    const asking = confirm().catch((error: unknown) => error);
    await waitFor(() => {
      expect(dialog.result.current).toBe(true);
    });
    act(() => {
      cancelElevation();
    });

    expect(backend.callsTo("GET /api/auth/session")).toHaveLength(1);
    await expect(asking).resolves.toBeInstanceOf(ElevationCancelledError);
  });
});
