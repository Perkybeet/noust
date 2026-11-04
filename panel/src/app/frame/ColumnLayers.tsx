import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { REDUCED_MOTION, useMediaQuery } from "../../components/ui/useMediaQuery";
import { cx } from "../../lib/cx";

/** How long the longer of the two movements takes, with a frame to spare: the leaving layer goes after it. */
export const SLIDE_MS = 260;

type Direction = "forward" | "back";

interface Leaving {
  key: string;
  node: ReactNode;
  direction: Direction;
}

/**
 * The context column's body, which slides when the list it holds becomes another one a level deeper (an
 * application opened from the list) or a level up (back to the list): the new list comes in from the side
 * it lies on, decelerating, while the old one leaves the other way in less time, accelerating; the head
 * and the way back are part of what moves, so the column never disappears and a third never appears.
 * Between two lists of one depth (two areas, or two servers) it changes in place, and with reduced motion
 * it always does (the durations are 0 as well: app.css).
 *
 * `layerKey` names the list: a new key is a new list. The caller gives the node as a self-contained element
 * (it reads its own data), so the layer that is leaving keeps working for the 140 ms it takes to go.
 */
export function ColumnLayers({ layerKey, depth, children }: { layerKey: string; depth: number; children: ReactNode }) {
  const reduced = useMediaQuery(REDUCED_MOTION);
  const [current, setCurrent] = useState({ key: layerKey, depth, node: children });
  const [leaving, setLeaving] = useState<Leaving | null>(null);
  const [entering, setEntering] = useState<Direction | null>(null);

  // The list changed: derived during the render, so the old list never paints a frame in the new one's place.
  if (current.key !== layerKey) {
    const direction: Direction | null = depth > current.depth ? "forward" : depth < current.depth ? "back" : null;
    setCurrent({ key: layerKey, depth, node: children });
    if (direction !== null && !reduced) {
      setLeaving({ key: current.key, node: current.node, direction });
      setEntering(direction);
    } else {
      setLeaving(null);
      setEntering(null);
    }
  }

  const moving = leaving !== null || entering !== null;
  useEffect(() => {
    if (!moving) return;
    const timer = window.setTimeout(() => {
      setLeaving(null);
      setEntering(null);
    }, SLIDE_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [moving, layerKey]);

  return (
    <div data-column-layers="" className="grid min-h-0 flex-1 grid-cols-1 grid-rows-1 overflow-hidden">
      {leaving !== null ? (
        <div
          key={`leaving:${leaving.key}`}
          data-layer="leaving"
          aria-hidden="true"
          inert
          className={cx("pointer-events-none col-start-1 row-start-1 flex min-h-0 flex-col", leaving.direction === "forward" ? "column-exit-forward" : "column-exit-back")}
        >
          {leaving.node}
        </div>
      ) : null}
      <div
        key={current.key}
        data-layer="current"
        className={cx("col-start-1 row-start-1 flex min-h-0 flex-col", entering === "forward" && "column-enter-forward", entering === "back" && "column-enter-back")}
      >
        {children}
      </div>
    </div>
  );
}
