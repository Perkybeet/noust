/**
 * "Servers of the fleet": one course of stone for each server, its disk and its applications as slabs, the ones
 * with a problem first and tinted. The whole of it is `FleetCourses`; this gives it the servers, the words and the
 * pages each slab leads to (always on the server it names).
 */
import { Link } from "@tanstack/react-router";
import { useMemo } from "react";
import type { ReactElement, ReactNode } from "react";

import { coursesOf } from "../../../features/overview/homeData";
import { useScopedHome } from "../../../features/overview/useHome";
import { useT } from "../../../i18n";
import { appStatus } from "../../page/status";
import { FleetCourses } from "../../ui/FleetCourses";
import type { FleetCourseTarget } from "../../ui/FleetCourses";
import { WidgetPending } from "../parts";

/** Every server with its applications. It scrolls inside its cell when there are more servers than the cell has rows. */
export function FleetBody() {
  const t = useT();
  const home = useScopedHome("fleet");
  const courses = useMemo(
    () => (home.servers === undefined ? undefined : coursesOf(home.servers, { describe: (status) => appStatus(status, t.locale), unreachable: t("overview.fleet.unreachable") })),
    [home.servers, t],
  );
  const nodeOf = useMemo(() => new Map((home.servers ?? []).map((server) => [server.key, server.node])), [home.servers]);
  if (courses === undefined) return <WidgetPending title={t("widgets.fleet.title")} failed={home.error !== null} onRetry={home.retry} />;
  const linkFor = (target: FleetCourseTarget, className: string, children: ReactNode): ReactElement => {
    const node = nodeOf.get(target.type === "server" ? target.id : target.server);
    const search = node === undefined ? {} : { node: node ?? undefined };
    return target.type === "server" ? (
      <Link to="/server" search={search as never} className={className}>
        {children}
      </Link>
    ) : (
      <Link to="/apps/$domain" params={{ domain: target.id }} search={search as never} className={className}>
        {children}
      </Link>
    );
  };
  return (
    <div className="h-full overflow-y-auto scroll-thin">
      <FleetCourses label={t("overview.fleet.label")} courses={courses} ungrouped={!home.several} linkFor={linkFor} />
    </div>
  );
}
