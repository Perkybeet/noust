import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { announce } from "../../app/Announcer";
import { setLocale } from "../../app/locale";
import { clearErrors, recordError } from "../../lib/errorBuffer";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { FakeBackend, RouteHandler } from "../../test/fakes";
import { closeReport, openReport } from "./store";

const downloads: { name: string; bytes: Uint8Array; type: string | undefined }[] = [];
vi.mock("../../lib/clipboard", async (original) => ({
  ...(await original<typeof import("../../lib/clipboard")>()),
  downloadBytes: (name: string, bytes: Uint8Array, type?: string) => {
    downloads.push({ name, bytes, type });
  },
}));

// What the live region is told is recorded, not read back from the page after a delay.
vi.mock("../../app/Announcer", async (original) => ({
  ...(await original<typeof import("../../app/Announcer")>()),
  announce: vi.fn(),
}));

// A 1x1 PNG.
const PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg==";

const SUPPORT = {
  version: "2.0.0",
  generated_at: "2026-11-05T10:00:00+00:00",
  hide_domains: false,
  markdown: "# Noust support report\n\n## Noust\n\n```\nversion 2.0.0 token=***\n```\n",
  sections: [
    { key: "noust", title: "Noust", body: "version 2.0.0", collected: true },
    { key: "journal", title: "Journal", body: "left out", collected: false },
  ],
};

function backend(more: Record<string, RouteHandler> = {}): FakeBackend {
  return fakeBackend({
    ...signedInRoutes(),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    "GET /api/system/support-report": () => json(200, SUPPORT),
    ...more,
  });
}

async function openFromHelp(path = "/databases") {
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1 });
  await harness.user.click(await screen.findByRole("button", { name: "Help" }));
  await harness.user.click(await screen.findByRole("menuitem", { name: "Report a problem" }));
  const dialog = await screen.findByRole("dialog", { name: "Report a problem" });
  return { ...harness, dialog };
}

/** The row of the list of parts that is named so, found by its button. */
function part(dialog: HTMLElement, name: RegExp): HTMLElement {
  const row = within(within(dialog).getByRole("list", { name: "Parts of the file" })).getByRole("button", { name }).closest("li");
  if (row === null) throw new Error(`no part named ${String(name)}`);
  return row;
}

function fileInput(dialog: HTMLElement): HTMLInputElement {
  const input = dialog.querySelector<HTMLInputElement>('input[type="file"]');
  if (input === null) throw new Error("the report has no file input");
  return input;
}

async function fillAndGo(harness: Awaited<ReturnType<typeof openFromHelp>>, title = "The table stays grey") {
  await harness.user.type(within(harness.dialog).getByRole("textbox", { name: "Title" }), title);
  await harness.user.type(within(harness.dialog).getByRole("textbox", { name: "What happened" }), "Opening Data it never loads.");
  await harness.user.click(within(harness.dialog).getByRole("button", { name: "Next: what is sent" }));
}

beforeEach(() => {
  downloads.length = 0;
  clearErrors();
});

afterEach(() => {
  closeReport();
});

describe("reporting a problem from the help menu", () => {
  it("opens from the help menu with the area of the page and the three steps", async () => {
    backend();
    const { dialog } = await openFromHelp("/databases");

    expect(within(dialog).getByText("It reaches whoever maintains Noust as a public issue on GitHub.")).toBeInTheDocument();
    const steps = within(dialog).getByRole("list", { name: /steps/i });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(3);
    expect(within(steps).getByRole("listitem", { current: "step" })).toHaveTextContent("What happened");
    expect(within(dialog).getByRole("combobox", { name: "Where does it happen" })).toHaveTextContent("Databases");
  });

  it("asks for a title and for what happened before it goes on, and puts the person where they are missing", async () => {
    backend();
    const { dialog, user } = await openFromHelp();

    await user.click(within(dialog).getByRole("button", { name: "Next: what is sent" }));

    expect(within(dialog).getByText("Write a title: one line that says what broke.")).toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: "Title" })).toHaveFocus();
    await user.type(within(dialog).getByRole("textbox", { name: "Title" }), "It broke");
    expect(within(dialog).queryByText("Write a title: one line that says what broke.")).toBeNull();

    await user.click(within(dialog).getByRole("button", { name: "Next: what is sent" }));

    expect(within(dialog).getByText("Say what happened: what you did, what you expected and what you saw.")).toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: "What happened" })).toHaveFocus();
    expect(within(dialog).queryByRole("button", { name: "Next: on GitHub" })).toBeNull();
  });

  it("shows what is sent: the support report made now, the browser's block and no image, each part opened as written", async () => {
    const fake = backend();
    recordError("request", "GET /api/databases/shop/data 500");
    const harness = await openFromHelp("/databases");

    await fillAndGo(harness);
    const { dialog } = harness;

    const parts = within(dialog).getByRole("list", { name: "Parts of the file" });
    expect(await within(parts).findByRole("button", { name: /support-report\.md/ })).toBeInTheDocument();
    expect(within(parts).getByRole("button", { name: /browser\.json/ })).toBeInTheDocument();
    expect(fake.callsTo("GET /api/system/support-report")[0]?.search.get("hide_domains")).toBe("false");

    await harness.user.click(within(parts).getByRole("button", { name: /browser\.json/ }));
    const block = part(dialog, /browser\.json/);
    expect(block).toHaveTextContent('"page": "/databases"');
    expect(block).toHaveTextContent("GET /api/databases/shop/data 500");
  });

  it("marks what was hidden as hidden, in the support report the server masked and in the browser's block", async () => {
    backend();
    recordError("console", "request to postgres://app:hunter22@db:5432/shop failed");
    const harness = await openFromHelp();
    await fillAndGo(harness);
    const { dialog, user } = harness;

    const parts = within(dialog).getByRole("list", { name: "Parts of the file" });
    await user.click(await within(parts).findByRole("button", { name: /support-report\.md/ }));
    await user.click(within(parts).getByRole("button", { name: /browser\.json/ }));

    const support = part(dialog, /support-report\.md/);
    expect(within(support).getByText("hidden")).toBeInTheDocument();
    const browser = part(dialog, /browser\.json/);
    expect(within(browser).getByText("hidden")).toBeInTheDocument();
    expect(browser).not.toHaveTextContent("hunter22");
    expect(parts).toHaveTextContent("1 hidden");
  });

  it("leaves the support report out when it is unchecked, and asks again with the domains replaced when asked", async () => {
    const fake = backend();
    const harness = await openFromHelp();
    await fillAndGo(harness);
    const { dialog, user } = harness;
    const parts = within(dialog).getByRole("list", { name: "Parts of the file" });
    await within(parts).findByRole("button", { name: /support-report\.md/ });

    await user.click(within(dialog).getByRole("checkbox", { name: /Replace domains/ }));
    await waitFor(() => {
      expect(fake.callsTo("GET /api/system/support-report").at(-1)?.search.get("hide_domains")).toBe("true");
    });
    await user.click(within(dialog).getByRole("checkbox", { name: /Include the support report/ }));

    expect(within(parts).queryByRole("button", { name: /support-report\.md/ })).toBeNull();
    expect(within(parts).getByRole("button", { name: /browser\.json/ })).toBeInTheDocument();
    expect(within(dialog).queryByRole("checkbox", { name: /Replace domains/ })).toBeNull();
  });

  it("says in words that the account cannot read the support report, and goes on without it", async () => {
    backend({ "GET /api/system/support-report": () => problem(403, "forbidden", "This credential cannot read the server.") });
    const harness = await openFromHelp();
    await fillAndGo(harness);

    expect(await within(harness.dialog).findByText("Your account cannot read the support report, so the file goes without it.")).toBeInTheDocument();
    const parts = within(harness.dialog).getByRole("list", { name: "Parts of the file" });
    expect(within(parts).queryByRole("button", { name: /support-report\.md/ })).toBeNull();
    expect(within(harness.dialog).getByRole("button", { name: "Next: on GitHub" })).toBeInTheDocument();
  });

  it("shows the server's own words when the support report cannot be collected, with a way to try again", async () => {
    const fake = backend({ "GET /api/system/support-report": () => problem(500, "internal", "The journal could not be read.") });
    const harness = await openFromHelp();
    await fillAndGo(harness);

    expect(await within(harness.dialog).findByText("Could not collect the support report")).toBeInTheDocument();
    expect(within(harness.dialog).getByText("The journal could not be read.")).toBeInTheDocument();
    fake.on("GET /api/system/support-report", () => json(200, SUPPORT));
    await harness.user.click(within(harness.dialog).getByRole("button", { name: "Try again" }));

    expect(await within(harness.dialog).findByRole("button", { name: /support-report\.md/ })).toBeInTheDocument();
  });

  it("takes an image the person chooses, shows it in the file and takes it out again", async () => {
    backend();
    const harness = await openFromHelp();
    const input = fileInput(harness.dialog);
    const file = new File([Uint8Array.from(atob(PNG_BASE64), (character) => character.charCodeAt(0))], "Captura de pantalla.png", { type: "image/png" });

    await harness.user.upload(input, file);

    expect(await within(harness.dialog).findByText("Captura-de-pantalla.png")).toBeInTheDocument();
    await harness.user.type(within(harness.dialog).getByRole("textbox", { name: "Title" }), "It broke");
    await harness.user.type(within(harness.dialog).getByRole("textbox", { name: "What happened" }), "Never loads.");
    await harness.user.click(within(harness.dialog).getByRole("button", { name: "Next: what is sent" }));
    expect(await within(harness.dialog).findByRole("button", { name: /images\/Captura-de-pantalla\.png/ })).toBeInTheDocument();

    await harness.user.click(within(harness.dialog).getByRole("button", { name: "What happened: done" }));
    await harness.user.click(within(harness.dialog).getByRole("button", { name: "Remove Captura-de-pantalla.png" }));

    expect(within(harness.dialog).queryByText("Captura-de-pantalla.png")).toBeNull();
  });

  it("refuses a file that is not an image a report can carry, in a sentence", async () => {
    backend();
    const harness = await openFromHelp();
    const input = fileInput(harness.dialog);

    // The picker's own filter is not what is under test: a file that gets past it is.
    fireEvent.change(input, { target: { files: [new File(["<svg/>"], "a.svg", { type: "image/svg+xml" })] } });

    expect(await within(harness.dialog).findByText("a.svg is not an image a report can carry. Use PNG, JPEG, WebP or GIF.")).toBeInTheDocument();
  });

  it("takes an image pasted from the clipboard, and leaves a pasted text alone", async () => {
    backend();
    const harness = await openFromHelp();
    const title = within(harness.dialog).getByRole("textbox", { name: "Title" });
    title.focus();
    const image = new File([Uint8Array.from(atob(PNG_BASE64), (character) => character.charCodeAt(0))], "image.png", { type: "image/png" });

    const text = new Event("paste", { bubbles: true, cancelable: true });
    Object.assign(text, { clipboardData: { files: [] } });
    const textCancelled = !title.dispatchEvent(text);
    const paste = new Event("paste", { bubbles: true, cancelable: true });
    Object.assign(paste, { clipboardData: { files: [image] } });
    const imageCancelled = !title.dispatchEvent(paste);

    expect(textCancelled).toBe(false);
    expect(imageCancelled).toBe(true);
    expect(await within(harness.dialog).findByText(/pasted-1\.png/)).toBeInTheDocument();
  });

  it("explains what happens on GitHub and shows what will already be written there, with the form's own option", async () => {
    backend();
    const harness = await openFromHelp("/databases");
    await fillAndGo(harness, "The table stays grey");
    await harness.user.click(await within(harness.dialog).findByRole("button", { name: "Next: on GitHub" }));
    const { dialog } = harness;

    expect(within(dialog).getByText("A file is downloaded")).toBeInTheDocument();
    expect(within(dialog).getByText("GitHub opens in another tab")).toBeInTheDocument();
    expect(within(dialog).getByText("There, you attach the file and send it yourself")).toBeInTheDocument();
    expect(within(dialog).getByText("[Console] The table stays grey")).toBeInTheDocument();
    expect(within(dialog).getByText("2.0.0")).toBeInTheDocument();
    expect(within(dialog).getByText("Databases")).toBeInTheDocument();
    expect(within(dialog).getByText("Opening Data it never loads.")).toBeInTheDocument();
    expect(within(dialog).getByText(/A security problem does not go here/)).toBeInTheDocument();
  });

  it("downloads the zip and opens GitHub's form filled in, in one press, and says what is left to do there", async () => {
    backend();
    const open = vi.fn(() => ({ opener: {} }) as unknown as Window);
    vi.stubGlobal("open", open);
    const harness = await openFromHelp("/databases");
    await fillAndGo(harness, "The table stays grey");
    await harness.user.click(await within(harness.dialog).findByRole("button", { name: "Next: on GitHub" }));

    await harness.user.click(within(harness.dialog).getByRole("button", { name: "Download and open GitHub" }));

    expect(downloads).toHaveLength(1);
    expect(downloads[0]?.name).toMatch(/^noust-report-\d{8}-\d{4}\.zip$/);
    expect(downloads[0]?.type).toBe("application/zip");
    const archive = new TextDecoder().decode(downloads[0]?.bytes);
    expect(archive).toContain("support-report.md");
    expect(archive).toContain("browser.json");
    expect(archive).toContain("# Noust support report");
    expect(open).toHaveBeenCalledOnce();
    const [url, target] = open.mock.calls[0] as unknown as [string, string];
    const opened = new URL(url);
    expect(target).toBe("_blank");
    expect(opened.searchParams.get("template")).toBe("console_report.yml");
    expect(opened.searchParams.get("title")).toBe("[Console] The table stays grey");
    expect(opened.searchParams.get("area")).toBe("Databases");
    expect(opened.searchParams.get("version")).toBe("2.0.0");
    expect(opened.searchParams.get("what-happened")).toBe("Opening Data it never loads.");

    expect(await within(harness.dialog).findByText(/was downloaded/)).toBeInTheDocument();
    expect(within(harness.dialog).getByText(downloads[0]?.name ?? "x")).toBeInTheDocument();
    expect(within(harness.dialog).getByText("GitHub was opened in another tab, with the form filled in")).toBeInTheDocument();
    expect(within(harness.dialog).getByText(/Drag the file into the last box, The report file from the console\./)).toBeInTheDocument();
    expect(within(harness.dialog).getByText(/Nothing is published until then\./)).toBeInTheDocument();
    expect(within(harness.dialog).queryByRole("button", { name: "Download and open GitHub" })).toBeNull();
  });

  it("can download and open again from the end, and says so when the browser did not open the tab", async () => {
    backend();
    const open = vi.fn(() => null);
    vi.stubGlobal("open", open);
    const harness = await openFromHelp();
    await fillAndGo(harness);
    await harness.user.click(await within(harness.dialog).findByRole("button", { name: "Next: on GitHub" }));
    await harness.user.click(within(harness.dialog).getByRole("button", { name: "Download and open GitHub" }));

    expect(await within(harness.dialog).findByText(/The browser did not open the new tab/)).toBeInTheDocument();
    await harness.user.click(within(harness.dialog).getByRole("button", { name: "Download again" }));
    await harness.user.click(within(harness.dialog).getByRole("button", { name: "Open again" }));

    expect(downloads).toHaveLength(2);
    expect(downloads[1]?.name).toBe(downloads[0]?.name);
    expect(open).toHaveBeenCalledTimes(2);
    // The dialog's own cross and the footer's button: both close it.
    const closes = within(harness.dialog).getAllByRole("button", { name: "Close" });
    expect(closes).toHaveLength(2);
    await harness.user.click(closes.at(-1) ?? document.body);
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Report a problem" })).toBeNull();
    });
  });

  it("hides a secret the person pasted into their own text, before it reaches the address or the file, and says so", async () => {
    backend();
    const open = vi.fn(() => ({ opener: null }) as unknown as Window);
    vi.stubGlobal("open", open);
    const harness = await openFromHelp();
    await harness.user.type(within(harness.dialog).getByRole("textbox", { name: "Title" }), "It broke");
    await harness.user.click(within(harness.dialog).getByRole("textbox", { name: "What happened" }));
    await harness.user.paste("It says DB_PASSWORD=hunter2222 in the log.");
    await harness.user.click(within(harness.dialog).getByRole("button", { name: "Next: what is sent" }));
    await harness.user.click(await within(harness.dialog).findByRole("button", { name: "Next: on GitHub" }));

    expect(within(harness.dialog).getByText("1 value that looks like a secret was hidden in what you wrote.")).toBeInTheDocument();
    expect(within(harness.dialog).getByText("It says DB_PASSWORD=*** in the log.")).toBeInTheDocument();
    await harness.user.click(within(harness.dialog).getByRole("button", { name: "Download and open GitHub" }));

    const [url] = open.mock.calls[0] as unknown as [string];
    expect(new URL(url).searchParams.get("what-happened")).toBe("It says DB_PASSWORD=*** in the log.");
    expect(new URL(url).search).not.toContain("hunter2222");
  });

  it("goes back to a step that is done, with what was written still there", async () => {
    backend();
    const harness = await openFromHelp();
    await fillAndGo(harness, "Kept title");
    await within(harness.dialog).findByRole("list", { name: "Parts of the file" });

    await harness.user.click(within(harness.dialog).getByRole("button", { name: "Back" }));

    expect(within(harness.dialog).getByRole("textbox", { name: "Title" })).toHaveValue("Kept title");
  });

  it("says each step to a screen reader as it changes", async () => {
    backend();
    const harness = await openFromHelp();
    await fillAndGo(harness);

    expect(announce).toHaveBeenCalledWith("What is sent", "polite");
  });

  it("is in Spanish when the console is, with the form's own option beside it on GitHub's step", async () => {
    backend();
    await act(() => setLocale("es"));
    const harness = renderConsole("/databases");
    await screen.findByRole("heading", { level: 1 });
    await harness.user.click(await screen.findByRole("button", { name: "Ayuda" }));
    await harness.user.click(await screen.findByRole("menuitem", { name: "Informar de un problema" }));
    const dialog = await screen.findByRole("dialog", { name: "Informar de un problema" });

    await harness.user.type(within(dialog).getByRole("textbox", { name: "Título" }), "La tabla no carga");
    await harness.user.type(within(dialog).getByRole("textbox", { name: "Qué ha pasado" }), "Al abrir Datos se queda en gris.");
    await harness.user.click(within(dialog).getByRole("button", { name: "Siguiente: qué se envía" }));
    await harness.user.click(await within(dialog).findByRole("button", { name: "Siguiente: en GitHub" }));

    expect(within(dialog).getByText("Databases (Bases de datos)")).toBeInTheDocument();
    expect(within(dialog).getByText("Se descarga un archivo")).toBeInTheDocument();
  });

  it("has no accessibility violations in any of its steps", async () => {
    backend();
    const harness = await openFromHelp();
    await expectNoAxeViolations(document.body, { page: true });

    await fillAndGo(harness);
    await within(harness.dialog).findByRole("button", { name: /support-report\.md/ });
    await harness.user.click(within(harness.dialog).getByRole("button", { name: /browser\.json/ }));
    await expectNoAxeViolations(document.body, { page: true });

    await harness.user.click(within(harness.dialog).getByRole("button", { name: "Next: on GitHub" }));
    await expectNoAxeViolations(document.body, { page: true });
  });
});

describe("reporting a problem on a phone", () => {
  afterEach(() => {
    Object.defineProperty(window, "innerWidth", { value: 1024, configurable: true });
  });

  it("is in More, where the top bar has no help menu, and opens the same report", async () => {
    Object.defineProperty(window, "innerWidth", { value: 390, configurable: true });
    backend();
    const { user } = renderConsole("/databases");
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("button", { name: "Help" })).toBeNull();

    await user.click(within(screen.getByRole("navigation", { name: "Areas" })).getByRole("button", { name: "More" }));
    const sheet = await screen.findByRole("dialog", { name: "All areas" });
    await user.click(within(sheet).getByRole("button", { name: "Report a problem" }));
    const dialog = await screen.findByRole("dialog", { name: "Report a problem" });

    expect(within(dialog).getByRole("combobox", { name: "Where does it happen" })).toHaveTextContent("Databases");
  });
});

describe("reporting a problem from the error screen", () => {
  it("puts the error that was shown into what happened, masked, and keeps the page's area", async () => {
    backend();
    renderConsole("/databases");
    await screen.findByRole("heading", { level: 1 });

    await act(() => openReport({ from: "error", pathname: "/databases/postgresql/shop", error: "TypeError: Cannot read properties of undefined (reading 'rows')\nGET /x?token=0123456789abcdef" }));
    const dialog = await screen.findByRole("dialog", { name: "Report a problem" });

    const happened = within(dialog).getByRole("textbox", { name: "What happened" });
    expect((happened as HTMLTextAreaElement).value).toBe(
      "The page broke with this error:\nTypeError: Cannot read properties of undefined (reading 'rows')\nGET /x?token=***",
    );
    expect(within(dialog).getByRole("combobox", { name: "Where does it happen" })).toHaveTextContent("Databases");
  });
});
