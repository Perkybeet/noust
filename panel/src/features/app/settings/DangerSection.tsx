import { useNavigate } from "@tanstack/react-router";
import { Trash2 } from "lucide-react";
import { useState } from "react";

import { ElevationCancelledError } from "../../../api/errors";
import type { App } from "../../../api/queries/apps";
import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { DangerZone } from "../../../components/page/DangerZone";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { translate } from "../../../i18n/translate";
import { reportActionError } from "../../apps/useAppActions";
import { useConfirmItsYou, useDeleteApp } from "../useDeleteApp";

/** Joins a list the way a sentence does: "a", "a and b", "a, b and c". */
function joinList(parts: readonly string[], and: string): string {
  if (parts.length === 1) return parts[0] ?? "";
  return `${parts.slice(0, -1).join(", ")} ${and} ${parts.at(-1) ?? ""}`;
}

/** Exactly what a deletion with these options removes and keeps. */
export function deletionSummary(app: Pick<App, "path">, removeFiles: boolean, removeSsl: boolean, locale: Locale = getLocale()): string {
  const and = translate(locale, "appSettings.danger.and");
  const gone = [translate(locale, "appSettings.danger.goneService"), translate(locale, "appSettings.danger.goneSite")];
  if (removeSsl) gone.push(translate(locale, "appSettings.danger.goneCertificate"));
  if (removeFiles) {
    gone.push(
      app.path
        ? translate(locale, "appSettings.danger.goneFilesIn", { path: app.path })
        : translate(locale, "appSettings.danger.goneFilesGeneric"),
    );
  }
  const kept = [translate(locale, "appSettings.danger.keptBackups")];
  if (!removeSsl) kept.push(translate(locale, "appSettings.danger.keptCertificate"));
  if (!removeFiles) kept.push(translate(locale, "appSettings.danger.keptFiles"));
  return translate(locale, "appSettings.danger.summary", { list: joinList(gone, and), kept: joinList(kept, and) });
}

/**
 * Deleting the app: the choice of what goes with it first, then "Confirm it's you", then the
 * domain typed out. Queued as a job, like every change that takes a while.
 */
export function DangerSection({ app }: { app: App }) {
  const t = useT();
  const domain = app.domain;
  const navigate = useNavigate();
  const remove = useDeleteApp(domain);
  const confirmItsYou = useConfirmItsYou();
  const [removeFiles, setRemoveFiles] = useState(true);
  const [removeSsl, setRemoveSsl] = useState(true);
  const [open, setOpen] = useState(false);

  const start = (): void => {
    confirmItsYou().then(
      () => {
        setOpen(true);
      },
      (error: unknown) => {
        if (!(error instanceof ElevationCancelledError)) reportActionError(t("appSettings.danger.startFailed", { domain }), error);
      },
    );
  };

  return (
    <DangerZone title={t("appSettings.danger.zoneTitle")}>
      <div className="flex flex-col gap-4 px-5 py-4 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
        <div className="flex min-w-0 flex-col gap-3">
          <div>
            <h3 className="text-14 font-medium text-fg">{t("appSettings.danger.title")}</h3>
            <p className="mt-0.5 max-w-[60ch] text-13 text-pretty text-fg-muted">{t("appSettings.danger.description")}</p>
          </div>
          <div className="flex flex-col gap-2.5">
            <Checkbox
              label={t("appSettings.danger.deleteFilesLabel")}
              description={
                app.path
                  ? t("appSettings.danger.deleteFilesDescriptionWithPath", { path: app.path })
                  : t("appSettings.danger.deleteFilesDescriptionNoPath")
              }
              checked={removeFiles}
              onCheckedChange={setRemoveFiles}
            />
            <Checkbox
              label={t("appSettings.danger.deleteSslLabel")}
              description={t("appSettings.danger.deleteSslDescription")}
              checked={removeSsl}
              onCheckedChange={setRemoveSsl}
            />
          </div>
        </div>
        <div className="shrink-0">
          <Button variant="danger" icon={<Trash2 aria-hidden="true" />} onClick={start}>
            {t("appSettings.danger.deleteButton")}
          </Button>
        </div>
      </div>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={t("appSettings.danger.deleteDialogTitle", { domain })}
        description={deletionSummary(app, removeFiles, removeSsl, t.locale)}
        confirmText={domain}
        actionLabel={t("appSettings.danger.deleteButton")}
        onConfirm={async () => {
          await remove.mutateAsync({ removeFiles, removeSsl });
          // The page is about to go; the toast is what stays to say the job is on its way.
          toast.info(t("appSettings.danger.deletionQueued", { domain }), { description: t("appSettings.danger.deletionQueuedHint") });
          void navigate({ to: "/apps" });
        }}
      />
    </DangerZone>
  );
}
