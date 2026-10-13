/**
 * What the output viewer lets a reader do with a log that has levels, times and a lot of lines
 * (checklist M-38): keep only the levels that matter, with the word and the shape of each and not
 * only a colour, hide the time column, and take the output to the whole screen.
 */

import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { downloadText } from "../../lib/clipboard";
import { expectNoAxeViolations } from "../../test/axe";
import type { LogLine } from "./LogViewer";
import { LogViewer } from "./LogViewer";

vi.mock("../../lib/clipboard", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/clipboard")>();
  return { ...actual, downloadText: vi.fn(actual.downloadText) };
});

const LOG: LogLine[] = [
  { id: 1, text: "starting", level: "info", ts: "14:31:01" },
  { id: 2, text: "listening on 3000", ts: "14:31:02" },
  { id: 3, text: "deprecated glob@7", level: "warn", ts: "14:31:03" },
  { id: 4, text: "502 Bad Gateway", level: "error", ts: "14:31:04" },
  { id: 5, text: "retrying", level: "info", ts: "14:31:05" },
  { id: 6, text: "ECONNREFUSED", level: "error", ts: "14:31:06" },
];

function copied(): { writeText: ReturnType<typeof vi.fn> } {
  const writeText = vi.fn(() => Promise.resolve());
  vi.stubGlobal("isSecureContext", true);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  return { writeText };
}

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the level filter", () => {
  it("offers a chip for each level the output has, with its word, its shape and its count", () => {
    render(<LogViewer lines={LOG} />);

    const group = screen.getByRole("group", { name: "Filter by level" });
    // A line without a level is information.
    expect(within(group).getByRole("button", { name: "Info 3" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "Warning 1" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "Error 2" })).toBeInTheDocument();
    // Never a colour alone: the error is a cross and the warning a triangle, as everywhere else.
    expect(within(group).getByRole("button", { name: "Error 2" }).querySelector("[data-glyph='cross']")).not.toBeNull();
    expect(within(group).getByRole("button", { name: "Warning 1" }).querySelector("[data-glyph='triangle']")).not.toBeNull();
    // A level the output does not have is not offered.
    expect(within(group).queryByRole("button", { name: /Debug/ })).not.toBeInTheDocument();
  });

  it("says the words it is given for a level, and keeps its shape", () => {
    render(<LogViewer lines={LOG} levelWords={{ warn: "Client errors", error: "Server errors" }} />);

    const group = screen.getByRole("group", { name: "Filter by level" });
    expect(within(group).getByRole("button", { name: "Server errors 2" }).querySelector("[data-glyph='cross']")).not.toBeNull();
    expect(within(group).getByRole("button", { name: "Client errors 1" }).querySelector("[data-glyph='triangle']")).not.toBeNull();
    // A level it was told nothing about keeps its own word.
    expect(within(group).getByRole("button", { name: "Info 3" })).toBeInTheDocument();
  });

  it("offers no filter when every line is at the same level", () => {
    render(<LogViewer lines={[{ id: 1, text: "one" }, { id: 2, text: "two", level: "info" }]} />);

    expect(screen.queryByRole("group", { name: "Filter by level" })).not.toBeInTheDocument();
  });

  it("keeps only the chosen levels, with the line numbers they have in the whole output", async () => {
    render(<LogViewer lines={LOG} label="Journal" />);
    const region = screen.getByRole("region", { name: "Journal" });

    await userEvent.click(screen.getByRole("button", { name: "Error 2" }));

    expect(screen.getByRole("button", { name: "Error 2" })).toHaveAttribute("aria-pressed", "true");
    expect(within(region).getByText("502 Bad Gateway")).toBeInTheDocument();
    expect(within(region).getByText("ECONNREFUSED")).toBeInTheDocument();
    expect(within(region).queryByText("starting")).not.toBeInTheDocument();
    expect(within(region).queryByText("deprecated glob@7")).not.toBeInTheDocument();
    // Lines 4 and 6 of the whole output, so the number still finds the line in the full log.
    const numbers = [...region.querySelectorAll("[data-index] > span[aria-hidden='true']")].map((cell) => cell.textContent);
    expect(numbers).toEqual(["4", "6"]);

    await userEvent.click(screen.getByRole("button", { name: "Warning 1" }));
    expect(within(region).getByText("deprecated glob@7")).toBeInTheDocument();
    expect(within(region).queryByText("retrying")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Error 2" }));
    await userEvent.click(screen.getByRole("button", { name: "Warning 1" }));
    expect(within(region).getByText("starting")).toBeInTheDocument();
    expect(within(region).getByText("ECONNREFUSED")).toBeInTheDocument();
  });

  it("searches only the lines it shows", async () => {
    render(<LogViewer lines={LOG} />);

    await userEvent.click(screen.getByRole("button", { name: "Warning 1" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "Search output" }), "e");

    // "deprecated" has three e's and is the only line shown: the other lines' e's are not counted.
    expect(screen.getByText("1 of 3")).toHaveAttribute("role", "status");
    expect(within(screen.getByRole("region", { name: "Log output" })).queryByText("retrying")).not.toBeInTheDocument();
  });

  it("copies and downloads what the filter shows, and says so", async () => {
    const { writeText } = copied();
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:log"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<LogViewer lines={LOG} filename="shop.log" />);
    const whole = "Copies or downloads the entire log, not only the lines currently shown.";
    expect(screen.getByText(whole)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Error 2" }));

    const filtered = "Copies or downloads only the lines of the levels chosen, not the whole log.";
    expect(screen.getByText(filtered)).toBeInTheDocument();
    expect(screen.queryByText(whole)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy output" })).toHaveAccessibleDescription(filtered);
    await userEvent.click(screen.getByRole("button", { name: "Copy output" }));
    expect(writeText).toHaveBeenCalledWith("14:31:04 502 Bad Gateway\n14:31:06 ECONNREFUSED");
    await userEvent.click(screen.getByRole("button", { name: "Download output" }));
    expect(downloadText).toHaveBeenCalledWith("shop.log", "14:31:04 502 Bad Gateway\n14:31:06 ECONNREFUSED\n");
  });

  it("forgets a level the output no longer has instead of hiding everything behind it", async () => {
    const { rerender } = render(<LogViewer lines={LOG} label="Journal" />);
    await userEvent.click(screen.getByRole("button", { name: "Error 2" }));

    rerender(<LogViewer lines={LOG.filter((line) => line.level !== "error")} label="Journal" />);

    const region = screen.getByRole("region", { name: "Journal" });
    expect(within(region).getByText("starting")).toBeInTheDocument();
    expect(within(region).getByText("deprecated glob@7")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Error/ })).not.toBeInTheDocument();
  });

  it("has no accessibility violations with a level chosen", async () => {
    const { container } = render(<LogViewer lines={LOG} label="Build log" />);
    await userEvent.click(screen.getByRole("button", { name: "Warning 1" }));

    await expectNoAxeViolations(container);
  });
});

describe("the time column", () => {
  it("is shown while the lines have times, and can be hidden", async () => {
    const { writeText } = copied();
    render(<LogViewer lines={LOG} label="Journal" />);
    const region = screen.getByRole("region", { name: "Journal" });
    const toggle = screen.getByRole("button", { name: "Show times" });
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(within(region).getByText("14:31:01")).toBeInTheDocument();

    await userEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(within(region).queryByText("14:31:01")).not.toBeInTheDocument();
    expect(within(region).getByText("starting")).toBeInTheDocument();
    // What is copied is what is shown.
    await userEvent.click(screen.getByRole("button", { name: "Copy output" }));
    expect(writeText).toHaveBeenCalledWith(LOG.map((line) => line.text).join("\n"));
  });

  it("is not offered when no line has a time", () => {
    render(<LogViewer lines={[{ id: 1, text: "one" }, { id: 2, text: "two" }]} />);

    expect(screen.queryByRole("button", { name: "Show times" })).not.toBeInTheDocument();
  });
});

describe("full screen", () => {
  function supportFullScreen(request: () => Promise<void> = () => Promise.resolve()) {
    Object.defineProperty(document, "fullscreenEnabled", { configurable: true, value: true });
    const requestFullscreen = vi.fn(request);
    const exitFullscreen = vi.fn(() => Promise.resolve());
    Object.defineProperty(HTMLElement.prototype, "requestFullscreen", { configurable: true, value: requestFullscreen });
    Object.defineProperty(document, "exitFullscreen", { configurable: true, value: exitFullscreen });
    return { requestFullscreen, exitFullscreen };
  }

  /** What the browser does once it has granted the request: the element is the full-screen one. */
  function enter(element: Element | null): void {
    Object.defineProperty(document, "fullscreenElement", { configurable: true, get: () => element });
    act(() => {
      document.dispatchEvent(new Event("fullscreenchange"));
    });
  }

  afterEach(() => {
    Reflect.deleteProperty(document, "fullscreenEnabled");
    Reflect.deleteProperty(document, "fullscreenElement");
    Reflect.deleteProperty(document, "exitFullscreen");
    Reflect.deleteProperty(HTMLElement.prototype, "requestFullscreen");
  });

  it("is not offered where the browser cannot do it", () => {
    render(<LogViewer lines={LOG} />);

    expect(screen.queryByRole("button", { name: "Full screen" })).not.toBeInTheDocument();
  });

  it("takes the viewer, not the page, to the whole screen and back", async () => {
    const { requestFullscreen, exitFullscreen } = supportFullScreen();
    const { container } = render(<LogViewer lines={LOG} height={300} />);
    const viewer = container.firstElementChild as HTMLElement;
    const button = screen.getByRole("button", { name: "Full screen" });
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(viewer).toHaveStyle({ height: "300px" });

    await userEvent.click(button);
    expect(requestFullscreen.mock.contexts[0]).toBe(viewer);

    enter(viewer);
    expect(screen.getByRole("button", { name: "Full screen" })).toHaveAttribute("aria-pressed", "true");
    // The fixed height would keep a full-screen viewer 300px tall.
    expect(viewer).not.toHaveStyle({ height: "300px" });

    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(exitFullscreen).toHaveBeenCalledOnce();
    enter(null);
    expect(screen.getByRole("button", { name: "Full screen" })).toHaveAttribute("aria-pressed", "false");
    expect(viewer).toHaveStyle({ height: "300px" });
  });

  it("stays as it was when the browser refuses", async () => {
    supportFullScreen(() => Promise.reject(new TypeError("not allowed")));
    render(<LogViewer lines={LOG} />);

    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));

    expect(screen.getByRole("button", { name: "Full screen" })).toHaveAttribute("aria-pressed", "false");
  });

  it("does not follow another element's full screen", () => {
    supportFullScreen();
    render(<LogViewer lines={LOG} />);

    enter(document.body);

    expect(screen.getByRole("button", { name: "Full screen" })).toHaveAttribute("aria-pressed", "false");
  });
});

describe("in Spanish", () => {
  afterEach(async () => {
    await act(async () => {
      await setLocale("en");
    });
  });

  it("names the chips, the times and the full screen", async () => {
    Object.defineProperty(document, "fullscreenEnabled", { configurable: true, value: true });
    await act(async () => {
      await setLocale("es");
    });
    render(<LogViewer lines={LOG} />);

    const group = screen.getByRole("group", { name: "Filtrar por nivel" });
    expect(within(group).getByRole("button", { name: "Aviso 1" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "Error 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mostrar la hora" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pantalla completa" })).toBeInTheDocument();
    Reflect.deleteProperty(document, "fullscreenEnabled");
  });
});
