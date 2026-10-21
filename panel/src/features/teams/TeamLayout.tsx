import { useQuery } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";
import { Users } from "lucide-react";
import { createContext, useContext } from "react";

import { isApiError } from "../../api/client";
import { teamQuery } from "../../api/queries/teams";
import type { TeamDetail } from "../../api/queries/teams";
import { announce } from "../../app/Announcer";
import { LinkTabs } from "../../app/LinkTabs";
import { TEAM_TABS } from "../nav/team";
import { DetailPage } from "../../components/page/DetailPage";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button, buttonClassName } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { reportActionError } from "../apps/useAppActions";
import { teamRoleLabel } from "./roles";
import { useOrganization } from "./useOrganization";

/**
 * The team whose page is open. The layout provides it once it is read, so every tab renders
 * with the team in hand and none of them repeats the load, its skeleton or its failure.
 */
const TeamContext = createContext<TeamDetail | null>(null);

export function useTeam(): TeamDetail {
  const team = useContext(TeamContext);
  if (team === null) throw new Error("useTeam is used outside TeamLayout");
  return team;
}

function BodySkeleton({ slug }: { slug: string }) {
  const t = useT();
  return (
    <LoadingRegion label={t("teams.team.loading", { name: slug })} className="flex flex-col gap-8">
      <Skeleton className="h-10 w-full max-w-xl" />
      <Skeleton className="h-64 w-full rounded-card" />
    </LoadingRegion>
  );
}

/**
 * One team, as a T2 page: its name, its short name and the operator's role in it, "Work in this
 * team" beside them when it is not the one the console is on, and its sections as tabs whose
 * state is the URL (what it holds, who is in it, its settings). A team the operator does not
 * see reads as one that does not exist, as the server answers it.
 */
export function TeamLayout({ team: slug }: { team: string }) {
  const t = useT();
  const { node } = useNode();
  const org = useOrganization();
  const query = useQuery({ ...teamQuery(slug), retry: false });
  const team = query.data;

  const header = {
    title: team?.name ?? slug,
    server: node,
    breadcrumbs: [{ label: t("teams.list.title"), to: "/projects/teams" }],
  };

  if (query.isError && isApiError(query.error) && query.error.status === 404) {
    return (
      <DetailPage header={{ ...header, mono: true }}>
        <EmptyState
          variant="firstUse"
          icon={<Users />}
          title={t("teams.team.notFoundTitle")}
          description={t("teams.team.notFoundDescription", { team: slug })}
          action={
            <Link to="/projects/teams" className={buttonClassName("secondary")}>
              {t("teams.team.allTeams")}
            </Link>
          }
          command="noust team list"
        />
      </DetailPage>
    );
  }

  if (team === undefined) {
    return (
      <DetailPage header={{ ...header, mono: true }}>
        {query.isError ? <ErrorBlock error={query.error} title={t("teams.team.couldNotLoad", { name: slug })} onRetry={() => void query.refetch()} retrying={query.isRefetching} /> : <BodySkeleton slug={slug} />}
      </DetailPage>
    );
  }

  const here = org.active === team.slug;
  return (
    <DetailPage
      header={{
        ...header,
        meta: (
          <>
            <Mono tone="muted">{team.slug}</Mono>
            <span>{team.role === null || team.role === undefined ? t("teams.team.notAMember") : t("teams.team.yourRole", { role: teamRoleLabel(t, team.role) })}</span>
          </>
        ),
        ...(org.teamsMatter && !here
          ? {
              secondaryActions: (
                <Button
                  icon={<Users aria-hidden="true" />}
                  loading={org.choosing}
                  onClick={() => {
                    org.choose(team.slug).then(
                      () => announce(t("teams.switcher.switched", { name: team.name })),
                      (error: unknown) => reportActionError(t("teams.switcher.failed"), error),
                    );
                  }}
                >
                  {t("teams.team.workHere")}
                </Button>
              ),
            }
          : {}),
      }}
      tabs={<LinkTabs label={t("nav.landmarks.teamSections")} tabs={TEAM_TABS.map((tab) => ({ ...tab, params: { team: team.slug } }))} />}
    >
      <TeamContext.Provider value={team}>
        <Outlet />
      </TeamContext.Provider>
    </DetailPage>
  );
}
