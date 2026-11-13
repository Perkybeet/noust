import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import { Ban } from "lucide-react";
import type { ReactNode } from "react";

import { sessionQuery } from "../api/queries/auth";
import { DetailPage } from "../components/page/DetailPage";
import { buttonClassName } from "../components/ui/Button";
import { EmptyState } from "../components/ui/EmptyState";
import { ServerText } from "../components/ui/ServerText";
import { featureAnswer, offers } from "../features/central/features";
import type { FeatureKey } from "../features/central/features";
import { VIEWS } from "../features/nav/views";
import { useT } from "../i18n";
import { useServerList } from "../nodes/servers";
import { useConsoleContext, useScopeOffers } from "../nodes/useNode";
import { areaLanding, areaOf, entryAt, fleetTarget } from "./nav";
import type { Dest } from "./nav";
import type { NavContext } from "./navCore";
import { useRenderedPathname } from "./renderedPath";

/**
 * What the page on screen needs of the server: its section's function, or (an area made of views, such as Logs) the
 * function of the view the address names. Undefined when it needs none.
 */
function needed(entry: Dest, search: Readonly<Record<string, unknown>>): FeatureKey | undefined {
  const views = VIEWS[entry.id];
  if (views === undefined) return entry.feature;
  // A view is the page with a search; the one the address names, or the first when it names none.
  const named = views.find((view) => view.search !== undefined && Object.entries(view.search).every(([key, value]) => search[key] === value));
  const view = named ?? views.find((candidate) => candidate.search === undefined) ?? views[0];
  return view?.feature ?? entry.feature;
}

/**
 * The page of a section the server chosen cannot do, reached by an address that was typed or kept (every list leaves out what a
 * server cannot do, so a click never gets here): what it is, why this server does not have it in the words the server's table
 * says, and the way out. It is a calm page and not a failure, with no "Try again" that could never work (item 187).
 */
export function FeatureUnavailable({ entry, feature }: { entry: Dest; feature: FeatureKey }) {
  const t = useT();
  const { hostname } = useServerList();
  const features = useScopeOffers();
  const { data: session } = useQuery(sessionQuery());
  const answer = featureAnswer(session, feature);
  const ctx: NavContext = { features };
  const area = areaOf(entry.to) ?? entry;
  const landing = areaLanding(area, ctx);
  const elsewhere = landing.to !== entry.to || landing.search !== undefined;
  const section = t(area.label);
  const server = hostname ?? t("fleet.selector.thisServer");
  return (
    <DetailPage header={{ title: section }}>
      <EmptyState
        variant="firstUse"
        icon={<Ban />}
        title={t("servers.unavailable.title", { server })}
        description={
          <>
            {/* The table's own sentence and its fix, each on a line of its own: they are two sentences, and the second says what to run. */}
            {answer?.reason ? (
              <span className="mb-2 block">
                <ServerText coded={answer.reason} text={answer.reason.text} />
              </span>
            ) : null}
            {answer?.reason?.hint ? (
              <span className="mb-2 block">
                <ServerText coded={answer.reason} text={answer.reason.hint} part="hint" />
              </span>
            ) : null}
            <span className="block">{t("servers.unavailable.hint", { section })}</span>
          </>
        }
        action={
          <>
            <Link to={fleetTarget(entry)} className={buttonClassName("primary", "md")}>
              {t("servers.unavailable.seeAll")}
            </Link>
            {elsewhere ? (
              <Link to={landing.to} {...(landing.search !== undefined ? { search: landing.search as never } : {})} className={buttonClassName("secondary", "md")}>
                {t("servers.unavailable.open", { section: t(area.label) })}
              </Link>
            ) : null}
          </>
        }
      />
    </DetailPage>
  );
}

/**
 * The page that is drawn instead of one the server chosen cannot do. Only the central itself is judged (its own table,
 * `session.central.features`): a node's machine and the fleet's pages are not its to answer for. A page that needs nothing, and
 * every page of a server that can do it, is drawn as it always was.
 */
export function FeatureGate({ children }: { children: ReactNode }) {
  const pathname = useRenderedPathname();
  const search = useRouterState({ select: (state) => state.location.search as Record<string, unknown> });
  const context = useConsoleContext();
  const { data: session } = useQuery(sessionQuery());
  const entry = context.kind === "server" && context.node === null ? entryAt(pathname) : undefined;
  const key = entry === undefined ? undefined : needed(entry, search);
  if (entry === undefined || key === undefined || offers(session, key)) return <>{children}</>;
  return <FeatureUnavailable entry={entry} feature={key} />;
}
