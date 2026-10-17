import { useQuery } from "@tanstack/react-query";

import { TEAM_ROLES, myTeamsQuery } from "../../../api/queries/teams";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import { teamRoleLabel } from "../../teams/roles";
import { CREATED_ROLES } from "./providerForm";
import type { AdmissionDraft, Provisioning } from "./providerForm";

/** The default team is "the default team" until one is chosen: a Select needs a value to hold that. */
const DEFAULT_TEAM = "__default";

/** The fields of the shared part of a form, for a caller to put a refusal about a folded one above the form. */
export const ADMISSION_FOLDED = [
  "provisioning",
  "default_role",
  "default_team",
  "default_team_role",
  "allowed_domains",
  "require_amr",
  "groups_claim",
  "group_mappings",
] as const;

export interface AdmissionFieldsProps {
  draft: AdmissionDraft;
  /** Called with what changed, for the caller to merge into its own, larger draft. */
  onChange: (patch: Partial<AdmissionDraft>) => void;
  /** What the server refused, by the API's field name. */
  errors: Readonly<Record<string, string | undefined>>;
  creating: boolean;
  /**
   * Which of the shared fields this kind of source has. A directory that provisions has no
   * `provisioning` to choose and always creates accounts; one that is signed in against has no
   * authentication methods to require.
   */
  parts: {
    provisioning: boolean;
    emailTrust: boolean;
    methods: boolean;
    claim: boolean;
  };
}

/**
 * What every source of identities decides in the same way: who may come in, what a new account
 * starts as, and what each group means. One component for them all, because the server has one
 * implementation of it (`core/accounts/federation.py`): a source that asked differently here
 * would say a thing the server does not do.
 */
export function AdmissionFields({ draft, onChange, errors, creating, parts }: AdmissionFieldsProps) {
  const t = useT();
  const set = <K extends keyof AdmissionDraft>(key: K, value: AdmissionDraft[K]): void => {
    onChange({ [key]: value });
  };
  const teams = useQuery(myTeamsQuery());
  const teamOptions = [
    { value: DEFAULT_TEAM, label: t("ssoAdmin.form.fields.defaultTeamNone") },
    ...(teams.data?.teams ?? []).map((team) => ({ value: team.slug, label: team.name })),
  ];
  const provisioningOptions: { value: Provisioning; label: string; description: string }[] = [
    { value: "off", label: t("ssoAdmin.form.fields.provisioningOff"), description: t("ssoAdmin.form.fields.provisioningOffHelp") },
    { value: "invite", label: t("ssoAdmin.form.fields.provisioningInvite"), description: t("ssoAdmin.form.fields.provisioningInviteHelp") },
    { value: "auto", label: t("ssoAdmin.form.fields.provisioningAuto"), description: t("ssoAdmin.form.fields.provisioningAutoHelp") },
  ];
  // Accounts are created when the source decides so, or always, for one that has no choice.
  const creates = !parts.provisioning || draft.provisioning === "auto";

  return (
    <>
      <Disclosure label={t("ssoAdmin.form.groups.who")} defaultOpen={!creating}>
        <div className="flex flex-col gap-5">
          {parts.provisioning ? (
            <ChoiceCards
              legend={t("ssoAdmin.form.fields.provisioning")}
              options={provisioningOptions}
              value={draft.provisioning}
              onValueChange={(value) => {
                set("provisioning", value);
              }}
            />
          ) : null}
          {creates ? (
            <>
              <Field label={t("ssoAdmin.form.fields.defaultRole")} nativeLabel={false} description={t("ssoAdmin.form.fields.defaultRoleHelp")} error={errors["default_role"]}>
                <Select
                  options={CREATED_ROLES.map((role) => ({ value: role, label: role }))}
                  value={draft.defaultRole}
                  onValueChange={(value) => {
                    set("defaultRole", value);
                  }}
                  mono
                />
              </Field>
              <Field label={t("ssoAdmin.form.fields.defaultTeam")} nativeLabel={false} error={errors["default_team"]}>
                <Select
                  options={teamOptions}
                  value={draft.defaultTeam === "" ? DEFAULT_TEAM : draft.defaultTeam}
                  onValueChange={(value) => {
                    set("defaultTeam", value === DEFAULT_TEAM ? "" : value);
                  }}
                />
              </Field>
              {draft.defaultTeam !== "" ? (
                <Field label={t("ssoAdmin.form.fields.defaultTeamRole")} nativeLabel={false} error={errors["default_team_role"]}>
                  <Select
                    options={TEAM_ROLES.map((role) => ({ value: role, label: teamRoleLabel(t, role) }))}
                    value={draft.defaultTeamRole === "" ? "viewer" : draft.defaultTeamRole}
                    onValueChange={(value) => {
                      set("defaultTeamRole", value);
                    }}
                  />
                </Field>
              ) : null}
            </>
          ) : null}
          <Checkbox
            label={t("ssoAdmin.form.fields.linkByEmail")}
            description={t("ssoAdmin.form.fields.linkByEmailHelp")}
            checked={draft.linkByEmail}
            onCheckedChange={(value) => {
              set("linkByEmail", value);
            }}
          />
          {parts.emailTrust ? (
            <>
              <Checkbox
                label={t("ssoAdmin.form.fields.requireEmailVerified")}
                checked={draft.requireEmailVerified}
                onCheckedChange={(value) => {
                  set("requireEmailVerified", value);
                }}
              />
              <Checkbox
                label={t("ssoAdmin.form.fields.assumeEmailVerified")}
                description={t("ssoAdmin.form.fields.assumeEmailVerifiedHelp")}
                checked={draft.assumeEmailVerified}
                onCheckedChange={(value) => {
                  set("assumeEmailVerified", value);
                }}
              />
            </>
          ) : null}
          <Field label={t("ssoAdmin.form.fields.allowedDomains")} optional description={t("ssoAdmin.form.fields.allowedDomainsHelp")} error={errors["allowed_domains"]}>
            <Textarea
              mono
              rows={2}
              spellCheck={false}
              placeholder="acme.test"
              value={draft.allowedDomains}
              onChange={(event) => {
                set("allowedDomains", event.target.value);
              }}
            />
          </Field>
          {parts.methods ? (
            <Field label={t("ssoAdmin.form.fields.requireAmr")} optional description={t("ssoAdmin.form.fields.requireAmrHelp")} error={errors["require_amr"]}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                placeholder="mfa"
                value={draft.requireAmr}
                onValueChange={(value: string) => {
                  set("requireAmr", value);
                }}
              />
            </Field>
          ) : null}
        </div>
      </Disclosure>

      <Disclosure label={t("ssoAdmin.form.groups.groupsTitle")} defaultOpen={!creating && draft.groupSync}>
        <div className="flex flex-col gap-5">
          <Checkbox
            label={t("ssoAdmin.form.fields.groupSync")}
            description={t("ssoAdmin.form.fields.groupSyncHelp")}
            checked={draft.groupSync}
            onCheckedChange={(value) => {
              set("groupSync", value);
            }}
          />
          {parts.claim ? (
            <Field label={t("ssoAdmin.form.fields.groupsClaim")} description={t("ssoAdmin.form.fields.groupsClaimHelp")} error={errors["groups_claim"]}>
              <Input
                mono
                autoComplete="off"
                spellCheck={false}
                value={draft.groupsClaim}
                onValueChange={(value: string) => {
                  set("groupsClaim", value);
                }}
              />
            </Field>
          ) : null}
          <Field label={t("ssoAdmin.form.fields.mappings")} optional description={t("ssoAdmin.form.fields.mappingsHelp")} error={errors["group_mappings"]}>
            <Textarea
              mono
              rows={3}
              spellCheck={false}
              placeholder={"ops=admin\ndevs=acme:operator\nrelease=release-manager@acme/web"}
              value={draft.mappings}
              onChange={(event) => {
                set("mappings", event.target.value);
              }}
            />
          </Field>
        </div>
      </Disclosure>
    </>
  );
}
