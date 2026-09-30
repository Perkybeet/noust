import { Link } from "@tanstack/react-router";
import { useRef } from "react";

import { useTabStrip } from "../components/ui/tabStrip";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import type { TabItem } from "./nav";

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
 * opened in a new tab. Styled like the Tabs primitive; focus stays on the tab after a switch
 * (`data-keep-focus`) instead of jumping to the page heading, and the page does not scroll to
 * the top: the header and the strip are the same page, only the section under them changes.
 */
export function LinkTabs({ label, tabs, className }: LinkTabsProps) {
  const t = useT();
  const ref = useRef<HTMLUListElement>(null);
  // On a phone the row is wider than the screen: the current section stays in view.
  useTabStrip(ref, '[data-status="active"]', "data-status");
  return (
    <nav aria-label={label} data-keep-focus="" className={cx("border-b border-border", className)}>
      <ul ref={ref} className="tab-strip -mb-px flex gap-1 overflow-x-auto [scrollbar-width:none]">
        {tabs.map((tab) => (
          <li key={tab.to} className="shrink-0">
            <Link
              // The union of every path loses the per-path params type; the caller supplies them.
              to={tab.to as string}
              params={tab.params as never}
              activeOptions={{ exact: tab.exact ?? false, includeSearch: false }}
              // A section of the page it is on: the strip stays under the pointer (searchNavigation.ts).
              resetScroll={false}
              className={cx(
                "group relative flex h-10 items-center px-2.5 text-13 font-medium whitespace-nowrap text-fg-muted outline-none",
                "hover:text-fg data-[status=active]:text-fg",
                "focus-visible:after:absolute focus-visible:after:inset-x-0 focus-visible:after:inset-y-1.5 focus-visible:after:rounded-control focus-visible:after:outline-2 focus-visible:after:outline-focus",
              )}
            >
              {t(tab.label)}
              {tab.count !== undefined ? (
                // The same count as the Tabs primitive's, so both kinds of tab strip read alike.
                <span
                  className={cx(
                    "mono ml-1.5 min-w-5 rounded-chip bg-bg-sunken px-1 text-center text-12 text-fg-muted group-data-[status=active]:text-fg",
                    tab.count === null && "invisible",
                  )}
                >
                  {tab.count ?? 0}
                </span>
              ) : null}
              <span
                aria-hidden="true"
                className="absolute inset-x-2.5 bottom-0 h-0.5 rounded-pill bg-transparent group-data-[status=active]:bg-accent-fg"
              />
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
