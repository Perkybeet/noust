/**
 * The gallery a person adds widgets from while a panel is customised: the widgets their role may have in a panel
 * of this scope, grouped by what they are about, each with what it answers and the sizes it takes drawn as their
 * proportion. One is dragged to the place it is wanted in the panel (the panel shows where it will land, at its
 * real size), or added with its "+" (which needs no dragging, WCAG 2.5.7): it goes at the end of the panel at its
 * first size, and the size is changed there. One already in the panel has a check, and can be added again (a second
 * chart of another metric is a reason to). The cards are `GalleryCard`.
 */
import { Search } from "lucide-react";
import { useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { useT } from "../../i18n";
import { Input } from "../ui/Input";
import { CATEGORY_ORDER, offeredWidgets } from "./catalog";
import type { Offers } from "./catalog";
import type { WidgetDef } from "./catalog";
import { GalleryCard } from "./GalleryCard";
import type { Placed } from "./model";
import type { PanelScope } from "./types";

export interface GalleryProps {
  scope: PanelScope;
  /** What the credential holds: a widget it may not see is not listed. */
  can: (permission: string) => boolean;
  /** What the server offers (`useScopeOffers`): a widget that reads what it cannot do is not listed. Absent: every widget is. */
  features?: Offers;
  /** What is in the panel now, for the check beside what is already there. */
  layout: readonly Placed[];
  /** The panel holds as many as it may: a widget is not added, and the gallery says why. */
  full: boolean;
  onAdd: (widget: WidgetDef) => void;
  /** Takes hold of a widget with a pointer; not given where there is nowhere to drop it (a sheet over the page). */
  onStartDrag?: (widget: WidgetDef, event: ReactPointerEvent<HTMLElement>) => void;
}

/** Whether a widget answers what was typed: its name, what it says it answers, or what group it is in. */
function answers(t: ReturnType<typeof useT>, widget: WidgetDef, query: string): boolean {
  const words = query.trim().toLocaleLowerCase(t.locale).split(/\s+/).filter(Boolean);
  const haystack = `${t(`widgets.${widget.type}.title`)} ${t(`widgets.${widget.type}.line`)} ${t(`widgets.categories.${widget.category}`)}`.toLocaleLowerCase(t.locale);
  return words.every((word) => haystack.includes(word));
}

export function Gallery({ scope, can, features, layout, full, onAdd, onStartDrag }: GalleryProps) {
  const t = useT();
  const [query, setQuery] = useState("");
  const present = new Set(layout.map((placed) => placed.type));
  const offered = offeredWidgets(scope, can, features).filter((widget) => query.trim() === "" || answers(t, widget, query));
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 px-3 pb-3">
        <Input
          type="search"
          size="sm"
          aria-label={t("widgets.gallery.search")}
          placeholder={t("widgets.gallery.searchPlaceholder")}
          value={query}
          onValueChange={(value: string) => setQuery(value)}
          icon={<Search />}
          autoComplete="off"
          spellCheck={false}
        />
      </div>
      {full ? (
        <p role="status" className="shrink-0 px-4 pb-3 text-13 text-fg-muted">
          {t("widgets.gallery.full")}
        </p>
      ) : null}
      {/* Positioned, so that the labels a card keeps for a screen reader (position: absolute) are placed against this list and not against the column, which is as tall as the window: against the column they stretched the page. */}
      <div className="relative min-h-0 flex-1 overflow-y-auto px-3 pb-4 scroll-thin">
        {offered.length === 0 ? (
          <p role="status" className="px-1 text-13 text-fg-muted">
            {t("widgets.gallery.none", { query: query.trim() })}
          </p>
        ) : null}
        {CATEGORY_ORDER.map((category) => {
          const widgets = offered.filter((widget) => widget.category === category);
          if (widgets.length === 0) return null;
          return (
            <section key={category} aria-label={t(`widgets.categories.${category}`)} className="mb-4">
              <h3 className="px-1 pb-2 text-13 font-medium text-fg-muted">{t(`widgets.categories.${category}`)}</h3>
              <ul className="flex flex-col gap-2">
                {widgets.map((widget) => (
                  <GalleryCard
                    key={widget.type}
                    widget={widget}
                    present={present.has(widget.type)}
                    full={full}
                    grabbable={onStartDrag !== undefined && !full}
                    onAdd={() => onAdd(widget)}
                    // A pointer takes hold of the card, and a finger after a long press, since a finger that moves scrolls the list.
                    onPointerDown={
                      onStartDrag === undefined || full
                        ? undefined
                        : (event) => {
                            if (event.button !== 0 || (event.target as HTMLElement).closest("button") !== null) return;
                            onStartDrag(widget, event);
                          }
                    }
                  />
                ))}
              </ul>
            </section>
          );
        })}
      </div>
    </div>
  );
}
