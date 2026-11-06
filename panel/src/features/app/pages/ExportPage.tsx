import { useMutation, useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";

import { request } from "../../../api/client";
import { appQuery } from "../../../api/queries/apps";
import { useDocumentTitle } from "../../../app/documentTitle";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { GuardedButton, ReadOnlyLine } from "../../../components/page/permissions";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { SkeletonSection } from "../../../components/ui/Skeleton";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { downloadText } from "../../../lib/clipboard";
import { reportActionError } from "../../apps/useAppActions";
import { DeclarationCard } from "../settings/Declaration";
import { SETTINGS_ICONS } from "../settings/objectIcons";

/**
 * The whole application as a file `noust app import` or the new-application page reads back, on this server or
 * another. Secret values are left out unless asked for, which needs sudo mode: the API client asks "Confirm it's
 * you" itself when the answer is `elevation_required`, the same as any other request.
 */
function ExportCard({ domain }: { domain: string }) {
  const t = useT();
  const [includeSecrets, setIncludeSecrets] = useState(false);
  const exportApp = useMutation({
    mutationFn: (withSecrets: boolean) =>
      request("get", "/api/apps/{domain}/export", { params: { domain }, ...(withSecrets ? { query: { with_secrets: true as const } } : {}) }),
    onSuccess: (document) => {
      downloadText(`${domain}.noust-app.json`, JSON.stringify(document, null, 2), "application/json");
      // design-exception: toast-success A download happens outside the page: nothing on screen says it went through.
      toast.success(t("appSettings.exportApp.exported", { domain }));
    },
    onError: (error: unknown) => {
      reportActionError(t("appSettings.exportApp.exportFailed"), error);
    },
  });
  return (
    <Card title={t("appSettings.exportApp.title")} level={2} icon={SETTINGS_ICONS.export} description={t("appSettings.exportApp.description")}>
      <div className="flex flex-col gap-4">
        <Checkbox
          label={t("appSettings.exportApp.includeSecretsLabel")}
          description={t("appSettings.exportApp.includeSecretsDescription")}
          checked={includeSecrets}
          onCheckedChange={setIncludeSecrets}
        />
        <div>
          <GuardedButton
            permission="secrets.reveal"
            variant="secondary"
            icon={<Download aria-hidden="true" />}
            loading={exportApp.isPending}
            onClick={() => exportApp.mutate(includeSecrets)}
          >
            {t("appSettings.exportApp.exportButton")}
          </GuardedButton>
        </div>
      </div>
    </Card>
  );
}

/**
 * Taking the application somewhere else, or keeping how it is put together (item 120: it left Danger, which is now
 * Delete alone): the full copy to import on another server and the declaration, the `noust.yaml` that describes it.
 * Nothing here changes the application.
 */
export function ExportPage({ domain }: { domain: string }) {
  const t = useT();
  const app = useQuery(appQuery(domain));
  useDocumentTitle(t("appPages.config.documentTitle", { section: t("nav.appTabs.export.label"), domain }), 1);
  if (app.data === undefined) {
    return app.isError ? null : (
      <LoadingRegion label={t("appPages.config.loading", { section: t("nav.appTabs.export.label") })}>
        <SkeletonSection rows={3} />
      </LoadingRegion>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <ReadOnlyLine permission="secrets.reveal" />
      <div className="grid min-w-0 items-start gap-6 xl:grid-cols-2">
        <ExportCard domain={domain} />
        <DeclarationCard domain={domain} level={2} />
      </div>
    </div>
  );
}
