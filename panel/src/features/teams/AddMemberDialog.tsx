import { useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { addMember, TEAM_ROLES } from "../../api/queries/teams";
import type { TeamRole } from "../../api/queries/teams";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";
import { splitErrors } from "../settings/formErrors";
import { refreshOrganization } from "./refresh";
import { teamRoleDescription, teamRoleLabel } from "./roles";

export interface AddMemberDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  team: { slug: string; name: string };
}

/**
 * Putting an account that exists in a team, with its role there. A person who has no account
 * yet is invited instead (that is the security officer's call, because it creates a
 * credential); the dialog says so, and the server's refusal for an unknown account says it
 * again where the name was typed.
 */
export function AddMemberDialog({ open, onOpenChange, team }: AddMemberDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const nameRef = useRef<HTMLInputElement>(null);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<TeamRole>("viewer");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const errors = splitErrors(error, ["username", "role"] as const);

  // Each opening starts empty, not from what the last one left typed.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setUsername("");
      setRole("viewer");
      setError(null);
      setPending(false);
    }
  }

  const close = (next: boolean): void => {
    if (!pending) onOpenChange(next);
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (pending) return;
    const name = username.trim();
    if (name === "") {
      setError({ fields: { username: t("teams.members.accountRequired") } });
      nameRef.current?.focus();
      return;
    }
    setPending(true);
    setError(null);
    addMember(team.slug, name, role).then(
      async () => {
        await refreshOrganization(queryClient);
        setPending(false);
        onOpenChange(false);
      },
      (failure: unknown) => {
        setPending(false);
        setError(failure);
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="md"
      initialFocus={nameRef}
      title={t("teams.members.addTitle", { team: team.name })}
      description={t("teams.members.addDescription")}
      footer={
        <>
          <Button disabled={pending} onClick={() => close(false)}>
            {t("teams.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={pending}>
            {t("teams.members.addSubmit")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("teams.members.addFailed")} /> : null}
        <Field label={t("teams.members.account")} description={t("teams.members.accountHelp")} error={errors.fields.username}>
          <Input
            ref={nameRef}
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            maxLength={64}
            value={username}
            onValueChange={(value: string) => {
              setUsername(value);
            }}
          />
        </Field>
        <ChoiceCards
          legend={t("teams.members.roleLegend")}
          options={TEAM_ROLES.map((value) => ({ value, label: teamRoleLabel(t, value), description: teamRoleDescription(t, value) }))}
          value={role}
          onValueChange={setRole}
          error={errors.fields.role}
        />
      </form>
    </Dialog>
  );
}
