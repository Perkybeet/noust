import type { ReactNode } from "react";

import { cx } from "../../lib/cx";
import { ICONS } from "../ui/icons";
import { Input } from "../ui/Input";
import { Kbd } from "../ui/Kbd";

export interface FilterBarSearch {
  /** The text, kept in the URL (`q`) by the page so a filtered list can be shared. */
  value: string;
  onChange: (value: string) => void;
  /** The box's accessible name: "Search applications". */
  label: string;
  placeholder?: string;
}

export interface FilterBarProps {
  /** Names the search landmark: "Filter applications". */
  label: string;
  search?: FilterBarSearch;
  /** Selects (`size="sm"` is not needed: the bar sets the height) or a SegmentedControl. */
  filters?: ReactNode;
  /**
   * How many rows are shown, as a sentence from the catalog: "17 applications", "3 of 17
   * applications". Announced politely as it changes.
   */
  count?: ReactNode;
  /** Secondary actions for the list (Export, Refresh), at the right. Never the primary one. */
  actions?: ReactNode;
  className?: string;
}

/**
 * Search and filters above a list, the same on every T1 page: a search box of one fixed width
 * that the `/` shortcut focuses, the filters after it, the count and any secondary action at
 * the right. Nothing else goes between a page header and its table.
 */
export function FilterBar({ label, search, filters, count, actions, className }: FilterBarProps) {
  const Search = ICONS.search;
  return (
    <div role="search" aria-label={label} className={cx("flex min-w-0 flex-wrap items-center gap-2", className)}>
      {search !== undefined ? (
        <Input
          type="search"
          aria-label={search.label}
          {...(search.placeholder !== undefined ? { placeholder: search.placeholder } : {})}
          data-page-search=""
          value={search.value}
          onValueChange={(value: string) => search.onChange(value)}
          icon={<Search />}
          // The shortcut is for keyboards; a phone has no use for the hint.
          {...(search.value === "" ? { suffix: <Kbd className="pointer-coarse:hidden">/</Kbd> } : {})}
          className="w-full sm:w-search"
          autoComplete="off"
          spellCheck={false}
        />
      ) : null}
      {filters !== undefined ? <div className="flex flex-wrap items-center gap-2">{filters}</div> : null}
      {count !== undefined || actions !== undefined ? (
        <div className="ml-auto flex flex-wrap items-center justify-end gap-3">
          {count !== undefined ? (
            <p role="status" className="text-12 whitespace-nowrap text-fg-muted tabular-nums">
              {count}
            </p>
          ) : null}
          {actions}
        </div>
      ) : null}
    </div>
  );
}
