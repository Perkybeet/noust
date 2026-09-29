import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Dialog } from "../../../components/ui/Dialog";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { centralNameFrom, nodeKeyQuery, nodeKeys, removeNode } from "../../fleet/nodes";
import type { NodeRemoved } from "../../fleet/nodes";

export interface RemoveServerDialogProps {
  name: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Removing a server: the name typed to confirm, sudo mode, and what happens on the server -
 * the central revokes its own token there when it answers; when it does not, the command to
 * run on the server to finish, which the central spells from the name it gave itself there.
 * Afterwards, what the central did, in its own words.
 */
export function RemoveServerDialog({ name, open, onOpenChange }: RemoveServerDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  // The central's name on the server is in the command that authorized it.
  const key = useQuery({ ...nodeKeyQuery(name), enabled: open });
  const central = key.data === undefined ? null : centralNameFrom(key.data.authorize_command);
  const [removed, setRemoved] = useState<NodeRemoved | null>(null);

  return (
    <>
      <ConfirmDialog
        open={open}
        onOpenChange={onOpenChange}
        title={t("servers.settings.removeDialog.title", { name })}
        description={
          <>
            {t("servers.settings.removeDialog.description", { name })}
            <span className="mt-2 block">
              {central !== null
                ? t("servers.settings.removeDialog.unreachable", { name })
                : t("servers.settings.removeDialog.unreachableUnknown", { name })}
            </span>
            {central !== null ? (
              <code translate="no" className="mt-1.5 block rounded-control border border-border bg-bg-sunken px-2.5 py-1.5 text-12 break-all text-fg select-all">
                {`noust fleet deauthorize --name ${central}`}
              </code>
            ) : null}
          </>
        }
        confirmText={name}
        actionLabel={t("servers.settings.removeDialog.action")}
        onConfirm={async () => {
          const result = await removeNode(name, true);
          queryClient.removeQueries({ queryKey: ["fleet", name] });
          void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
          // What the central did is worth reading (a token it could not revoke, the command
          // to finish on the server); with nothing to say, a toast is enough.
          if (result.messages.length > 0) setRemoved(result);
          else toast.success(t("servers.settings.removeDialog.removedToast", { name: result.name }));
        }}
      />
      {removed !== null ? (
        <Dialog
          open
          onOpenChange={(next) => {
            if (!next) setRemoved(null);
          }}
          size="md"
          title={t("servers.settings.removeDialog.resultTitle", { name: removed.name })}
          description={t("servers.settings.removeDialog.resultDescription")}
          footer={
            <Button
              variant="primary"
              onClick={() => {
                setRemoved(null);
              }}
            >
              {t("settings.shared.done")}
            </Button>
          }
        >
          <SystemOutput
            label={t("servers.settings.removeDialog.resultTitle", { name: removed.name })}
            className="rounded-control border border-border bg-bg-sunken px-3 py-2"
          >
            {removed.messages.join("\n")}
          </SystemOutput>
        </Dialog>
      ) : null}
    </>
  );
}
