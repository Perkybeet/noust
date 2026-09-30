import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { SqlEditor } from "./SqlEditor";

function Harness({ onRun }: { onRun: () => void }) {
  const [value, setValue] = useState("SELECT 1;\n");
  return <SqlEditor value={value} onChange={setValue} onRun={onRun} label="Statement" />;
}

describe("the SQL editor", () => {
  it("is a plain textarea, runs with Ctrl+Enter, and numbers every line", async () => {
    const onRun = vi.fn();
    const { container } = render(<Harness onRun={onRun} />);
    const editor = screen.getByRole("textbox", { name: "Statement" });
    const user = userEvent.setup();
    await user.click(editor);
    await user.type(editor, "SELECT 2;");
    await user.keyboard("{Control>}{Enter}{/Control}");
    expect(onRun).toHaveBeenCalledTimes(1);
    expect(editor).toHaveValue("SELECT 1;\nSELECT 2;");
    // The drawn copy is React elements, never an HTML string: a keyword is its own element.
    const keyword = [...container.querySelectorAll("pre span")].find((span) => span.textContent === "SELECT");
    expect(keyword).toHaveClass("font-medium");
    expect(container.querySelector("style")).toBeNull();
    expect(container.querySelector("[aria-hidden='true']")?.textContent).toBe("12");
  });
});
