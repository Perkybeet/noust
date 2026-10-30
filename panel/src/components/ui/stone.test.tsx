import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { ChoiceCards } from "./ChoiceCards";
import { EmptyState } from "./EmptyState";
import { FleetCourses, isWell, planCourse } from "./FleetCourses";
import type { FleetCourse, FleetCourseApp } from "./FleetCourses";
import { HelpPopover } from "./HelpPopover";
import { Illustration } from "./Illustration";
import { LogoTile } from "./LogoTile";
import { LOGOS } from "./logos/logoData";
import { SectionSwitch, SwitchedRegion } from "./SectionSwitch";
import { StatusPill } from "./StatusPill";
import type { Status } from "./StatusPill";

function app(id: string, state: Status): FleetCourseApp {
  return { id, name: `${id}.example.com`, state, stateLabel: state === "failed" ? "Failed" : state === "stopped" ? "Stopped" : "Running" };
}

describe("planCourse: which slabs a course draws", () => {
  it("draws everything, the problems first, while it fits in two rows", () => {
    const plan = planCourse([app("c", "running"), app("a", "stopped"), app("b", "failed"), app("d", "running")], 4);
    expect(plan.slabs.map((slab) => slab.id)).toEqual(["b", "a", "c", "d"]);
    expect(plan.grouped).toBe(0);
  });

  it("orders problems by urgency: failed, then a warning, then work in progress, then stopped", () => {
    const apps = [app("stopped", "stopped"), app("deploying", "deploying"), app("failed", "failed"), app("warning", "warning"), app("unknown", "unknown")];
    expect(planCourse(apps, 4).slabs.map((slab) => slab.id)).toEqual(["failed", "warning", "deploying", "unknown", "stopped"]);
  });

  it("says the well ones as one slab when they do not fit, and never hides a problem", () => {
    const well = Array.from({ length: 9 }, (_, index) => app(`w${String(index)}`, "running"));
    const plan = planCourse([...well, app("p", "stopped"), app("f", "failed")], 4);
    expect(plan.slabs.map((slab) => slab.id)).toEqual(["f", "p"]);
    expect(plan.grouped).toBe(9);
  });

  it("draws every application when told not to group: a fleet of one server has nothing to compare with", () => {
    const well = Array.from({ length: 9 }, (_, index) => app(`w${String(index)}`, "running"));
    const plan = planCourse([...well, app("f", "failed")], 4, true);
    expect(plan.grouped).toBe(0);
    expect(plan.slabs).toHaveLength(10);
    expect(plan.slabs[0]?.id).toBe("f");
  });

  it("keeps a course of exactly two rows as it is", () => {
    const eight = Array.from({ length: 8 }, (_, index) => app(`w${String(index)}`, "running"));
    expect(planCourse(eight, 4).grouped).toBe(0);
    expect(planCourse(eight, 4).slabs).toHaveLength(8);
    expect(planCourse([...eight, app("x", "running")], 4).grouped).toBe(9);
  });

  it("knows what is well: running and static, and nothing else", () => {
    expect((["running", "static"] as Status[]).every(isWell)).toBe(true);
    expect((["deploying", "queued", "warning", "failed", "stopped", "unknown"] as Status[]).some(isWell)).toBe(false);
  });
});

const COURSES: FleetCourse[] = [
  { id: "web-01", name: "web-01", place: "Madrid", diskPercent: 52, apps: [app("a", "failed"), app("b", "running")] },
  { id: "clientes-01", name: "clientes-01", place: "Helsinki", diskPercent: 91, apps: Array.from({ length: 9 }, (_, index) => app(`w${String(index)}`, "running")) },
  { id: "web-03", name: "web-03", diskPercent: 7, apps: [] },
];

describe("FleetCourses", () => {
  it("is a list of courses, one per server, named as the operator named them", async () => {
    render(<FleetCourses label="Servers and their applications" courses={COURSES} />);
    const list = screen.getByRole("list", { name: "Servers and their applications" });
    expect(within(list).getByText("web-01")).toBeInTheDocument();
    expect(within(list).getByText("Madrid")).toBeInTheDocument();
    expect(within(list).getByText("web-03")).toBeInTheDocument();
    await expectNoAxeViolations(list);
  });

  it("tells a problem by its word and its glyph, and a well one by its glyph alone with the word for a screen reader", () => {
    render(<FleetCourses label="Fleet" courses={COURSES} />);
    expect(screen.getByText("Failed")).toBeVisible();
    // The failed slab is tinted by its state; the healthy one is stone.
    expect(screen.getByText("a.example.com").closest("div")?.className).toContain("bg-fail-soft");
    expect(screen.getByText("b.example.com").closest("div")?.className).toContain("bg-surface-raised");
    expect(screen.getByText("b.example.com").closest("div")?.className).not.toMatch(/bg-(?:ok|fail|warn)/);
  });

  it("says the well ones of a crowded server as '+N running', in the catalog's words", () => {
    render(<FleetCourses label="Fleet" courses={COURSES} />);
    expect(screen.getByText("+9 running")).toBeInTheDocument();
  });

  it("shows an empty slot for a server with nothing on it", () => {
    render(<FleetCourses label="Fleet" courses={COURSES} />);
    expect(screen.getByText("No applications")).toBeInTheDocument();
  });

  it("warns, with a glyph and a word and not by colour alone, when a disk is nearly full", () => {
    render(<FleetCourses label="Fleet" courses={COURSES} />);
    const disk = screen.getByText("Disk 91 %");
    expect(disk.className).toContain("text-warn");
    expect(disk.querySelector("svg")).not.toBeNull();
    expect(screen.getByText("Disk 52 %").className).not.toContain("text-warn");
  });

  it("puts what is wrong with a server at the head of its course, tinted, and still draws its applications", () => {
    render(
      <FleetCourses
        label="Fleet"
        courses={[{ id: "ams-3", name: "ams-3", problem: { state: "failed", label: "Not answering" }, apps: [app("a", "running")] }]}
      />,
    );
    const slab = screen.getByText("Not answering");
    expect(slab.closest("div")?.className).toContain("bg-fail-soft");
    expect(slab.querySelector("svg")).not.toBeNull();
    // A problem is not what hides the rest: the last answer's applications are drawn after it.
    expect(screen.getByText("a.example.com")).toBeInTheDocument();
    expect(screen.queryByText("No applications")).toBeNull();
  });

  it("does not say a server with a problem and nothing on it has no applications", () => {
    render(<FleetCourses label="Fleet" courses={[{ id: "ams-3", name: "ams-3", problem: { state: "failed", label: "Not answering" }, apps: [] }]} />);
    expect(screen.getByText("Not answering")).toBeVisible();
    expect(screen.queryByText("No applications")).toBeNull();
  });

  it("raises the slabs that open a page, and seats the ones that do not", () => {
    const { rerender } = render(<FleetCourses label="Fleet" courses={COURSES} />);
    expect(screen.getByText("a.example.com").closest("div")?.className).toContain("elev-seated");
    rerender(
      <FleetCourses
        label="Fleet"
        courses={COURSES}
        linkFor={(target, className, children) => (
          <a href={`/${target.type}/${target.id}`} className={className}>
            {children}
          </a>
        )}
      />,
    );
    const link = screen.getByRole("link", { name: /a\.example\.com/ });
    expect(link).toHaveAttribute("href", "/app/a");
    expect(link.className).toContain("elev-raised");
    expect(screen.getByRole("link", { name: "web-01" })).toHaveAttribute("href", "/server/web-01");
  });
});

describe("LogoTile", () => {
  it("draws the packaged mark of a logo and fetches nothing", () => {
    const { container } = render(<LogoTile logo="nextcloud" />);
    const path = container.querySelector("path");
    expect(path?.getAttribute("d")).toBe(LOGOS.nextcloud.path);
    expect(container.querySelector("img, image, use")).toBeNull();
    expect(container.innerHTML).not.toMatch(/https?:\/\//);
  });

  it("is decoration unless it is asked to name the logo", () => {
    const { container, rerender } = render(<LogoTile logo="ghost" />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    rerender(<LogoTile logo="ghost" label />);
    expect(screen.getByRole("img", { name: "Ghost" })).toBeInTheDocument();
  });

  it("is carved, in three sizes, and is the only tile", () => {
    const { container } = render(<LogoTile logo="redis" size="lg" />);
    expect(container.firstElementChild).toHaveClass("elev-carved-fine", "size-14");
  });

  it("has a brand colour in both themes for every logo it knows, and a licence and a notice beside the data", () => {
    for (const [slug, logo] of Object.entries(LOGOS)) {
      expect(logo.light, slug).toMatch(/^[0-9a-f]{6}$/);
      expect(logo.dark, slug).toMatch(/^[0-9a-f]{6}$/);
      expect(logo.path.length, slug).toBeGreaterThan(20);
    }
    expect(Object.keys(LOGOS).length).toBeGreaterThanOrEqual(29);
  });
});

describe("HelpPopover", () => {
  it("is a button, named for what it explains, that opens the explanation", async () => {
    render(
      <HelpPopover label="About deploy keys" title="Deploy keys">
        A key that lets this server read one private repository.
      </HelpPopover>,
    );
    const button = screen.getByRole("button", { name: "About deploy keys" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(button);
    expect(await screen.findByText("A key that lets this server read one private repository.")).toBeVisible();
    expect(screen.getByRole("button", { name: "About deploy keys" })).toHaveAttribute("aria-expanded", "true");
  });

  it("opens from the keyboard, which a tooltip would not", async () => {
    render(<HelpPopover label="About previews">Each pull request gets its own copy.</HelpPopover>);
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "About previews" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("Each pull request gets its own copy.")).toBeVisible();
  });
});

describe("SectionSwitch and SwitchedRegion", () => {
  function Page({ start = true }: { start?: boolean }) {
    const [on, setOn] = useState(start);
    return (
      <>
        <SectionSwitch label="Alerts" checked={on} onCheckedChange={setOn} />
        <SwitchedRegion on={on}>
          <p>The rules</p>
        </SwitchedRegion>
      </>
    );
  }

  it("is one switch named for the section, which never changes its name with its state", async () => {
    render(<Page />);
    const toggle = screen.getByRole("switch", { name: "Alerts" });
    expect(toggle).toBeChecked();
    await userEvent.click(toggle);
    expect(screen.getByRole("switch", { name: "Alerts" })).not.toBeChecked();
  });

  it("dims what it governs when the section is off, and leaves it readable", async () => {
    const { container } = render(<Page />);
    const region = container.querySelector('[data-slot="switched-region"]');
    expect(region).toHaveAttribute("data-state", "on");
    expect(region).not.toHaveClass("opacity-60");
    await userEvent.click(screen.getByRole("switch", { name: "Alerts" }));
    expect(region).toHaveAttribute("data-state", "off");
    expect(region).toHaveClass("opacity-60");
    expect(screen.getByText("The rules")).toBeInTheDocument();
  });

  it("says why a switch that cannot be moved cannot, next to it and tied to it", () => {
    render(<SectionSwitch label="Backups" checked={false} onCheckedChange={() => undefined} disabled reason="Add a destination first" />);
    const toggle = screen.getByRole("switch", { name: "Backups" });
    expect(toggle).toHaveAttribute("aria-disabled", "true");
    expect(toggle).toHaveAccessibleDescription("Add a destination first");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<Page />);
    await expectNoAxeViolations(container);
  });
});

describe("Illustration and the first time", () => {
  it("is decoration unless it is given a label", () => {
    const { container, rerender } = render(<Illustration name="first-steps" />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    rerender(<Illustration name="first-steps" label="The noust with a boat in it" />);
    expect(screen.getByRole("img", { name: "The noust with a boat in it" })).toBeInTheDocument();
  });

  it("is drawn in tokens and in no gradient, no filter and no colour of its own", () => {
    for (const name of ["first-steps", "empty-slab"] as const) {
      const { container, unmount } = render(<Illustration name={name} />);
      const html = container.innerHTML;
      expect(html).not.toMatch(/gradient|filter|#[0-9a-fA-F]{3,6}|rgb\(/);
      expect(html).toMatch(/stroke-il-stroke/);
      unmount();
    }
  });

  it("is drawn by a first-use empty state only, in the place of its icon, and never by an inline one", () => {
    const { container, rerender } = render(<EmptyState variant="firstUse" illustration="empty-slab" title="No backups yet" />);
    expect(container.querySelector("svg.stroke-il-stroke, svg")).not.toBeNull();
    expect(screen.getByRole("heading", { name: "No backups yet" })).toBeInTheDocument();
    rerender(<EmptyState variant="inline" title="Nothing matched" />);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("draws an icon bare, with no tile behind it", () => {
    const { container } = render(<EmptyState variant="firstUse" icon={<svg data-testid="icon" />} title="Nothing here" />);
    const icon = screen.getByTestId("icon");
    expect(icon.parentElement?.className).not.toMatch(/bg-|rounded-|border|elev-/);
    expect(container.querySelector("[class*='elev-']")).toBeNull();
  });
});

describe("ChoiceCards: a title, a line and the detail folded", () => {
  const options = [
    { value: "viewer", label: "Viewer", description: "Sees everything and changes nothing." },
    { value: "operator", label: "Operator", description: "Deploys and reads the logs.", detail: "Cannot change who has access." },
  ] as const;

  function Roles() {
    const [value, setValue] = useState<"viewer" | "operator">("viewer");
    return <ChoiceCards legend="What they can do" options={options} value={value} onValueChange={setValue} columns={1} />;
  }

  it("keeps the detail folded until it is asked for, and does not read it as the option's description", async () => {
    render(<Roles />);
    const operator = screen.getByRole("radio", { name: "Operator" });
    expect(operator).toHaveAccessibleDescription("Deploys and reads the logs.");
    expect(screen.getByText("Cannot change who has access.")).not.toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /Details/ }));
    expect(screen.getByText("Cannot change who has access.")).toBeVisible();
    expect(screen.getByRole("button", { name: /Hide details/ })).toHaveAttribute("aria-expanded", "true");
    expect(operator).toHaveAccessibleDescription("Deploys and reads the logs.");
  });

  it("asks for the detail without choosing the option", async () => {
    render(<Roles />);
    await userEvent.click(screen.getByRole("button", { name: /Details/ }));
    expect(screen.getByRole("radio", { name: "Viewer" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Operator" })).not.toBeChecked();
  });

  it("raises the options, and takes the accent as a ring for the chosen one", () => {
    render(<Roles />);
    expect(screen.getByRole("radio", { name: "Viewer" })).toHaveClass("elev-raised-chosen");
    expect(screen.getByRole("radio", { name: "Operator" })).toHaveClass("elev-raised");
  });

  it("has no accessibility violations", async () => {
    const { container } = render(<Roles />);
    await expectNoAxeViolations(container);
  });
});

describe("StatusPill: only what is not well tints its surface", () => {
  it.each([
    ["running", false],
    ["static", false],
    ["deploying", true],
    ["queued", true],
    ["warning", true],
    ["failed", true],
    ["stopped", true],
    ["unknown", true],
  ] as const)("%s has a tinted ground: %s", (state, tinted) => {
    const { container } = render(<StatusPill state={state} size="sm" />);
    expect(/\bbg-(?:warn|fail|idle)-soft\b/.test(container.firstElementChild?.className ?? "")).toBe(tinted);
    expect(container.firstElementChild?.className).not.toMatch(/bg-ok-soft/);
  });

  it("says its state by a glyph and a word, tinted or not", () => {
    const { container } = render(<StatusPill state="running" />);
    expect(container.querySelector('[data-glyph="dot"]')).not.toBeNull();
    expect(screen.getByText("Running")).toBeInTheDocument();
  });
});
