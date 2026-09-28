import { useMutation } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";

import { request } from "../../../api/client";
import type { App } from "../../../api/queries/apps";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { downloadText } from "../../../lib/clipboard";
import { reportActionError } from "../../apps/useAppActions";
import { PANEL } from "./panel";

/**
 * Everything that defines the application, as a JSON document: `wasm app export`'s own shape,
 * so the file downloaded here is exactly what `wasm app import` or the new-app wizard's import
 * reads back, on this server or another. Secret values are left out unless asked for, which
 * needs sudo mode - the API client asks "Confirm it's you" itself when the answer is
 * `elevation_required`, the same as any other request.
 */
export function ExportSection({ app }: { app: App }) {
  const t = useT();
  const domain = app.domain;
  const [includeSecrets, setIncludeSecrets] = useState(false);

  const exportApp = useMutation({
    mutationFn: (withSecrets: boolean) =>
      request("get", "/api/apps/{domain}/export", { params: { domain }, ...(withSecrets ? { query: { with_secrets: true as const } } : {}) }),
    onSuccess: (document) => {
      downloadText(`${domain}.wasm-app.json`, JSON.stringify(document, null, 2), "application/json");
      toast.success(t("appSettings.exportApp.exported", { domain }));
    },
    onError: (error: unknown) => {
      reportActionError(t("appSettings.exportApp.exportFailed"), error);
    },
  });

  return (
    <Section title={t("appSettings.exportApp.title")} description={t("appSettings.exportApp.description")}>
      <div className={`${PANEL} flex flex-col gap-4 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5`}>
        <Checkbox
          label={t("appSettings.exportApp.includeSecretsLabel")}
          description={t("appSettings.exportApp.includeSecretsDescription")}
          checked={includeSecrets}
          onCheckedChange={setIncludeSecrets}
        />
        <div className="shrink-0">
          <Button
            icon={<Download aria-hidden="true" />}
            loading={exportApp.isPending}
            onClick={() => exportApp.mutate(includeSecrets)}
          >
            {t("appSettings.exportApp.exportButton")}
          </Button>
        </div>
      </div>
    </Section>
  );
}
