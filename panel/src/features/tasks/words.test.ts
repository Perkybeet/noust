import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { titleOf } from "./words";

/** The title of an install the Software page queued: the server puts the registry's id in the sentence. */
const INSTALL = {
  title: { code: "jobs.title.server_action_software_install", params: { component: "nginx_stream_module" } },
  name: "Install nginx_stream_module",
};

describe("what a task says it is", () => {
  it("names an install and a removal from Software by the name the page gives the component, not by its id", () => {
    expect(titleOf(INSTALL, "en")).toBe("Install nginx stream module");
    expect(titleOf({ title: { code: "jobs.title.server_action_software_remove", params: { component: "fail2ban" } }, name: "Remove fail2ban" }, "en")).toBe("Remove fail2ban");
  });

  it("says it in Spanish with the Spanish name", async () => {
    await setLocale("es");
    expect(titleOf(INSTALL, "es")).toBe("Instalar Módulo stream de nginx");
  });

  it("keeps the id of a component this console has never heard of, which is the server's word for it", () => {
    expect(titleOf({ title: { code: "jobs.title.server_action_software_install", params: { component: "brand_new" } }, name: "Install brand_new" }, "en")).toBe("Install brand_new");
  });

  it("leaves every other title as the server said it", () => {
    expect(titleOf({ title: { code: "jobs.title.deploy", params: { domain: "shop.example.com" } }, name: "Deploy shop.example.com" }, "en")).toBe("Deploy shop.example.com");
  });
});
