import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { jobKeys } from "../../../api/queries/jobs";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import type { Accepted } from "../jobs";

type Target = "new" | "replace";
const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;

export interface RestoreSource {
  /** The dump's file name. */
  dump: string;
  /** The destination it is on, for a dump this server does not hold. */
  destination?: string | undefined;
}

export interface RestoreDialogProps {
  engine: string;
  /** The database the dump is of. */
  name: string;
  source: RestoreSource | null;
  /** Redis restores its one instance: there is no "new database" to restore into. */
  replaceOnly?: boolean;
  onClose: () => void;
  onQueued: (accepted: Accepted, target: Target, database: string) => void;
}

/**
 * Restoring a dump. Into a new database beside this one by default, which touches nothing that
 * exists; or over this one, which takes a safety copy first and puts it back if the restore
 * fails, and asks for the database's name to be typed, since what it holds now is replaced.
 */
export function RestoreDialog({ engine, name, source, replaceOnly = false, onClose, onQueued }: RestoreDialogProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<Target>(replaceOnly ? "replace" : "new");
  const [typedName, setNewName] = useState<string | null>(null);
  const [recreate, setRecreate] = useState(false);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const suggestion = useQuery({
    queryKey: ["databases", "suggest-name", engine, name, source?.dump ?? ""],
    queryFn: ({ signal }) =>
      request("get", "/api/databases/backups/suggest-name", { query: { engine, database: name, ...(source ? { backup_name: source.dump } : {}) }, signal }),
    enabled: source !== null && !replaceOnly,
    staleTime: 0,
  });
  // What the operator typed, else the free name the server suggests.
  const newName = typedName ?? suggestion.data?.name ?? "";

  const restore = useMutation({
    mutationFn: () => {
      if (source === null) throw new Error("no dump");
      const common = {
        engine,
        database: name,
        backup_name: source.dump,
        drop_existing: target === "replace" && recreate,
        safety_backup: true,
        new_name: target === "new" ? newName.trim() : null,
      };
      return source.destination !== undefined
        ? request("post", "/api/databases/backups/restore-remote", { body: { ...common, destination: source.destination } })
        : request("post", "/api/databases/backups/restore", { body: common });
    },
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      onQueued(accepted, target, target === "new" ? newName.trim() : name);
      onClose();
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (target === "new" && !NAME.test(newName.trim())) {
      setError(t("databases.restore.nameInvalid"));
      return;
    }
    if (target === "replace" && typed !== name) return;
    setError(null);
    restore.mutate();
  };

  const formId = "restore-dump-form";
  const replacing = target === "replace";
  return (
    <Dialog
      open={source !== null}
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="md"
      title={t("databases.restore.title")}
      description={
        source?.destination !== undefined
          ? t.rich("databases.restore.fromRemote", { dump: <Mono>{source.dump}</Mono>, destination: <Mono>{source.destination}</Mono> })
          : t.rich("databases.restore.fromLocal", { dump: <Mono>{source?.dump ?? ""}</Mono> })
      }
      footer={
        <>
          <Button onClick={onClose}>{t("databases.common.cancel")}</Button>
          <Button
            type="submit"
            form={formId}
            variant={replacing ? "danger" : "primary"}
            loading={restore.isPending}
            disabled={replacing && typed !== name}
          >
            {replacing ? t("databases.restore.replaceAction", { name }) : t("databases.restore.newAction")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {node !== null ? <p className="text-13 text-fg-muted">{t.rich("databases.restore.onServer", { server: <Mono tone="default">{node}</Mono> })}</p> : null}
        {replaceOnly ? null : (
          <Field label={t("databases.restore.into")} nativeLabel={false}>
            <Select<Target>
              value={target}
              onValueChange={setTarget}
              options={[
                { value: "new", label: t("databases.restore.intoNew"), hint: t("databases.restore.intoNewHint") },
                { value: "replace", label: t("databases.restore.intoExisting", { name }), hint: t("databases.restore.intoExistingHint") },
              ]}
            />
          </Field>
        )}
        {target === "new" ? (
          <Field label={t("databases.restore.newName")} description={t("databases.restore.newNameHelp")} error={error}>
            <Input mono value={newName} onValueChange={(value: string) => setNewName(value)} autoComplete="off" spellCheck={false} />
          </Field>
        ) : (
          <>
            <Notice tone="warning" title={t("databases.restore.replaceTitle", { name })}>
              {t("databases.restore.replaceBody", { name })}
            </Notice>
            {replaceOnly ? null : <Checkbox checked={recreate} onCheckedChange={setRecreate} label={t("databases.restore.recreate")} description={t("databases.restore.recreateHelp")} />}
            <Field label={t.rich("databases.restore.typeName", { name: <Mono tone="default">{name}</Mono> })}>
              <Input mono value={typed} onValueChange={(value: string) => setTyped(value)} autoComplete="off" autoCapitalize="off" spellCheck={false} />
            </Field>
          </>
        )}
        {restore.isError ? <ErrorBlock live compact error={restore.error} title={t("databases.restore.failed")} /> : null}
      </form>
    </Dialog>
  );
}
