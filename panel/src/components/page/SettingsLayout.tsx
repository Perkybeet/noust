import { Link } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useId } from "react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { SM_UP, useMediaQuery } from "../ui/useMediaQuery";

export interface SettingsNavItem {
  /** The subsection's own URL. */
  to: string;
  params?: Record<string, string>;
  /** Search parameters the link sets, such as the server a section is about. */
  search?: Record<string, unknown>;
  /** Already translated. */
  label: string;
  /** Only this path, not its children, marks the item current. */
  exact?: boolean;
  /** The destructive subsection ("Delete"): set apart, last. */
  danger?: boolean;
  /**
   * The heading of the group the item belongs to, when the sections belong to different
   * owners ("web-2", "Central · nas"): consecutive items with the same group are listed under
   * it. Already translated; a node may be given to set a name in mono.
   */
  group?: ReactNode;
  /** A key for `group`, when it is not a string. */
  groupKey?: string;
}

export interface SettingsLayoutProps {
  /** Names the navigation: "Application settings". */
  label: string;
  items: readonly SettingsNavItem[];
  /**
   * True on the settings' own index route. On a phone the index is the list of subsections
   * and nothing else; on a wider screen it shows its content (usually General) beside the nav.
   */
  index?: boolean;
  /** Where "All sections" leads on a phone: the index route. */
  backTo?: string;
  backParams?: Record<string, string>;
  /** The current subsection: its own route, one form, at most one and a half screens. */
  children: ReactNode;
  className?: string;
}

const LINK =
  "flex items-center rounded-control px-2.5 text-13 text-fg-muted outline-none " +
  "hover:bg-surface-hover hover:text-fg focus-visible:outline-2 focus-visible:outline-focus " +
  "data-[status=active]:bg-surface-active data-[status=active]:font-medium data-[status=active]:text-fg";

function NavList({ items, phone, labelledBy }: { items: readonly SettingsNavItem[]; phone: boolean; labelledBy?: string }) {
  return (
    <ul className="flex flex-col gap-0.5" {...(labelledBy !== undefined ? { "aria-labelledby": labelledBy } : {})}>
      {items.map((item) => (
        <li key={item.to}>
          <Link
            to={item.to}
            params={item.params as never}
            {...(item.search !== undefined ? { search: item.search as never } : {})}
            activeOptions={{ exact: item.exact ?? false, includeSearch: false }}
            className={cx(LINK, phone ? "h-11 justify-between" : "h-8", item.danger === true && "text-fail hover:text-fail data-[status=active]:text-fail")}
          >
            <span className="truncate">{item.label}</span>
            {phone ? <ChevronRight aria-hidden="true" className="size-icon-md text-fg-faint" /> : null}
          </Link>
        </li>
      ))}
    </ul>
  );
}

/**
 * T3, settings with side navigation: a 200px list of subsections, each its own URL, beside
 * content of at most 880px. The destructive subsection comes last, set apart. On a phone the
 * navigation is an index page of its own and each subsection has a way back to it.
 */
export function SettingsLayout({ label, items, index = false, backTo, backParams, children, className }: SettingsLayoutProps) {
  const t = useT();
  const id = useId();
  const wide = useMediaQuery(SM_UP);
  const safe = items.filter((item) => item.danger !== true);
  const danger = items.filter((item) => item.danger === true);
  // Consecutive items of one group, each group under its heading.
  const groups: { key: string; heading: ReactNode; items: SettingsNavItem[] }[] = [];
  for (const item of safe) {
    const key = item.groupKey ?? (typeof item.group === "string" ? item.group : "");
    const last = groups.at(-1);
    if (last?.key === key) last.items.push(item);
    else groups.push({ key, heading: item.group, items: [item] });
  }
  const nav = (phone: boolean) => (
    <nav aria-label={label} className="flex flex-col gap-2">
      {groups.map((group, index) =>
        group.heading === undefined ? (
          <NavList key={group.key} items={group.items} phone={phone} />
        ) : (
          <div key={group.key} className={cx("flex flex-col gap-1", index > 0 && "mt-3")}>
            <p id={`${id}-${phone ? "p" : "w"}-${String(index)}`} className="flex min-w-0 items-center gap-1.5 px-2.5 text-12 font-medium text-fg-muted">
              {group.heading}
            </p>
            <NavList items={group.items} phone={phone} labelledBy={`${id}-${phone ? "p" : "w"}-${String(index)}`} />
          </div>
        ),
      )}
      {danger.length > 0 ? (
        <div className="border-t border-border pt-2">
          <NavList items={danger} phone={phone} />
        </div>
      ) : null}
    </nav>
  );
  return (
    <div
      data-template="settings"
      className={cx("grid min-w-0 gap-8 sm:grid-cols-[var(--width-settings-nav)_minmax(0,var(--width-settings))]", className)}
    >
      {/* Wider than a phone: the list beside the content, following the page. Each list is
          only rendered where it can show (the classes still hide it while the width changes),
          so the page has one navigation landmark, in a browser and in a test alike. */}
      {wide || !index ? (
        <div data-slot="nav" className="max-sm:hidden">
          <div className="sticky top-20">{nav(false)}</div>
        </div>
      ) : null}
      {/* A phone's index: the list is the page. */}
      {index && !wide ? (
        <div data-slot="index" className="sm:hidden">
          {nav(true)}
        </div>
      ) : null}
      <div data-slot="content" className={cx("flex min-w-0 flex-col gap-6", index && "max-sm:hidden")}>
        {!index && backTo !== undefined ? (
          <Link
            to={backTo}
            params={backParams as never}
            className="-ml-1 inline-flex items-center gap-1 self-start rounded-chip px-1 text-13 text-fg-muted hover:text-fg focus-visible:outline-2 focus-visible:outline-focus sm:hidden"
          >
            <ChevronLeft aria-hidden="true" className="size-icon-sm" />
            {t("common.settingsLayout.allSections")}
          </Link>
        ) : null}
        {children}
      </div>
    </div>
  );
}
