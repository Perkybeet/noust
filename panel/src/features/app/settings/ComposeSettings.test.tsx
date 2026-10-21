import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { joinWords, splitNames, splitWords } from "./composeWords";
import { DOMAIN, ELEVATED, RELEASE_APP, part, renderSettings, saveBar } from "./testKit";

const COMPOSE_APP = { ...RELEASE_APP, app_type: "docker-compose", layout: "inplace", follow_tags: null };

const PLAIN = {
  domain: DOMAIN,
  compose_file: null,
  profiles: [] as string[],
  build_args: [] as string[],
  up_args: [] as string[],
  services: [] as string[],
  customised: false,
  allowed_build_args: ["--build-arg", "--no-cache", "--pull"],
  allowed_up_args: ["--wait", "--wait-timeout"],
};

function compose(settings: object | (() => object), routes: Record<string, RouteHandler> = {}) {
  return renderSettings("/compose", "Compose", COMPOSE_APP, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/compose`]: () => json(200, typeof settings === "function" ? settings() : settings),
    ...routes,
  });
}

const card = () => part("Compose file, profiles and arguments");

describe("the words of an argument line", () => {
  it("are split like a shell would split them, and never run by one", () => {
    expect(splitWords("--pull --build-arg VERSION=1.2")).toEqual(["--pull", "--build-arg", "VERSION=1.2"]);
    expect(splitWords("--build-arg 'NOTE=two words' \"a b\" plain\\ space")).toEqual(["--build-arg", "NOTE=two words", "a b", "plain space"]);
    expect(splitWords("  ")).toEqual([]);
    expect(splitWords("--a ''")).toEqual(["--a", ""]);
    expect(splitWords("--x $(id) ; ls")).toEqual(["--x", "$(id)", ";", "ls"]);
  });

  it("go back into a line that splits to the same words", () => {
    for (const words of [["--pull"], ["--build-arg", "NOTE=two words"], ["--a", "it's"], ["--a", ""], []]) {
      expect(splitWords(joinWords(words)), JSON.stringify(words)).toEqual(words);
    }
  });

  it("names are separated by spaces, commas or new lines", () => {
    expect(splitNames("web, jobs\nworker  web")).toEqual(["web", "jobs", "worker", "web"]);
    expect(splitNames("")).toEqual([]);
  });
});

describe("the Compose settings of a stack", () => {
  it("shows what applies, the flags that are accepted, and nothing to save yet", { timeout: 30_000 }, async () => {
    const { container } = await compose({ ...PLAIN, profiles: ["web", "jobs"], up_args: ["--wait"], customised: true });
    await screen.findByRole("heading", { name: "Compose file, profiles and arguments" });
    const form = within(card());

    expect(await form.findByLabelText(/^Profiles/)).toHaveValue("web jobs");
    expect(form.getByLabelText(/^Arguments of docker compose up/)).toHaveValue("--wait");
    expect(form.getByText(/Only these are accepted: --wait --wait-timeout/)).toBeInTheDocument();
    expect(within(saveBar()).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(form.getByRole("button", { name: "Forget these choices" })).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("sends each field as its words and shows the stored result", { timeout: 40_000 }, async () => {
    let stored: object = PLAIN;
    const { user, backend } = await compose(() => stored, {
      [`PUT /api/apps/${DOMAIN}/compose`]: (call) => {
        stored = { ...PLAIN, ...(call.body as object), customised: true };
        return json(200, stored);
      },
    });
    await screen.findByRole("heading", { name: "Compose file, profiles and arguments" });
    const form = within(card());
    await user.type(await form.findByLabelText(/^Compose file/), "deploy/compose.prod.yml");
    await user.type(form.getByLabelText(/^Profiles/), "web, jobs");
    await user.type(form.getByLabelText(/^Arguments of docker compose build/), "--pull --build-arg 'NOTE=two words'");
    await user.type(form.getByLabelText(/^Services/), "web");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/compose`)).toHaveLength(1));
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/compose`)[0]?.body).toEqual({
      compose_file: "deploy/compose.prod.yml",
      profiles: ["web", "jobs"],
      build_args: ["--pull", "--build-arg", "NOTE=two words"],
      up_args: [],
      services: ["web"],
    });
    expect(await form.findByRole("button", { name: "Forget these choices" })).toBeInTheDocument();
    expect(form.getByLabelText(/^Arguments of docker compose build/)).toHaveValue("--pull --build-arg 'NOTE=two words'");
  });

  it("puts the server's refusal of an argument beside its field", { timeout: 40_000 }, async () => {
    const { user } = await compose(PLAIN, {
      [`PUT /api/apps/${DOMAIN}/compose`]: () =>
        json(400, { error: "validation_error", detail: "up_args: '--build' is not an argument Noust passes to docker compose", hint: "Allowed: --wait.", fields: { up_args: "'--build' is not an argument Noust passes to docker compose" } }),
    });
    await screen.findByRole("heading", { name: "Compose file, profiles and arguments" });
    const form = within(card());
    await user.type(await form.findByLabelText(/^Arguments of docker compose up/), "--build");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await form.findByText(/'--build' is not an argument Noust passes/)).toBeInTheDocument();
    expect(form.getByLabelText(/^Arguments of docker compose up/)).toHaveValue("--build");
  });

  it("forgets the choices after asking", { timeout: 40_000 }, async () => {
    let stored: object = { ...PLAIN, profiles: ["web"], customised: true };
    const { user, backend } = await compose(() => stored, {
      [`DELETE /api/apps/${DOMAIN}/compose`]: () => {
        stored = PLAIN;
        return json(200, PLAIN);
      },
    });
    await screen.findByRole("heading", { name: "Compose file, profiles and arguments" });
    await user.click(await within(card()).findByRole("button", { name: "Forget these choices" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Forget them" }));

    await waitFor(() => expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/compose`)).toHaveLength(1));
    await waitFor(() => expect(within(card()).getByLabelText(/^Profiles/)).toHaveValue(""));
  });

  it("is only for a Compose stack", { timeout: 30_000 }, async () => {
    await renderSettings("", "General", RELEASE_APP, {});

    // A stack's own subsection is not in the list of an application that is not one.
    const list = screen.getByRole("navigation", { name: "Application settings" });
    expect(within(list).queryByRole("link", { name: "Compose" })).not.toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "Automation" })).toBeInTheDocument();
  });
});
