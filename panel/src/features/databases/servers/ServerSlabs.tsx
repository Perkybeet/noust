import { Link } from "@tanstack/react-router";
import { useMemo } from "react";
import type { ReactNode } from "react";

import type { DbServer } from "../../../api/queries/databases";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { EmptyState } from "../../../components/ui/EmptyState";
import { LogoTile } from "../../../components/ui/LogoTile";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusGlyph, StatusPill, stateTextClass } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { TEXT_LINK } from "../ui";
import { canMoveUp, defaultMark, logoFor, placeOf, serverTitle, sortServers, supportLine, supportOf } from "../servers";
import type { SupportView } from "../servers";
import type { ServerActions } from "./useServerActions";

export interface ServerSlabsProps {
  servers: readonly DbServer[] | undefined;
  /** The machine's name, said of the servers that run on its system and in its containers. */
  machine: string;
  actions: ServerActions;
  /** What to do when there is none: "Another version" and installing an engine. */
  emptyAction?: ReactNode;
  /** The heading level of a server's name: 3 under a section's heading (the databases page), 2 directly under the page's title. */
  level?: 2 | 3;
  className?: string;
}

/** How a server that is not running says it: Docker's own word becomes the nearest state of the console. */
function stateOf(t: T, server: DbServer): { state: Status; label: string } | null {
  if (server.running) return null;
  switch (server.state) {
    case "stopped":
      return { state: "stopped", label: t("common.statusPill.stopped") };
    case "creating":
      return { state: "deploying", label: t("dbServers.state.creating") };
    case "upgrading":
      return { state: "deploying", label: t("dbServers.state.upgrading") };
    case "removing":
      return { state: "deploying", label: t("dbServers.state.removing") };
    default:
      return { state: "unknown", label: t("dbServers.state.other") };
  }
}

/** The support of a slab: a plain line when it is well, and the line with its glyph (and the slab's ground) when it is not. */
function Support({ view, text }: { view: SupportView; text: string | null }) {
  if (text === null) return null;
  if (view.kind !== "soon" && view.kind !== "ended") return <>{text}</>;
  const state: Status = view.kind === "ended" ? "failed" : "warning";
  return (
    <span className="flex items-start gap-1.5 font-medium text-fg">
      <StatusGlyph state={state} size={12} className={`mt-1 ${stateTextClass(state)}`} />
      <span>{text}</span>
    </span>
  );
}

function Slab({ server, all, machine, actions, now, level }: { server: DbServer; all: readonly DbServer[]; machine: string; actions: ServerActions; now: Date; level: 2 | 3 }) {
  const t = useT();
  const view = supportOf(server, now);
  const mark = defaultMark(server, all);
  const state = stateOf(t, server);
  const logo = logoFor(server.flavour);
  const place = placeOf(server);
  const tone = view.kind === "ended" ? "failed" : view.kind === "soon" ? "warning" : undefined;
  const upgradeable = (view.kind === "ended" || view.kind === "soon") && canMoveUp(server);
  const count = server.databases;
  return (
    <Card
      as="li"
      padding="sm"
      level={level}
      {...(tone !== undefined ? { tone } : {})}
      leading={
        logo !== null ? (
          <LogoTile logo={logo} size="md" />
        ) : (
          <span aria-hidden="true" className="flex size-10 shrink-0 items-center justify-center text-fg-muted">
            <OBJECT_ICONS.database className="size-icon-xl" />
          </span>
        )
      }
      title={serverTitle(server)}
      status={
        mark !== null || state !== null ? (
          <>
            {mark !== null ? <Badge>{mark.kind === "default" ? t("dbServers.default") : t("dbServers.defaultFor", { engine: mark.flavour })}</Badge> : null}
            {state !== null ? <StatusPill state={state.state} label={state.label} size="sm" /> : null}
          </>
        ) : undefined
      }
      description={view.kind === "unknown" ? undefined : <Support view={view} text={supportLine(t, view)} />}
      actions={actions.menu(server)}
      className="h-full"
    >
      <div className="flex h-full flex-col justify-between gap-3">
        {upgradeable ? (
          <div>
            <Button size="sm" disabled={actions.busy} onClick={() => actions.upgrade(server)}>
              {t("dbServers.upgradeVersion")}
            </Button>
          </div>
        ) : null}
        <div className="mt-auto flex items-end justify-between gap-3 text-13 text-fg-muted">
          <span className="flex min-w-0 items-start gap-1.5">
            <OBJECT_ICONS.server aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0" />
            <span className="min-w-0">
              {place.kind === "app" ? (
                t.rich("dbServers.place.app", {
                  app: (
                    <Link to="/apps/$domain" params={{ domain: place.app }} translate="no" className={TEXT_LINK}>
                      {place.app}
                    </Link>
                  ),
                })
              ) : place.kind === "noust" ? (
                t("dbServers.place.noust", { server: machine })
              ) : (
                t("dbServers.place.system", { server: machine })
              )}
            </span>
          </span>
          {count === null || count === undefined ? null : (
            <span className="shrink-0 whitespace-nowrap">{count === 0 ? t("dbServers.noDatabases") : t("dbServers.databases", { count })}</span>
          )}
        </div>
      </div>
    </Card>
  );
}

/**
 * The database servers as slabs, the engines in the CLI's order and the default of each first: the engine
 * and its version, where it stands in its project's support (a slab whose support is ending or ended is
 * tinted, with its glyph and its words, and offers to move up a version), where it runs and how many
 * databases it holds. Beside two flavours of one engine the default says which it is the default of.
 */
export function ServerSlabs({ servers, machine, actions, emptyAction, level = 3, className }: ServerSlabsProps) {
  const t = useT();
  const now = useMemo(() => new Date(), []);
  const sorted = useMemo(() => (servers === undefined ? [] : sortServers(servers)), [servers]);
  const grid = "grid gap-4 sm:grid-cols-2 xl:grid-cols-3";
  if (servers === undefined) {
    return (
      <LoadingRegion label={t("dbServers.loading")} className={grid}>
        {[0, 1, 2].map((slot) => (
          <SkeletonCard key={slot} lines={2} />
        ))}
      </LoadingRegion>
    );
  }
  if (servers.length === 0) {
    return <EmptyState variant="inline" title={t("dbServers.empty.title")} description={t("dbServers.empty.description")} {...(emptyAction !== undefined ? { action: emptyAction } : {})} />;
  }
  return (
    <ul aria-label={t("dbServers.label")} className={`${grid} ${className ?? ""}`.trim()}>
      {sorted.map((server) => (
        <Slab key={server.key} server={server} all={sorted} machine={machine} actions={actions} now={now} level={level} />
      ))}
    </ul>
  );
}
