import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { Button } from "./Button";
import { Field } from "./Field";
import { Input } from "./Input";

describe("Field", () => {
  it("labels its control and describes it with the help text", () => {
    render(
      <Field label="Domain" description="The address the app will answer on.">
        <Input />
      </Field>,
    );
    const input = screen.getByRole("textbox", { name: "Domain" });
    expect(input).toHaveAccessibleDescription(/The address the app will answer on/);
  });

  it("marks the control invalid and announces the error with it", () => {
    render(
      <Field label="Port" error="Port 3004 is already used by wasm-shop.service.">
        <Input defaultValue="3004" />
      </Field>,
    );
    const input = screen.getByRole("textbox", { name: "Port" });
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(/Port 3004 is already used/);
  });

  it("shows no error when there is none", () => {
    render(
      <Field label="Port" error={null}>
        <Input />
      </Field>,
    );
    expect(screen.getByRole("textbox", { name: "Port" })).not.toHaveAttribute("aria-invalid", "true");
  });

  it("marks optional fields instead of required ones", () => {
    render(
      <Field label="Branch" optional>
        <Input />
      </Field>,
    );
    expect(screen.getByText("Optional")).toBeInTheDocument();
  });

  it("passes the typed value through", async () => {
    render(
      <Field label="Domain">
        <Input />
      </Field>,
    );
    const input = screen.getByRole("textbox", { name: "Domain" });
    await userEvent.type(input, "example.com");
    expect(input).toHaveValue("example.com");
  });

  it("has no accessibility violations, valid or not", async () => {
    const { container } = render(
      <form>
        <Field label="Domain" description="DNS must point here.">
          <Input />
        </Field>
        <Field label="Port" error="Port is in use.">
          <Input />
        </Field>
        <Field label="Build command" disabled>
          <Input defaultValue="npm run build" />
        </Field>
      </form>,
    );
    await expectNoAxeViolations(container);
  });

  it("marks optional fields in Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    render(
      <Field label="Branch" optional>
        <Input />
      </Field>,
    );
    expect(screen.getByText("Opcional")).toBeInTheDocument();
  });

  it("puts a button that acts on the control beside it, aligned with the control, not the label", () => {
    render(
      <Field label="Repository" action={<Button>Inspect</Button>}>
        <Input />
      </Field>,
    );
    const input = screen.getByRole("textbox", { name: "Repository" });
    const button = screen.getByRole("button", { name: "Inspect" });
    const row = input.closest("[data-field-row]");
    expect(row).not.toBeNull();
    expect(row).toContainElement(button);
  });
});
