import { createFileRoute, redirect } from "@tanstack/react-router";

/** The section each kind of setting of the fleet is in now: its natural home (item 166). */
const HOME = {
  channel: "/settings/notifications",
  alert_rule: "/alerts",
  backup_destination: "/backups/destinations",
  registry: "/settings/registries",
  variable: "/settings/variables",
  console_link: "/settings/console",
} as const;

type Kind = keyof typeof HOME;

function validateSearch(search: Record<string, unknown>): { kind?: Kind } {
  const kind = search["kind"];
  return typeof kind === "string" && kind in HOME ? { kind: kind as Kind } : {};
}

/**
 * "Fleet settings" is not a page of its own since 4.3: each kind of setting the central carries is shown in the section
 * it belongs to, with the servers that follow it. The old address keeps working: with a `?kind=` it opens that kind's
 * section, and without one the hub of Settings, where every section is a door (app/redirects.ts).
 */
export const Route = createFileRoute("/_console/settings/fleet")({
  validateSearch,
  beforeLoad: ({ search }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: search.kind === undefined ? "/settings" : HOME[search.kind], replace: true });
  },
});
