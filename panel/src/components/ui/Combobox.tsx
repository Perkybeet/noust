import { Combobox as BaseCombobox } from "@base-ui/react/combobox";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Check, ChevronsUpDown } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode, RefObject } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { matchesComboboxQuery } from "./combobox.filter";
import { CONTROL_FRAME } from "./Input";
import { POPUP_MOTION } from "./Tooltip";

/** What every item of a combobox has: the value it stands for and the text that names it. */
export interface ComboboxItem {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface ComboboxProps<T extends ComboboxItem> {
  /**
   * The items, in the order they are listed. Any other text field they carry (a city, an
   * abbreviation, an offset) is searched by the default filter.
   */
  items: readonly T[];
  value?: string | null;
  defaultValue?: string | null;
  /** The value chosen, with its item. Never called with nothing: the field keeps a value. */
  onValueChange?: (value: string, item: T) => void;
  /** Lists the items under headings ("Europe", "America"), in the order groups first appear. */
  groupBy?: (item: T) => string;
  /**
   * Whether an item answers what was typed. The default matches every word in any text field,
   * ignoring case and accents, and reads `+2`, `utc+1` or `gmt-03:30` as a UTC offset.
   */
  filter?: (item: T, query: string) => boolean;
  /** The content of an option. Defaults to the label. The input always shows the label. */
  renderItem?: (item: T) => ReactNode;
  /**
   * Renders only the options in view. On by default beyond a hundred items (DESIGN 6.8), as
   * in the list of time zones; group headings are then drawn but not announced, so a
   * virtualized item's label should name its group (`Europe/Madrid` does).
   */
  virtualized?: boolean;
  placeholder?: string;
  /** Required when there is no enclosing Field. */
  "aria-label"?: string;
  name?: string;
  disabled?: boolean;
  size?: "sm" | "md";
  /** Sets labels in mono: the items are system values (zone names, units, paths). */
  mono?: boolean;
  /** What to say when nothing matches. Defaults to "Nothing matches this search." */
  emptyMessage?: string;
  className?: string;
}

interface ItemGroup<T> {
  value: string;
  items: T[];
}

type Row<T> = { kind: "group"; key: string; label: string } | { kind: "item"; key: string; item: T; index: number };

const ITEM_HEIGHT = 32;
const GROUP_HEIGHT = 28;

const OPTION =
  "flex cursor-pointer items-center gap-3 rounded-control py-1.5 pr-2 pl-2.5 text-13 outline-none select-none " +
  "data-disabled:cursor-not-allowed data-disabled:opacity-50 data-highlighted:bg-surface-hover";

const GROUP_LABEL = "px-2.5 pt-2 pb-1 text-12 font-medium text-fg-faint";

/** Groups in the order they first appear; items keep their order inside each one. */
function groupItems<T>(items: readonly T[], groupBy: (item: T) => string): ItemGroup<T>[] {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const key = groupBy(item);
    const list = groups.get(key);
    if (list) list.push(item);
    else groups.set(key, [item]);
  }
  return [...groups].map(([value, members]) => ({ value, items: members }));
}

function isGroup<T>(entry: T | ItemGroup<T>): entry is ItemGroup<T> {
  return typeof entry === "object" && entry !== null && "items" in entry && Array.isArray(entry.items);
}

function rowsOf<T extends ComboboxItem>(filtered: readonly (T | ItemGroup<T>)[]): Row<T>[] {
  const rows: Row<T>[] = [];
  let index = 0;
  for (const entry of filtered) {
    if (isGroup(entry)) {
      rows.push({ kind: "group", key: `group:${entry.value}`, label: entry.value });
      for (const item of entry.items) rows.push({ kind: "item", key: item.value, item, index: index++ });
    } else {
      rows.push({ kind: "item", key: entry.value, item: entry, index: index++ });
    }
  }
  return rows;
}

/** What a virtualized option needs from its row: its place, and the virtualizer's hooks. */
interface VirtualOption {
  index: number;
  total: number;
  style: CSSProperties;
  ref: (node: Element | null) => void;
  dataIndex: number;
}

interface VirtualRowsProps<T extends ComboboxItem> {
  /** The list that scrolls. A state, not a ref: the list mounts after these rows do. */
  scrollElement: HTMLDivElement | null;
  /** Filled by the rows: scrolls the item at a flat index into view. */
  revealRef: RefObject<((index: number) => void) | null>;
  renderOption: (item: T, extra: VirtualOption) => ReactNode;
}

/**
 * The options in view, out of however many match. Base UI keeps the highlight by index over
 * the whole filtered list, so the keyboard can reach an option that is not rendered yet; the
 * root asks this list to scroll it in.
 */
function VirtualRows<T extends ComboboxItem>({ scrollElement, revealRef, renderOption }: VirtualRowsProps<T>) {
  const filtered = BaseCombobox.useFilteredItems<T | ItemGroup<T>>();
  const rows = useMemo(() => rowsOf(filtered), [filtered]);
  const total = rows.reduce((count, row) => count + (row.kind === "item" ? 1 : 0), 0);

  // TanStack Virtual returns fresh functions each render; this component does not use the
  // React Compiler, so the library's memoisation caveat does not apply.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollElement,
    estimateSize: (index) => (rows[index]?.kind === "group" ? GROUP_HEIGHT : ITEM_HEIGHT),
    getItemKey: (index) => rows[index]?.key ?? index,
    overscan: 8,
  });

  useEffect(() => {
    revealRef.current = (index: number) => {
      const row = rows.findIndex((entry) => entry.kind === "item" && entry.index === index);
      if (row >= 0) virtualizer.scrollToIndex(row, { align: "auto" });
    };
    return () => {
      revealRef.current = null;
    };
  }, [rows, virtualizer, revealRef]);

  if (rows.length === 0) return null;
  return (
    <div role="presentation" className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
      {virtualizer.getVirtualItems().map((virtual) => {
        const row = rows[virtual.index];
        if (!row) return null;
        const style: CSSProperties = { position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${String(virtual.start)}px)` };
        if (row.kind === "group") {
          // Drawn for the eye; a virtualized listbox cannot keep its options inside group
          // elements, and an orphan heading would be read as an option-less group.
          return (
            <div key={virtual.key} data-index={virtual.index} ref={virtualizer.measureElement} aria-hidden="true" className={GROUP_LABEL} style={style}>
              {row.label}
            </div>
          );
        }
        return renderOption(row.item, {
          index: row.index,
          total,
          style,
          ref: virtualizer.measureElement,
          dataIndex: virtual.index,
        });
      })}
    </div>
  );
}

/**
 * Choose one value from a long list by typing: time zones, units, users. The list filters as
 * the operator types, the arrows move, Enter chooses and Escape closes, as the ARIA combobox
 * pattern describes. For a short, known list, use Select.
 */
export function Combobox<T extends ComboboxItem>({
  items,
  value,
  defaultValue,
  onValueChange,
  groupBy,
  filter,
  renderItem,
  virtualized,
  placeholder,
  "aria-label": ariaLabel,
  name,
  disabled = false,
  size = "md",
  mono = false,
  emptyMessage,
  className,
}: ComboboxProps<T>) {
  const t = useT();
  // The list is both state (the virtualizer needs to re-render once it exists) and a ref (the
  // input handler writes its scroll position).
  const [scrollElement, setScrollElement] = useState<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const attachList = useCallback((node: HTMLDivElement | null) => {
    listRef.current = node;
    setScrollElement(node);
  }, []);
  const revealRef = useRef<((index: number) => void) | null>(null);
  const virtual = virtualized ?? items.length > 100;

  const byValue = useMemo(() => new Map(items.map((item) => [item.value, item])), [items]);
  const grouped = useMemo(() => (groupBy ? groupItems(items, groupBy) : null), [items, groupBy]);
  const resolve = (key: string | null | undefined): T | null | undefined =>
    key === undefined ? undefined : key === null ? null : (byValue.get(key) ?? null);
  const selected = resolve(value);
  const initial = resolve(defaultValue);

  const matches = useCallback((item: T, query: string) => (filter ?? matchesComboboxQuery)(item, query), [filter]);

  const option = (
    item: T,
    extra?: VirtualOption,
  ): ReactNode => (
    <BaseCombobox.Item
      key={item.value}
      value={item}
      {...(item.disabled ? { disabled: true } : {})}
      {...(extra
        ? {
            index: extra.index,
            ref: extra.ref,
            style: extra.style,
            "data-index": extra.dataIndex,
            "aria-setsize": extra.total,
            "aria-posinset": extra.index + 1,
          }
        : {})}
      className={OPTION}
    >
      <span className="min-w-0 flex-1">
        {renderItem ? renderItem(item) : <span className={cx("block truncate", mono && "mono")}>{item.label}</span>}
      </span>
      {/* The check's room is kept on every row, so choosing one does not shift its text. */}
      <span className="flex size-4 shrink-0">
        <BaseCombobox.ItemIndicator className="flex text-accent-fg">
          <Check aria-hidden="true" className="size-4" />
        </BaseCombobox.ItemIndicator>
      </span>
    </BaseCombobox.Item>
  );

  return (
    <BaseCombobox.Root<T>
      items={grouped ?? items}
      filter={matches}
      itemToStringLabel={(item: T) => item.label}
      itemToStringValue={(item: T) => item.value}
      isItemEqualToValue={(a: T, b: T) => a.value === b.value}
      {...(selected !== undefined ? { value: selected } : {})}
      {...(initial !== undefined ? { defaultValue: initial } : {})}
      onInputValueChange={(_text: string, details: { reason: string }) => {
        // Base UI puts a plain list back at the top as the query changes; a virtualized one
        // owns its scroller, which would otherwise stay where the old selection was and show
        // the end of the new matches.
        if (virtual && details.reason === "input-change" && listRef.current) listRef.current.scrollTop = 0;
      }}
      onValueChange={(next: T | null) => {
        if (next !== null) onValueChange?.(next.value, next);
      }}
      {...(virtual
        ? {
            virtualized: true,
            onItemHighlighted: (_item: T | undefined, details: { reason: string; index: number }) => {
              if (details.reason !== "pointer" && details.index >= 0) revealRef.current?.(details.index);
            },
          }
        : {})}
      {...(name !== undefined ? { name } : {})}
      disabled={disabled}
    >
      <BaseCombobox.InputGroup
        className={cx("relative flex min-w-40 items-center", CONTROL_FRAME, size === "sm" ? "h-control-sm" : "h-control-md", className)}
      >
        <BaseCombobox.Input
          {...(ariaLabel !== undefined ? { "aria-label": ariaLabel } : {})}
          placeholder={placeholder ?? t("common.combobox.placeholder")}
          autoComplete="off"
          spellCheck={false}
          // The input holds the chosen label: selecting it on focus makes the first key typed
          // start a new search instead of appending to "Europe/Madrid". After the click that
          // focused it, which would otherwise put the caret back.
          onFocus={(event) => {
            const input = event.currentTarget;
            requestAnimationFrame(() => {
              if (document.activeElement === input) input.select();
            });
          }}
          className={cx(
            "h-full min-w-0 flex-1 rounded-control bg-transparent pr-8 pl-2.5 text-13 text-fg outline-none placeholder:text-fg-faint",
            "disabled:cursor-not-allowed",
            mono && "mono",
          )}
        />
        <BaseCombobox.Trigger
          aria-label={t("common.combobox.showOptions")}
          className="absolute inset-y-0 right-0 flex w-8 cursor-pointer items-center justify-center text-fg-faint outline-none data-disabled:cursor-not-allowed"
        >
          <ChevronsUpDown aria-hidden="true" className="size-3.5" />
        </BaseCombobox.Trigger>
      </BaseCombobox.InputGroup>
      <BaseCombobox.Portal>
        <BaseCombobox.Positioner sideOffset={4} className="z-overlay outline-none">
          <BaseCombobox.Popup
            className={cx(
              "flex max-h-(--available-height) min-w-(--anchor-width) flex-col rounded-card border border-border bg-surface-raised text-fg shadow-overlay outline-none",
              POPUP_MOTION,
            )}
          >
            <BaseCombobox.Status className="sr-only">
              <ResultCount />
            </BaseCombobox.Status>
            <BaseCombobox.Empty className="px-3 py-2.5 text-13 text-fg-muted empty:hidden">
              {emptyMessage ?? t("common.combobox.empty")}
            </BaseCombobox.Empty>
            <BaseCombobox.List
              ref={attachList}
              className="max-h-72 overflow-y-auto overscroll-contain p-1 scroll-thin empty:hidden"
            >
              {virtual ? (
                <VirtualRows<T>
                  scrollElement={scrollElement}
                  revealRef={revealRef}
                  renderOption={(item, extra) => option(item, extra)}
                />
              ) : grouped ? (
                (group: ItemGroup<T>) => (
                  <BaseCombobox.Group key={group.value} items={group.items}>
                    <BaseCombobox.GroupLabel className={GROUP_LABEL}>{group.value}</BaseCombobox.GroupLabel>
                    <BaseCombobox.Collection>{(item: T) => option(item)}</BaseCombobox.Collection>
                  </BaseCombobox.Group>
                )
              ) : (
                (item: T) => option(item)
              )}
            </BaseCombobox.List>
          </BaseCombobox.Popup>
        </BaseCombobox.Positioner>
      </BaseCombobox.Portal>
    </BaseCombobox.Root>
  );
}

/** "12 results.", read politely as the list narrows; nothing while every item is shown. */
function ResultCount() {
  const t = useT();
  const filtered = BaseCombobox.useFilteredItems<ComboboxItem | ItemGroup<ComboboxItem>>();
  const count = filtered.reduce((sum, entry) => sum + (isGroup(entry) ? entry.items.length : 1), 0);
  return <>{t("common.combobox.results", { count })}</>;
}
