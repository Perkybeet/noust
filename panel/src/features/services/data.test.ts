import { describe, expect, it } from "vitest";

import type { ServiceInfo } from "./data";
import { appOfUnit, filterServices, isFiltered, serviceState, validateServicesSearch } from "./data";

function service(name: string, active: boolean, description: string | null = null): ServiceInfo {
  return { name, active, enabled: true, status: active ? "running" : "stopped", description, pid: null, uptime: null, memory: null, managed: true };
}

describe("serviceState", () => {
  it("is running when active and systemd reports nothing more specific", () => {
    expect(serviceState({ active: true, active_state: null, sub_state: null, result: null })).toEqual({
      state: "running",
      label: "services.state.running",
    });
    // Falls back to the bare `active` flag when the finer fields are absent entirely.
    expect(serviceState({ active: true })).toEqual({ state: "running", label: "services.state.running" });
  });

  it("is running when active_state says active, even with a quiet sub_state", () => {
    expect(
      serviceState({ active: true, active_state: "active", sub_state: "running", result: "success" }),
    ).toEqual({ state: "running", label: "services.state.running" });
  });

  it("is stopped when inactive and nothing says otherwise", () => {
    expect(serviceState({ active: false, active_state: "inactive", sub_state: "dead", result: "success" })).toEqual({
      state: "stopped",
      label: "services.state.stopped",
    });
    expect(serviceState({ active: false })).toEqual({ state: "stopped", label: "services.state.stopped" });
  });

  it("is failed - the state word stays Failed - with systemd's own result word as a separate detail", () => {
    expect(serviceState({ active: false, active_state: "failed", sub_state: "failed", result: "exit-code" })).toEqual({
      state: "failed",
      label: "services.state.failed",
      detail: "exit-code",
    });
    expect(serviceState({ active: false, active_state: "failed", sub_state: "failed", result: "signal" })).toEqual({
      state: "failed",
      label: "services.state.failed",
      detail: "signal",
    });
  });

  it("is failed without a specific word when result is missing or success", () => {
    expect(serviceState({ active: false, active_state: "failed", sub_state: "failed", result: null })).toEqual({
      state: "failed",
      label: "services.state.failed",
    });
    expect(serviceState({ active: false, active_state: "failed", sub_state: "failed", result: "success" })).toEqual({
      state: "failed",
      label: "services.state.failed",
    });
  });

  it("is restarting (a distinct, attention-worthy state) mid crash-loop, before systemd gives up", () => {
    // A problem worth a look, not work in progress: "warning" (amber, triangle), not
    // "deploying" (which would draw the spinning arc a real deploy uses).
    expect(serviceState({ active: false, active_state: "activating", sub_state: "auto-restart", result: null })).toEqual({
      state: "warning",
      label: "services.state.restarting",
    });
    // Either signal alone is enough: they do not always arrive together.
    expect(serviceState({ active: false, active_state: "activating", sub_state: "start", result: null })).toEqual({
      state: "warning",
      label: "services.state.restarting",
    });
    expect(serviceState({ active: true, active_state: "active", sub_state: "auto-restart", result: null })).toEqual({
      state: "warning",
      label: "services.state.restarting",
    });
  });

  it("is restarting rather than failed while a failing unit is still being retried", () => {
    expect(
      serviceState({ active: false, active_state: "activating", sub_state: "auto-restart", result: "exit-code" }),
    ).toEqual({ state: "warning", label: "services.state.restarting" });
  });

  it("reads the crash-loop fields case-insensitively and trims them", () => {
    expect(serviceState({ active: false, active_state: " Activating ", sub_state: null, result: null })).toEqual({
      state: "warning",
      label: "services.state.restarting",
    });
  });
});

describe("validateServicesSearch", () => {
  it("keeps a trimmed query", () => {
    expect(validateServicesSearch({ q: " worker " })).toEqual({ q: "worker" });
  });

  it("drops what is malformed", () => {
    expect(validateServicesSearch({ q: "" })).toEqual({});
    expect(validateServicesSearch({ q: 42 })).toEqual({});
    expect(validateServicesSearch({})).toEqual({});
  });

  it("bounds free text", () => {
    expect(validateServicesSearch({ q: "x".repeat(500) }).q).toHaveLength(200);
  });

  it("reads the show-all-units toggle from the URL, the same way a boolean filter elsewhere does", () => {
    expect(validateServicesSearch({ all: "1" })).toEqual({ all: true });
    expect(validateServicesSearch({ all: true })).toEqual({ all: true });
    expect(validateServicesSearch({})).toEqual({});
    expect(validateServicesSearch({ all: "0" })).toEqual({});
    expect(validateServicesSearch({ all: "yes" })).toEqual({});
  });

  it("keeps the query and the toggle together", () => {
    expect(validateServicesSearch({ q: "nginx", all: "1" })).toEqual({ q: "nginx", all: true });
  });
});

describe("filterServices", () => {
  const services = [service("wasm-queue", true, "node worker.js"), service("wasm-cache", false, "redis-server"), service("nginx", true)];

  it("matches the name or the command, case-insensitively", () => {
    expect(filterServices(services, { q: "QUEUE" }).map((s) => s.name)).toEqual(["wasm-queue"]);
    expect(filterServices(services, { q: "redis" }).map((s) => s.name)).toEqual(["wasm-cache"]);
  });

  it("keeps everything without a filter", () => {
    expect(filterServices(services, {})).toHaveLength(services.length);
    expect(isFiltered({})).toBe(false);
    expect(isFiltered({ q: "x" })).toBe(true);
  });

  it("does not fail a service with no recorded command", () => {
    expect(filterServices(services, { q: "nginx" }).map((s) => s.name)).toEqual(["nginx"]);
  });

  it("is not affected by the show-all-units toggle: that scope is server-side", () => {
    expect(isFiltered({ all: true })).toBe(false);
    expect(filterServices(services, { all: true })).toHaveLength(services.length);
  });
});

describe("the state filter and the application a unit runs", () => {
  const failed: ServiceInfo = { ...service("worker", false), active_state: "failed", result: "exit-code" };
  const units = [service("web", true), service("idle", false), failed];

  it("keeps the units in the state asked for, a crash loop counting as failed", () => {
    expect(filterServices(units, { state: "failed" }).map((unit) => unit.name)).toEqual(["worker"]);
    expect(filterServices(units, { state: "running" }).map((unit) => unit.name)).toEqual(["web"]);
    expect(filterServices(units, { state: "stopped" }).map((unit) => unit.name)).toEqual(["idle"]);
    expect(isFiltered({ state: "failed" })).toBe(true);
    expect(validateServicesSearch({ state: "failed", all: "1" })).toEqual({ all: true, state: "failed" });
    expect(validateServicesSearch({ state: "exploded" })).toEqual({});
  });

  it("finds the application whose unit it is, with or without the suffix", () => {
    const apps = [{ domain: "shop.example.com", unit: "shop-example-com.service" }, { domain: "static.example.com", unit: null }];
    expect(appOfUnit(apps, "shop-example-com")?.domain).toBe("shop.example.com");
    expect(appOfUnit(apps, "shop-example-com.service")?.domain).toBe("shop.example.com");
    expect(appOfUnit(apps, "worker")).toBeNull();
  });
});
