/**
 * The page of one section of a database: its header (the section's own name, what that section does as its actions,
 * and one line of what the database is), and what every section of a database shares through the layout (the banner
 * that says the engine is stopped or the database gone, the job in hand). The sections of a database are the second
 * column's, so there is no strip of tabs here; each page is headed by its own name, and Overview by the database's.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { createContext, useContext } from "react";
import type { ReactNode } from "react";

import { databaseOverviewQuery, databasesQuery } from "../../api/queries/databases";
import type { DbServer } from "../../api/queries/databases";
import type { PageHeaderProps } from "../../app/PageHeader";
import { DetailPage } from "../../components/page/DetailPage";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { buttonClassName } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { LogoMark } from "../../components/ui/LogoTile";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { sizeWords } from "./DatabasesTable";
import { can } from "./engines";
import type { Capability } from "./engines";
import { answers } from "./engineOf";
import type { EngineLife } from "./engineOf";
import { logoFor, serverTitle } from "./servers";
import type { ServerActions } from "./servers/useServerActions";
import { TEXT_LINK } from "./ui";

/** What the layout of a database shares with every section of it. */
export interface DatabaseFrame {
  engine: string;
  name: string;
  /** The engine it runs on, when the list of engines says. */
  server: DbServer | undefined;
  life: EngineLife;
  /** The list of engines is still being read: what needs the engine waits for it and never draws what it would then replace. */
  reading: boolean;
  /** What its engine can do (`sql`, `keys`, `dump`...): the engine's own answer, or the list of engines' for one that is stopped. Undefined while unknown. */
  capabilities: readonly string[] | undefined;
  /** The engines' own actions: start one, move a database up a version. */
  actions: ServerActions;
  /** Said once for every section: the engine is stopped, the database is gone, the overview could not be read. */
  banner: ReactNode;
  /** The job in hand on this database. */
  job: ReactNode;
}

const FrameContext = createContext<DatabaseFrame | null>(null);

export const DatabaseFrameProvider = FrameContext.Provider;

/**
 * Whether a section may ask its engine: the list of engines has been read and says it runs, and the engine has what the section is
 * about. A section the engine has none of (a query on Redis) asks nothing, and says so (`DatabasePage`'s `needs`).
 */
export function useAsking(needs?: Capability | readonly Capability[]): boolean {
  const frame = useDatabaseFrame();
  return answers(frame) && !lacks(frame, needs);
}

/** Whether the engine is known to lack every capability a section needs: unknown is not lacking. */
function lacks(frame: Pick<DatabaseFrame, "capabilities">, needs?: Capability | readonly Capability[]): boolean {
  if (needs === undefined || frame.capabilities === undefined) return false;
  const wanted = typeof needs === "string" ? [needs] : needs;
  return !wanted.some((capability) => can({ capabilities: frame.capabilities }, capability));
}

/** What the layout of the database on screen shares. */
export function useDatabaseFrame(): DatabaseFrame {
  const frame = useContext(FrameContext);
  if (frame === null) throw new Error("a section of a database is drawn inside its layout");
  return frame;
}

/** The line under the title: the engine and its version, the size, and who uses it. From the overview, or from the list when the engine does not answer. */
function Facts({ frame }: { frame: DatabaseFrame }) {
  const t = useT();
  const { engine, name, server } = frame;
  const overview = useQuery({ ...databaseOverviewQuery(engine, name), retry: false, enabled: answers(frame) });
  const row = useQuery({ ...databasesQuery(), select: (list) => list.databases.find((item) => item.engine === engine && item.name === name) });
  const database = overview.data?.database ?? row.data;
  const label = overview.data !== undefined ? `${overview.data.display_name}${database?.engine_version ? ` ${database.engine_version}` : ""}` : server !== undefined ? serverTitle(server) : null;
  const flavour = server?.flavour ?? engine.split("@")[0] ?? engine;
  const logo = logoFor(flavour);
  const size = sizeWords(database?.size, t);
  const apps = database?.apps ?? [];
  if (label === null && size === null && apps.length === 0) {
    return row.isPending || (overview.isPending && answers(frame)) ? (
      <span aria-hidden="true" className="flex h-5 items-center gap-3">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-3 w-14" />
      </span>
    ) : null;
  }
  return (
    <>
      {label !== null ? (
        <span className="inline-flex items-center gap-1.5">
          {logo !== null ? <LogoMark logo={logo} className="size-icon-md" /> : <OBJECT_ICONS.database aria-hidden="true" className="size-icon-md text-fg-muted" />}
          {label}
        </span>
      ) : null}
      {size !== null ? <span>{size}</span> : null}
      {apps.length > 0 ? (
        <span className="flex min-w-0 flex-wrap items-center gap-x-1.5">
          {t.rich("databases.layout.usedByFact", {
            apps: (
              <span className="inline-flex flex-wrap gap-x-1.5">
                {apps.map((domain) => (
                  <Link key={domain} to="/apps/$domain/database" params={{ domain }} translate="no" className={TEXT_LINK}>
                    {domain}
                  </Link>
                ))}
              </span>
            ),
          })}
        </span>
      ) : null}
    </>
  );
}

export interface DatabasePageProps {
  /** The section's name; the database's own on its Overview, which `mono` sets as the system value it is. */
  title: string;
  mono?: boolean;
  /** The database's state, beside the title: on the Overview only. */
  status?: ReactNode;
  /** The section's own actions: what it does, the last of them primary. */
  secondaryActions?: ReactNode;
  primaryAction?: PageHeaderProps["primaryAction"];
  /** What the section reads from the engine itself: while the engine does not answer, the banner says so and this is not drawn. */
  needsEngine?: boolean;
  /** What the engine must be able to do for the section to exist (`sql` for a query): without it the page says the engine has none, and where to go. */
  needs?: Capability | readonly Capability[];
  children: ReactNode;
}

/**
 * A section of a database as a page: the header with the section's name, the layout's banner and job, and the section's
 * content. A section that reads from the engine (`needsEngine`) draws its content only while the engine answers, and waits for
 * the list of engines before it draws anything, so a stopped engine is said at once and never after a failing request.
 */
export function DatabasePage({ title, mono = false, status, secondaryActions, primaryAction, needsEngine = false, needs, children }: DatabasePageProps) {
  const t = useT();
  const frame = useDatabaseFrame();
  const blocked = needsEngine && (frame.life === "stopped" || frame.life === "changing");
  const waiting = needsEngine && frame.reading;
  const without = needs !== undefined && lacks(frame, needs);
  return (
    <DetailPage
      header={{
        title,
        mono,
        ...(status !== undefined ? { status } : {}),
        meta: <Facts frame={frame} />,
        ...(secondaryActions !== undefined ? { secondaryActions } : {}),
        ...(primaryAction !== undefined ? { primaryAction } : {}),
      }}
      {...(frame.banner !== null ? { banner: frame.banner } : {})}
      {...(frame.job !== null ? { job: frame.job } : {})}
    >
      {without ? (
        <EmptyState
          variant="firstUse"
          icon={<OBJECT_ICONS.database />}
          title={t("databases.unavailable.title", { section: title })}
          description={t("databases.unavailable.body")}
          action={
            <Link to="/databases/$engine/$name" params={{ engine: frame.engine, name: frame.name }} className={buttonClassName("secondary")}>
              {t("databases.unavailable.back")}
            </Link>
          }
        />
      ) : waiting ? (
        <LoadingRegion label={t("databases.layout.loading", { name: frame.name })} className="flex flex-col gap-4">
          <Skeleton className="h-32 w-full rounded-card" />
        </LoadingRegion>
      ) : blocked ? null : (
        children
      )}
    </DetailPage>
  );
}
