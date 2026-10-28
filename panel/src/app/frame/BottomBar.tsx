import { Menu } from "lucide-react";

import { SlidingIndicator } from "../../components/ui/SlidingIndicator";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { AreaLink, useAreas } from "./areas";

/** The four areas a phone keeps one press away: where it starts, what it runs, its data and what is going on. */
const KEPT = ["overview", "apps", "databases", "activity"] as const;

/**
 * The bar at the foot of a phone: four areas and "More", which opens the rest. A phone has no room for the
 * main bar beside the page and no hover for an icon's name, so each is drawn with its name under it. The
 * current area is the raised piece, as in the other bars.
 */
export function BottomBar({ onOpenMore }: { onOpenMore: () => void }) {
  const t = useT();
  const areas = useAreas();
  const kept = KEPT.flatMap((id) => areas.groups.flat().filter((item) => item.entry.id === id));
  return (
    <nav aria-label={t("nav.bottomBar.label")} className="fixed inset-x-0 bottom-0 z-sticky bg-bg-rail px-2 py-1.5">
      <ul className="relative isolate flex items-stretch gap-0.5">
        <SlidingIndicator className="rounded-control bg-surface" />
        {kept.map((item) => (
          <li key={item.entry.id} className="min-w-0 flex-1">
            <AreaLink item={item} variant="bottom" {...(item.entry.id === "databases" ? { label: t("nav.bottomBar.databases") } : {})} />
          </li>
        ))}
        <li className="min-w-0 flex-1">
          <button
            type="button"
            onClick={onOpenMore}
            className={cx(
              "group elev-lift relative flex h-14 w-full cursor-pointer flex-col items-center justify-center gap-0.5 rounded-control px-1 text-12 font-medium text-fg-muted outline-none hover:bg-surface-raised hover:text-fg",
              "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus",
            )}
          >
            <Menu aria-hidden="true" className="size-icon-lg shrink-0 text-fg-faint group-hover:text-fg-muted" />
            <span className="max-w-full truncate">{t("nav.bottomBar.more")}</span>
          </button>
        </li>
      </ul>
    </nav>
  );
}
