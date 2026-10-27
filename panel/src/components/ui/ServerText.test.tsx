import { act, render, renderHook, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { ServerText, useServerSentence } from "./ServerText";

const FRAMEWORK = {
  code: "terminal.server_framework",
  params: {
    control: "server_terminal",
    frameworks: "ens-media, iso-27001",
    command: 'noust config set terminal.server.force_ens true --reason "<why>"',
    turn_off_command: "noust compliance disable ens-media iso-27001",
  },
} as const;

describe("ServerText", () => {
  it("says a code in the console's English with the values put in", () => {
    render(<ServerText coded={FRAMEWORK} text="the server's own words" />);
    expect(screen.getByText(/The compliance framework in force \(ens-media, iso-27001\) keeps the terminal on the server itself off \(the control server_terminal is refused\)/)).toBeInTheDocument();
    expect(screen.queryByText("the server's own words")).not.toBeInTheDocument();
  });

  it("says it in Spanish when the console speaks Spanish", async () => {
    await setLocale("es");
    render(<ServerText coded={FRAMEWORK} text="the server's own words" />);
    expect(screen.getByText(/El marco de cumplimiento en vigor \(ens-media, iso-27001\) mantiene desactivado el terminal del propio servidor/)).toBeInTheDocument();
  });

  it("sets a command as it is, in mono, and never translates it", async () => {
    await setLocale("es");
    render(<ServerText coded={FRAMEWORK} text="x" part="hint" />);
    const force = screen.getByText('noust config set terminal.server.force_ens true --reason "<why>"');
    expect(force).toHaveClass("mono");
    expect(force).toHaveAttribute("translate", "no");
    expect(screen.getByText("noust compliance disable ens-media iso-27001")).toHaveClass("mono");
    expect(screen.getByText(/Para permitirlo de todos modos, lo cual queda registrado:/)).toBeInTheDocument();
  });

  it("does not set a value that is not a command in mono", () => {
    render(<ServerText coded={FRAMEWORK} text="x" />);
    expect(document.querySelector(".mono")).toBeNull();
  });

  it("says the fix of a sentence apart from the sentence", () => {
    render(<ServerText coded={{ code: "tunnels.store_sealed", params: {} }} text="The secret store is sealed" part="hint" />);
    expect(screen.getByText(/A tunnel's connector reads its token at boot/)).toBeInTheDocument();
    expect(screen.queryByText("The secret store is sealed")).not.toBeInTheDocument();
  });

  it("shows the server's English for a code this console has no entry for", () => {
    render(<ServerText coded={{ code: "terminal.from_a_newer_server", params: { x: 1 } }} text="A sentence this console never heard of" />);
    expect(screen.getByText("A sentence this console never heard of")).toBeInTheDocument();
  });

  it("shows the server's English for a code it knows when a value did not arrive", () => {
    // The fix of "the terminal is off" puts the command in; an older server did not send it.
    render(<ServerText coded={{ code: "terminal.server_off", params: {} }} text="Turn it on, said by an older server" part="hint" />);
    expect(screen.getByText("Turn it on, said by an older server")).toBeInTheDocument();
  });

  it("says nothing for the fix of a sentence that has none", () => {
    const { container } = render(<ServerText coded={{ code: "doctor.cpu.ok", params: { amount: 4, minimum: 1 } }} text={null} part="hint" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the server's English when there is no code at all, and nothing when there is nothing", () => {
    const { container, rerender } = render(<ServerText coded={null} text="An answer nobody gave a code" />);
    expect(screen.getByText("An answer nobody gave a code")).toBeInTheDocument();
    rerender(<ServerText coded={null} text={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("takes numbers and flags as values, printed as given", () => {
    render(<ServerText coded={{ code: "terminal.limit_total", params: { open: 8 } }} text="x" />);
    expect(screen.getByText("8 terminals are open, the most this server allows")).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <p>
        <ServerText coded={FRAMEWORK} text="x" /> <ServerText coded={FRAMEWORK} text="x" part="hint" />
      </p>,
    );
    await expectNoAxeViolations(container);
  });
});

describe("useServerSentence", () => {
  it("gives the same words as plain text, a command included", () => {
    const { result } = renderHook(() => useServerSentence());
    expect(result.current({ code: "terminal.server_off", params: { command: "noust config set terminal.server.enabled true" } }, "x", "hint")).toBe(
      "It is a root shell, so it stays off until an administrator turns it on: noust config set terminal.server.enabled true",
    );
  });

  it("follows the language", async () => {
    const { result } = renderHook(() => useServerSentence());
    await act(() => setLocale("es"));
    expect(result.current({ code: "tunnels.store_sealed", params: {} }, "x")).toBe("El almacén de secretos está sellado");
  });

  it("falls back to the English the server sent, or to nothing", () => {
    const { result } = renderHook(() => useServerSentence());
    expect(result.current({ code: "no.such.code", params: {} }, "As the server said it")).toBe("As the server said it");
    expect(result.current(null, undefined)).toBe("");
  });
});
