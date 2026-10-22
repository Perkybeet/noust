import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { deleteTeam, renameTeam, teamKeys } from "../../api/queries/teams";
import type { MyTeams } from "../../api/queries/teams";
import { announce } from "../../app/Announcer";
import { CommandHint } from "../../components/page/CommandHint";
import { DangerAction, DangerZone } from "../../components/page/DangerZone";
import { ErrorBlock } from "../../components/page/QueryState";
import { SaveBar } from "../../components/page/SaveBar";
import { Sections } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Field } from "../../components/ui/Field";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { useSudoFirst } from "../app/settings/formParts";
import { splitErrors } from "../settings/formErrors";
import { heldBy } from "./organization";
import { refreshOrganization } from "./refresh";
import { useTeam } from "./TeamLayout";
import { useOrganization } from "./useOrganization";

/** Rename the team: one field, saved from the bar, which is there from the first frame. */
function NameCard({ editable }: { editable: boolean }) {
  const t = useT();
  const team = useTeam();
  const queryClient = useQueryClient();
  const formId = useId();
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const value = draft ?? team.name;
  const changes = draft !== null && draft.trim() !== team.name ? 1 : 0;
  const errors = splitErrors(error, ["name"] as const, "name");

  const save = (event?: SyntheticEvent): void => {
    event?.preventDefault();
    if (changes === 0 || saving) return;
    setSaving(true);
    setError(null);
    renameTeam(team.slug, value.trim()).then(
      async () => {
        await refreshOrganization(queryClient);
        setDraft(null);
        setSaving(false);
        announce(t("teams.settings.renamed", { name: value.trim() }));
      },
      (failure: unknown) => {
        setSaving(false);
        setError(failure);
      },
    );
  };

  return (
    <form id={formId} noValidate onSubmit={save} className="flex flex-col gap-4">
      {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("teams.settings.renameFailed")} /> : null}
      <Card title={t("common.words.name")} description={t.rich("teams.settings.nameDescription", { slug: <Mono>{team.slug}</Mono> })}>
        <Field label={t("teams.settings.nameLabel")} error={errors.fields.name} disabled={!editable}>
          <Input
            autoComplete="off"
            maxLength={128}
            readOnly={!editable}
            value={value}
            onValueChange={(next: string) => {
              setDraft(next);
            }}
          />
        </Field>
      </Card>
      {editable ? (
        <SaveBar
          changes={changes}
          form={formId}
          saving={saving}
          onDiscard={() => {
            setDraft(null);
            setError(null);
          }}
        />
      ) : null}
    </form>
  );
}

/**
 * The team's own settings: its name, and deleting it. The default team is never deleted (it
 * receives what nobody assigned) and one that still holds applications, sites, databases or
 * backup schedules is not either; the button is there, and says why it will not work, rather
 * than failing after the operator typed the name. Deleting asks for the short name to be typed.
 */
export function TeamSettings() {
  const t = useT();
  const team = useTeam();
  const { node } = useNode();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const org = useOrganization();
  const sudoFirst = useSudoFirst();
  const [deleting, setDeleting] = useState(false);
  const governs = org.can("teams.manage", team.slug);
  const { apps, sites, databases, schedules } = heldBy(team.resources);
  const holds = apps + sites + databases + schedules;
  const isDefault = team.slug === "default";
  const reason = isDefault ? t("teams.settings.deleteDefault") : holds > 0 ? t("teams.settings.deleteHeld", { apps, sites, databases, schedules }) : null;
  const Delete = ICONS.delete;

  return (
    <Sections>
      <NameCard editable={governs} />
      {governs ? (
        <DangerZone title={t("common.words.delete")} description={t("teams.settings.dangerDescription")}>
          <DangerAction
            title={t("teams.settings.deleteActionTitle")}
            description={reason ?? t("teams.settings.deleteActionDescription")}
            action={
              // design-exception: danger-button-outside-confirm the button of a DangerAction, which opens the ConfirmDialog below
              <Button
                variant="danger"
                icon={<Delete aria-hidden="true" />}
                disabled={reason !== null}
                onClick={() => sudoFirst(() => setDeleting(true), t("teams.settings.deleteCouldNotStart", { name: team.name }))}
              >
                {t("teams.settings.deleteButton")}
              </Button>
            }
          />
        </DangerZone>
      ) : null}
      <CommandHint command={`noust team rename ${team.slug} "New name"`} label={t("teams.fromTerminal")} />
      <ConfirmDialog friction="type"
        open={deleting}
        onOpenChange={setDeleting}
        server={node}
        title={t("common.words.deleteNamed", { name: team.name })}
        description={t("teams.settings.deleteDescription", { name: team.name })}
        actionLabel={t("teams.settings.deleteAction")}
        confirmText={team.slug}
        onConfirm={async () => {
          await deleteTeam(team.slug);
          // The list of teams forgets it before anything reads it again, and this page leaves
          // before the queries are refreshed: asking the server for a team it just deleted would
          // be answered "not found", by this page's own read and by the list's row for it.
          queryClient.setQueryData<MyTeams>(teamKeys.mine, (mine) => {
            if (mine === undefined) return mine;
            const { active, ...rest } = mine;
            const kept = { ...rest, teams: mine.teams.filter((candidate) => candidate.slug !== team.slug) };
            return active === undefined || active === team.slug ? kept : { ...kept, active };
          });
          await navigate({ to: "/projects/teams" });
          await refreshOrganization(queryClient);
          announce(t("teams.settings.deleted", { name: team.name }));
        }}
      />
    </Sections>
  );
}
