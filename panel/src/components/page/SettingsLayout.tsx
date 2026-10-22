import { Link } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { ICONS } from "../ui/icons";
import { Input } from "../ui/Input";
import { Kbd } from "../ui/Kbd";
import { SM_UP, useMediaQuery } from "../ui/useMediaQuery";

export interface SettingsNavItem {
  /** The subsection's own URL. */
  to: string;
  params?: Record<string, string>;
  /** Search parameters the link sets, such as the server a section is about. */
  search?: Record<string, unknown>;
  /** Already translated. */
  label: string;
  /**
   * What the section is, drawn before its name: an object of the register's, in the colour of the text
   * (colour is for state). Give one to every item of a list or to none, so the names stay aligned.
   */
  icon?: LucideIcon;
  /** Only this path, not its children, marks the item current. */
  exact?: boolean;
  /** The destructive subsection ("Delete"): set apart, last. */
  danger?: boolean;
  /**
   * The heading of the group the item belongs to, when the sections belong to different
   * owners or subjects ("Monitoring", "People and access"): consecutive items with the same
   * group are listed under it. Already translated.
   */
  group?: ReactNode;
  /** A key for `group`, when it is not a string. */
  groupKey?: string;
  /**
   * The block above the group, when the same groups exist more than once: a server's name on a
   * fleet (`web-2`, set in mono by the caller), "Central" for the instance's. It leads the
   * heading of each of its groups, "web-2 · Monitoring", so a heading can be read on its own.
   * Where there is only one, give none: a lone server shows the group alone.
   */
  scope?: ReactNode;
  /** A key for `scope`, when it is not a string. */
  scopeKey?: string;
  /** Other words that find the section in the filter: its old names ("Notifications"). */
  keywords?: string;
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
  /**
   * From how many sections the list has a box that narrows it by name (and by `keywords`), 12 by
   * default (NV-11): a list that long is searched, not read. Its `/` shortcut is the page's.
   */
  filterFrom?: number;
  /** The current subsection: its own route, one form, at most one and a half screens. */
  children: ReactNode;
  className?: string;
}

const LINK =
  "group flex items-center rounded-control px-2.5 text-13 text-fg-muted outline-none " +
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
            <span className="flex min-w-0 items-center gap-2.5">
              {item.icon !== undefined ? (
                <item.icon
                  aria-hidden="true"
                  className={cx("size-icon-md shrink-0", item.danger === true ? "text-fail" : "text-fg-faint group-hover:text-fg-muted group-data-[status=active]:text-fg")}
                />
              ) : null}
              <span className="truncate">{item.label}</span>
            </span>
            {phone ? <ChevronRight aria-hidden="true" className="size-icon-md shrink-0 text-fg-faint" /> : null}
          </Link>
        </li>
      ))}
    </ul>
  );
}

interface NavGroup {
  key: string;
  scopeKey: string;
  scope: ReactNode;
  heading: ReactNode;
  items: SettingsNavItem[];
}

/** What a heading is made of, as the string that tells two apart: a node has to bring a key. */
const keyOf = (key: string | undefined, node: ReactNode): string => key ?? (typeof node === "string" ? node : "");

/** Whether an item answers a filter: every word typed is in its name or in its keywords. */
function matches(item: SettingsNavItem, query: string): boolean {
  const haystack = `${item.label} ${item.keywords ?? ""}`.toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter((word) => word !== "")
    .every((word) => haystack.includes(word));
}

/**
 * T3, settings with side navigation: a 200px list of subsections, each its own URL, beside
 * content of at most 880px. The destructive subsection comes last, set apart. A long list is
 * grouped under headings that say whose the sections are (and, on a fleet, of which server), and
 * from twelve sections has a box that narrows it. On a phone the navigation is an index page of
 * its own and each subsection has a way back to it.
 */
export function SettingsLayout({ label, items, index = false, backTo, backParams, filterFrom = 12, children, className }: SettingsLayoutProps) {
  const t = useT();
  const id = useId();
  const wide = useMediaQuery(SM_UP);
  const [query, setQuery] = useState("");
  const filterable = items.length >= filterFrom;
  const narrowing = filterable && query.trim() !== "";
  const kept = narrowing ? items.filter((item) => matches(item, query)) : items;
  const safe = kept.filter((item) => item.danger !== true);
  const danger = kept.filter((item) => item.danger === true);
  // Consecutive items of one group, each group under its heading.
  const groups: NavGroup[] = [];
  for (const item of safe) {
    const scopeKey = keyOf(item.scopeKey, item.scope);
    const key = `${scopeKey}|${keyOf(item.groupKey, item.group)}`;
    const last = groups.at(-1);
    if (last?.key === key) last.items.push(item);
    else groups.push({ key, scopeKey, scope: item.scope, heading: item.group, items: [item] });
  }
  const Search = ICONS.search;
  const filterBox = filterable ? (
    <Input
      type="search"
      size="sm"
      aria-label={t("common.settingsLayout.filter")}
      placeholder={t("common.settingsLayout.filterPlaceholder")}
      // The page's `/` goes here when nothing else on it searches (the shell takes the first).
      data-page-search=""
      value={query}
      onValueChange={(value: string) => setQuery(value)}
      icon={<Search />}
      {...(query === "" ? { suffix: <Kbd className="pointer-coarse:hidden">/</Kbd> } : {})}
      autoComplete="off"
      spellCheck={false}
    />
  ) : null;
  const nav = (phone: boolean) => (
    <div className="flex flex-col gap-3">
      {filterBox}
      <nav aria-label={label} className="flex flex-col gap-2">
        {groups.map((group, position) => {
          const headingId = `${id}-${phone ? "p" : "w"}-${String(position)}`;
          const previous = groups[position - 1];
          const newBlock = previous !== undefined && previous.scopeKey !== group.scopeKey;
          const titled = group.heading !== undefined || group.scope !== undefined;
          return titled ? (
            <div key={group.key} className={cx("flex flex-col gap-1", position > 0 && !newBlock && "mt-3", newBlock && "mt-4 border-t border-border pt-4")}>
              <p id={headingId} className="flex min-w-0 items-center gap-1.5 px-2.5 text-12 font-medium text-fg-muted">
                {/* The spaces are the heading's name for assistive technology: the layout's gap is not text. */}
                {group.scope !== undefined ? (
                  <>
                    <span className="min-w-0 truncate text-fg">{group.scope}</span>{" "}
                    {group.heading !== undefined ? <span aria-hidden="true">·</span> : null}{" "}
                  </>
                ) : null}
                {group.heading !== undefined ? <span className="min-w-0 truncate">{group.heading}</span> : null}
              </p>
              <NavList items={group.items} phone={phone} labelledBy={headingId} />
            </div>
          ) : (
            <NavList key={group.key} items={group.items} phone={phone} />
          );
        })}
        {danger.length > 0 ? (
          <div className="border-t border-border pt-2">
            <NavList items={danger} phone={phone} />
          </div>
        ) : null}
        {narrowing && kept.length === 0 ? (
          <p role="status" className="px-2.5 text-13 text-fg-muted">
            {t("common.settingsLayout.noMatches")}
          </p>
        ) : null}
      </nav>
    </div>
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
          {/* A list of twenty-four sections taller than the screen scrolls inside it, below the top bar. The padding keeps the focus ring of its edges from being cut. */}
          {/* design-exception: arbitrary-dimension no token is a height relative to the viewport but the editor's */}
          <div className="sticky top-20 -m-1 max-h-[calc(100dvh-6rem)] overflow-y-auto p-1 scroll-thin">{nav(false)}</div>
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
