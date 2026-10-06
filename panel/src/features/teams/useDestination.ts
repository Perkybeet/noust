import { useMemo, useState } from "react";

import { destinationBody, resolveDestination } from "./destination";
import type { DestinationView, Place } from "./destination";
import { useOrganization } from "./useOrganization";

export interface Destination extends DestinationView {
  /** Chooses where it goes; what no longer fits the team chosen is replaced by its defaults. */
  choose: (patch: Partial<Place>) => void;
  /** What the request carries: nothing when there was nothing to choose. */
  body: { team?: string; project?: string; environment?: string };
}

/**
 * Where a new application, site or database goes, for a form: the team the operator works in,
 * its default project and production unless they choose another, and nothing at all to ask
 * on a server nobody organised (`needed` false, an empty `body`, so the server decides as it
 * always did). Only the teams where the operator may create applications are offered.
 *
 * `initial` is where the form was asked to start (a link from a project's page): it is read
 * while the operator has chosen nothing, so a link followed later still moves it.
 */
export function useDestination(initial: Partial<Place> = {}): Destination {
  const org = useOrganization();
  const [choice, setChoice] = useState<Partial<Place>>({});
  const view = useMemo(
    () =>
      resolveDestination({
        teams: org.teams.filter((team) => org.can("apps.manage", team.slug)).map((team) => ({ slug: team.slug, name: team.name })),
        projects: org.projects,
        environments: org.environments,
        active: org.active,
        choice,
        initial,
      }),
    // `initial` is read by value: a new object with the same places is not a new place.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [org.teams, org.projects, org.environments, org.active, org.can, choice, initial.team, initial.project, initial.environment],
  );
  return {
    ...view,
    needed: org.available && view.needed,
    choose: (patch) => {
      setChoice((previous) => {
        const next = { ...previous, ...patch };
        // Another team starts from its own default project, another project from its production.
        if (patch.team !== undefined && patch.team !== previous.team) {
          delete next.project;
          delete next.environment;
        } else if (patch.project !== undefined && patch.project !== previous.project) {
          delete next.environment;
        }
        return next;
      });
    },
    body: org.available ? destinationBody(view) : {},
  };
}
