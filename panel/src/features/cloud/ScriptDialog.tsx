import { useQuery } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { Textarea } from "../../components/ui/Textarea";
import { useT } from "../../i18n";
import { SCRIPT_NAME, cloudScriptQuery, guessKind } from "./data";
import type { ScriptKind } from "./data";
import { useCloudActions } from "./useCloudActions";

export interface ScriptDialogProps {
  /** The library entry to open; absent to write a new one. */
  name?: string;
  /** Start a new entry from this built-in or custom one's text (its name is not kept). */
  copyOf?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Reads or writes an entry of the cloud-init library. One that ships with Noust is shown and can
 * be copied, not changed; one of the operator's can be edited and is checked by the server when it
 * is saved (a fragment cannot set what Noust's own part owns, a script says what runs it, and
 * neither may carry a credential), with the server's reason shown as it came.
 */
export function ScriptDialog({ name: existingName, copyOf, open, onOpenChange }: ScriptDialogProps) {
  const t = useT();
  const formId = useId();
  const source = existingName ?? copyOf ?? "";
  const loaded = useQuery({ ...cloudScriptQuery(source), enabled: open && source !== "" });
  const { saveScript } = useCloudActions();
  const nameRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState(existingName ?? "");
  const [kind, setKind] = useState<ScriptKind | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [description, setDescription] = useState<string | null>(null);
  const [tried, setTried] = useState(false);

  const entry = loaded.data;
  const builtin = existingName !== undefined && entry?.builtin === true;
  const shownContent = content ?? entry?.content ?? "";
  const shownKind: ScriptKind = kind ?? (entry === undefined ? guessKind(shownContent) : entry.kind === "cloud-config" ? "cloud-config" : "script");
  const shownDescription = description ?? entry?.description ?? "";
  const effectiveName = existingName ?? name.trim();
  const nameOk = SCRIPT_NAME.test(effectiveName);
  const saving = saveScript.isPending;
  const waiting = source !== "" && loaded.data === undefined && !loaded.isError;

  const close = (next: boolean): void => {
    if (!next && saving) return;
    onOpenChange(next);
    if (!next) {
      setName(existingName ?? "");
      setKind(null);
      setContent(null);
      setDescription(null);
      setTried(false);
      saveScript.reset();
    }
  };

  const submit = (event: SyntheticEvent): void => {
    event.preventDefault();
    setTried(true);
    if (!nameOk || shownContent.trim() === "" || saving || builtin) return;
    saveScript.mutate(
      { name: effectiveName, kind: shownKind, content: shownContent, description: shownDescription.trim() },
      {
        onSuccess: () => {
          close(false);
        },
      },
    );
  };

  const title = builtin
    ? t("cloud.scripts.viewTitle", { name: effectiveName })
    : existingName !== undefined
      ? t("cloud.scripts.editTitle", { name: effectiveName })
      : t("cloud.scripts.newTitle");

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="lg"
      initialFocus={nameRef}
      title={title}
      description={builtin ? t("cloud.scripts.builtinNote") : t("cloud.scripts.dialogDescription")}
      footer={
        builtin ? (
          <Button
            onClick={() => {
              close(false);
            }}
          >
            {t("cloud.common.close")}
          </Button>
        ) : (
          <>
            <Button
              onClick={() => {
                close(false);
              }}
            >
              {t("cloud.common.cancel")}
            </Button>
            <Button variant="primary" type="submit" form={formId} loading={saving} disabled={waiting}>
              {t("cloud.scripts.save")}
            </Button>
          </>
        )
      }
    >
      {waiting ? (
        <Skeleton className="h-48 w-full" />
      ) : loaded.isError ? (
        <ErrorBlock error={loaded.error} title={t("cloud.scripts.loadFailed")} onRetry={() => void loaded.refetch()} />
      ) : (
        <form id={formId} noValidate className="flex min-w-0 flex-col gap-5" onSubmit={submit}>
          {existingName === undefined ? (
            <Field label={t("cloud.scripts.name")} description={t("cloud.scripts.nameHelp")} error={tried && !nameOk ? t("cloud.scripts.nameInvalid") : undefined}>
              <Input ref={nameRef} mono maxLength={64} value={name} onValueChange={(value: string) => setName(value)} placeholder="swap-4g" autoComplete="off" autoCapitalize="off" spellCheck={false} />
            </Field>
          ) : null}
          <ChoiceCards<ScriptKind>
            legend={t("cloud.scripts.kind")}
            value={shownKind}
            onValueChange={(next) => {
              if (!builtin) setKind(next);
            }}
            options={[
              { value: "script", label: t("cloud.scripts.kindScript"), description: t("cloud.scripts.kindScriptHelp") },
              { value: "cloud-config", label: t("cloud.scripts.kindConfig"), description: t("cloud.scripts.kindConfigHelp") },
            ]}
          />
          <Field label={t("cloud.scripts.descriptionField")} optional>
            <Input maxLength={200} value={shownDescription} disabled={builtin} onValueChange={(value: string) => setDescription(value)} autoComplete="off" />
          </Field>
          <Field label={t("cloud.scripts.content")} description={t("cloud.scripts.contentHelp")} error={tried && shownContent.trim() === "" ? t("cloud.scripts.contentRequired") : undefined}>
            <Textarea mono rows={14} value={shownContent} readOnly={builtin} onChange={(event) => setContent(event.target.value)} autoComplete="off" spellCheck={false} placeholder={shownKind === "script" ? "#!/bin/sh" : "#cloud-config"} />
          </Field>
          <p className="text-13 text-pretty text-fg-muted">
            <span className="font-medium text-fg">{t("cloud.scripts.visibleTitle")}.</span> {t("cloud.scripts.visibleBody")}
          </p>
          {saveScript.isError ? <ErrorBlock live compact error={saveScript.error} title={t("cloud.scripts.saveFailed")} /> : null}
        </form>
      )}
    </Dialog>
  );
}
