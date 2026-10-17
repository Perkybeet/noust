/**
 * Sending the code of an application as an archive (checklist A-16): the picker in the wizard's
 * source step and in the change-source dialog, over the fake API of the upload endpoints.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { DOMAIN, ELEVATED, RELEASE_APP, renderSettings } from "../settings/testKit";
import { isArchiveName } from "./sourceUploadApi";

const ID = "0123456789abcdef0123456789abcdef";
const SOURCE = `upload:${ID}`;
const NOW = "2026-10-17T10:00:00+00:00";

function session(size: number, received = 0, extra: Record<string, unknown> = {}) {
  return { id: ID, file_name: "shop.zip", size, received, sha256: null, complete: false, chunk_size: 524288, created_at: NOW, updated_at: NOW, ...extra };
}

function archive(name = "shop.zip", bytes = 10): File {
  return new File([new Uint8Array(bytes)], name, { type: "application/zip" });
}

/** The upload endpoints answering as a server that accepts every chunk in order. */
function uploadRoutes(size: number, overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  let received = 0;
  return {
    "POST /api/uploads": () => json(201, session(size)),
    [`PUT /api/uploads/${ID}`]: (call) => {
      received = Math.min(size, Number(call.search.get("offset")) + (call.raw instanceof Blob ? call.raw.size : size));
      return json(200, session(size, received));
    },
    [`POST /api/uploads/${ID}/complete`]: () => json(200, session(size, size, { complete: true, source: SOURCE, files: 24, bytes: 4096 })),
    [`DELETE /api/uploads/${ID}`]: () => new Response(null, { status: 204 }),
    ...overrides,
  };
}

describe("which files are archives", () => {
  it("are the formats the server extracts, in any case", () => {
    for (const name of ["a.zip", "a.TAR", "a.tar.gz", "a.TGZ", "a.tar.bz2", "a.tar.xz"]) expect(isArchiveName(name)).toBe(true);
    for (const name of ["a.gz", "a.txt", "a", "a.rar", "a.7z", "a.zip.exe"]) expect(isArchiveName(name)).toBe(false);
  });
});

describe("in the change-source dialog", () => {
  async function dialog(routes: Record<string, RouteHandler>) {
    const rendered = await renderSettings("/source", "Source", RELEASE_APP, {
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`GET /api/apps/${DOMAIN}/deploy-key`]: () =>
        json(200, { domain: DOMAIN, repository: "github.com/shop/storefront", source_is_ssh: false, ssh_url: null, key: null, shared_with: [], server_key_fingerprint: "SHA256:x", warnings: [] }),
      [`GET /api/apps/${DOMAIN}/git-credential`]: () => json(200, { domain: DOMAIN, repository: "github.com/shop/storefront", host: "github.com", configured: false, username: null, source_is_https: true }),
      [`GET /api/apps/${DOMAIN}/source-options`]: () => json(200, { domain: DOMAIN, submodules: true, lfs: "auto", shallow: true, watch_paths: [], skip_markers: true, customised: false }),
      "GET /api/deploy-keys": () => json(200, { keys: [], total: 0 }),
      ...routes,
    });
    await screen.findByRole("heading", { name: "Where the code comes from" });
    await rendered.user.click(screen.getByRole("button", { name: "Change the source" }));
    const box = within(await screen.findByRole("dialog", { name: `Change the source of ${DOMAIN}` }));
    return { ...rendered, box };
  }

  it("sends the archive in chunks, extracts it, and makes it the source", { timeout: 60_000 }, async () => {
    const { user, box, backend, container } = await dialog(uploadRoutes(10));

    await user.upload(box.getByLabelText(/^Archive of the project/), archive());

    expect(await box.findByText("shop.zip was uploaded")).toBeInTheDocument();
    expect(box.getByText("24 files were extracted on the server.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/uploads")[0]?.body).toEqual({ file_name: "shop.zip", size: 10, sha256: null });
    const chunks = backend.callsTo(`PUT /api/uploads/${ID}`);
    expect(chunks).toHaveLength(1);
    expect(chunks[0]?.search.get("offset")).toBe("0");
    expect(backend.callsTo(`POST /api/uploads/${ID}/complete`)).toHaveLength(1);
    // The archive is the source, with no branch to ask for and no remote to question.
    expect(box.getByLabelText(/^New source/)).toHaveValue(SOURCE);
    expect(box.queryByLabelText(/^Branch/)).not.toBeInTheDocument();
    expect(box.queryByRole("checkbox", { name: /Ask the remote first/ })).not.toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("splits what is bigger than a chunk, each at the offset the server reached", { timeout: 60_000 }, async () => {
    const size = 524288 * 2 + 100;
    const { user, box, backend } = await dialog(uploadRoutes(size));

    await user.upload(box.getByLabelText(/^Archive of the project/), archive("shop.zip", size));

    await box.findByText("shop.zip was uploaded");
    const offsets = backend.callsTo(`PUT /api/uploads/${ID}`).map((call) => call.search.get("offset"));
    expect(offsets).toEqual(["0", "524288", "1048576"]);
  });

  it("plans and changes the source to the upload without a branch", { timeout: 60_000 }, async () => {
    const plan = {
      domain: DOMAIN,
      current: RELEASE_APP.source,
      target: SOURCE,
      current_branch: "main",
      target_branch: null,
      current_kind: "git",
      target_kind: "upload",
      same_repository: false,
      head: null,
      checked: false,
      pinned: false,
      previews: [],
      warnings: ["The old repository stops deploying this application: its webhook and pushes no longer match it."],
      changed: true,
    };
    const { user, box, backend } = await dialog(
      uploadRoutes(10, {
        [`POST /api/apps/${DOMAIN}/source/plan`]: () => json(200, plan),
        [`PUT /api/apps/${DOMAIN}/source`]: () => json(200, { ...plan, applied: true }),
      }),
    );
    await user.upload(box.getByLabelText(/^Archive of the project/), archive());
    await box.findByText("shop.zip was uploaded");

    await user.click(box.getByRole("button", { name: "Check the new source" }));
    expect(await box.findByText(/The old repository stops deploying this application/)).toBeInTheDocument();
    await user.click(box.getByRole("button", { name: "Change the source" }));

    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/source`)).toHaveLength(1));
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/source/plan`)[0]?.body).toEqual({ source: SOURCE, check: true });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/source`)[0]?.body).toEqual({ source: SOURCE, check: true });
  });

  it("refuses a file that is not an archive before sending a byte", { timeout: 40_000 }, async () => {
    const { box, backend } = await dialog(uploadRoutes(10));

    // The browser's own filter (the input's accept) is not what is under test.
    await userEvent.setup({ applyAccept: false }).upload(box.getByLabelText(/^Archive of the project/), archive("notes.txt"));

    expect(await box.findByText(/notes\.txt is not an archive Noust extracts/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/uploads")).toHaveLength(0);
  });

  it("shows why the server refused an archive, in its words, and keeps no upload", { timeout: 40_000 }, async () => {
    const { user, box } = await dialog(
      uploadRoutes(10, {
        [`POST /api/uploads/${ID}/complete`]: () =>
          problem(400, "sourceerror", "Unsafe path in archive: ../escaped.txt", { hint: "Archive the project's own files, with no path that leaves the project." }),
      }),
    );

    await user.upload(box.getByLabelText(/^Archive of the project/), archive());

    expect(await box.findByText("The archive was not accepted")).toBeInTheDocument();
    expect(box.getByText("Unsafe path in archive: ../escaped.txt")).toBeInTheDocument();
    expect(box.getByLabelText(/^New source/)).toHaveValue("");
    expect(box.getByLabelText(/^Archive of the project/)).toBeEnabled();
  });

  it("cancels an upload and has the server throw it away", { timeout: 40_000 }, async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { user, box, backend } = await dialog(
      uploadRoutes(10, {
        [`PUT /api/uploads/${ID}`]: async () => {
          await gate;
          return json(200, session(10, 10));
        },
      }),
    );

    await user.upload(box.getByLabelText(/^Archive of the project/), archive());
    await user.click(await box.findByRole("button", { name: "Cancel the upload" }));

    await waitFor(() => expect(backend.callsTo(`DELETE /api/uploads/${ID}`)).toHaveLength(1));
    expect(await box.findByText("The upload was cancelled. Nothing was kept.")).toBeInTheDocument();
    release();
  });

  it("lets another file be chosen after one was uploaded, and forgets the first", { timeout: 60_000 }, async () => {
    const { user, box } = await dialog(uploadRoutes(10));
    await user.upload(box.getByLabelText(/^Archive of the project/), archive());
    await box.findByText("shop.zip was uploaded");

    await user.click(box.getByRole("button", { name: "Choose another file" }));

    expect(await box.findByLabelText(/^Archive of the project/)).toBeEnabled();
    expect(box.getByLabelText(/^New source/)).toHaveValue("");
  });
});

describe("in the new application wizard", () => {
  it("fills the source with the upload, hides the branch, and inspects it", { timeout: 60_000 }, async () => {
    const backend = fakeBackend({
      ...signedInRoutes(),
      "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
      "GET /api/apps/types": () => json(200, { types: [{ type: "nodejs", name: "Node.js", default_port: 3000 }] }),
      "GET /api/recipes": () => json(200, { recipes: [], total: 0 }),
      "POST /api/apps/inspect": () =>
        json(200, {
          app_type: "nodejs",
          detected_types: ["nodejs"],
          package_manager: "npm",
          install_command: ["npm", "ci"],
          build_command: ["npm", "run", "build"],
          start_command: "npm start",
          default_port: 3000,
          env_keys: [],
          branch: "",
          commit: "",
          compatible: true,
        }),
      ...uploadRoutes(10),
    });
    const { user } = renderConsole("/apps/new");
    await screen.findByRole("heading", { level: 1, name: "New application" });

    await user.upload(screen.getByLabelText(/^Archive of the project/), archive());

    await screen.findByText("shop.zip was uploaded");
    expect(screen.getByLabelText("Repository or directory")).toHaveValue(SOURCE);
    expect(screen.queryByLabelText(/^Branch/)).not.toBeInTheDocument();
    expect(screen.getByText(/Archive uploaded from this browser/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    await waitFor(() => expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(1));
    expect(backend.callsTo("POST /api/apps/inspect")[0]?.body).toMatchObject({ source: SOURCE });
  });
});
