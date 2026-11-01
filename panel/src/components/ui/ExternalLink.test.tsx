import { render, screen } from "@testing-library/react";
import { BookOpen } from "lucide-react";
import { describe, expect, it } from "vitest";

import { expectLabelInName } from "../../test/labelInName";
import { ExternalLink } from "./ExternalLink";

describe("ExternalLink", () => {
  it("opens a page outside the console in a new tab without handing it this window", () => {
    render(<ExternalLink href="https://example.com/docs">Documentation</ExternalLink>);
    const link = screen.getByRole("link", { name: /Documentation/ });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("draws its icon beside the text, never inside the span that truncates it", () => {
    render(
      <ExternalLink href="https://example.com/docs" button="ghost" icon={<BookOpen aria-hidden="true" data-testid="icon" />}>
        Documentation for this page
      </ExternalLink>,
    );
    const icon = screen.getByTestId("icon");
    const text = screen.getByText("Documentation for this page");
    // A block icon inside the truncating span sat on a line of its own above the words.
    expect(icon.parentElement).toBe(screen.getByRole("link"));
    expect(text.contains(icon)).toBe(false);
  });

  it("gives a fuller name that starts with the words it draws, and adds that it opens a new tab", () => {
    render(
      <ExternalLink href="https://pr-42.example.com" label="Open preview of pr-42">
        Open preview
      </ExternalLink>,
    );
    const link = screen.getByRole("link", { name: "Open preview of pr-42 (opens in a new tab)" });
    expectLabelInName(link);
  });

  it("links nothing that is not http(s)", () => {
    render(<ExternalLink href="javascript:alert(1)">Click</ExternalLink>);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
