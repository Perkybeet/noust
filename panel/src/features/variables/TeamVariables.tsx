import { CommandHint } from "../../components/page/CommandHint";
import { useDocumentTitle } from "../../app/documentTitle";
import { useT } from "../../i18n";
import { useOrganization } from "../teams/useOrganization";
import { useTeam } from "../teams/TeamLayout";
import { SharedVariables } from "./SharedVariables";

/**
 * The team's shared variables, a tab of the team's page. Defined once here, an application of
 * the team refers to one with `{{team.NAME}}`; a project or an environment of the team keeps
 * its own beside the applications it holds.
 */
export function TeamVariables() {
  const t = useT();
  const team = useTeam();
  const org = useOrganization();
  useDocumentTitle(t("variables.shared.team.documentTitle", { team: team.name }), 1);
  return (
    <div className="flex flex-col gap-6">
      <SharedVariables scope={{ kind: "team", team: team.slug }} name={team.name} canManage={org.can("apps.manage", team.slug)} command={false} />
      <CommandHint command={`noust variables list team --team ${team.slug}`} label={t("common.words.fromTerminal")} />
    </div>
  );
}
