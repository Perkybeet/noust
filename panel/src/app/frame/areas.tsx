/**
 * The areas of the main bar as a page draws them, whichever bar it is (the rail of the double bar, the
 * one bar, the foot of a phone): which are offered, where each opens, which is current and what is
 * worth counting beside it. One answer, so the three bars never disagree about any of it.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import type { LucideIcon } from "lucide-react";

import { activeJobsQuery } from "../../api/queries/jobs";
import { StatusGlyph } from "../../components/ui/StatusPill";
import { Tooltip } from "../../components/ui/Tooltip";
import { isLocalOnlyPath, useCentral } from "../../features/central/central";
import { useAttention } from "../../features/inbox/useAttention";
import { iconOf } from "../../features/nav/icons";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { useReturnServer } from "../../nodes/servers";
import { useConsoleContext } from "../../nodes/useNode";
import { areaOf, areaTarget, dest, sidebarFor } from "../nav";
import type { ConsolePath, Dest, Scope } from "../nav";
import { useNavScope } from "../useNavScope";

/** What to say beside an area: how many have failed, how many are about to, how many are running. */
export interface AreaCounts {
  failed: number;
  expiring: number;
  running: number;
}

export interface AreaItem {
  entry: Dest;
  to: ConsolePath;
  /** The server the link opens, when the page on screen is the fleet's or the central's and the area has no face there. */
  node?: string | null;
  Icon: LucideIcon;
  current: boolean;
  counts: AreaCounts;
}

export interface Areas {
  /** The areas in their groups, Settings not among them. */
  groups: AreaItem[][];
  settings: AreaItem;
  scope: Scope;
}

const NONE: AreaCounts = { failed: 0, expiring: 0, running: 0 };

/** What is running now (a deploy, a backup, an update): the jobs the server has queued or is running, kept current by the `job` events. */
function useRunningJobs(enabled: boolean): number {
  const { data } = useQuery({ ...activeJobsQuery(), enabled, retry: false });
  return enabled && data !== undefined ? data.jobs.length : 0;
}

/** Counts by area, from the same list the inbox shows (NV-16), so the bell and the bar never disagree. */
function useCounts(enabled: boolean): Readonly<Record<string, AreaCounts>> {
  const { counters } = useAttention();
  const running = useRunningJobs(enabled);
  if (!enabled) return {};
  return {
    apps: { ...NONE, failed: counters.appsFailed },
    server: { ...NONE, failed: counters.unitsFailed },
    domains: { ...NONE, failed: counters.certificatesExpired, expiring: counters.certificates - counters.certificatesExpired },
    alerts: { ...NONE, failed: counters.alerts },
    activity: { ...NONE, running },
  };
}

/**
 * The bar's areas for the page on screen. On a hub, which deploys nothing of its own, the areas about a
 * machine's deployments are left out of a server's pages (they only redirect to the fleet), and from the
 * fleet's pages the ones with no fleet face lead back to the server the person came from (what Settings has always done).
 */
export function useAreas(): Areas {
  const context = useConsoleContext();
  const { role } = useCentral();
  const back = useReturnServer();
  const scope = useNavScope();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const counts = useCounts(scope === "server");
  const onHub = role === "hub" && context.kind === "server" && context.node === null;
  const current = areaOf(pathname);
  // The server the area opens, when it opens one from a page that is no server's.
  const node = context.kind === "server" ? undefined : (back?.node ?? null);

  const item = (entry: Dest): AreaItem => {
    const face = scope === "fleet" && entry.fleetTo !== undefined;
    return {
      entry,
      to: areaTarget(entry, scope),
      ...(scope === "fleet" && !face && node !== undefined ? { node } : {}),
      Icon: iconOf(entry),
      current: current?.id === entry.id,
      counts: counts[entry.id] ?? NONE,
    };
  };
  const offered = (entry: Dest): boolean => !onHub || (entry.to !== "/" && !isLocalOnlyPath(entry.to));
  return {
    groups: sidebarFor()
      .map((group) => group.filter(offered).map(item))
      .filter((group) => group.length > 0),
    settings: { ...item(dest("settings")), ...(context.kind === "server" ? {} : { node: back?.node ?? null }) },
    scope,
  };
}

const DOT = "inline-flex min-w-5 items-center justify-center rounded-pill px-1 text-12 leading-5 font-semibold tabular-nums";

/** The counts beside an area's name, each with a shape and, for a screen reader, a sentence. */
function Counts({ counts, folded }: { counts: AreaCounts; folded: boolean }) {
  const t = useT();
  // Spaces between the parts keep the accessible name "Servers 1 failed"; the visible number is read once, inside that sentence.
  return (
    <>
      {counts.failed > 0 ? " " : null}
      {counts.failed > 0 ? (
        <span className={cx(DOT, "bg-fail-strong text-on-fail", folded && "absolute top-0.5 right-0.5 min-w-4 text-12 leading-4")}>
          <span aria-hidden="true">{counts.failed}</span>
          <span className="sr-only">{t("nav.failed", { count: counts.failed })}</span>
        </span>
      ) : null}
      {counts.expiring > 0 ? " " : null}
      {counts.expiring > 0 ? (
        <span className={cx("inline-flex items-center gap-1 text-12 font-medium text-warn", folded && "absolute top-0.5 right-0.5")}>
          <StatusGlyph state="warning" size={10} />
          <span aria-hidden="true" className={cx("tabular-nums", folded && "sr-only")}>
            {counts.expiring}
          </span>
          <span className="sr-only">{t("nav.expiring", { count: counts.expiring })}</span>
        </span>
      ) : null}
      {counts.running > 0 ? " " : null}
      {counts.running > 0 ? (
        <span className={cx("inline-flex items-center gap-1 text-12 font-medium text-warn", folded && "absolute top-0.5 right-0.5")}>
          <StatusGlyph state="deploying" size={10} />
          <span aria-hidden="true" className={cx("tabular-nums", folded && "sr-only")}>
            {counts.running}
          </span>
          <span className="sr-only">{t("nav.running", { count: counts.running })}</span>
        </span>
      ) : null}
    </>
  );
}

/**
 * The current item is the raised piece under it, which slides to the one chosen (SlidingIndicator, in
 * the list that holds the links); the others rise when the pointer is over them. `aria-current` is the
 * item's own state, set from the one answer `areaOf` gives, so an area stays current beneath its own
 * pages and a link never has to guess from its address.
 */
const ROW =
  "group elev-lift relative flex items-center rounded-control text-fg-muted " +
  "hover:bg-surface-raised hover:text-fg focus-visible:-outline-offset-2 " +
  "aria-[current=page]:bg-transparent aria-[current=page]:text-fg aria-[current=page]:hover:transform-none aria-[current=page]:hover:shadow-none";

export type AreaLinkVariant = "rail" | "folded" | "single" | "bottom";

const VARIANT: Record<AreaLinkVariant, string> = {
  rail: "h-10 gap-3 px-3 text-14 font-medium",
  folded: "size-11 justify-center text-14 font-medium",
  single: "h-9 gap-2.5 px-3 text-14 font-medium",
  // A name that needs two lines (Bases de datos) takes them, from the top, so every icon of the foot is on one line.
  bottom: "h-14 w-full flex-col justify-start gap-0.5 px-1 pt-1.5 text-12 font-medium",
};

export function AreaLink({
  item,
  variant,
  label,
  onNavigate,
}: {
  item: AreaItem;
  variant: AreaLinkVariant;
  /** Overrides the area's name where the room is short (the foot of a phone). */
  label?: string;
  onNavigate?: (() => void) | undefined;
}) {
  const t = useT();
  const name = label ?? t(item.entry.label);
  const folded = variant === "folded";
  const link = (
    <Link
      to={item.to}
      {...(item.node !== undefined ? { search: { node: item.node ?? undefined } } : {})}
      activeOptions={{ exact: true, includeSearch: false }}
      {...(onNavigate !== undefined ? { onClick: onNavigate } : {})}
      {...(item.current ? { "aria-current": "page" as const } : {})}
      className={cx(ROW, VARIANT[variant])}
    >
      <item.Icon aria-hidden="true" className="size-icon-lg shrink-0 text-fg-faint group-hover:text-fg-muted group-aria-[current=page]:text-accent-fg" />
      {folded ? <span className="sr-only">{name}</span> : <span className={variant === "bottom" ? "max-w-full text-center leading-4" : "min-w-0 flex-1 truncate"}>{name}</span>}
      {variant === "bottom" ? null : <Counts counts={item.counts} folded={folded} />}
    </Link>
  );
  // A folded bar says each name on hover and on focus: touch has no hover, which is why only the rail does it, and only folded.
  return folded ? (
    <Tooltip content={name} side="right">
      {link}
    </Tooltip>
  ) : (
    link
  );
}
