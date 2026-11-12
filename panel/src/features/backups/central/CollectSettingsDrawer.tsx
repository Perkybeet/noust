import { useState } from "react";
import type { SyntheticEvent } from "react";

import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Drawer } from "../../../components/ui/Drawer";
import { Field, FieldGroup } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { useT } from "../../../i18n";
import type { PlainKey } from "../../../i18n";
import { focusFirstInvalid } from "../../settings/formErrors";
import { KINDS, bodyOf, formOf, problemsOf } from "./central";
import type { Knobs, SettingsField, SettingsForm } from "./central";
import { useCentralBackupActions } from "./useCentralBackupActions";

const KIND_LABEL: Record<(typeof KINDS)[number], PlainKey> = {
  app: "centralBackups.settings.kind.app",
  database: "centralBackups.settings.kind.database",
  volume: "centralBackups.settings.kind.volume",
  noust: "centralBackups.settings.kind.noust",
};

export interface CollectSettingsDrawerProps {
  knobs: Knobs | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * How the central collects, as one form in a drawer: whether it comes for the copies of every server, which kinds, at what
 * speed, and how long it keeps them. The retention is the central's own and does not touch what a server keeps for itself.
 * Saving asks "Confirm it's you": turning collecting on makes the central read every server's copies, `.env` files included.
 */
export function CollectSettingsDrawer({ knobs, open, onOpenChange }: CollectSettingsDrawerProps) {
  const t = useT();
  const formId = "central-collect-settings-form";
  const { saveSettings } = useCentralBackupActions();
  const [form, setForm] = useState<SettingsForm>(() => formOf(knobs));
  const [errors, setErrors] = useState<Partial<Record<SettingsField, PlainKey>>>({});
  const set = (patch: Partial<SettingsForm>): void => setForm((previous) => ({ ...previous, ...patch }));

  const toggleKind = (kind: (typeof KINDS)[number], on: boolean): void => {
    set({ kinds: on ? [...form.kinds, kind] : form.kinds.filter((existing) => existing !== kind) });
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found = problemsOf(form);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirstInvalid(formId);
      return;
    }
    // design-exception: write-without-permission the drawer is opened only from controls drawn for backups.manage, and the server decides again
    saveSettings.mutate(bodyOf(form), {
      onSuccess: () => {
        onOpenChange(false);
      },
    });
  };

  const message = (field: SettingsField): string | undefined => {
    const key = errors[field];
    return key === undefined ? undefined : t(key);
  };

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title={t("centralBackups.settings.title")}
      description={t("centralBackups.settings.description")}
      footer={
        <>
          <Button
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={saveSettings.isPending}>
            {t("centralBackups.settings.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-6">
        <Checkbox
          checked={form.enabled}
          onCheckedChange={(enabled) => {
            set({ enabled });
          }}
          label={t("centralBackups.settings.enabled")}
          description={t("centralBackups.settings.enabledHelp")}
        />

        <FieldGroup legend={t("centralBackups.settings.kinds")} error={message("kinds")}>
          {KINDS.map((kind) => (
            <Checkbox
              key={kind}
              checked={form.kinds.includes(kind)}
              onCheckedChange={(on) => {
                toggleKind(kind, on);
              }}
              label={t(KIND_LABEL[kind])}
            />
          ))}
        </FieldGroup>

        <Field label={t("centralBackups.settings.speed")} description={t("centralBackups.settings.speedHelp")} error={message("speed")} optional>
          <Input
            mono
            inputMode="numeric"
            value={form.speed}
            onValueChange={(speed: string) => {
              set({ speed });
            }}
            suffix={t("centralBackups.settings.speedSuffix")}
            autoComplete="off"
          />
        </Field>

        <FieldGroup legend={t("centralBackups.settings.retentionTitle")} description={t("centralBackups.settings.retentionHelp")}>
          <Field label={t("centralBackups.settings.count")} error={message("count")} optional>
            <Input
              mono
              inputMode="numeric"
              value={form.count}
              onValueChange={(count: string) => {
                set({ count });
              }}
              autoComplete="off"
            />
          </Field>
          <Field label={t("centralBackups.settings.days")} error={message("days")} optional>
            <Input
              mono
              inputMode="numeric"
              value={form.days}
              onValueChange={(days: string) => {
                set({ days });
              }}
              autoComplete="off"
            />
          </Field>
          <Field label={t("centralBackups.settings.gb")} error={message("gb")} optional>
            <Input
              mono
              inputMode="decimal"
              value={form.gb}
              onValueChange={(gb: string) => {
                set({ gb });
              }}
              suffix={t("centralBackups.settings.gbSuffix")}
              autoComplete="off"
            />
          </Field>
        </FieldGroup>

        <Field label={t("centralBackups.settings.minFree")} description={t("centralBackups.settings.minFreeHelp")} error={message("minFree")} optional>
          <Input
            mono
            inputMode="decimal"
            value={form.minFree}
            onValueChange={(minFree: string) => {
              set({ minFree });
            }}
            suffix={t("centralBackups.settings.gbSuffix")}
            autoComplete="off"
          />
        </Field>

        {saveSettings.isError ? <ErrorBlock live compact error={saveSettings.error} title={t("centralBackups.settings.saveFailed")} /> : null}
      </form>
    </Drawer>
  );
}
