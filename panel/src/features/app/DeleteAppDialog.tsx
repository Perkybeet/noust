import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import type { App } from "../../api/queries/apps";
import { getLocale } from "../../app/locale";
import type { Locale } from "../../app/locale";
import { Checkbox } from "../../components/ui/Checkbox";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Mono } from "../../components/ui/Mono";
import { toast } from "../../components/ui/toast";
import { translate, useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { useDeleteApp } from "./useDeleteApp";

/**
 * What a deletion with these options removes and what it keeps, as one sentence. The service
 * and the web server site always go; backups always stay; the files and the certificate go
 * only when the operator ticks them.
 */
export function deletionSummary(removeFiles: boolean, removeSsl: boolean, locale: Locale = getLocale()): string {
  if (removeFiles && removeSsl) return translate(locale, "appPages.deleteApp.summaryFilesAndCertificate");
  if (removeFiles) return translate(locale, "appPages.deleteApp.summaryFiles");
  if (removeSsl) return translate(locale, "appPages.deleteApp.summaryCertificate");
  return translate(locale, "appPages.deleteApp.summaryKeepsBoth");
}

export interface DeleteAppDialogProps {
  app: Pick<App, "domain" | "path">;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Deleting an application, with the friction losing it deserves: the domain typed out. What
 * goes with it besides the service and the site - its files, its certificate - is a choice
 * made in the dialog, and both start unticked: nothing that cannot be made again is destroyed
 * unless the operator says so. Queued as a job; the page goes back to the list.
 */
export function DeleteAppDialog({ app, open, onOpenChange }: DeleteAppDialogProps) {
  const t = useT();
  const { node } = useNode();
  const navigate = useNavigate();
  const domain = app.domain;
  const remove = useDeleteApp(domain);
  const [removeFiles, setRemoveFiles] = useState(false);
  const [removeSsl, setRemoveSsl] = useState(false);

  // Opening again starts from nothing ticked, whatever was chosen the last time.
  const [wasOpen, setWasOpen] = useState(open);
  if (wasOpen !== open) {
    setWasOpen(open);
    if (open) {
      setRemoveFiles(false);
      setRemoveSsl(false);
    }
  }

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      friction="type"
      server={node}
      title={t("appPages.deleteApp.title", { domain })}
      // The summary follows the options, so the question always says what will be destroyed.
      description={deletionSummary(removeFiles, removeSsl, t.locale)}
      confirmText={domain}
      actionLabel={t("appPages.deleteApp.action")}
      onConfirm={async () => {
        await remove.mutateAsync({ removeFiles, removeSsl });
        // The page is about to go; the toast is what stays to say the job is on its way.
        toast.info(t("appPages.actions.deletionQueued", { domain }), { description: t("appPages.actions.deletionQueuedDescription") });
        void navigate({ to: "/apps" });
      }}
    >
      <Checkbox
        label={t("appPages.deleteApp.filesLabel")}
        description={
          app.path
            ? t.rich("appPages.deleteApp.filesDescription", { path: <Mono tone="muted">{app.path}</Mono> })
            : t("appPages.deleteApp.filesDescriptionNoPath")
        }
        checked={removeFiles}
        onCheckedChange={setRemoveFiles}
      />
      <Checkbox
        label={t("appPages.deleteApp.certificateLabel")}
        description={t("appPages.deleteApp.certificateDescription")}
        checked={removeSsl}
        onCheckedChange={setRemoveSsl}
      />
    </ConfirmDialog>
  );
}
