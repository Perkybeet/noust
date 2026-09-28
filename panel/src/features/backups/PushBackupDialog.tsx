import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { backupDestinationsQuery } from "../../api/queries/backupDestinations";
import type { Backup } from "../../api/queries/backups";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { useBackupActions } from "./useBackupActions";

export interface PushBackupDialogProps {
  backup: Backup;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Uploads a backup already on this machine to a remote destination, as a background job. */
export function PushBackupDialog({ backup, open, onOpenChange }: PushBackupDialogProps) {
  const t = useT();
  const formId = useId();
  const destinations = useQuery({ ...backupDestinationsQuery(), enabled: open });
  const [destination, setDestination] = useState("");
  const { push } = useBackupActions();

  const close = (next: boolean): void => {
    if (!next && push.isPending) return;
    onOpenChange(next);
    if (!next) {
      setDestination("");
      push.reset();
    }
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (destination === "") return;
    push.mutate({ backupId: backup.backup_id, destination }, { onSuccess: () => close(false) });
  };

  const options = (destinations.data?.destinations ?? []).map((entry) => ({ value: entry.name, label: entry.name }));

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="sm"
      title={t("backups.pushDialog.title", { id: backup.backup_id })}
      description={t("backups.pushDialog.description")}
      footer={
        <>
          <Button disabled={push.isPending} onClick={() => close(false)}>
            {t("backups.common.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={push.isPending} disabled={destination === ""}>
            {t("backups.pushDialog.submit")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="flex flex-col gap-4">
        <Field label={t("backups.fields.destination")} nativeLabel={false}>
          <Select
            aria-label={t("backups.fields.destination")}
            value={destination}
            onValueChange={setDestination}
            placeholder={destinations.isPending ? t("backups.fields.loadingDestinations") : t("backups.fields.chooseDestination")}
            options={options}
            disabled={destinations.isPending || options.length === 0}
          />
        </Field>
        {!destinations.isPending && options.length === 0 ? (
          <p className="text-13 text-fg-muted">{t("backups.pushDialog.noDestinations")}</p>
        ) : null}
        {push.isError ? <ErrorBlock live compact error={push.error} title={t("backups.pushDialog.error")} /> : null}
      </form>
    </Dialog>
  );
}
