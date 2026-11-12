import { useParams, useRouter, useRouterState } from "@tanstack/react-router";
import { ChevronDown } from "lucide-react";
import { Suspense, lazy, useEffect, useState } from "react";

import { VIEWS } from "../../features/nav/views";
import { useHomeAddress } from "../../features/overview/search";
import { Drawer } from "../../components/ui/Drawer";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { RESOURCE_PARENT } from "../columns";
import type { ColumnKind } from "../columns";
import { DESTS, areaLabelKey, dest } from "../nav";
import { useNavScope } from "../useNavScope";
import { columnLabel } from "./columnBodies";
import { ColumnContent } from "./NavColumn";

/** The pages that are the first section of their list: named for what they are in it ("Overview"), not for what they head. */
const HEADS: ReadonlySet<string> = new Set(["app", "database", "service", "team", "account"]);

/** What the selector says it is: the resource's name, or the area's. */
function useTitle(kind: ColumnKind): string {
  const t = useT();
  const params = useParams({ strict: false });
  const { node } = useNode();
  const scope = useNavScope();
  switch (kind) {
    case "app":
      return params.domain ?? "";
    case "database":
    case "service":
      return params.name ?? "";
    case "team":
      return params.team ?? "";
    case "account":
      return t("account.title");
    case "server":
      // "Server" over one server and "Servers" from All servers, as the bar and the trail say it (item 154).
      return node ?? t(areaLabelKey(dest("server"), scope));
    default:
      return t(dest(kind).label);
  }
}

// Home's sections are its panels, whose names are the person's own: read where the panels are, in a chunk of their own.
const HomePanelName = lazy(() => import("./HomePanelName"));

/** The line under "Home" on a phone: the panel that is open, which is the server's or the fleet's until the panels are known. */
function HomeSection() {
  const t = useT();
  const { scope } = useHomeAddress();
  return (
    <Suspense fallback={t(scope === "fleet" ? "overview.panels.fleet" : "overview.panels.server")}>
      <HomePanelName />
    </Suspense>
  );
}

/**
 * The section the page is: the register's name for the route the page is (or the one it is opened from), or
 * the view of the list that the address chose.
 */
function useSection(kind: ColumnKind): string | null {
  const t = useT();
  const route = useRouterState({ select: (state) => state.matches.at(-1)?.fullPath ?? "" });
  const search = useRouterState({ select: (state) => state.location.search as Record<string, unknown> });
  const path = route.length > 1 ? route.replace(/\/+$/, "") : route;
  const views = VIEWS[kind];
  if (views !== undefined && path === dest(kind).to) {
    const chosen = views.find((view) => view.search !== undefined && Object.entries(view.search).every(([key, value]) => search[key] === value)) ?? views[0];
    return chosen === undefined ? null : t(chosen.label);
  }
  const own = DESTS.filter((entry) => path === entry.to || path.startsWith(`${entry.to === "/" ? "" : entry.to}/`)).sort((a, b) => b.to.length - a.to.length)[0];
  if (own === undefined) return null;
  return t(own.kind === "area" || HEADS.has(own.id) ? (own.rootLabel ?? own.label) : own.label);
}

/**
 * On a phone the column is a selector at the top of the page: where you are and which section, and a
 * press opens the same list as a sheet from the foot. Never a row of tabs (NV-23). A resource's sheet has
 * its way back and its switch, because it is the column's own body.
 */
export function SectionSelector({ kind }: { kind: ColumnKind }) {
  const t = useT();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const title = useTitle(kind);
  const section = useSection(kind);
  const resource = kind in RESOURCE_PARENT;

  // Choosing a section is leaving the sheet: it closes with the page that arrives.
  useEffect(
    () =>
      router.subscribe("onRendered", (event) => {
        if (event.pathChanged || event.hrefChanged) setOpen(false);
      }),
    [router],
  );

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        className="elev-raised mb-4 flex w-full cursor-pointer items-center gap-3 rounded-card bg-surface-raised px-3.5 py-2.5 text-left"
      >
        <span className="min-w-0 flex-1">
          <span translate={resource ? "no" : undefined} className="block truncate text-12 text-fg-muted">
            {title}
          </span>
          <span className="block truncate text-14 font-medium text-fg">{kind === "overview" ? <HomeSection /> : (section ?? title)}</span>
        </span>
        <span className="text-12 text-fg-muted">{t("nav.column.sections")}</span>
        <ChevronDown aria-hidden="true" className="size-icon-md shrink-0 text-fg-faint" />
      </button>
      <Drawer open={open} onOpenChange={setOpen} placement="bottom" title={resource ? columnLabel(kind, t, title) : kind === "overview" ? t("overview.panels.list") : t("nav.column.sectionsOf", { area: title })}>
        <div className="flex min-h-0 flex-1 flex-col">
          <ColumnContent kind={kind} />
        </div>
      </Drawer>
    </>
  );
}
