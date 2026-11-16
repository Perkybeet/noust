import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";

/**
 * The installation check says Noust's own sentences in this console's language: each line the server gave a
 * code, the note under it, the explanation of a section and the name of the section. A line it did not give a
 * code is another program's words and stays as it was written, which is what the console showed for all of it.
 */
const DOCTOR = {
  ready: true,
  issues: 0,
  warnings: 1,
  sections: [
    {
      title: "Resources",
      key: "resources",
      explain: null,
      explain_coded: null,
      items: [
        {
          status: "ok",
          text: "Memory: 19.5 GB, swap 8 GB (minimum 1 GB)",
          fix: null,
          notes: [],
          coded: { code: "doctor.memory.ok_swap", params: { amount: "19.5 GB", swap: "8 GB", minimum: "1 GB" } },
          notes_coded: [],
        },
      ],
    },
    {
      title: "Web Server",
      key: "web_server",
      explain: null,
      explain_coded: null,
      items: [{ status: "ok", text: "nginx 1.24.0 (nginx version: nginx/1.24.0)", fix: null, notes: [], coded: null, notes_coded: [] }],
    },
  ],
};

describe("Settings > About > Installation check", () => {
  it("says the lines and the sections in Spanish from their codes and leaves what a program wrote as it was", async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/system/doctor": () => json(200, DOCTOR),
      "GET /api/system/version": () => json(200, { current_version: "4.3.0", latest_version: "4.3.0", has_update: false }),
      "GET /api/config": () => json(200, { config: {}, path: "/etc/noust/config.yaml", writable: true }),
      "GET /api/system/update": () => json(200, { current_version: "4.3.0", method: "apt", supported: true }),
      "GET /api/system/update/auto": () =>
        json(200, { settings: { enabled: false, channel: "patch", window: "03:00-05:00", days: "daily" }, supported: true, check_enabled: true, window_open: false, timer: { installed: false } }),
    });
    await act(async () => {
      await setLocale("es");
    });
    renderConsole("/settings/about");
    const region = await screen.findByRole("region", { name: "Comprobación de la instalación" });
    await userEvent.click(await within(region).findByRole("button", { name: /Mostrar las comprobaciones/ }));

    // Noust's sentence, in Spanish, with its values; its section, named in Spanish.
    expect(await within(region).findByText("Memoria: 19.5 GB, swap 8 GB (mínimo 1 GB)")).toBeInTheDocument();
    expect(within(region).getByText("Recursos")).toBeInTheDocument();
    expect(within(region).getByText("Servidor web")).toBeInTheDocument();
    expect(within(region).queryByText(/minimum 1 GB/)).not.toBeInTheDocument();
    // What nginx printed is not ours to translate.
    expect(within(region).getByText("nginx 1.24.0 (nginx version: nginx/1.24.0)")).toBeInTheDocument();
  });
});
