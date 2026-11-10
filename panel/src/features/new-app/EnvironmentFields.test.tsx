import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { EnvironmentFields } from "./EnvironmentFields";
import type { EnvRow } from "./wizard";

function added(id: string, name: string, value: string): EnvRow {
  return { id: `added:${id}`, name, value, secret: false, required: false, declared: false, example: null };
}

/** The fields over a state of their own, as the wizard holds them. */
function Fields({ start }: { start: EnvRow[] }) {
  const [rows, setRows] = useState(start);
  return <EnvironmentFields rows={rows} errors={{}} onChange={setRows} />;
}

describe("a variable added by hand (item 148)", () => {
  it("hides the value of one whose name says it is a secret, and says why, with a button to see it", async () => {
    const user = userEvent.setup();
    render(<Fields start={[added("1", "STRIPE_SECRET_KEY", "sk_live_4eC39HqLyjWDarjtT1zdp7dc")]} />);

    const value = screen.getByLabelText("Value");
    expect(value).toHaveAttribute("type", "password");
    expect(screen.getByText("Hidden because it looks like a secret.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show STRIPE_SECRET_KEY" }));
    expect(value).toHaveAttribute("type", "text");
    expect(value).toHaveValue("sk_live_4eC39HqLyjWDarjtT1zdp7dc");
    await user.click(screen.getByRole("button", { name: "Hide STRIPE_SECRET_KEY" }));
    expect(value).toHaveAttribute("type", "password");
  });

  it("leaves a plain variable in the open, with no button and nothing to explain", () => {
    render(<Fields start={[added("1", "API_URL", "https://api.example.com")]} />);

    expect(screen.getByLabelText("Value")).toHaveAttribute("type", "text");
    expect(screen.queryByRole("button", { name: /^Show / })).not.toBeInTheDocument();
    expect(screen.queryByText("Hidden because it looks like a secret.")).not.toBeInTheDocument();
  });

  it("hides one by what is pasted in it, whatever it is called", async () => {
    const user = userEvent.setup();
    render(<Fields start={[added("1", "PAYMENTS", "")]} />);
    const value = screen.getByLabelText("Value");
    expect(value).toHaveAttribute("type", "text");

    await user.click(value);
    await user.paste("sk_live_4eC39HqLyjWDarjtT1zdp7dc");

    expect(value).toHaveAttribute("type", "password");
    // Named after nothing: the button says what it shows without a name to give.
    expect(screen.getByRole("button", { name: "Show PAYMENTS" })).toBeInTheDocument();
  });

  it("hides it the moment the name says so, and shows it again when the name no longer does", async () => {
    const user = userEvent.setup();
    render(<Fields start={[added("1", "", "abc12345")]} />);
    const value = screen.getByLabelText("Value");
    expect(value).toHaveAttribute("type", "text");

    await user.type(screen.getByLabelText("Name"), "DB_PASSWORD");
    expect(value).toHaveAttribute("type", "password");

    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "DB_HOST");
    expect(value).toHaveAttribute("type", "text");
  });

  it("names the button for a value that has no name yet", async () => {
    const user = userEvent.setup();
    render(<Fields start={[added("1", "", "")]} />);

    await user.click(screen.getByLabelText("Value"));
    await user.paste("ghp_abcdefghijklmnopqrstuvwxyz0123456789");

    expect(screen.getByRole("button", { name: "Show the value" })).toBeInTheDocument();
  });
});
