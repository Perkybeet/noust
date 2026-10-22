import { useMutation, useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { backupDestinationsQuery } from "../../api/queries/backupDestinations";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";

export interface PushToDestinationWords {
  title: string;
  description: string;
  destination: string;
  choose: string;
  none: string;
  action: string;
  failed: string;
}

export interface PushToDestinationDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  words: PushToDestinationWords;
  /** Starts the upload to the chosen destination; a rejection is shown in the dialog, verbatim. */
  onPush: (destination: string) => Promise<void>;
}

/**
 * Sends a backup that is on this server to one of the destinations, as the job `onPush` queues.
 * The same dialog for an application's data and for Noust's own state: only the words change.
 */
export function PushToDestinationDialog({ open, onOpenChange, words, onPush }: PushToDestinationDialogProps) {
  const t = useT();
  const formId = useId();
  const destinations = useQuery({ ...backupDestinationsQuery(), enabled: open });
  const [destination, setDestination] = useState("");
  const [pressed, setPressed] = useState(false);
  const push = useMutation({ mutationFn: (name: string) => onPush(name) });
  const options = (destinations.data?.destinations ?? []).map((entry) => ({ value: entry.name, label: entry.name }));

  const close = (next: boolean): void => {
    if (!next && push.isPending) return;
    onOpenChange(next);
    if (!next) {
      setDestination("");
      setPressed(false);
      push.reset();
    }
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (destination === "") {
      setPressed(true);
      return;
    }
    push.mutate(destination, { onSuccess: () => close(false) });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="sm"
      title={words.title}
      description={words.description}
      footer={
        <>
          <Button disabled={push.isPending} onClick={() => close(false)}>
            {t("backups.common.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={push.isPending}>
            {words.action}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="flex flex-col gap-4">
        <Field label={words.destination} nativeLabel={false} error={pressed && destination === "" ? words.choose : undefined}>
          <Select
            aria-label={words.destination}
            value={destination}
            onValueChange={setDestination}
            placeholder={destinations.isPending ? t("backups.fields.loadingDestinations") : words.choose}
            options={options}
            disabled={destinations.isPending || options.length === 0}
          />
        </Field>
        {!destinations.isPending && options.length === 0 ? <p className="text-13 text-fg-muted">{words.none}</p> : null}
        {push.isError ? <ErrorBlock live compact error={push.error} title={words.failed} /> : null}
      </form>
    </Dialog>
  );
}
