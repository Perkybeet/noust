import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectLabelInName, nameHoldsLabel, visibleText, wordsOf } from "./labelInName";

describe("label in name (WCAG 2.5.3)", () => {
  it("cuts a text into words the way axe does", () => {
    expect(wordsOf("Open the service queue-worker (opens in a new tab)")).toEqual(["open", "the", "service", "queue", "worker"]);
    expect(wordsOf("Acción: ¡Firmar de nuevo!")).toEqual(["accio", "n", "firmar", "de", "nuevo"]);
    expect(wordsOf("  ·  ")).toEqual([]);
  });

  it("holds a label that is a run of the name", () => {
    expect(nameHoldsLabel("Open service", "Open service queue-worker")).toBe(true);
    expect(nameHoldsLabel("Open probes", "Open probes, blog.example.org")).toBe(true);
    expect(nameHoldsLabel("tienda.example.org", "tienda.example.org, the live site (opens in a new tab)")).toBe(true);
    expect(nameHoldsLabel("Abrir actualizaciones", "Abrir actualizaciones de Noust")).toBe(true);
  });

  it("holds nothing drawn, which a name cannot contradict", () => {
    expect(nameHoldsLabel("", "Anything")).toBe(true);
    expect(nameHoldsLabel(" · ", "Anything")).toBe(true);
  });

  it("refuses a name that puts a word between the drawn ones, or drops one", () => {
    expect(nameHoldsLabel("Open service", "Open the service queue-worker")).toBe(false);
    expect(nameHoldsLabel("#12 3 days ago", "Deploy 12, succeeded, 3 days ago")).toBe(false);
    expect(nameHoldsLabel("Look at it", "Look at the process x on the server")).toBe(false);
  });

  it("refuses a name that has the drawn words in another order", () => {
    expect(nameHoldsLabel("tienda Running 3000", "tienda 3000, Running")).toBe(false);
  });

  it("reads what an element draws, leaving out the words only a screen reader gets", () => {
    render(
      <a href="/x" aria-label="Open service x">
        <svg aria-hidden="true" />
        <span>Open</span> <span>service</span>
        <span className="sr-only"> (opens in a new tab)</span>
      </a>,
    );
    expect(visibleText(screen.getByRole("link"))).toBe("Open service");
  });

  it("joins elements with no space between them into one word, as axe does", () => {
    render(
      <button type="button" aria-label="Database sessions, redis">
        <span>sessions</span>
        <span>redis</span>
      </button>,
    );
    expect(visibleText(screen.getByRole("button"))).toBe("sessionsredis");
    expect(() => {
      expectLabelInName(screen.getByRole("button"));
    }).toThrow(/does not hold what the control draws/);
  });

  it("fails a control whose aria-label drops what it draws, and passes one named by its content", () => {
    render(
      <div>
        <a href="/a" aria-label="Open the service x">
          Open service
        </a>
        <a href="/b" aria-label="Open service x">
          Open service
        </a>
        <a href="/c">Open service</a>
      </div>,
    );
    const wrong = screen.getByRole("link", { name: "Open the service x" });
    const right = screen.getByRole("link", { name: "Open service x" });
    const byContent = screen.getByRole("link", { name: "Open service" });
    expect(() => {
      expectLabelInName(wrong);
    }).toThrow(/does not hold what the control draws/);
    expect(() => {
      expectLabelInName(right);
      expectLabelInName(byContent);
    }).not.toThrow();
  });
});
