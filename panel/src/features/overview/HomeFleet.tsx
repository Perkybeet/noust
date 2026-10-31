import { Link } from "@tanstack/react-router";
import { useMemo } from "react";
import type { ReactElement, ReactNode } from "react";

import { LoadingRegion } from "../../components/page/LoadingRegion";
import { Section } from "../../components/page/Section";
import { EmptyState } from "../../components/ui/EmptyState";
import { FleetCourses } from "../../components/ui/FleetCourses";
import type { FleetCourse, FleetCourseTarget } from "../../components/ui/FleetCourses";
import { Skeleton } from "../../components/ui/Skeleton";
import { STATUS, StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { legendOf } from "./homeData";
import type { HomeServer } from "./homeData";

export interface HomeFleetProps {
  /** Offer the whole fleet: this is one server of a console that has several, and its Home is not theirs. */
  allServers?: boolean;
  /** The servers, this one first; undefined while they are read. */
  servers: readonly HomeServer[] | undefined;
  /** The same servers as courses, or undefined while they are read. */
  courses: readonly FleetCourse[] | undefined;
}

/**
 * The fleet in courses: a row for each server, its applications as slabs of one size, the ones with a
 * problem first and tinted, the well ones neutral and, when a server has many, named as far as two rows go, the rest said as "+3 running".
 * Over it, how many applications are in each state. A server's name and each slab lead to its page, on
 * that server. With one server there is nothing to tell apart and the block says only what runs.
 */
export function HomeFleet({ servers, courses, allServers = false }: HomeFleetProps) {
  const t = useT();
  const legend = useMemo(() => (courses === undefined ? [] : legendOf(courses)), [courses]);
  const nodeOf = useMemo(() => new Map((servers ?? []).map((server) => [server.key, server.node])), [servers]);
  const several = (servers?.length ?? 0) > 1;
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
    <Section
      title={several ? t("overview.fleet.title") : t("overview.fleet.titleOne")}
      icon={OBJECT_ICONS.server}
      loading={courses === undefined}
      {...(legend.length > 0 || allServers
        ? {
            actions: (
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                {allServers ? (
                  <TextLink to="/fleet" size="ui">
                    {t("overview.fleet.allServers")}
                  </TextLink>
                ) : null}
                <ul aria-label={t("overview.fleet.legend")} className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  {legend.map(({ state, count }) => (
                    <li key={state} className="flex items-center gap-1.5 text-13 text-fg-muted">
                      <StatusGlyph state={state} size={12} className={stateTextClass(state)} />
                      {t(STATUS[state].labelKey)}
                      {/* A space for the name a screen reader reads: the layout's gap is not text. */}{" "}
                      <span className="tabular-nums">{count}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ),
          }
        : {})}
    >
      {courses === undefined ? (
        <LoadingRegion label={t("overview.fleet.loading")}>
          <Skeleton className="h-80 rounded-card" />
        </LoadingRegion>
      ) : courses.length === 0 ? (
        <EmptyState variant="inline" title={t("overview.fleet.none")} />
      ) : (
        <FleetCourses label={t("overview.fleet.label")} courses={courses} ungrouped={!several} linkFor={linkFor} />
      )}
    </Section>
  );
}
