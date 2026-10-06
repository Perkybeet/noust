import { QueryClient, QueryClientProvider, QueryObserver } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { certKeys } from "../../api/queries/certs";
import { siteKeys } from "../../api/queries/sites";
import { useServerEvents } from "../../realtime/events";
import { ANONYMOUS, FakeEventSource, fakeBackend, json } from "../../test/fakes";
import { useCertificateRefresh } from "./useCertificateJobs";

function mount(client: QueryClient) {
  fakeBackend({ "GET /api/auth/session": () => json(200, { ...ANONYMOUS, authenticated: true }) });
  return renderHook(
    () => {
      useServerEvents((url) => new FakeEventSource(url));
      useCertificateRefresh();
    },
    { wrapper: ({ children }) => createElement(QueryClientProvider, { client }, children) },
  );
}

describe("useCertificateRefresh", () => {
  it("refreshes the certificate list, one certificate's own entry, and the sites once a certificate job ends", () => {
    const client = new QueryClient();
    client.setQueryData(certKeys.all, { certificates: [] });
    client.setQueryData(certKeys.detail("shop.example.com"), { domain: "shop.example.com" });
    client.setQueryData(siteKeys.all, { sites: [] });
    client.setQueryData(siteKeys.detail("shop.example.com"), { domain: "shop.example.com" });
    mount(client);

    FakeEventSource.latest().emit("job", { id: "j1", type: "cert_renew", status: "running", metadata: { domain: "shop.example.com" } });
    expect(client.getQueryState(certKeys.all)?.isInvalidated).toBe(false);

    FakeEventSource.latest().emit("job", { id: "j1", type: "cert_renew", status: "completed", metadata: { domain: "shop.example.com" } });
    expect(client.getQueryState(certKeys.all)?.isInvalidated).toBe(true);
    expect(client.getQueryState(certKeys.detail("shop.example.com"))?.isInvalidated).toBe(true);
    expect(client.getQueryState(siteKeys.all)?.isInvalidated).toBe(true);
    expect(client.getQueryState(siteKeys.detail("shop.example.com"))?.isInvalidated).toBe(true);
  });

  it("does not read the certificates again for a job that is not about one", async () => {
    const client = new QueryClient();
    const read = vi.fn(() => Promise.resolve({ certificates: [] }));
    // A page showing the list: a refetch of it would be a second read.
    const observer = new QueryObserver(client, { queryKey: certKeys.all, queryFn: read, staleTime: Infinity });
    const unsubscribe = observer.subscribe(() => undefined);
    await vi.waitFor(() => {
      expect(read).toHaveBeenCalledTimes(1);
    });
    mount(client);

    // The palette's index goes stale with any job that ends (events.test.ts); this hook's refresh does not.
    FakeEventSource.latest().emit("job", { id: "j2", type: "deploy", status: "completed", metadata: { domain: "shop.example.com" } });
    await Promise.resolve();
    expect(client.isFetching()).toBe(0);
    expect(read).toHaveBeenCalledTimes(1);
    unsubscribe();
  });
});
