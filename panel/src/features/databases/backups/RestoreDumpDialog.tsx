import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { jobKeys } from "../../../api/queries/jobs";
import { RestoreDialog } from "../../../components/page/RestoreDialog";
import type { RestoreTarget } from "../../../components/page/RestoreDialog";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import type { Accepted } from "../jobs";

const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;

export interface RestoreSource {
  /** The dump's file name. */
  dump: string;
  /** The destination it is on, for a dump this server does not hold. */
  destination?: string | undefined;
}

export interface RestoreDumpDialogProps {
  engine: string;
  /** The database the dump is of. */
  name: string;
  source: RestoreSource | null;
  /** Redis restores its one instance: there is no "new database" to restore into. */
  replaceOnly?: boolean;
  onClose: () => void;
  onQueued: (accepted: Accepted, target: RestoreTarget, database: string) => void;
}

/**
 * Restoring a dump of a database: the console's one restore confirmation (`RestoreDialog`, DB-1) with
 * the database's words. Into a new database beside this one by default, which touches nothing that
 * exists; or over this one, which takes a safety copy first, keeps it with the database's backups, puts
 * it back if the restore fails, and asks for the database's name to be typed.
 */
export function RestoreDumpDialog({ engine, name, source, replaceOnly = false, onClose, onQueued }: RestoreDumpDialogProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [recreate, setRecreate] = useState(false);
  const suggestion = useQuery({
    queryKey: ["databases", "suggest-name", engine, name, source?.dump ?? ""],
    queryFn: ({ signal }) =>
      request("get", "/api/databases/backups/suggest-name", { query: { engine, database: name, ...(source ? { backup_name: source.dump } : {}) }, signal }),
    enabled: source !== null && !replaceOnly,
    staleTime: 0,
  });

  const restore = useMutation({
    mutationFn: ({ target, newName }: { target: RestoreTarget; newName: string }) => {
      if (source === null) throw new Error("no dump");
      const replacing = target === "replace";
      const common = {
        engine,
        database: name,
        backup_name: source.dump,
        drop_existing: replacing && recreate,
        safety_backup: true,
        new_name: replacing ? null : newName,
      };
      return source.destination !== undefined
        ? request("post", "/api/databases/backups/restore-remote", { body: { ...common, destination: source.destination } })
        : request("post", "/api/databases/backups/restore", { body: common });
    },
    onSuccess: (accepted, { target, newName }) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      onQueued(accepted, target, target === "replace" ? name : newName);
    },
  });

  return (
    <RestoreDialog
      // Starts over for each dump, with the name the server suggests for it.
      key={source?.dump ?? ""}
      open={source !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={t("databases.restore.title")}
      source={
        source?.destination !== undefined
          ? t.rich("databases.restore.fromRemote", { dump: <Mono>{source.dump}</Mono>, destination: <Mono>{source.destination}</Mono> })
          : t.rich("databases.restore.fromLocal", { dump: <Mono>{source?.dump ?? ""}</Mono> })
      }
      server={node}
      existing={name}
      defaultTarget={replaceOnly ? "replace" : "new"}
      replaceOnly={replaceOnly}
      suggestion={suggestion.data?.name ?? ""}
      isValidName={(candidate) => NAME.test(candidate)}
      words={{
        into: t("databases.restore.into"),
        intoNew: t("databases.restore.intoNew"),
        intoNewHint: t("databases.restore.intoNewHint"),
        intoReplace: t("databases.restore.intoExisting", { name }),
        intoReplaceHint: t("databases.restore.intoExistingHint"),
        replaceNote: (
          <>
            <span className="font-medium">{t("databases.restore.replaceTitle", { name })}.</span> {t("databases.restore.replaceBody", { name })}
          </>
        ),
        newName: t("databases.restore.newName"),
        newNameHelp: t("databases.restore.newNameHelp"),
        nameInvalid: t("databases.restore.nameInvalid"),
        actionReplace: t("databases.restore.replaceAction", { name }),
        actionNew: t("databases.restore.newAction"),
      }}
      options={(target) =>
        target === "replace" && !replaceOnly ? (
          <Checkbox checked={recreate} onCheckedChange={setRecreate} label={t("databases.restore.recreate")} description={t("databases.restore.recreateHelp")} />
        ) : null
      }
      onRestore={async ({ target, name: newName }) => {
        await restore.mutateAsync({ target, newName });
      }}
    />
  );
}
