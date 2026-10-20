import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { CopyTextButton } from "../../../components/ui/CopyTextButton";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatMoment } from "../../../lib/format";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { TEAM_ROLES } from "../../../api/queries/teams";
import type { TeamRole } from "../../../api/queries/teams";
import { teamRoleDescription, teamRoleLabel } from "../../teams/roles";
import { splitErrors } from "../formErrors";
import { accountKeys, invite } from "./api";
import type { Account, InvitationIssued } from "./api";
import { ROLES, incompatible, roleDescription, roleLabel } from "./roles";
import type { Role } from "./roles";
import { samePerson } from "./data";

const FIELDS = ["username", "role", "team", "team_role", "display_name", "person_ref", "email", "expires_hours"] as const;

const EXPIRY_HOURS = ["24", "72", "168", "336"] as const;

function expiryLabel(t: T, hours: (typeof EXPIRY_HOURS)[number]): string {
  switch (hours) {
    case "24":
      return t("accounts.invite.expiry.day");
    case "72":
      return t("accounts.invite.expiry.threeDays");
    case "168":
      return t("accounts.invite.expiry.week");
    case "336":
      return t("accounts.invite.expiry.twoWeeks");
  }
}

/** The link that opens an invitation: the code after `#`, which the browser never sends. */
export function invitationLink(code: string, origin: string = window.location.origin): string {
  return `${origin}/invite#${encodeURIComponent(code)}`;
}

/** The invitation, once: the link to send and the code, which cannot be shown again. */
function IssuedOnce({ t, issued, expiresAt }: { t: T; issued: InvitationIssued; expiresAt: number }) {
  const link = invitationLink(issued.code);
  const expires = formatMoment(new Date(expiresAt), t.locale);
  return (
    <div className="flex flex-col gap-4">
      {issued.emailed ? (
        <Notice tone="success" title={t("accounts.invite.mailedTitle")}>
          {t("accounts.invite.mailed", { address: issued.account.email ?? "" })}
        </Notice>
      ) : issued.email_error ? (
        // The mail is the convenience, the link is the invitation: a failure is said, verbatim,
        // and the link below still works.
        <Notice tone="warning" title={t("accounts.invite.notMailedTitle")}>
          {issued.email_error}
        </Notice>
      ) : null}
      <Notice tone="warning" title={t("accounts.invite.onceTitle")}>
        {t("accounts.invite.onceWarning", { when: expires })}
      </Notice>
      <Field label={t("accounts.invite.linkLabel")} description={t("accounts.invite.linkHint")}>
        <Input mono readOnly value={link} data-testid="invitation-link" />
      </Field>
      <div className="flex flex-wrap gap-2">
        <CopyTextButton value={link} variant="primary">
          {t("accounts.invite.copyLink")}
        </CopyTextButton>
        <CopyTextButton value={issued.code}>{t("accounts.invite.copyCode")}</CopyTextButton>
      </div>
      <p className="text-13 text-fg-muted">
        {t.rich("accounts.invite.codeLine", { code: <Mono key="code">{issued.code}</Mono> })}
      </p>
    </div>
  );
}

export interface InviteDialogProps {
  open: boolean;
  onClose: () => void;
  /** Every account, to warn about roles one person may not hold together. */
  accounts: readonly Account[];
  /** A new invitation for an existing account: recovery, which replaces its password and factor. */
  recover?: Account | null;
  /**
   * Invite the person into a team: they belong to it only, with the role the operator chooses
   * there, and hold no role of their own on the server beyond reading (the team's owners
   * govern the rest). The team's own page offers it; the accounts page invites to the server.
   */
  team?: { slug: string; name: string } | null;
}

/**
 * Inviting a person: their name, role and how long the invitation lasts. The person sets
 * their own password and second factor from the link; nobody else ever knows either. For an
 * existing account it is how access is recovered.
 */
export function InviteDialog({ open, onClose, accounts, recover = null, team = null }: InviteDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [username, setUsername] = useState(recover?.username ?? "");
  const [displayName, setDisplayName] = useState("");
  const [person, setPerson] = useState("");
  const [mailTo, setMailTo] = useState("");
  const [role, setRole] = useState<Role>("operator");
  const [teamRole, setTeamRole] = useState<TeamRole>("viewer");
  const [expiry, setExpiry] = useState<(typeof EXPIRY_HOURS)[number]>("24");
  const [issued, setIssued] = useState<{ answer: InvitationIssued; expiresAt: number } | null>(null);
  const usernameRef = useRef<HTMLInputElement>(null);
  const formId = useId();

  const create = useMutation({
    mutationFn: () =>
      invite({
        username: username.trim(),
        expires_hours: Number(expiry),
        ...(recover === null ? { role: team === null ? role : "viewer" } : {}),
        ...(recover === null && team !== null ? { team: team.slug, team_role: teamRole } : {}),
        ...(recover === null && displayName.trim() !== "" ? { display_name: displayName.trim() } : {}),
        ...(recover === null && person.trim() !== "" ? { person_ref: person.trim() } : {}),
        ...(mailTo.trim() !== "" ? { email: mailTo.trim() } : {}),
      }),
    onSuccess: (result) => {
      setIssued({ answer: result, expiresAt: Date.now() + result.expires_in * 1000 });
      void queryClient.invalidateQueries({ queryKey: accountKeys.list });
    },
  });
  const errors = splitErrors(create.error, FIELDS);
  const clashes = recover === null && team === null ? samePerson(accounts, { username: username.trim(), person_ref: person }).filter((other) => incompatible(other.role, role)) : [];

  const onOpenChange = (next: boolean): void => {
    if (next || create.isPending) return;
    if (issued !== null) toast.success(t("accounts.invite.sentToast", { name: issued.answer.account.username }));
    onClose();
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (create.isPending) return;
    create.mutate();
  };

  if (issued !== null) {
    return (
      <Dialog
        open={open}
        onOpenChange={onOpenChange}
        size="md"
        title={t("accounts.invite.issuedTitle", { name: issued.answer.account.username })}
        description={t("accounts.invite.issuedDescription")}
        footer={<Button onClick={() => onOpenChange(false)}>{t("settings.shared.done")}</Button>}
      >
        <IssuedOnce t={t} issued={issued.answer} expiresAt={issued.expiresAt} />
      </Dialog>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      initialFocus={usernameRef}
      title={team !== null ? t("teams.invite.title", { team: team.name }) : recover === null ? t("accounts.invite.title") : t("accounts.invite.recoverTitle", { name: recover.username })}
      description={team !== null ? t("teams.invite.description", { team: team.name }) : recover === null ? t("accounts.invite.description") : t("accounts.invite.recoverDescription")}
      footer={
        <>
          <Button disabled={create.isPending} onClick={() => onOpenChange(false)}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={create.isPending} disabled={username.trim() === ""}>
            {recover === null ? t("accounts.invite.submit") : t("accounts.invite.recoverSubmit")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("accounts.invite.failed")} /> : null}
        {recover === null ? (
          <>
            <Field label={t("accounts.fields.username")} description={t("accounts.fields.usernameHint")} error={errors.fields.username}>
              <Input
                ref={usernameRef}
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
            <Field label={t("accounts.fields.displayName")} optional error={errors.fields.display_name}>
              <Input
                autoComplete="off"
                maxLength={128}
                value={displayName}
                onValueChange={(value: string) => {
                  setDisplayName(value);
                }}
              />
            </Field>
            <Field label={t("accounts.fields.person")} optional description={t("accounts.fields.personHint")} error={errors.fields.person_ref}>
              <Input
                type="email"
                autoComplete="off"
                maxLength={254}
                value={person}
                onValueChange={(value: string) => {
                  setPerson(value);
                }}
              />
            </Field>
            {team !== null ? (
              <ChoiceCards
                legend={t("teams.members.roleLegend")}
                options={TEAM_ROLES.map((value) => ({ value, label: teamRoleLabel(t, value), description: teamRoleDescription(t, value) }))}
                value={teamRole}
                onValueChange={setTeamRole}
              />
            ) : (
              <ChoiceCards
                legend={t("accounts.fields.role")}
                options={ROLES.map((value) => ({ value, label: roleLabel(t, value), description: roleDescription(t, value) }))}
                value={role}
                onValueChange={setRole}
              />
            )}
            {errors.fields.role !== undefined ? <p className="text-13 text-fail">{errors.fields.role}</p> : null}
            {errors.fields.team !== undefined ? <p className="text-13 text-fail">{errors.fields.team}</p> : null}
            {errors.fields.team_role !== undefined ? <p className="text-13 text-fail">{errors.fields.team_role}</p> : null}
            {clashes.length > 0 ? (
              <Notice tone="warning" title={t("accounts.sod.clashTitle")}>
                {t("accounts.sod.clash", { accounts: clashes.map((other) => other.username).join(", ") })}
              </Notice>
            ) : null}
          </>
        ) : (
          <Notice tone="warning" title={t("accounts.invite.recoverNoticeTitle")}>
            {t("accounts.invite.recoverNotice")}
          </Notice>
        )}
        <Field label={t("accounts.invite.mailLabel")} optional description={t("accounts.invite.mailHint")} error={errors.fields.email}>
          <Input
            type="email"
            autoComplete="off"
            maxLength={254}
            value={mailTo}
            onValueChange={(value: string) => {
              setMailTo(value);
            }}
          />
        </Field>
        <Field label={t("accounts.invite.expiresLabel")} nativeLabel={false} error={errors.fields.expires_hours}>
          <Select
            options={EXPIRY_HOURS.map((value) => ({ value, label: expiryLabel(t, value) }))}
            value={expiry}
            onValueChange={(value) => {
              setExpiry(value);
            }}
            className="w-48"
          />
        </Field>
      </form>
    </Dialog>
  );
}
