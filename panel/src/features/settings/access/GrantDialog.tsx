import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { ApprovalPendingError } from "../../../api/errors";
import { accessKeys, accessRolesQuery, bindRole } from "../../../api/queries/access";
import type { ScopeKind } from "../../../api/queries/access";
import { environmentsQuery, myTeamsQuery, projectsQuery } from "../../../api/queries/teams";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Select } from "../../../components/ui/Select";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { accountsQuery } from "../accounts/api";
import { focusFirstInvalid, splitErrors } from "../formErrors";

const FIELDS = ["username", "role", "scope", "team", "project", "environment"] as const;
const KINDS: readonly ScopeKind[] = ["environment", "project", "team", "instance"];

export interface GrantDialogProps {
  onClose: () => void;
}

/**
 * Giving an account a role where it is needed: on an environment, a project, a team or the whole
 * server. Only the roles that can be held in the chosen place are offered; the server decides the
 * rest (one person cannot govern and run at once, an auditor holds nothing more) and its words are
 * shown. Where approvals apply the grant becomes a request another person decides, which the
 * console handles itself.
 */
export function GrantDialog({ onClose }: GrantDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const accounts = useQuery(accountsQuery());
  const roles = useQuery(accessRolesQuery());
  const teams = useQuery(myTeamsQuery());
  const projects = useQuery(projectsQuery());
  const environments = useQuery(environmentsQuery());
  const [username, setUsername] = useState("");
  const [kind, setKind] = useState<ScopeKind>("project");
  const [role, setRole] = useState("");
  const [team, setTeam] = useState("");
  const [project, setProject] = useState("");
  const [environment, setEnvironment] = useState("");
  const [tried, setTried] = useState(false);
  const required = t("settings.shared.required");

  const offered = (roles.data?.roles ?? []).filter((candidate) => (candidate.scopes ?? []).includes(kind));
  const projectsOfTeam = (projects.data?.projects ?? []).filter((item) => item.team === team);
  const environmentsOfProject = (environments.data?.environments ?? []).filter((item) => item.team === team && item.project === project);

  const grant = useMutation({
    mutationFn: () =>
      bindRole({
        username,
        role,
        scope: kind,
        ...(kind !== "instance" ? { team } : {}),
        ...(kind === "project" || kind === "environment" ? { project } : {}),
        ...(kind === "environment" ? { environment } : {}),
      }),
    onSuccess: (binding) => {
      const label = roles.data?.roles.find((candidate) => candidate.name === binding.role)?.label ?? binding.role;
      toast.success(t("access.grants.grantedToast", { account: binding.username, role: label, where: binding.scope }));
      void queryClient.invalidateQueries({ queryKey: accessKeys.all });
      onClose();
    },
    onError: (error) => {
      // Waiting for, or refused by, a second person: the approval dialog already said so.
      if (error instanceof ApprovalPendingError) onClose();
    },
  });
  const errors = splitErrors(grant.error instanceof ApprovalPendingError ? null : grant.error, FIELDS);

  const placed =
    kind === "instance" ||
    (team !== "" && (kind === "team" || (project !== "" && (kind === "project" || environment !== ""))));
  const ready = username !== "" && role !== "" && placed;

  // The button is never off for a missing choice: it says which one, beside it, and goes there.
  const submit = (): void => {
    if (grant.isPending) return;
    if (!ready) {
      setTried(true);
      focusFirstInvalid(formId);
      return;
    }
    grant.mutate();
  };

  const chooseKind = (next: ScopeKind): void => {
    setKind(next);
    setRole("");
    setEnvironment("");
    if (next === "team" || next === "instance") setProject("");
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !grant.isPending) onClose();
      }}
      size="md"
      title={t("access.grants.dialog.title")}
      description={t("access.grants.dialog.description")}
      footer={
        <>
          <Button disabled={grant.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={grant.isPending}>
            {t("access.grants.dialog.action")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
          event.preventDefault();
          submit();
        }}
      >
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("access.grants.dialog.failed")} /> : null}
        <Field label={t("access.grants.dialog.account")} nativeLabel={false} error={errors.fields.username ?? (tried && username === "" ? required : undefined)}>
          <Select
            options={(accounts.data?.accounts ?? []).filter((account) => account.status === "active").map((account) => ({ value: account.username, label: account.username }))}
            value={username === "" ? null : username}
            onValueChange={setUsername}
            mono
          />
        </Field>
        <Field label={t("access.grants.dialog.where")} nativeLabel={false} error={errors.fields.scope}>
          <Select options={KINDS.map((value) => ({ value, label: t(`access.scope.${value}`) }))} value={kind} onValueChange={chooseKind} />
        </Field>
        {kind !== "instance" ? (
          <Field label={t("access.grants.dialog.team")} nativeLabel={false} error={errors.fields.team ?? (tried && team === "" ? required : undefined)}>
            <Select
              options={(teams.data?.teams ?? []).map((item) => ({ value: item.slug, label: item.name }))}
              value={team === "" ? null : team}
              onValueChange={(value) => {
                setTeam(value);
                setProject("");
                setEnvironment("");
              }}
              {...(teams.data?.teams.length === 0 ? { placeholder: t("access.grants.dialog.noTeams") } : {})}
            />
          </Field>
        ) : null}
        {kind === "project" || kind === "environment" ? (
          <Field label={t("access.grants.dialog.project")} nativeLabel={false} error={errors.fields.project ?? (tried && project === "" ? required : undefined)}>
            <Select
              options={projectsOfTeam.map((item) => ({ value: item.slug, label: item.name }))}
              value={project === "" ? null : project}
              onValueChange={(value) => {
                setProject(value);
                setEnvironment("");
              }}
              disabled={team === ""}
            />
          </Field>
        ) : null}
        {kind === "environment" ? (
          <Field label={t("access.grants.dialog.environment")} nativeLabel={false} error={errors.fields.environment ?? (tried && environment === "" ? required : undefined)}>
            <Select
              options={environmentsOfProject.map((item) => ({ value: item.slug, label: item.name }))}
              value={environment === "" ? null : environment}
              onValueChange={setEnvironment}
              disabled={project === ""}
            />
          </Field>
        ) : null}
        <Field label={t("access.grants.dialog.role")} nativeLabel={false} description={t("access.grants.dialog.roleHelp")} error={errors.fields.role ?? (tried && role === "" ? required : undefined)}>
          <Select options={offered.map((candidate) => ({ value: candidate.name, label: candidate.label }))} value={role === "" ? null : role} onValueChange={setRole} />
        </Field>
      </form>
    </Dialog>
  );
}
