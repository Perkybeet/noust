import { useQuery } from "@tanstack/react-query";

import { environmentsQuery, myTeamsQuery, projectsQuery } from "../../api/queries/teams";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Field } from "../../components/ui/Field";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { NO_REACH } from "./tokenReach";
import type { ReachKind, TokenReach } from "./tokenReach";

export interface TokenReachFieldsProps {
  reach: TokenReach;
  onChange: (reach: TokenReach) => void;
  /** The server's refusal of a field, beside it. */
  errors: { team?: string | undefined; project?: string | undefined; environment?: string | undefined };
}

/**
 * Binding a token to a team, a project or an environment, so a client's token sees that and
 * nothing else. Drawn only where the server answers with teams (a server nobody organised has
 * nothing to bind to): the choice is a question of what the token may reach, not of its
 * permissions, which are the scope above.
 */
export function TokenReachFields({ reach, onChange, errors }: TokenReachFieldsProps) {
  const t = useT();
  const teams = useQuery(myTeamsQuery());
  const projects = useQuery(projectsQuery());
  const environments = useQuery(environmentsQuery());
  if (teams.data === undefined || teams.data.teams.length === 0) return null;

  const projectsOfTeam = (projects.data?.projects ?? []).filter((item) => item.team === reach.team);
  const environmentsOfProject = (environments.data?.environments ?? []).filter((item) => item.team === reach.team && item.project === reach.project);
  const choose = (kind: ReachKind): void => {
    onChange({ ...NO_REACH, kind, team: kind === "all" ? "" : reach.team });
  };
  const named = reach.kind === "all" ? "" : reach[reach.kind];

  return (
    <div className="flex flex-col gap-4">
      <ChoiceCards
        legend={t("access.tokenReach.legend")}
        options={[
          { value: "all", label: t("access.tokenReach.all"), description: t("access.tokenReach.allHelp") },
          { value: "team", label: t("access.tokenReach.team"), description: t("access.tokenReach.teamHelp") },
          { value: "project", label: t("access.tokenReach.project"), description: t("access.tokenReach.projectHelp") },
          { value: "environment", label: t("access.tokenReach.environment"), description: t("access.tokenReach.environmentHelp") },
        ]}
        value={reach.kind}
        onValueChange={choose}
      />
      {reach.kind !== "all" ? (
        <Field label={t("access.tokenReach.teamField")} nativeLabel={false} error={errors.team}>
          <Select
            options={teams.data.teams.map((item) => ({ value: item.slug, label: item.name }))}
            value={reach.team === "" ? null : reach.team}
            placeholder={t("access.tokenReach.pick")}
            onValueChange={(team) => {
              onChange({ ...reach, team, project: "", environment: "" });
            }}
          />
        </Field>
      ) : null}
      {reach.kind === "project" || reach.kind === "environment" ? (
        <Field label={t("access.tokenReach.projectField")} nativeLabel={false} error={errors.project}>
          <Select
            options={projectsOfTeam.map((item) => ({ value: item.slug, label: item.name }))}
            value={reach.project === "" ? null : reach.project}
            placeholder={projectsOfTeam.length === 0 ? t("access.tokenReach.none") : t("access.tokenReach.pick")}
            disabled={reach.team === ""}
            onValueChange={(project) => {
              onChange({ ...reach, project, environment: "" });
            }}
          />
        </Field>
      ) : null}
      {reach.kind === "environment" ? (
        <Field label={t("access.tokenReach.environmentField")} nativeLabel={false} error={errors.environment}>
          <Select
            options={environmentsOfProject.map((item) => ({ value: item.slug, label: item.name }))}
            value={reach.environment === "" ? null : reach.environment}
            placeholder={environmentsOfProject.length === 0 ? t("access.tokenReach.none") : t("access.tokenReach.pick")}
            disabled={reach.project === ""}
            onValueChange={(environment) => {
              onChange({ ...reach, environment });
            }}
          />
        </Field>
      ) : null}
      {reach.kind !== "all" && named !== "" ? <p className="text-13 text-fg-muted">{t("access.tokenReach.boundNotice", { where: named })}</p> : null}
    </div>
  );
}
