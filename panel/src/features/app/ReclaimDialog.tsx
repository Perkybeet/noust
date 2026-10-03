/**
 * Handing a Compose stack that runs outside its unit back to Noust (item 73; `POST
 * /api/apps/{domain}/reclaim`, behind sudo mode). Someone ran `docker compose up -d` by hand:
 * the containers serve, the unit is stopped, and neither a reboot nor Noust brings the stack
 * back. Handing it back enables the unit and starts it.
 *
 * The preview comes first, on opening: the containers that run and Compose's rehearsal of the
 * start, verbatim. A rehearsal that would recreate a container is refused with Compose's own
 * output, as adopting a stack is, and going on anyway is a choice the operator ticks after
 * reading it.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { isApiError, request } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import { appKeys } from "../../api/queries/apps";
import { announce } from "../../app/Announcer";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog, DialogClose } from "../../components/ui/Dialog";
import { SkeletonText } from "../../components/ui/Skeleton";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";

export type Reclaim = ResponseOf<"/api/apps/{domain}/reclaim", "post">;

/** Compose's rehearsal would recreate something: 409 with its output. */
export function isRecreateRefusal(error: unknown): boolean {
  return isApiError(error) && error.status === 409 && error.output !== null;
}

function reclaimCall(domain: string, body: { preview: boolean; accept_recreate: boolean }) {
  return request("post", "/api/apps/{domain}/reclaim", { params: { domain }, body });
}

/**
 * The dialog and its two presses: what was found, then handing it back. Mount it with a new
 * `key` each time it opens.
 */
export function ReclaimDialog({ domain, open, onOpenChange }: { domain: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [acceptRecreate, setAcceptRecreate] = useState(false);

  const preview = useMutation({
    mutationFn: () => reclaimCall(domain, { preview: true, accept_recreate: false }),
    onSuccess: () => announce(t("appPages.reclaim.previewReady")),
  });
  const handBack = useMutation({
    mutationFn: (accept: boolean) => reclaimCall(domain, { preview: false, accept_recreate: accept }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain) });
      void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
      toast.success(t("appPages.reclaim.done", { domain }));
      onOpenChange(false);
    },
  });

  // Asked when the dialog opens. The caller mounts a fresh dialog for each opening (a new `key`),
  // so what runs is looked at again and nothing from the last look is kept.
  const { mutate: ask } = preview;
  useEffect(() => {
    if (open) ask();
  }, [open, ask]);

  const refused = isRecreateRefusal(preview.error) || isRecreateRefusal(handBack.error);
  const found: Reclaim | undefined = preview.data;
  const ready = found !== undefined || (refused && acceptRecreate);
  // A held request (a cancelled "Confirm it's you", a second person asked) is ErrorBlock's to say.
  const handBackError = handBack.error !== null && !isRecreateRefusal(handBack.error) ? handBack.error : null;

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title={t("appPages.reclaim.title", { domain })}
      description={t("appPages.reclaim.description")}
      footer={
        <>
          <DialogClose render={<Button variant="secondary">{t("common.confirmDialog.cancel")}</Button>} />
          <Button variant="primary" disabled={!ready} loading={handBack.isPending} onClick={() => handBack.mutate(refused && acceptRecreate)}>
            {t("appPages.reclaim.action")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {preview.isPending ? (
          <div className="flex flex-col gap-2" role="status">
            <span className="sr-only">{t("appPages.reclaim.checking")}</span>
            <SkeletonText lines={4} />
          </div>
        ) : null}

        {found !== undefined ? (
          <>
            <KeyValueList
              items={[
                { label: t("appPages.reclaim.containers"), value: found.containers.join(", ") },
                {
                  label: t("appPages.reclaim.unit"),
                  value: `${found.unit}.service`,
                  hint: found.enabled ? t("appPages.reclaim.unitEnabled") : t("appPages.reclaim.unitWillEnable"),
                },
                { label: t("appPages.reclaim.project"), value: found.project ?? null },
              ]}
            />
            <div className="flex flex-col gap-2">
              <p className="text-13 font-medium text-fg">{t("appPages.reclaim.dryRunTitle")}</p>
              <p className="text-12 text-pretty text-fg-muted">{t("appPages.reclaim.dryRunDescription")}</p>
              <SystemOutput label={t("appPages.reclaim.dryRunLabel")} maxHeight="max-h-64" className="rounded-control border border-border bg-bg-sunken p-3">
                {found.dry_run === "" ? t("appPages.reclaim.dryRunEmpty") : found.dry_run}
              </SystemOutput>
            </div>
          </>
        ) : null}

        {refused ? (
          <div className="flex flex-col gap-3">
            <ErrorBlock live compact error={isRecreateRefusal(handBack.error) ? handBack.error : preview.error} title={t("appPages.reclaim.wouldRecreateTitle")} />
            <Checkbox
              label={t("appPages.reclaim.acceptRecreate")}
              description={t("appPages.reclaim.acceptRecreateDescription")}
              checked={acceptRecreate}
              onCheckedChange={setAcceptRecreate}
            />
          </div>
        ) : preview.isError ? (
          <ErrorBlock live compact error={preview.error} title={t("appPages.reclaim.couldNotCheck")} onRetry={() => ask()} />
        ) : null}

        {handBackError !== null ? <ErrorBlock live compact error={handBackError} title={t("appPages.reclaim.failed", { domain })} /> : null}

        <CommandHint command={`noust app reclaim ${domain}`} label={t("appPages.reclaim.terminal")} />
      </div>
    </Dialog>
  );
}
