import type { ReactElement, ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { StatusGlyph, stateTextClass } from "./StatusPill";
import type { Status } from "./StatusPill";

export interface FleetCourseApp {
  id: string;
  /** The application's name as the operator knows it (a domain). */
  name: string;
  state: Status;
  /** The state's word, already translated: it is what the slab says when something is wrong. */
  stateLabel: string;
}

export interface FleetCourse {
  id: string;
  /** The server's name: `web-01`, `clientes-01`. A name the operator chose, never an invented one. */
  name: string;
  /** Where it is ("Madrid"), when that tells servers apart. */
  place?: string;
  /** How full its disk is, 0 to 100. */
  diskPercent?: number;
  apps: readonly FleetCourseApp[];
}

/** What a slab or a name is, so the screen can wrap it in the router's link. */
export type FleetCourseTarget = { type: "server"; id: string } | { type: "app"; id: string; server: string };

export interface FleetCoursesProps {
  /** Names the list for assistive technology: "Servers and their applications". */
  label: string;
  courses: readonly FleetCourse[];
  /** How many slabs sit across a course. Two on a phone, always. Defaults to four. */
  columns?: 3 | 4 | 6;
  /**
   * Wraps a server's name or an application's slab in a link to its page: the kit does not know
   * the router's routes. Return the element given `className` and `children`; without it the slabs
   * are plain, seated, and do not rise.
   */
  linkFor?: (target: FleetCourseTarget, className: string, children: ReactNode) => ReactElement;
  className?: string;
}

/** The order a course is told in: what needs a look first. */
const URGENCY: Record<Status, number> = {
  failed: 0,
  warning: 1,
  deploying: 2,
  queued: 3,
  unknown: 4,
  stopped: 5,
  running: 6,
  static: 6,
};

/** Whether a state is a problem for the course: only running and static are well. */
export function isWell(state: Status): boolean {
  return URGENCY[state] >= 6;
}

export interface CoursePlan {
  /** Problems first, most urgent first, then the well ones when they are shown one by one. */
  slabs: readonly FleetCourseApp[];
  /** How many well applications are said as one slab ("+9 running"), 0 when each is shown. */
  grouped: number;
}

/**
 * Which slabs a course draws. Everything, problems first, while it fits in two rows. When it does
 * not, the problems stay one by one (a problem is never hidden) and the well ones become a single
 * "+N running" slab: twenty applications that are well say less than the one that is not.
 */
export function planCourse(apps: readonly FleetCourseApp[], columns: number): CoursePlan {
  const ordered = [...apps].sort((a, b) => URGENCY[a.state] - URGENCY[b.state] || a.name.localeCompare(b.name, "en"));
  if (ordered.length <= columns * 2) return { slabs: ordered, grouped: 0 };
  const problems = ordered.filter((app) => !isWell(app.state));
  return { slabs: problems, grouped: ordered.length - problems.length };
}

const GRID = { 3: "grid-cols-2 sm:grid-cols-3", 4: "grid-cols-2 sm:grid-cols-4", 6: "grid-cols-2 sm:grid-cols-6" } as const;

const SLAB =
  "flex h-10 min-w-0 flex-col justify-center rounded-chip px-2.5 text-12 text-fg outline-none " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus";

/** The ground of a slab: the colour of its state only when something is wrong. */
const SLAB_GROUND: Record<Status, string> = {
  failed: "bg-fail-soft",
  warning: "bg-warn-soft",
  deploying: "bg-warn-soft",
  queued: "bg-warn-soft",
  unknown: "bg-idle-soft",
  stopped: "bg-idle-soft",
  running: "bg-surface-raised",
  static: "bg-surface-raised",
};

function Slab({ app, linkFor, server }: { app: FleetCourseApp; linkFor: FleetCoursesProps["linkFor"]; server: string }) {
  const well = isWell(app.state);
  const inner = well ? (
    <span className="flex min-w-0 items-center gap-1.5">
      <span translate="no" className="min-w-0 flex-1 truncate">{app.name}</span>
      <StatusGlyph state={app.state} size={12} className={stateTextClass(app.state)} />
      <span className="sr-only">{app.stateLabel}</span>
    </span>
  ) : (
    <>
      <span translate="no" className="truncate leading-tight font-medium">{app.name}</span>
      <span className={cx("flex items-center gap-1 text-12 leading-tight font-semibold", stateTextClass(app.state))}>
        <StatusGlyph state={app.state} size={12} />
        {app.stateLabel}
      </span>
    </>
  );
  const className = cx(SLAB_GROUND[app.state], linkFor ? "elev-raised" : "elev-seated");
  return linkFor ? linkFor({ type: "app", id: app.id, server }, cx(SLAB, className), inner) : <div className={cx(SLAB, className)}>{inner}</div>;
}

/**
 * The fleet as courses of stone, like a dry-stone wall: one course (a row) per server, with its
 * name, its place and its disk at the left and its applications as slabs of one size in a regular
 * grid. The ones with a problem come first, tinted with their state, its glyph and its word;
 * the well ones are neutral stone and, when they do not fit, are said as "+N running". A server
 * with nothing on it shows an empty slot. Twenty applications are read at a glance because the
 * grid is always the same and only the problem has colour.
 */
export function FleetCourses({ label, courses, columns = 4, linkFor, className }: FleetCoursesProps) {
  const t = useT();
  return (
    <ul aria-label={label} className={cx("elev-seated flex flex-col divide-y divide-border rounded-card border border-border bg-surface", className)}>
      {courses.map((course) => {
        const plan = planCourse(course.apps, columns);
        const high = course.diskPercent !== undefined && course.diskPercent >= 85;
        const heading = <span className="title min-w-0 truncate text-16">{course.name}</span>;
        return (
          <li key={course.id} className="flex flex-col gap-3 px-3.5 py-3 sm:flex-row sm:items-center sm:gap-4">
            <div className="flex min-w-0 flex-col gap-0.5 sm:w-44 sm:shrink-0">
              {linkFor ? linkFor({ type: "server", id: course.id }, "min-w-0 hover:text-accent-fg hover:underline", heading) : heading}
              {course.place !== undefined ? <span className="text-12 text-fg-muted">{course.place}</span> : null}
              {course.diskPercent !== undefined ? (
                <span className={cx("mt-1 flex items-center gap-2 text-12", high ? "font-semibold text-warn" : "text-fg-muted")}>
                  <span aria-hidden="true" className="elev-carved-fine h-1.5 w-14 shrink-0 overflow-hidden rounded-pill bg-bg-sunken">
                    <span
                      className={cx("block h-full rounded-pill", high ? "bg-warn" : "bg-il-stroke")}
                      // The width is the measurement itself: the one number only the data knows.
                      style={{ width: `${String(Math.min(100, Math.max(0, course.diskPercent)))}%` }}
                    />
                  </span>
                  {high ? <StatusGlyph state="warning" size={12} /> : null}
                  {t("common.fleetCourses.disk", { percent: Math.round(course.diskPercent) })}
                </span>
              ) : null}
            </div>
            {course.apps.length === 0 ? (
              <div className={cx("min-w-0 flex-1", "grid gap-1.5", GRID[columns])}>
                <div className="col-span-2 flex h-10 items-center justify-center rounded-chip bg-bg-sunken text-12 text-fg-muted">
                  {t("common.fleetCourses.noApplications")}
                </div>
              </div>
            ) : (
              <ul className={cx("grid min-w-0 flex-1 gap-1.5", GRID[columns])}>
                {plan.slabs.map((app) => (
                  <li key={app.id} className="min-w-0">
                    <Slab app={app} linkFor={linkFor} server={course.id} />
                  </li>
                ))}
                {plan.grouped > 0 ? (
                  <li className="min-w-0">
                    <div className={cx(SLAB, "elev-seated bg-surface-raised")}>
                      <span className="leading-tight font-semibold">{t("common.fleetCourses.running", { count: plan.grouped })}</span>
                    </div>
                  </li>
                ) : null}
              </ul>
            )}
          </li>
        );
      })}
    </ul>
  );
}
