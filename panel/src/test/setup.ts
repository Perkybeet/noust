import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { resetApiHooks } from "../api/client";
import { resetLocale } from "../app/locale";
import { cancelElevation } from "../features/auth/elevation";
import { FakeEventSource, FakeWebSocket } from "./fakes";

// Every file starts in English, on <html> too, whatever the machine's browser languages.
resetLocale("en");

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  // Module-level state a test may leave behind: a pending "Confirm it's you", cookies, the
  // stored theme and language, the fakes' registries.
  cancelElevation();
  resetApiHooks();
  for (const cookie of document.cookie.split(";")) {
    const name = cookie.split("=")[0]?.trim();
    if (name) document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`;
  }
  window.localStorage.clear();
  delete document.documentElement.dataset["theme"];
  // Component tests read English: a test that switches to Spanish does not leak into the next.
  resetLocale("en");
  FakeEventSource.instances = [];
  FakeWebSocket.instances = [];
});

// jsdom implements neither of these; Base UI, uPlot and the log viewer read them. Plain
// functions, not mocks, so that restoreMocks between tests cannot strip them.
const noop = (): void => undefined;

if (typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string): MediaQueryList =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: noop,
        removeEventListener: noop,
        addListener: noop,
        removeListener: noop,
        dispatchEvent: () => false,
      }) as MediaQueryList,
  });
}

// The router restores scroll positions; jsdom has no layout to scroll.
Object.defineProperty(window, "scrollTo", { writable: true, value: noop });

if (typeof Element.prototype.scrollIntoView !== "function") {
  Object.defineProperty(Element.prototype, "scrollIntoView", { writable: true, value: noop });
}

class ResizeObserverStub {
  observe = noop;
  unobserve = noop;
  disconnect = noop;
}
if (typeof window.ResizeObserver !== "function") {
  Object.defineProperty(window, "ResizeObserver", { writable: true, value: ResizeObserverStub });
}
