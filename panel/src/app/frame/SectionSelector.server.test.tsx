import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/console";
import { fakeBackend, signedInRoutes } from "../../test/fakes";
import { serverRoutes } from "../../features/server/testing";

const WIDTH = window.innerWidth;

afterEach(() => {
  Object.defineProperty(window, "innerWidth", { value: WIDTH, configurable: true, writable: true });
});

describe("the selector of the Server area on a phone", () => {
  it("is called Server on a server's own pages, as the bar is: not Servers over a single server (item 154)", async () => {
    // The layout is the window's width: a phone is below 1024 pixels.
    Object.defineProperty(window, "innerWidth", { value: 390, configurable: true, writable: true });
    fakeBackend({ ...signedInRoutes(), ...serverRoutes() });
    renderConsole("/server/software");

    const selector = await screen.findByRole("button", { name: /Sections/ });

    expect(selector).toHaveTextContent(/^Server/);
    expect(selector).not.toHaveTextContent(/^Servers/);
  });
});
