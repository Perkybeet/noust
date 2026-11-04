import { useT } from "../../i18n";
import { LoadingRegion } from "../page/LoadingRegion";
import { Skeleton } from "../ui/Skeleton";
import { slotsOf } from "./model";
import type { Placed } from "./model";
import { cellStyle, gridStyle } from "./WidgetGrid";

/**
 * The panel before it is known which it is: the cells of the one that comes with the console, each a slab of the
 * size its widget has, so that what arrives takes the room that was kept for it. Only the first visit to Home on a
 * browser that has never heard the server's panels waits like this; every other draws its own panel at once.
 */
export function PanelSkeleton({ layout, columns, title }: { layout: readonly Placed[]; columns: number; title: string }) {
  const t = useT();
  const slots = slotsOf(layout, columns);
  return (
    <LoadingRegion label={t("widgets.loading", { title })}>
      <div className="grid min-w-0" style={gridStyle(columns)}>
        {slots.map((slot, index) => (
          <div key={layout[index]?.id ?? index} style={cellStyle(slot)} className="min-w-0">
            <Skeleton className="h-full w-full rounded-card" />
          </div>
        ))}
      </div>
    </LoadingRegion>
  );
}
