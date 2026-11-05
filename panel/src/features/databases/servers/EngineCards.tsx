import { Link } from "@tanstack/react-router";
import { useMemo } from "react";
import type { ReactNode } from "react";

import type { DbServer } from "../../../api/queries/databases";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Can } from "../../../components/page/permissions";
import { Badge } from "../../../components/ui/Badge";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { LogoTile } from "../../../components/ui/LogoTile";
import { ResourceCard } from "../../../components/ui/ResourceCard";
import { OBJECT_ICONS, VERB_ICONS } from "../../../components/ui/objectIcons";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusGlyph, StatusPill, stateTextClass } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { TEXT_LINK } from "../ui";
import { canMoveUp, defaultMark, logoFor, placeOf, serverTitle, sortServers, supportLine, supportOf } from "../servers";
import type { SupportView } from "../servers";
import type { ServerActions } from "./useServerActions";

export interface EngineCardsProps {
  servers: readonly DbServer[] | undefined;
  actions: ServerActions;
  /** The machine's name, said of the engines that run on its system. */
  machine: string;
  /** What to draw when there is none: a first use, with the action that makes one. */
  empty: ReactNode;
}

/** How an engine that is not running says it: Docker's own word becomes the nearest state of the console. */
function stateOf(t: T, server: DbServer): { state: Status; label: string } {
  if (server.running) return { state: "running", label: t("common.statusPill.running") };
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

/** Where an engine runs, in a line: the system's, a container of Noust's, or an application's own stack. */
function Where({ server, machine, t }: { server: DbServer; machine: string; t: T }) {
  const place = placeOf(server);
  if (place.kind === "app") {
    return t.rich("dbServers.place.app", {
      app: (
        <Link to="/apps/$domain" params={{ domain: place.app }} translate="no" className={TEXT_LINK}>
          {place.app}
        </Link>
      ),
    });
  }
  if (place.kind === "noust") return t("dbServers.place.noust", { server: place.name, machine });
  return t("dbServers.place.system", { machine });
}

/** The support line of a card: plain when it is well, with its glyph and its words when the version is ending or ended. */
function Support({ view, text }: { view: SupportView; text: string | null }) {
  if (text === null) return null;
  if (view.kind !== "soon" && view.kind !== "ended") return <span className="text-13 text-fg-muted">{text}</span>;
  const state: Status = view.kind === "ended" ? "failed" : "warning";
  return (
    <span className="flex items-start gap-1.5 text-13 font-medium text-fg">
      <StatusGlyph state={state} size={12} className={`mt-1 ${stateTextClass(state)}`} />
      <span>{text}</span>
    </span>
  );
}

function EngineCard({ server, all, machine, actions, now }: { server: DbServer; all: readonly DbServer[]; machine: string; actions: ServerActions; now: Date }) {
  const t = useT();
  const view = supportOf(server, now);
  const mark = defaultMark(server, all);
  const state = stateOf(t, server);
  const logo = logoFor(server.flavour);
  const upgradeable = (view.kind === "ended" || view.kind === "soon") && canMoveUp(server);
  const stopped = server.state === "stopped" && !server.running;
  const facts = [
    ...(server.port != null ? [{ label: t("dbServers.fact.port"), value: String(server.port) }] : []),
    { label: t("dbServers.fact.databases"), value: server.databases === null || server.databases === undefined ? "–" : String(server.databases) },
    ...(server.memory_limit_mb != null ? [{ label: t("dbServers.fact.memoryLimit"), value: formatBytes(server.memory_limit_mb * 1024 * 1024, t.locale) }] : []),
  ];
  const support = view.kind === "unknown" ? null : supportLine(t, view);
  return (
    <li className="min-w-0">
      <ResourceCard
        level={2}
        name={serverTitle(server)}
        untranslated
        {...(logo !== null ? { logo: <LogoTile logo={logo} size="md" /> } : { icon: OBJECT_ICONS.database })}
        status={
          <>
            <StatusPill state={state.state} label={state.label} size="sm" />
            {mark !== null ? <Badge>{mark.kind === "default" ? t("dbServers.default") : t("dbServers.defaultFor", { engine: mark.flavour })}</Badge> : null}
          </>
        }
        facts={facts}
        actions={actions.menu(server)}
      >
        <p className="text-13 text-fg-muted">
          <Where server={server} machine={machine} t={t} />
        </p>
        {support !== null || upgradeable || stopped ? (
          <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <Support view={view} text={support} />
            {stopped ? (
              <Can permission="apps.operate">
                <Button size="sm" icon={<VERB_ICONS.turnOn aria-hidden="true" />} loading={actions.starting} disabled={actions.busy} onClick={() => actions.start(server)}>
                  {t("common.words.start")}
                </Button>
              </Can>
            ) : null}
            {upgradeable ? (
              <Can permission="databases.manage">
                <Button size="sm" disabled={actions.busy} onClick={() => actions.upgrade(server)}>
                  {t("dbServers.upgradeVersion")}
                </Button>
              </Can>
            ) : null}
          </div>
        ) : null}
        {/* The engine's databases are one press away: its count is a count of them. */}
        {server.databases !== null && server.databases !== undefined && server.databases > 0 ? (
          <Link to="/databases" search={{ engine: server.key }} className={`${buttonClassName("ghost", "sm")} -ml-2 mt-1 w-fit`}>
            {t("dbServers.seeDatabases", { count: server.databases })}
          </Link>
        ) : null}
      </ResourceCard>
    </li>
  );
}

/**
 * The engines as cards, the engines in the CLI's order and the default of each first: the engine and its release, whether it
 * runs, its port and how many databases it holds, where it runs, and where its version stands in its project's support (a
 * version whose support is ending or ended says so with its glyph and offers to move up). A stopped engine has "Start" on its
 * card, because that is what a card that is grey asks. Few engines is a handful of cards, never a table (docs/DESIGN.md, CT-4).
 */
export function EngineCards({ servers, actions, machine, empty }: EngineCardsProps) {
  const t = useT();
  const now = useMemo(() => new Date(), []);
  const sorted = useMemo(() => (servers === undefined ? [] : sortServers(servers)), [servers]);
  const grid = "grid gap-4 md:grid-cols-2";
  if (servers === undefined) {
    return (
      <LoadingRegion label={t("dbServers.loading")} className={grid}>
        {[0, 1, 2, 3].map((slot) => (
          <SkeletonCard key={slot} lines={2} />
        ))}
      </LoadingRegion>
    );
  }
  if (servers.length === 0) return <>{empty}</>;
  return (
    <ul aria-label={t("dbServers.label")} className={grid}>
      {sorted.map((server) => (
        <EngineCard key={server.key} server={server} all={sorted} machine={machine} actions={actions} now={now} />
      ))}
    </ul>
  );
}
