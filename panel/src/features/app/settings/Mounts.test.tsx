import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { formatBytes } from "../../../lib/format";
import { json, problem } from "../../../test/fakes";
import { DOMAIN, ELEVATED, RELEASE_APP, STATIC_APP, renderSettings } from "./testKit";

const FILE = {
  kind: "file",
  target: "config/production.json",
  mode: "0600",
  read_only: false,
  path: "/var/www/apps/shop-example-com/shared/config/production.json",
  exists: true,
  size: 2048,
  modified: "2026-10-13T10:00:00",
  source: null,
  updated_at: "2026-10-13T10:00:00",
  updated_by: "ana",
};

const PATH = {
  kind: "path",
  target: "/data/uploads",
  mode: "0755",
  read_only: true,
  path: "/srv/shared/uploads",
  exists: true,
  size: null,
  modified: null,
  source: "/srv/shared/uploads",
  updated_at: null,
  updated_by: "ana",
};

function mounts(list: object[] = [FILE], container = false, routes: Parameters<typeof renderSettings>[3] = {}, app: object = RELEASE_APP) {
  return renderSettings("/mounts", "Mounts", app, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/mounts`]: () => json(200, { domain: DOMAIN, mounts: list, total: list.length, container }),
    ...routes,
  });
}

describe("the files and folders an application is given", () => {
  it("lists each with what it is, its permissions and its size", async () => {
    await mounts([FILE, PATH], true);

    const list = await screen.findByRole("list", { name: `Mounts of ${DOMAIN}` });
    expect(within(list).getByText("config/production.json")).toBeInTheDocument();
    expect(within(list).getByText("Mode 0600")).toBeInTheDocument();
    expect(within(list).getByText(formatBytes(2048, "en"))).toBeInTheDocument();
    expect(within(list).getByText("/data/uploads")).toBeInTheDocument();
    expect(within(list).getByText("Read-only")).toBeInTheDocument();
  });

  it("invites the first file, and points environment variables to their own place", async () => {
    await mounts([]);
    expect(await screen.findByText("No mounts")).toBeInTheDocument();
    expect(screen.getByText(/Environment variables have a place of their own/)).toBeInTheDocument();
  });

  it("offers to bind a server folder only to a container application", async () => {
    await mounts([FILE], false);
    await screen.findByRole("list", { name: `Mounts of ${DOMAIN}` });
    expect(screen.queryByRole("button", { name: "Bind a server folder" })).not.toBeInTheDocument();
  });

  it("adds a file with its content and permissions", async () => {
    const { user, backend } = await mounts([], false, {
      [`PUT /api/apps/${DOMAIN}/mounts`]: () => json(200, FILE),
    });
    await user.click(await screen.findByRole("button", { name: "Add a file" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a file" });
    await user.type(within(dialog).getByRole("textbox", { name: "Path in the application" }), "config/production.json");
    await user.type(within(dialog).getByRole("textbox", { name: "Content" }), "{{}");
    const mode = within(dialog).getByRole("textbox", { name: /Permissions/ });
    await user.clear(mode);
    await user.type(mode, "640");

    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/mounts`)[0]?.body).toEqual({ target: "config/production.json", kind: "file", content: "{}", mode: 640 });
    });
    expect(await screen.findByText("Saved config/production.json")).toBeInTheDocument();
  });

  it("binds a server folder into a container, read-only unless said otherwise", async () => {
    const { user, backend } = await mounts([], true, {
      [`PUT /api/apps/${DOMAIN}/mounts`]: () => json(200, PATH),
    });
    await user.click(await screen.findByRole("button", { name: "Bind a server folder" }));
    const dialog = await screen.findByRole("dialog", { name: "Bind a server folder" });
    await user.type(within(dialog).getByRole("textbox", { name: "Folder on the server" }), "/srv/shared/uploads");
    await user.type(within(dialog).getByRole("textbox", { name: "Path in the application" }), "/data/uploads");
    expect(within(dialog).getByRole("checkbox", { name: /read-only/ })).toBeChecked();

    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/mounts`)[0]?.body).toEqual({
        target: "/data/uploads",
        kind: "path",
        source: "/srv/shared/uploads",
        read_only: true,
      });
    });
  });

  it("reads a file before it lets it be edited, and saves what was typed over it", async () => {
    const { user, backend } = await mounts([FILE], false, {
      [`GET /api/apps/${DOMAIN}/mounts/content`]: () => json(200, { domain: DOMAIN, target: FILE.target, content: '{"token": "x"}' }),
      [`PUT /api/apps/${DOMAIN}/mounts`]: () => json(200, FILE),
    });
    await user.click(await screen.findByRole("button", { name: `Actions for ${FILE.target}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: `Edit ${FILE.target}` });
    const content = await within(dialog).findByRole("textbox", { name: "Content" });
    expect(content).toHaveValue('{"token": "x"}');
    expect(within(dialog).getByRole("textbox", { name: "Path in the application" })).toBeDisabled();

    await user.type(content, " ");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/mounts`)[0]?.body).toMatchObject({ target: FILE.target, kind: "file", content: '{"token": "x"} ' });
    });
  });

  it("does not let a file be saved over what could not be read", async () => {
    const { user, backend } = await mounts([FILE], false, {
      [`GET /api/apps/${DOMAIN}/mounts/content`]: () => problem(403, "forbidden", "Revealing secrets needs another permission"),
    });
    await user.click(await screen.findByRole("button", { name: `Actions for ${FILE.target}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: `Edit ${FILE.target}` });

    expect(await within(dialog).findByText("Could not read the file")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/mounts`)).toHaveLength(0);
  });

  it("checks the permissions are octal digits before asking anything", async () => {
    const { user, backend } = await mounts([]);
    await user.click(await screen.findByRole("button", { name: "Add a file" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a file" });
    await user.type(within(dialog).getByRole("textbox", { name: "Path in the application" }), "a.conf");
    const mode = within(dialog).getByRole("textbox", { name: /Permissions/ });
    await user.clear(mode);
    await user.type(mode, "999");

    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(await within(dialog).findByText("Give three or four octal digits, such as 600 or 640.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/mounts`)).toHaveLength(0);
  });

  it("removes a mount after one question", async () => {
    const { user, backend } = await mounts([FILE], false, {
      [`DELETE /api/apps/${DOMAIN}/mounts`]: () => json(200, { domain: DOMAIN, mounts: [], total: 0, container: false }),
    });
    await user.click(await screen.findByRole("button", { name: `Actions for ${FILE.target}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Stop mounting ${FILE.target}?` });
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));

    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/mounts`)).toHaveLength(1);
    });
    expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/mounts`)[0]?.search.get("target")).toBe(FILE.target);
  });

  it("takes none for a static site", async () => {
    await mounts([], false, {}, STATIC_APP);
    expect(await screen.findByText(/This application takes no mounts/)).toBeInTheDocument();
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    await mounts([FILE, PATH], true);
    await screen.findByRole("list", { name: `Mounts of ${DOMAIN}` });
    await expectNoAxeViolations(screen.getByRole("main"));

    await act(() => setLocale("es"));
    expect(await screen.findByRole("button", { name: "Añadir un archivo" })).toBeInTheDocument();
  });
});
