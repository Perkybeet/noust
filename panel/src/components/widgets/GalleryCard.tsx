/**
 * A card of the gallery: a row with the widget's icon, its name and the "+" that adds it, and under it what it answers
 * and the sizes it takes, across the whole card. Nothing is cut: the description takes the width the card has, which
 * the icon and the "+" beside it used to leave a third short of. The card is also what a pointer takes hold of to bring
 * the widget to the place it is wanted (`onPointerDown`).
 */
import { Check } from "lucide-react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { IconButton } from "../ui/IconButton";
import { ICONS } from "../ui/icons";
import type { WidgetDef } from "./catalog";
import type { SizeKey } from "./model";
import { SizeGlyph } from "./SizeGlyph";

export interface GalleryCardProps {
  widget: WidgetDef;
  /** The panel already has one of this kind. */
  present: boolean;
  /** The panel holds as many as it may. */
  full: boolean;
  /** Whether a pointer can take hold of the card, for the grab cursor. */
  grabbable: boolean;
  onAdd: () => void;
  onPointerDown: ((event: ReactPointerEvent<HTMLLIElement>) => void) | undefined;
}

/** The sizes a widget takes, drawn as their proportion across the card; the one it is added at is the sunken one. */
function Sizes({ widget }: { widget: WidgetDef }) {
  const t = useT();
  return (
    <ul className="mt-2 flex items-center gap-1.5" aria-label={t("widgets.gallery.sizes")}>
      {widget.sizes.map((size: SizeKey, index) => (
        <li key={size} className={cx("flex h-6 min-w-0 flex-1 items-center justify-center rounded-chip text-fg-faint", index === 0 && "bg-bg-sunken text-fg")}>
          <SizeGlyph size={size} />
          <span className="sr-only">{t(`widgets.sizes.${size}`)}</span>
        </li>
      ))}
    </ul>
  );
}

export function GalleryCard({ widget, present, full, grabbable, onAdd, onPointerDown }: GalleryCardProps) {
  const t = useT();
  const title = t(`widgets.${widget.type}.title`);
  return (
    // Positioned, so that what the card keeps for a screen reader (position: absolute) is placed against the card.
    <li onPointerDown={onPointerDown} className={cx("elev-raised relative rounded-card bg-surface-raised px-3 py-2.5", grabbable && "cursor-grab")}>
      <div className="flex items-center gap-2.5">
        <widget.icon aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />
        <p className="flex min-w-0 flex-1 items-center gap-1.5 text-13 font-medium text-fg">
          <span className="min-w-0 truncate">{title}</span>
          {present ? (
            <>
              <Check aria-hidden="true" className="size-icon-sm shrink-0 text-fg-muted" />
              <span className="sr-only">{t("widgets.gallery.inPanel")}</span>
            </>
          ) : null}
        </p>
        <IconButton
          label={t("widgets.gallery.add", { title })}
          icon={<ICONS.add />}
          size="sm"
          disabled={full}
          onClick={onAdd}
          // The button is 32px in a row of 20: it takes its room from the card's padding, not from its height.
          className="-my-1.5 -mr-1.5 [&_svg]:text-accent-fg"
        />
      </div>
      <p className="mt-1 text-12 text-pretty text-fg-muted">{t(`widgets.${widget.type}.line`)}</p>
      <Sizes widget={widget} />
    </li>
  );
}
