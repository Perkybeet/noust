import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";
import { LABEL_PART, fleetKeys, formatLabels, parseLabelSelector, saveLabels } from "./data";

export interface LabelsDialogProps {
  node: string;
  labels: readonly (readonly [string, string])[];
  onClose: () => void;
}

/** Reads what was typed into labels, or says what is wrong with it. */
export function readLabels(value: string): { labels: Record<string, string> } | { error: "format" | "characters" } {
  const labels = parseLabelSelector(value);
  if (labels === null) return { error: "format" };
  const bad = Object.entries(labels).some(([key, text]) => !LABEL_PART.test(key) || !LABEL_PART.test(text));
  return bad ? { error: "characters" } : { labels };
}

/**
 * A server's labels (`env=prod, team=web`): this central's own way to group its servers and
 * aim a bulk action at a group. The server never sees them. Saving replaces them all.
 */
export function LabelsDialog({ node, labels, onClose }: LabelsDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(formatLabels(labels));
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (next: Record<string, string>) => saveLabels(node, next),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: fleetKeys.all });
      onClose();
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (save.isPending) return;
    const read = readLabels(value);
    if ("error" in read) {
      setError(read.error === "format" ? t("fleet.labels.invalidFormat") : t("fleet.labels.invalidCharacters"));
      inputRef.current?.focus();
      return;
    }
    setError(null);
    save.mutate(read.labels);
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="md"
      initialFocus={inputRef}
      title={t("fleet.labels.title", { name: node })}
      description={t("fleet.labels.description")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("fleet.labels.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("fleet.labels.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-4">
        <Field label={t("fleet.labels.label")} description={t("fleet.labels.help")} error={error} optional>
          <Input
            ref={inputRef}
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="env=prod, team=web"
            value={value}
            onValueChange={(next: string) => {
              setValue(next);
            }}
          />
        </Field>
        {save.isError ? <ErrorBlock live compact error={save.error} title={t("fleet.labels.failed", { name: node })} /> : null}
      </form>
    </Dialog>
  );
}
