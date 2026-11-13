import { useEffect, useState } from "react";

import { ThemeSwitch } from "../app/ThemeSwitch";
import type { ThemeChoice } from "../app/theme";
import { Logo } from "../components/brand/Logo";
import { Components } from "./Components";
import { Foundations } from "./Foundations";
import { Kit } from "./Kit";
import { Kit32 } from "./Kit32";
import { Kit40 } from "./Kit40";
import { Kit42 } from "./Kit42";
import { Kit43 } from "./Kit43";
import { KitAssistant } from "./KitAssistant";
import { KitDocker } from "./KitDocker";
import { PageKit } from "./PageKit";
import { Stone } from "./Stone";
import { KitArrange } from "./KitArrange";
import { Patterns } from "./Patterns";
import { Templates } from "./Templates";

const NAV: { title: string; links: [string, string][] }[] = [
  {
    title: "Foundations",
    links: [
      ["colour", "Colour"],
      ["state", "State language"],
      ["viz", "Chart series"],
      ["type", "Type roles"],
      ["space", "Space"],
      ["shape", "Shape"],
      ["sizes", "Controls and layers"],
      ["widths", "Widths"],
      ["icons", "Icons"],
      ["elevation", "Elevation and motion"],
    ],
  },
  {
    title: "Dry stone (4.1)",
    links: [
      ["stone-heights", "Three heights"],
      ["stone-type", "Typefaces"],
      ["stone-controls", "Controls"],
      ["stone-surfaces", "Surfaces and data"],
      ["stone-motion", "The piece that moves"],
      ["stone-fleet", "The fleet in courses"],
      ["stone-logos", "Logos and the first time"],
    ],
  },
  {
    title: "Templates",
    links: [
      ["t1", "T1 List"],
      ["t2", "T2 Detail"],
      ["t3", "T3 Settings"],
      ["t4", "T4 Dashboard"],
      ["t5", "T5 Wizard"],
      ["t6", "T6 File editor"],
      ["t7", "T7 Access"],
      ["t-empty", "First use"],
    ],
  },
  {
    title: "Components (3.1)",
    links: [
      ["page-header", "Page header"],
      ["notice", "Notice"],
      ["job-progress", "Job progress"],
      ["loading-region", "Loading region"],
      ["filter-bar", "Filter bar"],
      ["empty", "Empty states"],
      ["card-kit", "Card and subsection"],
      ["friction", "Confirmation friction"],
    ],
  },
  {
    title: "Components (3.2)",
    links: [
      ["combobox", "Combobox"],
      ["flow-diagram", "Flow diagram"],
    ],
  },
  {
    title: "Components (4.0)",
    links: [
      ["k40-setting", "Setting and group"],
      ["k40-inline", "Inline message"],
      ["k40-leave", "Leaving a form"],
      ["k40-confirm", "One confirmation"],
      ["k40-split", "Split button"],
      ["k40-rows", "Rows and row menu"],
      ["k40-fold", "Disclosure card"],
      ["k40-group", "Field group"],
      ["k40-skeleton", "Skeleton presets"],
      ["k40-place", "Place line"],
      ["k40-long-settings", "Long settings list"],
      ["k40-dashboard-tabs", "Dashboard tabs"],
      ["k40-table", "A table that waits"],
      ["k40-icons", "Icons of objects"],
    ],
  },
  {
    title: "Components (4.2)",
    links: [
      ["k42-surface", "Section with a surface"],
      ["k42-first-use", "First use"],
      ["k42-pairs", "Key-value pairs"],
      ["k42-card", "Resource card"],
      ["k42-tags", "Tags in a table"],
      ["k42-help", "Help ?"],
      ["k42-roles", "What each role can do"],
      ["k42-header", "A header as wide as its content"],
      ["k42-hints", "Command with its reason"],
    ],
  },
  {
    title: "Components (4.3)",
    links: [
      ["k43-scope", "Scope line"],
      ["k43-fleet-list", "Fleet list"],
      ["k43-servers-table", "Table of servers"],
      ["k43-which-server", "On which server?"],
      ["kd-owner", "Owner mark"],
      ["kd-version", "Version mark"],
      ["kd-menu", "Object menu"],
      ["kd-diff", "Diff lines"],
    ],
  },
  {
    title: "The assistant and who calls (4.3)",
    links: [
      ["ka-mark", "The mark of an AI"],
      ["ka-text", "The text of an answer"],
      ["ka-steps", "What it read"],
      ["ka-proposal", "A proposal"],
      ["ka-plan", "See what it will do"],
      ["ka-usage", "Usage of the limits"],
      ["ka-explain", "Explain this"],
      ["ka-policy", "The policy of who calls"],
    ],
  },
  {
    title: "Components",
    links: [
      ["button", "Button"],
      ["icon-button", "Icon button"],
      ["field", "Fields"],
      ["overlay", "Dialogs and drawers"],
      ["tabs", "Tabs and menus"],
      ["status", "Status and feedback"],
      ["badge", "Badges and values"],
      ["card", "Card and empty state"],
      ["table", "Data table"],
      ["chart", "Chart"],
      ["logs", "Log viewer"],
      ["logo", "Logo"],
    ],
  },
  {
    title: "Patterns",
    links: [
      ["p-state", "Showing state"],
      ["p-actions", "Actions"],
      ["p-errors", "Errors"],
      ["p-empty", "Empty and loading"],
      ["p-forms", "Forms"],
      ["p-tables", "Tables"],
      ["p-notify", "Notifications"],
      ["p-fleet", "Which server"],
      ["p-content", "Words"],
      ["p-arrange", "Arranging a panel"],
    ],
  },
  {
    title: "Page kit",
    links: [
      ["page-section", "Section"],
      ["key-value", "Key-value list"],
      ["time", "Time and numbers"],
      ["query-state", "Query state"],
      ["app-state", "App and deploy state"],
      ["resources", "Tiles and meters"],
      ["command", "Command hint"],
      ["danger", "Danger zone"],
    ],
  },
];

function initialTheme(): ThemeChoice {
  const param = new URLSearchParams(window.location.search).get("theme");
  return param === "light" || param === "dark" ? param : "system";
}

/**
 * Development-only review surface: every foundation and every component in its states. This
 * is where the design is judged, in both themes, before any page uses it.
 */
export function DesignGallery() {
  const [theme, setTheme] = useState<ThemeChoice>(initialTheme);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset["theme"];
    else root.dataset["theme"] = theme;
    return () => {
      delete root.dataset["theme"];
    };
  }, [theme]);

  return (
    <div className="min-h-dvh bg-bg text-fg">
      <header className="sticky top-0 z-sticky border-b border-border bg-bg">
        <div className="mx-auto flex h-14 max-w-[1320px] items-center justify-between gap-4 px-6 max-sm:px-4">
          <div className="flex items-center gap-3">
            <Logo variant="wordmark" height={16} />
            <span aria-hidden="true" className="h-4 w-px bg-border" />
            <span className="text-13 font-medium text-fg-muted">Design system</span>
          </div>
          <ThemeSwitch value={theme} onChange={setTheme} compact />
        </div>
      </header>

      <div className="mx-auto grid max-w-[1320px] gap-10 px-6 lg:grid-cols-[12rem_1fr] max-sm:px-4">
        <nav aria-label="Design system" className="sticky top-14 hidden max-h-[calc(100dvh-3.5rem)] self-start overflow-y-auto py-10 scroll-thin lg:block">
          {NAV.map((group) => (
            <div key={group.title} className="mb-6">
              <p className="mb-2 text-12 font-medium text-fg-faint">{group.title}</p>
              <ul className="flex flex-col">
                {group.links.map(([id, label]) => (
                  <li key={id}>
                    <a
                      href={`#${id}`}
                      className="-mx-2 block rounded-control px-2 py-1 text-13 text-fg-muted hover:bg-surface-hover hover:text-fg focus-visible:outline-2 focus-visible:outline-focus"
                    >
                      {label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <main className="min-w-0 py-10">
          <div className="max-w-[62ch] pb-12">
            <h1 className="display text-32 text-fg">A precision instrument for one machine</h1>
            <p className="mt-3 text-16 text-pretty text-fg-muted">
              The console is dense where the operator works and generous where they decide. Surfaces are stone, with
              three heights under one light from above; colour is spent only on state, on what can be acted on, and on a
              chart&apos;s series. This page is the executable form of docs/DESIGN.md: change the system here first.
            </p>
          </div>
          <Stone />
          <Foundations />
          <Templates />
          <Kit />
          <Kit32 />
          <Kit40 />
          <Kit42 />
          <Kit43 />
          <KitAssistant />
          <KitDocker />
          <Components />
          <PageKit />
          <Patterns />
          <KitArrange />
        </main>
      </div>
    </div>
  );
}
