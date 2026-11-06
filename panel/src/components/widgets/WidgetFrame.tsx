import type { ReactNode } from "react";

import { cx } from "../../lib/cx";
import { Card } from "../ui/Card";
import type { SizeKey } from "./model";
import type { WidgetFace } from "./types";

export interface WidgetFrameProps {
  face: WidgetFace;
  size: SizeKey;
  /** Replaces the link in the head: the controls of a widget while the panel is being arranged. */
  controls?: ReactNode;
  /** The panel is being arranged: the body is only to look at, so a press on it is a press on the widget. */
  editing?: boolean;
  className?: string;
}

/**
 * The slab a widget is drawn in: its head (an icon, its title and a link, or its controls while it is
 * arranged) and its body, which is a size container (`widget-body`): the body asks how much room it has
 * and chooses what to show, so the same widget reads as a tile, a half page or a band. The slab fills its
 * cell, which has a fixed height, so a widget has its room from the first frame, loaded or not.
 */
export function WidgetFrame({ face, size, controls, editing = false, className }: WidgetFrameProps) {
  return (
    <Card as="article" padding="none" className={cx("h-full overflow-hidden", className)}>
      <div className="flex h-full flex-col">
        <header className="flex h-10 shrink-0 items-center gap-2 pr-2 pl-4">
          <face.icon aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />
          <h2 className="title min-w-0 flex-1 truncate text-14 text-fg">{face.title}</h2>
          {controls ?? face.link}
        </header>
        <div className="relative min-h-0 flex-1">
          <div className={cx("widget-body absolute inset-x-4 top-0 bottom-3.5", editing && "pointer-events-none")}>{face.body(size)}</div>
        </div>
      </div>
    </Card>
  );
}
