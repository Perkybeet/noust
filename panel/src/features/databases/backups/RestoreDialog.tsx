import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { jobKeys } from "../../../api/queries/jobs";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
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
 * Restoring a dump, as the system's one confirmation (docs/DESIGN.md, DB-1). Into a new database
 * beside this one by default, which touches nothing that exists and asks one question; or over this
 * one, which takes a safety copy first, keeps it with the database's backups, puts it back if the
 * restore fails, and asks for the database's name to be typed, since what it holds now is replaced.
 */
export function RestoreDialog({ engine, name, source, replaceOnly = false, onClose, onQueued }: RestoreDialogProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<Target>(replaceOnly ? "replace" : "new");
  const [typedName, setNewName] = useState<string | null>(null);
  const [recreate, setRecreate] = useState(false);
  const suggestion = useQuery({
    queryKey: ["databases", "suggest-name", engine, name, source?.dump ?? ""],
    queryFn: ({ signal }) =>
      request("get", "/api/databases/backups/suggest-name", { query: { engine, database: name, ...(source ? { backup_name: source.dump } : {}) }, signal }),
    enabled: source !== null && !replaceOnly,
    staleTime: 0,
  });
  // What the operator typed, else the free name the server suggests.
  const newName = typedName ?? suggestion.data?.name ?? "";
  const replacing = target === "replace";

  const restore = useMutation({
    mutationFn: () => {
      if (source === null) throw new Error("no dump");
      const common = {
        engine,
        database: name,
        backup_name: source.dump,
        drop_existing: replacing && recreate,
        safety_backup: true,
        new_name: replacing ? null : newName.trim(),
      };
      return source.destination !== undefined
        ? request("post", "/api/databases/backups/restore-remote", { body: { ...common, destination: source.destination } })
        : request("post", "/api/databases/backups/restore", { body: common });
    },
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      onQueued(accepted, target, replacing ? name : newName.trim());
    },
  });

  const nameOk = NAME.test(newName.trim());
  return (
    <ConfirmDialog
      {...(replacing ? { friction: "type" as const, confirmText: name } : { friction: "simple" as const })}
      open={source !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="md"
      title={t("databases.restore.title")}
      description={
        source?.destination !== undefined
          ? t.rich("databases.restore.fromRemote", { dump: <Mono>{source.dump}</Mono>, destination: <Mono>{source.destination}</Mono> })
          : t.rich("databases.restore.fromLocal", { dump: <Mono>{source?.dump ?? ""}</Mono> })
      }
      actionLabel={replacing ? t("databases.restore.replaceAction", { name }) : t("databases.restore.newAction")}
      destructive={replacing}
      // A name the server would refuse waits for a name it would not: the box says which.
      ready={replacing || nameOk}
      onConfirm={async () => {
        await restore.mutateAsync();
      }}
    >
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
      {replacing ? (
        <>
          <p className="text-13 text-pretty text-fg">
            <span className="font-medium">{t("databases.restore.replaceTitle", { name })}.</span> {t("databases.restore.replaceBody", { name })}
          </p>
          {replaceOnly ? null : <Checkbox checked={recreate} onCheckedChange={setRecreate} label={t("databases.restore.recreate")} description={t("databases.restore.recreateHelp")} />}
        </>
      ) : (
        <Field
          label={t("databases.restore.newName")}
          description={t("databases.restore.newNameHelp")}
          error={newName.trim() !== "" && !nameOk ? t("databases.restore.nameInvalid") : undefined}
        >
          <Input mono value={newName} onValueChange={(value: string) => setNewName(value)} autoComplete="off" spellCheck={false} />
        </Field>
      )}
    </ConfirmDialog>
  );
}
