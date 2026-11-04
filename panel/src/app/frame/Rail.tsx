import { Link } from "@tanstack/react-router";
import { PanelLeftClose, PanelLeftOpen, X } from "lucide-react";
import { useEffect, useId } from "react";

import { Logo } from "../../components/brand/Logo";
import { SlidingIndicator } from "../../components/ui/SlidingIndicator";
import { Tooltip } from "../../components/ui/Tooltip";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { areaTarget, dest } from "../nav";
import type { NavLayout } from "../navLayout";
import { setRailFold } from "../preferences";
import { AreaLink, useAreas } from "./areas";
import { AreaGroup, GROUP_TITLES } from "./Joint";

/**
 * The fold button: its name says what it does, and `aria-expanded` says what it is. Unfolded it folds the
 * bar to icons (a choice that is kept); folded it unfolds it; opened over the page, where there is no room
 * for it beside, it closes (which is not folding: nothing is saved, the bar was never unfolded).
 */
function FoldButton({ folded, open, controls, onPress }: { folded: boolean; open: boolean; controls: string; onPress: () => void }) {
  const t = useT();
  const label = open ? t("common.words.close") : folded ? t("nav.rail.unfold") : t("nav.rail.fold");
  const Icon = open ? X : folded ? PanelLeftOpen : PanelLeftClose;
  const button = (
    <button
      type="button"
      aria-expanded={!folded || open}
      aria-controls={controls}
      aria-label={folded && !open ? label : undefined}
      onClick={onPress}
      className={cx(
        "group elev-lift relative flex cursor-pointer items-center rounded-control text-14 font-medium text-fg-muted hover:bg-surface-raised hover:text-fg",
        "focus-visible:-outline-offset-2",
        folded && !open ? "size-11 justify-center" : "h-10 gap-3 px-3",
      )}
    >
      <Icon aria-hidden="true" className="size-icon-lg shrink-0 text-fg-faint group-hover:text-fg-muted" />
      {folded && !open ? null : <span className="min-w-0 flex-1 truncate text-left">{label}</span>}
    </button>
  );
  return folded && !open ? (
    <Tooltip content={label} side="right">
      {button}
    </Tooltip>
  ) : (
    button
  );
}

/**
 * The main bar: the areas with their icon and their name, in groups that carry a small title and a
 * joint, Settings under them, and its own control at the foot. It is drawn unfolded from 1440 px (an
 * icon is not a name: thirteen of them are not understood at first, and a phone has no tooltip) and folds
 * to icons by a button, which keeps the choice; folded, a group is only its joint. From 1024 to 1439 px
 * there is no room for it unfolded: it is folded and opens over the page without pushing it, and closes
 * by a choice, by Escape or by pressing the veil.
 *
 * The current area is the raised piece under it, which slides to the one chosen when the page changes
 * (SlidingIndicator): the bar and the second column each have their own.
 */
export function Rail({ layout, open, onOpenChange }: { layout: NavLayout; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const areas = useAreas();
  const navId = useId();
  // Over the page only while it is asked for and there is no room beside: a bar drawn unfolded takes its own.
  const over = layout.overlay && open;
  const folded = layout.rail === "folded" && !over;

  useEffect(() => {
    if (!over) return;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") onOpenChange(false);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, [over, onOpenChange]);

  const press = (): void => {
    if (layout.overlay) onOpenChange(!open);
    else setRailFold(layout.rail === "folded" ? "expanded" : "folded");
  };
  const home = areas.groups[0]?.[0]?.entry ?? dest("overview");
  const homeTarget = areaTarget(home, areas.scope);
  const variant = folded ? "folded" : "rail";
  const closeOver = over ? { onNavigate: () => onOpenChange(false) } : {};

  return (
    <div data-frame="rail" className={cx("relative shrink-0", layout.rail === "folded" ? "w-rail-folded" : "w-rail")}>
      {over ? (
        // The veil: the page is still there, under the bar, and pressing it is how the bar is dismissed (Escape is the keyboard's).
        <div aria-hidden="true" data-frame="veil" onClick={() => onOpenChange(false)} className="fixed inset-0 z-backdrop bg-backdrop" />
      ) : null}
      <aside
        aria-label={t("nav.rail.landmark")}
        className={cx(
          "flex h-dvh flex-col bg-bg-rail py-3.5",
          folded ? "items-center px-0" : "px-3",
          over ? "fixed top-0 left-0 z-overlay w-rail border-r border-border-strong" : "sticky top-0",
        )}
      >
        <Link
          to={homeTarget}
          aria-label={t("shell.sidebarOverview")}
          className={cx(
            "flex h-11 shrink-0 items-center rounded-control",
            folded ? "w-11 justify-center" : "px-2.5",
          )}
        >
          {folded ? <Logo variant="icon" height={26} decorative /> : <Logo variant="wordmark" height={18} decorative />}
        </Link>
        <nav
          id={navId}
          aria-label={t("nav.landmarks.main")}
          className={cx("relative isolate mt-3 flex min-h-0 flex-1 flex-col overflow-y-auto scroll-thin", folded ? "items-center" : "-mx-1 px-1")}
        >
          <SlidingIndicator className="rounded-control bg-surface" />
          {areas.groups.map((group) => (
            <AreaGroup key={group[0]?.entry.id} title={GROUP_TITLES[group[0]?.entry.group ?? ""]} folded={folded}>
              {group.map((item) => (
                <li key={item.entry.id}>
                  <AreaLink item={item} variant={variant} {...closeOver} />
                </li>
              ))}
            </AreaGroup>
          ))}
          <ul className="mt-auto pt-3">
            <li>
              <AreaLink item={areas.settings} variant={variant} {...closeOver} />
            </li>
          </ul>
        </nav>
        <div className={cx("mt-2 flex shrink-0 flex-col gap-1", folded && "items-center")}>
          <FoldButton folded={layout.rail === "folded"} open={over} controls={navId} onPress={press} />
        </div>
      </aside>
    </div>
  );
}
