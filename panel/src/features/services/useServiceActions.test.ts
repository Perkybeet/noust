import { act, renderHook } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { createQueryClient } from "../../app/App";
import { serviceKeys } from "../../api/queries/services";
import { fakeBackend, json } from "../../test/fakes";
import { useServiceActions } from "./useServiceActions";

function wrapper(client = createQueryClient()) {
  return { client, Wrapper: ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client }, children) };
}

describe("useServiceActions", () => {
  it("drops the deleted unit's cached detail (and its logs and config) rather than leaving it stale", async () => {
    fakeBackend({
      "DELETE /api/services/shop-web": () => json(200, { success: true, message: "Deleted shop-web" }),
    });
    const { client, Wrapper } = wrapper();
    client.setQueryData(serviceKeys.detail("shop-web"), { name: "shop-web", active: true });
    client.setQueryData(serviceKeys.config("shop-web"), { config: "[Unit]" });

    const { result } = renderHook(() => useServiceActions("shop-web"), { wrapper: Wrapper });
    await act(async () => {
      await result.current.remove.mutateAsync();
    });

    expect(client.getQueryData(serviceKeys.detail("shop-web"))).toBeUndefined();
    expect(client.getQueryData(serviceKeys.config("shop-web"))).toBeUndefined();
  });
});
