import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import type { AppDomainChange } from "../../api/queries/domains";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";

/**
 * Which port of the application a name goes to. An application with a second listener (an
 * admin interface, an API on its own port) can answer on a name for each; leaving the field
 * empty puts the application's own port back. The web server tests the new site before it is
 * reloaded, and the old port is back if it refuses.
 */
export function DomainPortDialog({
  app,
  name,
  port,
  onClose,
  onSaved,
}: {
  app: string;
  /** The alias whose port is being changed; null while closed. */
  name: string | null;
  /** Its port now, or null for the application's own. */
  port: number | null;
  onClose: () => void;
  onSaved: (change: AppDomainChange, name: string, port: number | null) => void;
}) {
  const t = useT();
  const formId = useId();
  const [typed, setTyped] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const shown = typed ?? (port === null ? "" : String(port));

  const save = useMutation({
    mutationFn: (next: number | null) =>
      request("put", "/api/apps/{domain}/domains/{name}/port", { params: { domain: app, name: name ?? "" }, body: { port: next } }),
    onSuccess: (change, next) => {
      onSaved(change, name ?? "", next);
      close(true);
    },
  });

  function close(settled = false): void {
    if (save.isPending && !settled) return;
    onClose();
    setTyped(null);
    setProblem(null);
    save.reset();
  }

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const text = shown.trim();
    if (text === "") {
      setProblem(null);
      save.mutate(null);
      return;
    }
    const number = Number(text);
    if (!/^\d+$/.test(text) || number < 1 || number > 65535) {
      setProblem(t("network.port.invalid"));
      return;
    }
    setProblem(null);
    save.mutate(number);
  };

  const fieldError = problem ?? (save.error && isApiError(save.error) ? (save.error.fields?.["port"] ?? null) : null);

  return (
    <Dialog
      open={name !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
      title={t("network.port.title", { name: name ?? "" })}
      description={t("network.port.description", { app })}
      footer={
        <>
          <Button disabled={save.isPending} onClick={() => close()}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("network.port.save")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field label={t("network.port.label")} description={t("network.port.help", { app })} error={fieldError}>
          <Input mono inputMode="numeric" value={shown} onValueChange={setTyped} autoComplete="off" placeholder={t("network.port.placeholder")} />
        </Field>
        {save.isError && fieldError === null ? <ErrorBlock live compact error={save.error} title={t("network.port.failed", { name: name ?? "" })} /> : null}
      </form>
    </Dialog>
  );
}
