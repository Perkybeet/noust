/**
 * The gallery a person adds widgets from while a panel is customised: the widgets their role may have in a panel
 * of this scope, grouped by what they are about, each with what it answers and the sizes it takes drawn as their
 * proportion. One is dragged onto the panel, or added with its "+" (which needs no dragging, WCAG 2.5.7): it goes
 * at the end of the panel at its first size, and the size is changed there. One already in the panel has a check, and
 * can be added again (a second chart of another metric is a reason to).
 */
import { Check, Search } from "lucide-react";
import { useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { IconButton } from "../ui/IconButton";
import { ICONS } from "../ui/icons";
import { Input } from "../ui/Input";
import { CATEGORY_ORDER, offeredWidgets } from "./catalog";
import type { WidgetDef } from "./catalog";
import type { Placed, SizeKey } from "./model";
import { SizeGlyph } from "./SizeGlyph";
import type { PanelScope } from "./types";

export interface GalleryProps {
  scope: PanelScope;
  /** What the credential holds: a widget it may not see is not listed. */
  can: (permission: string) => boolean;
  /** What is in the panel now, for the check beside what is already there. */
  layout: readonly Placed[];
  /** The panel holds as many as it may: a widget is not added, and the gallery says why. */
  full: boolean;
  onAdd: (widget: WidgetDef) => void;
  /** Takes hold of a widget with a pointer; not given where there is nowhere to drop it (a sheet over the page). */
  onStartDrag?: (widget: WidgetDef, event: ReactPointerEvent<HTMLElement>) => void;
}

function Sizes({ widget }: { widget: WidgetDef }) {
  const t = useT();
  return (
    <ul className="mt-1.5 flex items-center gap-1.5" aria-label={t("widgets.gallery.sizes")}>
      {widget.sizes.map((size: SizeKey, index) => (
        <li key={size} className={cx("flex h-6 items-center rounded-chip px-1 text-fg-faint", index === 0 && "bg-bg-sunken text-fg")}>
          <SizeGlyph size={size} />
          <span className="sr-only">{t(`widgets.sizes.${size}`)}</span>
        </li>
      ))}
    </ul>
  );
}

/** Whether a widget answers what was typed: its name, what it says it answers, or what group it is in. */
function answers(t: ReturnType<typeof useT>, widget: WidgetDef, query: string): boolean {
  const words = query.trim().toLocaleLowerCase(t.locale).split(/\s+/).filter(Boolean);
  const haystack = `${t(`widgets.${widget.type}.title`)} ${t(`widgets.${widget.type}.line`)} ${t(`widgets.categories.${widget.category}`)}`.toLocaleLowerCase(t.locale);
  return words.every((word) => haystack.includes(word));
}

export function Gallery({ scope, can, layout, full, onAdd, onStartDrag }: GalleryProps) {
  const t = useT();
  const [query, setQuery] = useState("");
  const present = new Set(layout.map((placed) => placed.type));
  const offered = offeredWidgets(scope, can).filter((widget) => query.trim() === "" || answers(t, widget, query));
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
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4 scroll-thin">
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
                  <li
                    key={widget.type}
                    // A pointer takes hold of the card; a finger scrolls the list, so touch has "+" only.
                    onPointerDown={
                      onStartDrag === undefined || full
                        ? undefined
                        : (event) => {
                            if (event.pointerType !== "mouse" || (event.target as HTMLElement).closest("button") !== null) return;
                            onStartDrag(widget, event);
                          }
                    }
                    className={cx("elev-raised flex items-start gap-3 rounded-card bg-surface-raised p-3", onStartDrag !== undefined && !full && "cursor-grab")}
                  >
                    <widget.icon aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
                    <div className="min-w-0 flex-1">
                      <p className="flex items-center gap-1.5 text-13 font-medium text-fg">
                        <span className="min-w-0 truncate">{t(`widgets.${widget.type}.title`)}</span>
                        {present.has(widget.type) ? (
                          <>
                            <Check aria-hidden="true" className="size-icon-sm shrink-0 text-fg-muted" />
                            <span className="sr-only">{t("widgets.gallery.inPanel")}</span>
                          </>
                        ) : null}
                      </p>
                      <p className="text-12 text-pretty text-fg-muted">{t(`widgets.${widget.type}.line`)}</p>
                      <Sizes widget={widget} />
                    </div>
                    <IconButton
                      label={t("widgets.gallery.add", { title: t(`widgets.${widget.type}.title`) })}
                      icon={<ICONS.add />}
                      size="sm"
                      disabled={full}
                      onClick={() => onAdd(widget)}
                      className="-mr-1 -mt-1 [&_svg]:text-accent-fg"
                    />
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>
    </div>
  );
}
