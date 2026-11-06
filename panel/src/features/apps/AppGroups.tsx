import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { Sections } from "../../components/page/Section";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { buttonClassName } from "../../components/ui/Button";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import type { Labels } from "../teams/organization";
import type { AppInfo } from "./data";
import { groupByProject, groupByState } from "./groups";
import type { StateGroup } from "./groups";

/** What each group is called: the states the kit already names (`common.statusPill`), and what needs a person. */
const STATE_TITLE: Readonly<Record<StateGroup, PlainKey>> = {
  problems: "apps.views.problems",
  deploying: "common.statusPill.deploying",
  running: "common.statusPill.running",
  stopped: "common.statusPill.stopped",
  unknown: "common.statusPill.unknown",
};

/** What draws the applications of one group: the one table of applications, embedded in the group's slab. */
export type GroupTable = (apps: readonly AppInfo[], caption: string) => ReactNode;

/** The applications by project: a slab for each, with its table (the same table as the list's) and the way to the project. */
export function AppsByProject({ apps, labels, table }: { apps: readonly AppInfo[]; labels: Pick<Labels, "project" | "team">; table: GroupTable }) {
  const t = useT();
  return (
    <Sections surfaces>
      {groupByProject(apps, labels).map((group) => {
        const title = group.key === "" ? t("apps.views.noProject") : group.name;
        return (
          <SurfaceSection
            key={group.key}
            title={title}
            icon={OBJECT_ICONS.project}
            description={group.team !== null ? t("apps.views.projectLine", { count: group.apps.length, team: labels.team(group.team) }) : t("apps.views.countLine", { count: group.apps.length })}
            flush
            actions={
              group.team !== null && group.project !== null ? (
                <Link to="/projects/$team/$project" params={{ team: group.team, project: group.project }} className={buttonClassName("secondary", "sm")}>
                  {t("apps.views.openProject")}
                </Link>
              ) : undefined
            }
          >
            {table(group.apps, t("apps.views.tableOf", { group: title }))}
          </SurfaceSection>
        );
      })}
    </Sections>
  );
}

/** The applications by state, what needs a person first: a slab for each, headed by the state it is. */
export function AppsByState({ apps, table }: { apps: readonly AppInfo[]; table: GroupTable }) {
  const t = useT();
  return (
    <Sections surfaces>
      {groupByState(apps).map(({ group, apps: inGroup }) => {
        const title = t(STATE_TITLE[group]);
        return (
          <SurfaceSection
            key={group}
            title={title}
            description={t("apps.views.countLine", { count: inGroup.length })}
            flush
          >
            {table(inGroup, t("apps.views.tableOf", { group: title }))}
          </SurfaceSection>
        );
      })}
    </Sections>
  );
}
