/**
 * What the palette finds on the other servers of the fleet: the hits of `GET /api/views/search` (applications,
 * databases, sites and certificates, found by their name, by what else their record says and by the server they are
 * on), as palette commands that open the thing on its own server (checklist M-23, task 18). The server does the
 * searching, so a thousand applications are not read into the browser to find one; this is the shape of what comes
 * back. Pure, so every rule is tested without a palette.
 *
 * What is on the server the console is on is the palette's own index already (`resourceCommands`), so a hit that is
 * local is not offered twice, and a server is found by the `Servers` group the register gives.
 */

import type { ReactElement } from "react";

import type { SearchHit } from "../api/queries/views";
import { OBJECT_ICONS } from "../components/ui/objectIcons";
import { linkOfHref } from "../features/fleet/links";
import type { Command, CommandGroup } from "./CommandPalette";

/** The kinds a hit may be and the palette group each belongs to; a kind the palette has no group for is not offered. */
const GROUPS: Readonly<Record<string, { group: CommandGroup; icon: () => ReactElement }>> = {
  app: { group: "Applications", icon: () => <OBJECT_ICONS.app /> },
  database: { group: "Databases", icon: () => <OBJECT_ICONS.database /> },
  site: { group: "Domains", icon: () => <OBJECT_ICONS.domain /> },
  certificate: { group: "Certificates", icon: () => <OBJECT_ICONS.certificate /> },
};

/** How many hits the palette asks the central for: more than it shows, so what it keeps is the best of them. */
export const FLEET_SEARCH_LIMIT = 20;

/**
 * The commands for what the search found on other servers. Each says which server it is on beside its name, and
 * is found by the words that were typed, since the server matched them in a field the label does not show (its
 * owner, its source, a name it answers on).
 */
export function fleetSearchCommands(hits: readonly SearchHit[], query: string, navigate: (target: { to: string; search?: Record<string, unknown> }) => void): Command[] {
  return hits.flatMap((hit): Command[] => {
    const kind = GROUPS[hit.kind];
    if (kind === undefined || hit.local) return [];
    const there = linkOfHref(hit.href);
    return [
      {
        id: `fleet:${hit.kind}:${hit.node}:${hit.name}`,
        group: kind.group,
        label: hit.name,
        // Which server it is on is what tells two of one name apart.
        detail: hit.detail !== null && hit.detail !== undefined && hit.detail !== "" ? `${hit.node}, ${hit.detail}` : hit.node,
        icon: kind.icon(),
        keywords: [query, hit.node, ...Object.values(hit.labels ?? {})].join(" "),
        kind: "navigate",
        run: () => navigate({ to: there.to, search: there.search }),
      },
    ];
  });
}
