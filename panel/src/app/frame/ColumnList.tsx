import { Link, useRouterState } from "@tanstack/react-router";
import type { LucideIcon } from "lucide-react";
import { useId, useMemo } from "react";
import type { ReactNode } from "react";

import { SlidingIndicator } from "../../components/ui/SlidingIndicator";
import { cx } from "../../lib/cx";
import { currentItem } from "../columns";

/** One link of the column, whatever resource it is the sections of. */
export interface ListItem {
  key: string;
  to: string;
  params?: Record<string, string>;
  search?: Record<string, unknown>;
  label: string;
  icon?: LucideIcon;
  /** A real logo in the icon's place (an engine's), drawn with no tile. */
  mark?: ReactNode;
  exact?: boolean;
  /** A count after the name, `null` while it loads: its room is kept, so the list does not change when it arrives. */
  count?: number | null;
}

export interface ListGroup {
  key: string;
  /** What the group is for ("Use", "Danger"), or whose it is in Settings. */
  heading?: ReactNode;
  /** The block above the group where the same groups exist more than once (a server's name on a fleet). */
  scope?: ReactNode;
  danger?: boolean;
  items: ListItem[];
}

// The current section is the raised piece under it (SlidingIndicator), which slides to the one chosen;
// the others rise when the pointer is over them.
const ITEM =
  "group elev-lift relative flex min-h-8 items-center gap-2.5 rounded-control px-2.5 py-1 text-13 leading-snug text-fg-muted " +
  "hover:bg-surface-raised hover:text-fg focus-visible:-outline-offset-2 " +
  "aria-[current=page]:bg-transparent aria-[current=page]:text-fg aria-[current=page]:hover:transform-none aria-[current=page]:hover:shadow-none";

/**
 * The column's body: the groups of a resource's sections under one raised piece, so it slides to the
 * section chosen even from one group to another. The current section is the one whose path is the longest
 * start of the page's route (`currentItem`), so a section beneath another is the only one marked.
 */
export function ColumnList({ label, groups, currentKey }: { label: string; groups: readonly ListGroup[]; currentKey?: string }) {
  const route = useRouterState({ select: (state) => state.matches.at(-1)?.fullPath ?? "" });
  const ids = useId();
  const all = useMemo(() => groups.flatMap((group) => group.items), [groups]);
  // A list whose views are one page with another filter (Databases' engines) says which is current itself:
  // the route alone cannot tell them apart.
  const current = currentKey === undefined ? currentItem(all, route) : all.find((item) => item.key === currentKey);
  return (
    <nav aria-label={label} className="relative isolate flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-3 pb-4 scroll-thin">
      <SlidingIndicator className="rounded-control bg-surface" />
      {groups.map((group, position) => {
        const headingId = `${ids}-${String(position)}`;
        const previous = groups[position - 1];
        const newBlock = previous !== undefined && previous.scope !== group.scope && group.scope !== undefined;
        const titled = group.heading !== undefined || group.scope !== undefined;
        return (
          <section key={group.key} className={cx("flex flex-col gap-0.5", newBlock && "border-t border-border pt-4")}>
            {titled ? (
              <p id={headingId} className={cx("flex min-w-0 items-center gap-1.5 px-2.5 pb-1 text-13 font-semibold", group.danger === true ? "text-fail" : "text-fg-muted")}>
                {group.scope !== undefined ? <span className="min-w-0 truncate text-fg">{group.scope}</span> : null}
                {group.heading !== undefined ? <span className="min-w-0 truncate">{group.heading}</span> : null}
              </p>
            ) : null}
            <ul aria-labelledby={titled ? headingId : undefined} className="flex flex-col gap-0.5">
              {group.items.map((item) => (
                <li key={item.key}>
                  <Link
                    to={item.to}
                    params={item.params as never}
                    {...(item.search !== undefined ? { search: item.search as never } : {})}
                    // Where views are one page with another filter (Databases' engines) the search is part of which one is
                    // current; everywhere else a filter on the page leaves the section current.
                    activeOptions={{ exact: true, includeSearch: currentKey !== undefined }}
                    {...(current === item ? { "aria-current": "page" as const } : {})}
                    className={ITEM}
                  >
                    {item.mark !== undefined ? (
                      item.mark
                    ) : item.icon !== undefined ? (
                      <item.icon aria-hidden="true" className="size-icon-md shrink-0 text-fg-faint group-hover:text-fg-muted group-aria-[current=page]:text-accent-fg" />
                    ) : null}
                    {/* A name that does not fit takes a second line: Spanish runs longer than the column, and a cut name is another name. */}
                    <span className="min-w-0 flex-1">{item.label}</span>
                    {item.count !== undefined ? (
                      <span className={cx("mono min-w-5 rounded-chip bg-bg-sunken px-1 text-center text-12 text-fg-muted", item.count === null && "invisible")}>{item.count ?? 0}</span>
                    ) : null}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </nav>
  );
}
