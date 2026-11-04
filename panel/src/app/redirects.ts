/**
 * The URLs that change in 4.0, as data and nothing else (docs/DESIGN.md, NV-18). This file names
 * no route type and imports nothing, so the browser tests (e2e/redirects.spec.ts) read it without
 * pulling the whole console into their compilation; app/nav.ts gives the rows their types
 * (`ConsolePath`), so a path that is not a route of the router stops the build there.
 *
 * A path is a route of the router with `$params` for its variable parts.
 */

/**
 * Each old address and where its screen is now (2.9 of the navigation design); `docs/UPGRADING-4.0.md`
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
  { from: "/server/logs", to: "/logs", search: { source: "system" }, state: "live" },
  // The account page has no filters to carry: what the old address carried is dropped.
  { from: "/settings/security", to: "/account/security", state: "live", plain: true },
  { from: "/settings/servers", to: "/fleet/servers", state: "live" },
  { from: "/apps/$domain/settings/builds", to: "/apps/$domain/settings/build", state: "live" },
  // 4.1: a server's page is ten sections. The updates joined the system, storage is files, and the
  // firewall is Network and ports (the firewall's old address is a view of the Security tab,
  // `?view=firewall`, which the route sends on with its search). The ports an application is published
  // on stay in Domains (/domains/ports, Published ports): that is a way to reach it.
  { from: "/server/storage", to: "/server/files", state: "live", plain: true },
  // 4.2: the machine's network and its security are areas of the bar, and the views of the System section are
  // sections of the server. The old addresses carry the view they asked for (`?view=`), which each route's
  // `beforeLoad` reads: the firewall of Security's old tab was Network's already, and the interfaces of the
  // System tab of 4.0. What each used to be called is a palette keyword (RENAMED).
  { from: "/domains/ports", to: "/network", state: "live", plain: true },
  { from: "/domains/tunnels", to: "/network/tunnels", state: "live", plain: true },
  { from: "/server/network", to: "/network/listening", state: "live" },
  { from: "/server/security", to: "/security", state: "live", plain: true },
  { from: "/server/system", to: "/server/updates", state: "live", plain: true },
  // 4.1: what warns an operator is an area of the bar.
  // The page of Alerts has no filters to carry: what the old address carried is dropped.
  { from: "/settings/alerts", to: "/alerts", state: "live", plain: true },
  { from: "/settings/probes", to: "/alerts/probes", state: "live", plain: true },
  // 4.1: the engines are database servers, and the page that listed them is the section of that name.
  { from: "/databases/engines", to: "/databases/servers", state: "live", plain: true },
] as const;

/**
 * New routes whose page does not exist yet. Until it does, each opens the screen that holds the
 * same thing today, so the route is never an empty page and nothing is stranded. Empty now: the
 * account, its sign-in security and Controls have their pages. A lane that adds a route before its
 * page puts a row here.
 */
export const INTERIM_ROWS: readonly { readonly from: string; readonly to: string; readonly after: string }[] = [];
