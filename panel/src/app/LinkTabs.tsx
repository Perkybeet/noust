import { Link } from "@tanstack/react-router";
import { useRef } from "react";

import { SlidingIndicator } from "../components/ui/SlidingIndicator";
import { useTabStrip } from "../components/ui/tabStrip";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import type { TabItem } from "./nav";
import { useCentralReturn } from "./useCentralReturn";

export interface LinkTab extends TabItem {
  params?: Record<string, string>;
  /**
   * A count after the label (backups, certificates). `null` while it loads: the room is kept,
   * so the strip does not change width, and every tab after it does not move, when it arrives.
   */
  count?: number | null;
}

export interface LinkTabsProps {
  /** Names the set for assistive technology: "Application sections". */
  label: string;
  tabs: readonly LinkTab[];
  className?: string;
}

/**
 * Tabs whose state is the URL: each is a link, so a section can be bookmarked, shared and
 * opened in a new tab. Styled like the Tabs primitive (a carved rail and a raised piece that
 * slides to the current section, also when the way there was the browser's back button); focus stays on the tab after a switch
 * (`data-keep-focus`) instead of jumping to the page heading, and the page does not scroll to
 * the top: the header and the strip are the same page, only the section under them changes.
 */
export function LinkTabs({ label, tabs, className }: LinkTabsProps) {
  const t = useT();
  const ref = useRef<HTMLElement>(null);
  const withServer = useCentralReturn();
  // On a phone the row is wider than the screen: the current section stays in view.
  useTabStrip(ref, '[data-status="active"]', "data-status");
  return (
    <nav
      ref={ref}
      aria-label={label}
      data-keep-focus=""
      className={cx(
        "tab-strip elev-carved relative isolate w-fit max-w-full overflow-x-auto rounded-card bg-bg-sunken p-0.5 [scrollbar-width:none]",
        className,
      )}
    >
      <SlidingIndicator className="rounded-control bg-surface" />
      <ul className="flex gap-0.5">
        {tabs.map((tab) => (
          <li key={tab.to} className="shrink-0">
            <Link
              // The union of every path loses the per-path params type; the caller supplies them.
              to={tab.to as string}
              params={tab.params as never}
              // On a page of the central's (the backup destinations) the tabs of the area lead to the server the person was on.
              {...(withServer(tab.to) !== undefined ? { search: withServer(tab.to) as never } : {})}
              activeOptions={{ exact: tab.exact ?? false, includeSearch: false }}
              // A section of the page it is on: the strip stays under the pointer (searchNavigation.ts).
              resetScroll={false}
              className={cx(
                "group relative flex h-8 items-center rounded-control px-3 text-13 font-medium whitespace-nowrap text-fg-muted outline-none",
                // The current section is the indicator under it; the others rise when the pointer is over them.
                "elev-lift hover:bg-surface-raised hover:text-fg data-[status=active]:bg-transparent data-[status=active]:text-fg data-[status=active]:hover:transform-none data-[status=active]:hover:shadow-none",
                "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus",
              )}
            >
              {t(tab.label)}
              {tab.count !== undefined ? (
                // The same count as the Tabs primitive's, so both kinds of tab strip read alike.
                <span
                  className={cx(
                    "mono ml-1.5 min-w-5 rounded-chip bg-bg-sunken px-1 text-center text-12 text-fg-muted group-data-[status=active]:bg-bg group-data-[status=active]:text-fg",
                    tab.count === null && "invisible",
                  )}
                >
                  {tab.count ?? 0}
                </span>
              ) : null}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
