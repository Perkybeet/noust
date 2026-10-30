/**
 * The Web server section's rules, apart from any page: which server a move goes to, what the plan
 * says about it in numbers, and how a site's findings are grouped so that what protects the site
 * (and the move would lose) is read before what is only carried over in another form.
 */

import type { Finding, MovePlan, SitePlan, WebServerName, WebserverStatus } from "./queries";

/** The other web server: nginx and Apache are the only two. */
export function otherThan(server: WebServerName): WebServerName {
  return server === "nginx" ? "apache" : "nginx";
}

/** How a web server is written for a person. */
export function display(server: string): string {
  return server === "apache" ? "Apache" : server === "nginx" ? "nginx" : server;
}

/** The web server sites are moved from: the one that holds ports 80 and 443, or else the one the setting names. */
export function movingFrom(status: WebserverStatus): WebServerName {
  return status.holds_ports ?? status.webserver;
}

/** How many applications and sites are behind a web server. */
export function behind(status: WebserverStatus, server: WebServerName): number {
  const row = status.servers[server];
  return row === undefined ? 0 : row.applications + row.sites;
}

/** Whether there is something to move: sites are behind the server that is serving. */
export function hasSomethingToMove(status: WebserverStatus): boolean {
  return behind(status, movingFrom(status)) > 0;
}

/**
 * The sites in the order the person needs to read them: what stays where it is first (it is why the move
 * says "8 of 9"), then the ones that lose a protection, then those that lose something else, then the rest by name.
 */
export function orderSites(sites: readonly SitePlan[]): SitePlan[] {
  const weight = (site: SitePlan): number => {
    if (!site.movable) return 0;
    const found = groupFindings(site);
    return found.protections.length > 0 ? 1 : found.loses.length > 0 ? 2 : 3;
  };
  return [...sites].sort((a, b) => weight(a) - weight(b) || a.domain.localeCompare(b.domain, "en"));
}

export interface Findings {
  /** Stops the site until it is fixed. */
  blocks: readonly Finding[];
  /** What kept visitors out or slowed them down and the new server has nothing for. */
  protections: readonly Finding[];
  /** What the new site will not do, other than protections. */
  loses: readonly Finding[];
  /** Carried over in another form, or a knob that changes no answer. */
  notes: readonly Finding[];
}

/** A site's findings in the order they matter: what blocks, what protection is lost, what else is lost, the notes. */
export function groupFindings(site: Pick<SitePlan, "findings">): Findings {
  const all = site.findings ?? [];
  return {
    blocks: all.filter((finding) => finding.severity === "blocks"),
    protections: all.filter((finding) => finding.severity === "loses" && finding.protection),
    loses: all.filter((finding) => finding.severity === "loses" && !finding.protection),
    notes: all.filter((finding) => finding.severity === "notes"),
  };
}

export interface PlanNumbers {
  sites: number;
  movable: number;
  blocked: number;
  /** Things the new sites will not do: what "accept" is about. */
  losses: number;
  /** Of those, protections. */
  protections: number;
}

/** What a plan comes to, in numbers a person reads at a glance. */
export function planNumbers(plan: MovePlan): PlanNumbers {
  const grouped = plan.sites.map(groupFindings);
  return {
    sites: plan.sites.length,
    movable: plan.sites.filter((site) => site.movable).length,
    blocked: plan.sites.filter((site) => !site.movable).length,
    losses: plan.losses,
    protections: grouped.reduce((sum, found) => sum + found.protections.length, 0),
  };
}

/** Whether going ahead needs the person to accept something first: the new sites will not do all the old ones did. */
export function needsAcceptance(plan: MovePlan): boolean {
  return plan.losses > 0;
}

/** Whether the move can be started at all: nothing stops it, the whole or any site. */
export function canMove(plan: MovePlan): boolean {
  return !plan.blocked && plan.sites.length > 0;
}
