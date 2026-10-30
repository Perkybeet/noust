import { Link } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useMemo } from "react";
import type { ReactNode } from "react";

import { LinkTabs } from "../../app/LinkTabs";
import { tabsOf } from "../../app/navCore";
import { CommandHint } from "../../components/page/CommandHint";
import { DashboardPage } from "../../components/page/DashboardPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { appStatus } from "../../components/page/status";
import { Sections } from "../../components/page/Section";
import { buttonClassName } from "../../components/ui/Button";
import { useT } from "../../i18n";
import { useHasFleet } from "../../nodes/servers";
import { OVERVIEW_SCREENS } from "../nav/overview";
import { FleetMetricsCard } from "../fleet/FleetMetricsCard";
import { PartialNotice } from "../fleet/parts";
import { AttentionBoard } from "./AttentionBoard";
import { FirstSteps } from "./FirstSteps";
import { HomeActivity } from "./HomeActivity";
import { HomeFleet } from "./HomeFleet";
import { coursesOf } from "./homeData";
import { MACHINE_METRICS, MachineCharts } from "./MachineCharts";
import { useMetricsRead } from "./metricsData";
import { DEFAULT_RANGE } from "./ranges";
import type { MetricRange } from "./ranges";
import { QuickActions } from "./QuickActions";
import { useFleetHome, useServerHome } from "./useHome";
import type { HomeData } from "./useHome";

export type HomeScope = "server" | "fleet";

function NewAppLink({ scope }: { scope: HomeScope }) {
  const t = useT();
  return (
    <Link to="/apps/new" {...(scope === "fleet" ? { search: { node: undefined } } : {})} className={buttonClassName("primary")}>
      <Plus aria-hidden="true" />
      {t("overview.newApplication")}
    </Link>
  );
}

export interface HomePageProps {
  /** One server (this one, or the node the address names), or every server of the fleet. */
  scope: HomeScope;
  /** The machine's time range, for one server: the charts under the page. The fleet compares servers instead. */
  range?: MetricRange;
  onRangeChange?: (range: MetricRange) => void;
}

/**
 * Home, the first page: what needs the operator, as cards with the one button that deals with each; the
 * fleet in courses, a row for each server; what happened lately; and the four things a person comes to
 * do. One page for one server and for the fleet, because they are the same page seen over a different
 * set of servers: `useHome` reads the set, this draws it. An empty installation gets its first steps
 * instead, never a board of nothing.
 */
export function HomePage({ scope, range = DEFAULT_RANGE, onRangeChange }: HomePageProps) {
  // Each scope reads its own: only the one on screen asks the server anything.
  return scope === "fleet" ? <FleetHome /> : <ServerHome range={range} onRangeChange={onRangeChange} />;
}

function ServerHome({ range, onRangeChange }: { range: MetricRange; onRangeChange: ((range: MetricRange) => void) | undefined }) {
  // The charts' read starts with the page, not once they are drawn.
  useMetricsRead(MACHINE_METRICS, range);
  return (
    <Home
      scope="server"
      home={useServerHome()}
      charts={
        <div className="col-span-full">
          <MachineCharts range={range} onRangeChange={onRangeChange ?? noop} />
        </div>
      }
    />
  );
}

function noop(): void {
  // A page nobody gave a way to change the range to keeps the one it has.
}

function FleetHome() {
  return <Home scope="fleet" home={useFleetHome()} />;
}

function Home({ scope, home, charts }: { scope: HomeScope; home: HomeData; charts?: ReactNode }) {
  const t = useT();
  const hasFleet = useHasFleet();
  const courses = useMemo(
    () => (home.servers === undefined ? undefined : coursesOf(home.servers, { describe: (status) => appStatus(status, t.locale), unreachable: t("overview.fleet.unreachable") })),
    [home.servers, t],
  );
  const header = { title: t("overview.title"), primaryAction: <NewAppLink scope={scope} /> };
  // Home and its Map are two views of the same server: one strip, as on every other area.
  const tabs = scope === "server" ? <LinkTabs label={t("nav.landmarks.overviewSections")} tabs={tabsOf(OVERVIEW_SCREENS)} /> : undefined;
  const footer = <CommandHint label={t("common.words.fromTerminal")} command={home.empty ? "noust create --temporary -s https://github.com/you/app" : scope === "fleet" ? "noust fleet apps" : "noust health"} />;

  if (home.error !== null) {
    return (
      <DashboardPage
        header={header}
        {...(tabs !== undefined ? { tabs } : {})}
        firstSteps={<ErrorBlock error={home.error} title={t("overview.couldNotLoad")} onRetry={home.retry} retrying={home.retrying} />}
        footer={footer}
      />
    );
  }

  if (home.empty) {
    return (
      <DashboardPage
        header={header}
        {...(tabs !== undefined ? { tabs } : {})}
        firstSteps={
          <Sections>
            <FirstSteps backupsScheduled={home.backupsScheduled} />
          </Sections>
        }
        footer={footer}
      />
    );
  }

  // What stands beside the fleet and under it is drawn when the fleet is: the fleet's own skeleton holds the
  // place of its courses, and nothing below it is there to be moved down when they arrive.
  const loaded = home.servers !== undefined;
  return (
    <DashboardPage
      header={header}
      {...(tabs !== undefined ? { tabs } : {})}
      attention={
        <div className="flex min-w-0 flex-col gap-8">
          {/* A server that did not answer fully is said once, in its own words: the fleet's Home is the central's overview. */}
          {home.view !== undefined ? <PartialNotice view={home.view} /> : null}
          <AttentionBoard entries={home.entries} total={home.total} showServer={home.several} />
        </div>
      }
      fleet={
        <div className="flex min-w-0 flex-col gap-8">
          <HomeFleet servers={home.servers} courses={courses} allServers={scope === "server" && hasFleet} />
          {/* Under the courses, which are the page's subject and never as tall as what is beside them: the four things a person comes to do. */}
          {loaded ? <QuickActions /> : null}
          {/* Which server is under the most load: the question a person with several asks next, answered under the courses. */}
          {scope === "fleet" && home.several ? <FleetMetricsCard /> : null}
        </div>
      }
      {...(loaded
        ? {
            side: <HomeActivity lines={home.lines} />,
          }
        : {})}
      {...(charts !== undefined && loaded ? { charts } : {})}
      {...(loaded ? { footer } : {})}
    />
  );
}
