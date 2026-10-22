import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { saveScimSource, scimKeys } from "../../../api/queries/scim";
import type { ScimSaved, ScimSource } from "../../../api/queries/scim";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { CopyTextButton } from "../../../components/ui/CopyTextButton";
import { CopyValue } from "../../../components/ui/CopyValue";
import { Drawer } from "../../../components/ui/Drawer";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { focusFirstInvalid, splitErrors } from "../formErrors";
import { ADMISSION_FOLDED, AdmissionFields } from "./AdmissionFields";
import { NEW_SCIM_DRAFT, scimDraftOf, scimSettingsOf } from "./scimForm";
import type { ScimDraft } from "./scimForm";

const FIELDS = ["label", "default_role", "default_team", "default_team_role", "allowed_domains", "group_mappings"] as const;

export interface ScimDrawerProps {
  /** The source being changed, or null to add one. */
  source: ScimSource | null;
  onClose: () => void;
}

/** The token, once: copy it now, because only its last characters are kept. */
function TokenOnce({ saved, endpoint }: { saved: ScimSaved; endpoint: string }) {
  const t = useT();
  const token = saved.token ?? "";
  return (
    <div className="flex flex-col gap-4">
      <Notice tone="warning" live>
        {t("scimAdmin.token.warning")}
      </Notice>
      <div className="flex flex-col gap-1.5">
        <span className="text-13 font-medium text-fg">{t("scimAdmin.token.tokenLabel", { name: saved.source.name })}</span>
        <span data-testid="new-scim-token" className="rounded-control border border-border-strong bg-surface px-3 py-2 text-13 break-all select-all">
          <Mono tone="default">{token}</Mono>
        </span>
      </div>
      <div>
        <CopyTextButton value={token} variant="primary">
          {t("scimAdmin.token.copy")}
        </CopyTextButton>
      </div>
      <div className="flex flex-col gap-1.5">
        <span className="text-13 font-medium text-fg">{t("scimAdmin.token.endpointLabel")}</span>
        <CopyValue value={endpoint} label={t("scimAdmin.token.endpointLabel")} name={t("scimAdmin.token.endpointLabel")} />
      </div>
    </div>
  );
}

/**
 * Adding or changing a provisioning source. What it creates is the same set of decisions every
 * source has (the shared fields); the token is shown once, here, after the save that issued it.
 */
export function ScimDrawer({ source, onClose }: ScimDrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const creating = source === null;
  const [draft, setDraft] = useState<ScimDraft>(source === null ? NEW_SCIM_DRAFT : scimDraftOf(source));
  const [badLine, setBadLine] = useState<number | null>(null);
  const [issued, setIssued] = useState<ScimSaved | null>(null);

  const save = useMutation({
    mutationFn: async () => {
      const built = scimSettingsOf(draft);
      if (built.settings === null) {
        setBadLine(built.badLine);
        return null;
      }
      setBadLine(null);
      return saveScimSource(draft.name.trim(), built.settings);
    },
    onSuccess: (saved) => {
      if (saved === null) return;
      void queryClient.invalidateQueries({ queryKey: scimKeys.all });
      if (saved.token !== null && saved.token !== undefined) {
        setIssued(saved);
        return;
      }
      toast.success(t("scimAdmin.savedToast", { name: saved.source.name }));
      onClose();
    },
  });
  const [tried, setTried] = useState(false);
  const required = t("settings.shared.required");
  const errors = splitErrors(save.error, FIELDS);
  const hiddenMessage = ADMISSION_FOLDED.map((name) => errors.fields[name as (typeof FIELDS)[number]]).find((message) => message !== undefined);

  const ready = !creating || draft.name.trim() !== "";
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (save.isPending) return;
    // The button is never off for a missing value: it says what is missing, where, and goes there.
    if (!ready) {
      setTried(true);
      focusFirstInvalid(formId);
      return;
    }
    save.mutate();
  };

  if (issued !== null) {
    return (
      <Drawer
        open
        onOpenChange={(next) => {
          if (!next) onClose();
        }}
        size="md"
        title={t("scimAdmin.token.title")}
        description={t("scimAdmin.token.description", { name: issued.source.name })}
        footer={<Button variant="primary" onClick={onClose}>{t("common.words.done")}</Button>}
      >
        <TokenOnce saved={issued} endpoint={issued.base_url} />
      </Drawer>
    );
  }

  return (
    <Drawer
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="md"
      title={creating ? t("scimAdmin.form.createTitle") : t("common.words.editNamed", { name: source.name })}
      description={t("scimAdmin.form.description")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {creating ? t("scimAdmin.form.create") : t("scimAdmin.form.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("scimAdmin.form.errorTitle")} /> : null}
        {hiddenMessage !== undefined ? (
          <InlineMessage tone="error" live>
            <span className="font-medium">{t("scimAdmin.form.errorTitle")}</span> {hiddenMessage}
          </InlineMessage>
        ) : null}
        {badLine !== null ? (
          <InlineMessage tone="error" live>
            <span className="font-medium">{t("scimAdmin.form.errorTitle")}</span> {t("scimAdmin.form.mappingsLine", { line: badLine })}
          </InlineMessage>
        ) : null}
        {creating ? (
          <Field label={t("common.words.name")} description={t("scimAdmin.form.nameHelp")} error={tried && draft.name.trim() === "" ? required : undefined}>
            <Input
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              maxLength={32}
              value={draft.name}
              onValueChange={(value: string) => {
                setDraft((previous) => ({ ...previous, name: value }));
              }}
            />
          </Field>
        ) : null}
        <Field label={t("scimAdmin.form.label")} optional description={t("scimAdmin.form.labelHelp")} error={errors.fields.label}>
          <Input
            maxLength={40}
            value={draft.label}
            onValueChange={(value: string) => {
              setDraft((previous) => ({ ...previous, label: value }));
            }}
          />
        </Field>
        <Checkbox
          label={t("scimAdmin.form.enabled")}
          description={t("scimAdmin.form.enabledHelp")}
          checked={draft.enabled}
          onCheckedChange={(value) => {
            setDraft((previous) => ({ ...previous, enabled: value }));
          }}
        />
        {!creating ? (
          <Checkbox
            label={t("scimAdmin.form.rotate")}
            description={t("scimAdmin.form.rotateHelp")}
            checked={draft.rotateToken}
            onCheckedChange={(value) => {
              setDraft((previous) => ({ ...previous, rotateToken: value }));
            }}
          />
        ) : null}
        <AdmissionFields
          draft={draft}
          onChange={(patch) => {
            setDraft((previous) => ({ ...previous, ...patch }));
          }}
          errors={errors.fields}
          creating={creating}
          parts={{ provisioning: false, emailTrust: false, methods: false, claim: false }}
        />
      </form>
    </Drawer>
  );
}
