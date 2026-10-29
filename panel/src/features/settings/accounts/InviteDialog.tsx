import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail } from "lucide-react";
import { useId, useMemo, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { rolesQuery } from "../../../api/queries/roles";
import { TEAM_ROLES } from "../../../api/queries/teams";
import type { TeamRole } from "../../../api/queries/teams";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { CopyTextButton } from "../../../components/ui/CopyTextButton";
import { Dialog } from "../../../components/ui/Dialog";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { ServerText } from "../../../components/ui/ServerText";
import { Skeleton } from "../../../components/ui/Skeleton";
import { toast } from "../../../components/ui/toast";
import { OBJECT_ICONS, VERB_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatMoment } from "../../../lib/format";
import { teamRoleDescription, teamRoleLabel } from "../../teams/roles";
import { focusFirstInvalid, splitErrors } from "../formErrors";
import { roleDescription, roleLabel } from "../roles/roleModel";
import { accountKeys, invite } from "./api";
import type { Account, InvitationIssued } from "./api";
import { samePerson } from "./data";
import { incompatible } from "./roles";
import { isUsername, suggestUsername } from "./username";

const FIELDS = ["username", "role", "roles", "team", "team_role", "display_name", "email", "expires_hours"] as const;

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

/** The icon of a role card: the object each shipped role is about, the key for one of your own. */
const ROLE_ICONS = {
  viewer: VERB_ICONS.show,
  operator: OBJECT_ICONS.deployment,
  admin: OBJECT_ICONS.settings,
  security: OBJECT_ICONS.signInSecurity,
  auditor: OBJECT_ICONS.audit,
  owner: OBJECT_ICONS.role,
} as const;

function iconOfRole(name: string) {
  return Object.hasOwn(ROLE_ICONS, name) ? ROLE_ICONS[name as keyof typeof ROLE_ICONS] : OBJECT_ICONS.key;
}

// design-exception: notice-stack the form shows the role clash and the result shows the delivery and the one-time warning; the dialog is one or the other
/**
 * The result of inviting, once: whether the email went, and the link, which cannot be shown again. The email
 * is the convenience and the link is the invitation, so an email that did not go says why, in the console's
 * language, and the link below it still works.
 */
function Result({ t, issued, expiresAt }: { t: T; issued: InvitationIssued; expiresAt: number }) {
  const link = invitationLink(issued.code);
  const expires = formatMoment(new Date(expiresAt), t.locale);
  return (
    <div className="flex flex-col gap-4">
      {issued.emailed ? (
        <Notice tone="success" title={t("accounts.invite.sentTitle")}>
          {t("accounts.invite.sentBody", { address: issued.account.email ?? "" })}
        </Notice>
      ) : (
        <Notice tone="warning" title={t("accounts.invite.notSentTitle")}>
          <div className="flex flex-col gap-1">
            {issued.email_error !== null && issued.email_error !== undefined ? (
              <p>
                <ServerText coded={issued.email_error_coded} text={issued.email_error} />
              </p>
            ) : null}
            {issued.email_error_hint !== null && issued.email_error_hint !== undefined ? (
              <p>
                <ServerText coded={issued.email_error_coded} text={issued.email_error_hint} part="hint" />
              </p>
            ) : null}
            <p>{t("accounts.invite.notSentBody")}</p>
          </div>
        </Notice>
      )}
      <Field label={t("accounts.invite.linkLabel")} description={t("accounts.invite.linkExpires", { when: expires })}>
        <Input mono readOnly value={link} data-testid="invitation-link" />
      </Field>
      <div className="flex flex-wrap gap-2">
        <CopyTextButton value={link} variant="primary">
          {t("accounts.invite.copyLink")}
        </CopyTextButton>
        <CopyTextButton value={issued.code}>{t("accounts.invite.copyCode")}</CopyTextButton>
      </div>
      <p className="text-13 text-fg-muted">{t.rich("accounts.invite.codeLine", { code: <Mono key="code">{issued.code}</Mono> })}</p>
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
   * govern the rest). The team's own page offers it; the people page invites to the server.
   */
  team?: { slug: string; name: string } | null;
}

/**
 * Inviting a person (the mock-up `invitar.html`): their email, and what they will be able to do. One address,
 * where the link is sent and who the person is; the account is named from it, and the name can be changed. The
 * person sets their own password and second factor from the link; nobody else ever knows either. For an
 * existing account it is how access is recovered.
 */
export function InviteDialog({ open, onClose, accounts, recover = null, team = null }: InviteDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const roles = useQuery({ ...rolesQuery(), enabled: recover === null && team === null });
  const [email, setEmail] = useState(recover?.email ?? "");
  // The account's name follows the email until the person inviting writes their own.
  const [custom, setCustom] = useState<string | null>(null);
  const [editingName, setEditingName] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState("operator");
  const [teamRole, setTeamRole] = useState<TeamRole>("viewer");
  const [expiry, setExpiry] = useState<(typeof EXPIRY_HOURS)[number]>("24");
  const [issued, setIssued] = useState<{ answer: InvitationIssued; expiresAt: number } | null>(null);
  const [tried, setTried] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);
  const formId = useId();

  const username = recover?.username ?? custom ?? suggestUsername(email);
  const offered = useMemo(() => (roles.data?.roles ?? []).filter((candidate) => !candidate.protected), [roles.data]);
  // The role chosen is one of the list: the default when it is there, the first otherwise.
  const chosenRole = offered.some((candidate) => candidate.name === role) ? role : (offered[0]?.name ?? role);

  const create = useMutation({
    mutationFn: () =>
      invite({
        username,
        expires_hours: Number(expiry),
        ...(recover === null ? { role: team === null ? chosenRole : "viewer" } : {}),
        ...(recover === null && team !== null ? { team: team.slug, team_role: teamRole } : {}),
        ...(recover === null && displayName.trim() !== "" ? { display_name: displayName.trim() } : {}),
        ...(email.trim() !== "" ? { email: email.trim() } : {}),
      }),
    onSuccess: (result) => {
      setIssued({ answer: result, expiresAt: Date.now() + result.expires_in * 1000 });
      void queryClient.invalidateQueries({ queryKey: accountKeys.list });
    },
  });
  const errors = splitErrors(create.error, FIELDS);
  const clashes = recover === null && team === null ? samePerson(accounts, { username, person_ref: email }).filter((other) => incompatible(other.role, chosenRole)) : [];
  const emailMissing = recover === null && email.trim() === "";
  const nameInvalid = recover === null && email.trim() !== "" && !isUsername(username);

  const onOpenChange = (next: boolean): void => {
    if (next || create.isPending) return;
    if (issued !== null) toast.success(t("accounts.invite.sentToast", { name: issued.answer.account.username }));
    onClose();
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (create.isPending) return;
    // The button is never off for a missing value: it says what is missing, where, and goes there.
    if (emailMissing || nameInvalid) {
      setTried(true);
      if (nameInvalid) setEditingName(true);
      focusFirstInvalid(formId);
      return;
    }
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
        <Result t={t} issued={issued.answer} expiresAt={issued.expiresAt} />
      </Dialog>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      initialFocus={emailRef}
      title={team !== null ? t("teams.invite.title", { team: team.name }) : recover === null ? t("accounts.invite.title") : t("accounts.invite.recoverTitle", { name: recover.username })}
      description={team !== null ? t("teams.invite.description", { team: team.name }) : recover === null ? undefined : t("accounts.invite.recoverDescription")}
      footer={
        <>
          <Button disabled={create.isPending} onClick={() => onOpenChange(false)}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" icon={<Mail aria-hidden="true" />} loading={create.isPending}>
            {recover === null ? t("accounts.invite.submit") : t("accounts.invite.recoverSubmit")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("accounts.invite.failed")} /> : null}
        {recover !== null ? (
          <Notice tone="warning" title={t("accounts.invite.recoverNoticeTitle")}>
            {t("accounts.invite.recoverNotice")}
          </Notice>
        ) : null}
        <Field
          label={t("accounts.invite.mailLabel")}
          error={errors.fields.email ?? (tried && emailMissing ? t("accounts.invite.emailRequired") : undefined)}
          description={
            recover === null && email.trim() !== "" && username !== "" ? (
              editingName ? undefined : (
                <span>
                  {t.rich("accounts.invite.accountName", { name: <Mono key="name">{username}</Mono> })}{" "}
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setEditingName(true);
                    }}
                  >
                    {t("accounts.invite.accountNameChange")}
                  </Button>
                </span>
              )
            ) : undefined
          }
        >
          <Input
            ref={emailRef}
            type="email"
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            maxLength={254}
            value={email}
            onValueChange={(value: string) => {
              setEmail(value);
            }}
          />
        </Field>
        {recover === null && editingName ? (
          <Field
            label={t("accounts.invite.accountNameLabel")}
            description={t("accounts.invite.accountNameHelp")}
            error={errors.fields.username ?? (nameInvalid ? t("accounts.invite.accountNameInvalid") : undefined)}
          >
            <Input
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              maxLength={64}
              value={username}
              onValueChange={(value: string) => {
                setCustom(value);
              }}
            />
          </Field>
        ) : null}
        {recover === null ? (
          <>
            {team !== null ? (
              <ChoiceCards
                legend={t("teams.members.roleLegend")}
                options={TEAM_ROLES.map((value) => ({ value, label: teamRoleLabel(t, value), description: teamRoleDescription(t, value) }))}
                value={teamRole}
                onValueChange={setTeamRole}
              />
            ) : roles.data === undefined ? (
              <Skeleton className="h-40 w-full rounded-card" />
            ) : (
              <ChoiceCards
                legend={t("accounts.invite.roleLegend")}
                columns={1}
                options={offered.map((candidate) => ({ value: candidate.name, label: roleLabel(t, candidate), description: roleDescription(t, candidate), icon: iconOfRole(candidate.name) }))}
                value={chosenRole}
                onValueChange={setRole}
                error={errors.fields.role ?? errors.fields.roles}
              />
            )}
            {errors.fields.team !== undefined ? <p className="text-13 text-fail">{errors.fields.team}</p> : null}
            {errors.fields.team_role !== undefined ? <p className="text-13 text-fail">{errors.fields.team_role}</p> : null}
            {clashes.length > 0 ? (
              <Notice tone="warning" title={t("accounts.sod.clashTitle")}>
                {t("accounts.sod.clash", { accounts: clashes.map((other) => other.username).join(", ") })}
              </Notice>
            ) : null}
          </>
        ) : null}
        <Disclosure label={t("accounts.invite.moreOptions")}>
          <div className="flex flex-col gap-4 pt-2">
            {recover === null ? (
              <Field label={t("common.words.name")} optional error={errors.fields.display_name}>
                <Input
                  autoComplete="off"
                  maxLength={128}
                  value={displayName}
                  onValueChange={(value: string) => {
                    setDisplayName(value);
                  }}
                />
              </Field>
            ) : null}
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
          </div>
        </Disclosure>
      </form>
    </Dialog>
  );
}
