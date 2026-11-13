import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { VersionMark } from "./VersionMark";
import type { VersionKind } from "./VersionMark";

describe("VersionMark", () => {
  it("says an image that is the latest in words and takes no state, because being well is not news", () => {
    const { container } = render(<VersionMark kind="current" />);
    expect(screen.getByText("Up to date")).toBeInTheDocument();
    expect(container.querySelector("[data-state]")).toBeNull();
  });

  it("marks a new version as waiting and a new major as a warning: a shape and a word, never the colour alone", () => {
    const { container, rerender } = render(<VersionMark kind="newer" />);
    expect(container.querySelector("[data-state='queued']")).not.toBeNull();
    expect(screen.getByText("New version")).toBeInTheDocument();
    rerender(<VersionMark kind="major" />);
    expect(container.querySelector("[data-state='warning']")).not.toBeNull();
    expect(screen.getByText("New major version")).toBeInTheDocument();
  });

  it("says what is neither: kept to go back to, built here, untagged, not looked at and could not be asked", () => {
    const words: Record<Exclude<VersionKind, "current" | "newer" | "major">, string> = {
      keeps: "Keeps the way back",
      builtHere: "Built here",
      untagged: "Untagged",
      notLooked: "Not looked at yet",
      unknown: "Could not be asked",
    };
    for (const [kind, word] of Object.entries(words) as [keyof typeof words, string][]) {
      const { unmount } = render(<VersionMark kind={kind} />);
      expect(screen.getByText(word)).toBeInTheDocument();
      unmount();
    }
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    render(<VersionMark kind="major" />);
    expect(screen.getByText("Versión mayor nueva")).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { container } = render(
      <ul>
        {(["current", "newer", "major", "keeps", "builtHere", "untagged", "notLooked", "unknown"] as const).map((kind) => (
          <li key={kind}>
            <VersionMark kind={kind} />
          </li>
        ))}
      </ul>,
    );
    await expectNoAxeViolations(container);
  });
});
