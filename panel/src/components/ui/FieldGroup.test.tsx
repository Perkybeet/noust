import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { Checkbox } from "./Checkbox";
import { ChoiceCards } from "./ChoiceCards";
import { FieldGroup } from "./Field";

describe("FieldGroup", () => {
  it("is a group named by its legend, so a screen reader says the question on entering it", () => {
    render(
      <FieldGroup legend="Also delete">
        <Checkbox label="Its files" />
        <Checkbox label="Its certificate" />
      </FieldGroup>,
    );
    const group = screen.getByRole("group", { name: "Also delete" });
    expect(group.tagName).toBe("FIELDSET");
    expect(group.querySelector("legend")).toHaveTextContent("Also delete");
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  });

  it("describes the group with its help, which stays visible", () => {
    render(
      <FieldGroup legend="Also delete" description="Nothing is deleted unless it is checked.">
        <Checkbox label="Its files" />
      </FieldGroup>,
    );
    expect(screen.getByRole("group", { name: "Also delete" })).toHaveAccessibleDescription("Nothing is deleted unless it is checked.");
    expect(screen.getByText("Nothing is deleted unless it is checked.")).toBeVisible();
  });

  describe("invalid", () => {
    it("shows the sentence a Field would show, under the controls, tied to the group", () => {
      render(
        <FieldGroup legend="Notify by" error="Choose at least one channel." description="Email, Telegram or a webhook.">
          <Checkbox label="Email" />
        </FieldGroup>,
      );
      const group = screen.getByRole("group", { name: "Notify by" });
      const error = screen.getByText("Choose at least one channel.");
      expect(error.closest("p")).toHaveClass("text-13", "text-fail");
      expect(error.closest("p")?.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
      expect(group).toHaveAccessibleDescription("Email, Telegram or a webhook. Choose at least one channel.");
      expect(group).toHaveAttribute("data-invalid");
    });

    it("draws no message while it is valid, and none for an empty one", () => {
      const { rerender } = render(
        <FieldGroup legend="Notify by" error={null}>
          <Checkbox label="Email" />
        </FieldGroup>,
      );
      expect(screen.queryByText(/channel/)).not.toBeInTheDocument();
      rerender(
        <FieldGroup legend="Notify by" error="">
          <Checkbox label="Email" />
        </FieldGroup>,
      );
      expect(screen.getByRole("group")).not.toHaveAttribute("data-invalid");
    });

    it("is not an alert: it is read with the group, not shouted over the page", () => {
      render(
        <FieldGroup legend="Notify by" error="Choose at least one channel.">
          <Checkbox label="Email" />
        </FieldGroup>,
      );
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  it("marks an optional group", () => {
    render(
      <FieldGroup legend="Also delete" optional>
        <Checkbox label="Its files" />
      </FieldGroup>,
    );
    expect(screen.getByText("Optional")).toBeInTheDocument();
  });

  it("says Optional in Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    render(
      <FieldGroup legend="También borrar" optional>
        <Checkbox label="Sus archivos" />
      </FieldGroup>,
    );
    expect(screen.getByText("Opcional")).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <div>
        <FieldGroup legend="Notify by" description="Pick the channels." error="Choose at least one channel.">
          <Checkbox label="Email" />
          <Checkbox label="Telegram" />
        </FieldGroup>
        <FieldGroup legend="Also delete" optional>
          <Checkbox label="Its files" />
        </FieldGroup>
      </div>,
    );
    await expectNoAxeViolations(container);
  });
});

describe("ChoiceCards error", () => {
  const OPTIONS = [
    { value: "releases", label: "Instant rollback", description: "Each deploy is kept apart." },
    { value: "inplace", label: "Single folder", description: "Every deploy rebuilds the same folder." },
  ] as const;

  it("shows the same sentence under the cards and ties it to the group", () => {
    render(<ChoiceCards legend="How deploys work" options={OPTIONS} value="releases" onValueChange={() => undefined} error="Choose how deploys work." />);
    const group = screen.getByRole("radiogroup", { name: "How deploys work" });
    expect(group).toHaveAttribute("aria-invalid", "true");
    expect(group).toHaveAccessibleDescription("Choose how deploys work.");
    expect(screen.getByText("Choose how deploys work.").closest("p")).toHaveClass("text-fail");
  });

  it("draws nothing when the choice is valid", () => {
    render(<ChoiceCards legend="How deploys work" options={OPTIONS} value="releases" onValueChange={() => undefined} />);
    const group = screen.getByRole("radiogroup");
    expect(group).not.toHaveAttribute("aria-invalid");
    expect(group).not.toHaveAttribute("aria-describedby");
  });

  it("still chooses, and has no accessibility violations with the error", async () => {
    const { container } = render(
      <ChoiceCards legend="How deploys work" options={OPTIONS} value="releases" onValueChange={() => undefined} error="Choose how deploys work." />,
    );
    await userEvent.click(screen.getByRole("radio", { name: "Single folder" }));
    await expectNoAxeViolations(container);
  });
});
