import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, problem } from "../../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../../test/fakes";
import { TAB_DOMAIN, appRoutes, screenWidth } from "../testRoutes";
import { cleanFolder, joinPath, parentOf, validateFilesSearch } from "./FilesPage";

const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const entry = (name: string, kind: string, extra: Record<string, unknown> = {}) => ({
  name,
  kind,
  size: kind === "dir" ? 4096 : 2048,
  modified: "2026-10-13T10:00:00",
  mode: kind === "dir" ? "755" : "644",
  owner: "noust-app-shop",
  target: null,
  sensitive: false,
  ...extra,
});

const ROOT = {
  domain: TAB_DOMAIN,
  path: "",
  truncated: false,
  limits: { edit: 1_048_576, upload: 52_428_800 },
  entries: [
    entry("config", "dir"),
    entry("package.json", "file"),
    entry(".env", "file", { sensitive: true, mode: "600" }),
    entry("current", "link", { target: "releases/20261013-100000-aaaaaaa" }),
  ],
};

const CONFIG = { ...ROOT, path: "config", entries: [entry("production.json", "file")] };

function listing(call: RecordedCall) {
  return json(200, call.search.get("path") === "config" ? CONFIG : ROOT);
}

afterEach(() => {
  vi.restoreAllMocks();
});

async function files(extra: Record<string, RouteHandler> = {}, path = "", { empty = false }: { empty?: boolean } = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    appRoutes(
      {},
      {
        "GET /api/auth/session": () => json(200, ELEVATED),
        [`GET /api/apps/${TAB_DOMAIN}/files`]: listing,
        ...extra,
      },
    ),
  );
  const harness = renderConsole(`/apps/${TAB_DOMAIN}/files${path}`);
  // An empty folder has no list, only the invitation that says so.
  const list = empty ? document.body : await screen.findByRole("list", { name: /Contents of/ });
  return { ...harness, backend, list };
}

describe("the files of an application", () => {
  it("lists a folder with what each entry is, its size, its owner and its permissions", async () => {
    const { list } = await files();

    expect(within(list).getAllByRole("listitem")).toHaveLength(4);
    expect(within(list).getByText("package.json")).toBeInTheDocument();
    expect(within(list).getAllByText("noust-app-shop")).toHaveLength(4);
    expect(within(list).getByText("Link to releases/20261013-100000-aaaaaaa")).toBeInTheDocument();
  });

  it("opens a folder into the URL, with the folders above it as links back", async () => {
    const { user, location, list } = await files();

    await user.click(within(list).getByRole("button", { name: "Open the folder config" }));

    expect(await screen.findByText("production.json")).toBeInTheDocument();
    expect(location().search).toEqual({ path: "config" });
    const trail = screen.getByRole("navigation", { name: "Folder" });
    await user.click(within(trail).getByRole("link", { name: TAB_DOMAIN }));
    expect(await screen.findByText("package.json")).toBeInTheDocument();
  });

  it("starts in the folder the URL names", async () => {
    await files({}, "?path=config");
    expect(await screen.findByText("production.json")).toBeInTheDocument();
    expect(screen.queryByText("package.json")).not.toBeInTheDocument();
  });

  it("does not open an environment file, and says where it is edited", async () => {
    const { list } = await files();

    expect(within(list).getByText("Environment file")).toBeInTheDocument();
    expect(within(list).getByText("Not opened here: edit it in Environment.")).toBeInTheDocument();
    expect(within(list).queryByRole("button", { name: "Edit the file .env" })).not.toBeInTheDocument();
  });

  it("opens a text file in an editor, and saves what was typed over it", async () => {
    const { user, backend, list } = await files({
      [`GET /api/apps/${TAB_DOMAIN}/files/content`]: () => json(200, { domain: TAB_DOMAIN, path: "package.json", size: 12, content: '{"name": "shop"}' }),
      [`PUT /api/apps/${TAB_DOMAIN}/files/content`]: () => json(200, { domain: TAB_DOMAIN, path: "package.json", bytes: 17 }),
    });
    await user.click(within(list).getByRole("button", { name: "Edit the file package.json" }));
    const dialog = await screen.findByRole("dialog", { name: "package.json" });
    const text = await within(dialog).findByRole("textbox", { name: "Content of package.json" });
    expect(text).toHaveValue('{"name": "shop"}');

    await user.type(text, "\n");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${TAB_DOMAIN}/files/content`)[0]?.body).toEqual({ path: "package.json", content: '{"name": "shop"}\n', create: false });
    });
    expect(await screen.findByText("Saved package.json")).toBeInTheDocument();
  });

  it("does not let a file be saved over what could not be opened", async () => {
    const { user, backend, list } = await files({
      [`GET /api/apps/${TAB_DOMAIN}/files/content`]: () => problem(400, "validationerror", "package.json is not a text file the console edits"),
    });
    await user.click(within(list).getByRole("button", { name: "Edit the file package.json" }));
    const dialog = await screen.findByRole("dialog", { name: "package.json" });

    expect(await within(dialog).findByText("Could not open the file")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(backend.callsTo(`PUT /api/apps/${TAB_DOMAIN}/files/content`)).toHaveLength(0);
  });

  it("makes a folder in the one that is open", async () => {
    const { user, backend } = await files({
      [`POST /api/apps/${TAB_DOMAIN}/files/directory`]: () => json(201, { domain: TAB_DOMAIN, path: "uploads" }),
    });
    await user.click(screen.getByRole("button", { name: "New folder" }));
    const dialog = await screen.findByRole("dialog", { name: "Make a folder" });
    await user.type(within(dialog).getByRole("textbox", { name: "Name" }), "uploads");
    await user.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/files/directory`)[0]?.body).toEqual({ path: "uploads" });
    });
  });

  it("makes a new empty file where the folder is open", async () => {
    const { user, backend } = await files(
      { [`PUT /api/apps/${TAB_DOMAIN}/files/content`]: () => json(200, { domain: TAB_DOMAIN, path: "config/new.json", bytes: 0 }) },
      "?path=config",
    );
    await screen.findByText("production.json");
    await user.click(screen.getByRole("button", { name: "New file" }));
    const dialog = await screen.findByRole("dialog", { name: "Make a file" });
    await user.type(within(dialog).getByRole("textbox", { name: "Name" }), "new.json");
    await user.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${TAB_DOMAIN}/files/content`)[0]?.body).toEqual({ path: "config/new.json", content: "", create: true });
    });
  });

  it("refuses a name with a slash before asking anything", async () => {
    const { user, backend } = await files();
    await user.click(screen.getByRole("button", { name: "New folder" }));
    const dialog = await screen.findByRole("dialog", { name: "Make a folder" });
    await user.type(within(dialog).getByRole("textbox", { name: "Name" }), "a/b");
    await user.click(within(dialog).getByRole("button", { name: "Create" }));

    expect(await within(dialog).findByText("A name has no slash: open the folder first.")).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/files/directory`)).toHaveLength(0);
  });

  it("uploads a file into the open folder", async () => {
    const { user, backend } = await files({
      [`PUT /api/apps/${TAB_DOMAIN}/files/upload`]: () => json(200, { domain: TAB_DOMAIN, path: "logo.png", bytes: 5 }),
    });
    const input = screen.getByLabelText("File to upload to /");

    await user.upload(input, new File(["bytes"], "logo.png", { type: "image/png" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${TAB_DOMAIN}/files/upload`)).toHaveLength(1);
    });
    const call = backend.callsTo(`PUT /api/apps/${TAB_DOMAIN}/files/upload`)[0];
    expect(call?.search.get("path")).toBe("logo.png");
    expect(call?.search.get("overwrite")).toBe("false");
    expect(await screen.findByText("Uploaded logo.png")).toBeInTheDocument();
  });

  it("asks before it replaces a file that is there, and then does", async () => {
    let attempt = 0;
    const { user, backend } = await files({
      [`PUT /api/apps/${TAB_DOMAIN}/files/upload`]: () => {
        attempt += 1;
        return attempt === 1
          ? problem(400, "validationerror", "package.json exists already", { hint: "Ask to replace it.", fields: { path: "package.json exists already" } })
          : json(200, { domain: TAB_DOMAIN, path: "package.json", bytes: 5 });
      },
    });

    await user.upload(screen.getByLabelText("File to upload to /"), new File(["bytes"], "package.json"));

    const dialog = await screen.findByRole("alertdialog", { name: "package.json is here already. Replace it?" });
    await user.click(within(dialog).getByRole("button", { name: "Replace" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${TAB_DOMAIN}/files/upload`)).toHaveLength(2);
    });
    expect(backend.callsTo(`PUT /api/apps/${TAB_DOMAIN}/files/upload`)[1]?.search.get("overwrite")).toBe("true");
  });

  it("renames or moves an entry within the application", async () => {
    const { user, backend } = await files({
      [`POST /api/apps/${TAB_DOMAIN}/files/move`]: () => json(200, { domain: TAB_DOMAIN, path: "config/package.json" }),
    });
    await user.click(await screen.findByRole("button", { name: "Actions for package.json" }));
    await user.click(await screen.findByRole("menuitem", { name: "Rename or move" }));
    const dialog = await screen.findByRole("dialog", { name: "Rename or move package.json" });
    const field = within(dialog).getByRole("textbox", { name: "New path" });
    await user.clear(field);
    await user.type(field, "config/package.json");
    await user.click(within(dialog).getByRole("button", { name: "Move" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/files/move`)[0]?.body).toEqual({ source: "package.json", destination: "config/package.json", overwrite: false });
    });
  });

  it("deletes a file after one question", async () => {
    const { user, backend } = await files({
      [`DELETE /api/apps/${TAB_DOMAIN}/files`]: () => json(200, { domain: TAB_DOMAIN, path: "package.json" }),
    });
    await user.click(await screen.findByRole("button", { name: "Actions for package.json" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete package.json?" });
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/apps/${TAB_DOMAIN}/files`)).toHaveLength(1);
    });
    const call = backend.callsTo(`DELETE /api/apps/${TAB_DOMAIN}/files`)[0];
    expect(call?.search.get("path")).toBe("package.json");
    expect(call?.search.get("recursive")).toBe("false");
  });

  it("asks for a folder's name before it deletes it with everything in it", async () => {
    const { user, backend } = await files({
      [`DELETE /api/apps/${TAB_DOMAIN}/files`]: () => json(200, { domain: TAB_DOMAIN, path: "config" }),
    });
    await user.click(await screen.findByRole("button", { name: "Actions for config" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete config?" });
    const action = within(dialog).getByRole("button", { name: "Delete" });
    expect(action).toBeDisabled();

    await user.type(within(dialog).getByRole("textbox"), "config");
    await user.click(action);

    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/apps/${TAB_DOMAIN}/files`)[0]?.search.get("recursive")).toBe("true");
    });
  });

  it("downloads a file by sending the browser to it, in sudo mode", async () => {
    const clicks: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicks.push(this.getAttribute("href") ?? "");
    });
    const { user } = await files();
    await user.click(await screen.findByRole("button", { name: "Actions for package.json" }));
    await user.click(await screen.findByRole("menuitem", { name: "Download" }));

    await waitFor(() => {
      expect(clicks).toEqual([`/api/apps/${TAB_DOMAIN}/files/download?path=package.json`]);
    });
  });

  it("says when a folder is empty", async () => {
    await files({ [`GET /api/apps/${TAB_DOMAIN}/files`]: () => json(200, { ...ROOT, entries: [] }) }, "", { empty: true });
    expect(await screen.findByText("This folder is empty")).toBeInTheDocument();
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    await files();
    await expectNoAxeViolations(screen.getByRole("main"));

    await act(() => setLocale("es"));
    expect(await screen.findByRole("button", { name: "Nueva carpeta" })).toBeInTheDocument();
  });
});

describe("the folder in the URL", () => {
  it("is kept clean: no leading slash, no dots, no empty parts", () => {
    expect(cleanFolder("/a//b/../c/./d/")).toBe("a/b/c/d");
    expect(cleanFolder(42)).toBe("");
    expect(validateFilesSearch({ path: "config/" })).toEqual({ path: "config" });
    expect(validateFilesSearch({ path: "" })).toEqual({});
  });

  it("joins a folder and a name, and finds the folder above", () => {
    expect(joinPath("", "a.txt")).toBe("a.txt");
    expect(joinPath("config", "a.txt")).toBe("config/a.txt");
    expect(parentOf("config/deep/a.txt")).toBe("config/deep");
    expect(parentOf("a.txt")).toBe("");
  });
});
