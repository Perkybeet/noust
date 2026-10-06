import { useId } from "react";

import { Field } from "../../components/ui/Field";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import type { Destination } from "./useDestination";

/**
 * Which team, project and environment a new resource belongs to, asked only when there is more
 * than one place to put it: a server with one team, one project and one environment draws
 * nothing, so the wizard's steps and clicks are what they were. A field appears only when it
 * has more than one answer (a team with a single project does not ask for it), and the
 * defaults - the team the operator works in, its default project, production - are what is
 * chosen until they say otherwise, so the common case costs no click.
 */
export function DestinationFields({ destination, help }: { destination: Destination; help?: string }) {
  const t = useT();
  const legendId = useId();
  if (!destination.needed || destination.value === null) return null;
  const { value } = destination;
  const fields = [
    destination.showTeam ? "team" : null,
    destination.projects.length > 1 ? "project" : null,
    destination.environments.length > 1 ? "environment" : null,
  ].filter((field) => field !== null);
  if (fields.length === 0) return null;

  return (
    <div role="group" aria-labelledby={legendId} className="flex flex-col gap-3">
      <div>
        <p id={legendId} className="text-13 font-medium text-fg">
          {t("teams.destination.title")}
        </p>
        <p className="mt-0.5 max-w-measure text-12 text-fg-muted">{help ?? t("teams.destination.help")}</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        {destination.showTeam ? (
          <Field label={t("teams.teamLabel")} nativeLabel={false}>
            <Select aria-label={t("teams.teamLabel")} value={value.team} options={destination.teams.map((team) => ({ value: team.slug, label: team.name }))} onValueChange={(team) => destination.choose({ team })} />
          </Field>
        ) : null}
        {destination.projects.length > 1 ? (
          <Field label={t("teams.destination.project")} nativeLabel={false}>
            <Select
              aria-label={t("teams.destination.project")}
              value={value.project}
              options={destination.projects.map((project) => ({ value: project.slug, label: project.name }))}
              onValueChange={(project) => destination.choose({ project })}
            />
          </Field>
        ) : null}
        {destination.environments.length > 1 ? (
          <Field label={t("teams.destination.environment")} nativeLabel={false}>
            <Select
              aria-label={t("teams.destination.environment")}
              value={value.environment}
              options={destination.environments.map((environment) => ({ value: environment.slug, label: environment.name }))}
              onValueChange={(environment) => destination.choose({ environment })}
            />
          </Field>
        ) : null}
      </div>
    </div>
  );
}
