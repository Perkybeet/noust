/**
 * The URLs that change in 4.0, as data and nothing else (docs/DESIGN.md, NV-18). This file names
 * no route type and imports nothing, so the browser tests (e2e/redirects.spec.ts) read it without
 * pulling the whole console into their compilation; app/nav.ts gives the rows their types
 * (`ConsolePath`), so a path that is not a route of the router stops the build there.
 *
 * A path is a route of the router with `$params` for its variable parts.
 */

/**
 * Each old address and where its screen is now (2.9 of the navigation design); `docs/MIGRATING-4.0.md`
 * carries this table as "where it is now".
 *
 * `live`: the old address redirects. `pending`: its screen is still where it was because the screen
 * at `to` does not hold everything yet, and redirecting now would strand part of it; the lane in
 * `after` flips it, here and in the route file, when it does. `search` is what the destination
 * gets besides the search the old address carried.
 */
export const REDIRECT_ROWS = [
  { from: "/teams", to: "/projects/teams", state: "live" },
  { from: "/services", to: "/server/services", state: "live" },
  { from: "/services/$name", to: "/server/services/$name", state: "live" },
  { from: "/server/logs", to: "/logs", search: { source: "system" }, state: "pending", after: "D: Logs gains the System source" },
  // The account page has no filters to carry: what the old address carried is dropped.
  { from: "/settings/security", to: "/account/security", state: "live", plain: true },
  { from: "/settings/servers", to: "/fleet/servers", state: "pending", after: "D: the fleet's servers list is the one list" },
  { from: "/apps/$domain/settings/builds", to: "/apps/$domain/settings/build", state: "pending", after: "P: Build holds the sandbox card" },
] as const;

/**
 * New routes whose page does not exist yet. Until it does, each opens the screen that holds the
 * same thing today, so the route is never an empty page and nothing is stranded. Empty now: the
 * account, its sign-in security and Controls have their pages. A lane that adds a route before its
 * page puts a row here.
 */
export const INTERIM_ROWS: readonly { readonly from: string; readonly to: string; readonly after: string }[] = [];
