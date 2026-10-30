import { vi } from "vitest";

/**
 * Gives every element a size, for the lists that draw only the rows in view.
 *
 * jsdom does no layout, so each element is 0 by 0 and a virtualized list (the log viewer, the
 * long comboboxes) finds an empty window and draws nothing. TanStack Virtual reads its scroller's
 * clientHeight and clientWidth (offsetHeight and offsetWidth before virtual-core 3.19), so both
 * pairs are given the same size: the next release that changes which one it reads costs this
 * function and not the sixteen test files that need a window.
 *
 * A spy, so `restoreMocks` takes it away after each test and the call belongs in a `beforeEach`
 * or at the start of the test that needs it.
 *
 * @param height - The height every element reports, the window's.
 * @param width - The width every element reports.
 */
export function giveElementsASize(height = 400, width = 800): void {
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(height);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(width);
  vi.spyOn(Element.prototype, "clientHeight", "get").mockReturnValue(height);
  vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(width);
}
