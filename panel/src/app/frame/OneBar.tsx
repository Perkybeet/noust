import { Link } from "@tanstack/react-router";
import { useId } from "react";

import { Logo } from "../../components/brand/Logo";
import { SlidingIndicator } from "../../components/ui/SlidingIndicator";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { cx } from "../../lib/cx";
import { areaTarget } from "../nav";
import type { NavLayout } from "../navLayout";
import { AreaLink, useAreas } from "./areas";
import type { AreaItem } from "./areas";
import { PresentationSwitch } from "./PresentationSwitch";

/** What each group of the bar is called when the bar is one: the double bar separates them by space instead. */
const GROUP_TITLES: Readonly<Record<string, PlainKey>> = {
  runs: "nav.groups.runs",
  reach: "nav.groups.reach",
  happens: "nav.groups.happens",
  safe: "nav.groups.safe",
  where: "nav.groups.where",
  pinned: "nav.groups.manage",
};

function Group({ title, items, onNavigate }: { title: PlainKey | undefined; items: readonly AreaItem[]; onNavigate: (() => void) | undefined }) {
  const t = useT();
  const id = useId();
  return (
    <section className="flex flex-col gap-0.5">
      {title !== undefined ? (
        <p id={id} className="px-3 pb-1 text-13 font-semibold text-fg-muted">
          {t(title)}
        </p>
      ) : null}
      <ul aria-labelledby={title !== undefined ? id : undefined} className="flex flex-col gap-0.5">
        {items.map((item) => (
          <li key={item.entry.id}>
            <AreaLink item={item} variant="single" onNavigate={onNavigate} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The areas as one list, each group under its title: the navigation of one bar, which is also what a
 * phone's menu holds. The current area is the raised piece under it, which slides to the one chosen.
 */
export function OneBarNav({ onNavigate, className }: { onNavigate?: () => void; className?: string }) {
  const t = useT();
  const areas = useAreas();
  return (
    <nav aria-label={t("nav.landmarks.main")} className={cx("relative isolate flex flex-col gap-4", className)}>
      <SlidingIndicator className="rounded-control bg-surface" />
      {areas.groups.map((group) => {
        const key = group[0]?.entry.group ?? "";
        return <Group key={key || group[0]?.entry.id} title={GROUP_TITLES[key]} items={group} onNavigate={onNavigate} />;
      })}
      <Group title={GROUP_TITLES["pinned"]} items={[areas.settings]} onNavigate={onNavigate} />
    </nav>
  );
}

/**
 * One bar: every area, named and grouped, at the side of the page, and the sections of the area the
 * person is in are tabs in the page itself. It is what a window under 1280 px has (two columns need about
 * 1410), and what anyone may choose above it, with the choice drawn at the foot.
 */
export function OneBar({ layout }: { layout: NavLayout }) {
  const t = useT();
  const areas = useAreas();
  const home = areas.groups[0]?.[0];
  return (
    <aside aria-label={t("nav.presentation.label")} className="sticky top-0 flex h-dvh w-sidebar shrink-0 flex-col bg-bg-column px-3 py-3.5">
      <Link
        to={home === undefined ? "/" : areaTarget(home.entry, areas.scope)}
        aria-label={t("shell.sidebarOverview")}
        className="flex h-11 shrink-0 items-center rounded-control px-2.5"
      >
        <Logo variant="wordmark" height={18} decorative />
      </Link>
      <div className="mt-3.5 -mx-1 min-h-0 flex-1 overflow-y-auto px-1 scroll-thin">
        <OneBarNav />
      </div>
      {layout.tier === "narrow" ? null : (
        <div className="mt-2 shrink-0">
          <PresentationSwitch presentation="single" />
        </div>
      )}
    </aside>
  );
}
