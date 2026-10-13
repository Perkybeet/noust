import { useQuery } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { useMemo, useState } from "react";

import { previewLogsQuery } from "../../../api/queries/previews";
import type { Preview } from "../../../api/queries/previews";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { Drawer } from "../../../components/ui/Drawer";
import { LogViewer } from "../../../components/ui/LogViewer";
import type { LogLine } from "../../../components/ui/LogViewer";
import { useT } from "../../../i18n";
import { previewName } from "./previews";

type View = "build" | "service";

/** A log read as text, one line each, numbered in the order they came. */
function linesOf(text: string | null | undefined): LogLine[] {
  if (text === null || text === undefined || text.trim() === "") return [];
  return text
    .replace(/\n$/, "")
    .split("\n")
    .map((line, index) => ({ id: index, text: line }));
}

/**
 * What one preview printed, in a drawer beside the list: the log of its last build (what its
 * deployment printed, scrubbed of its secrets) and its service's journal. Verbatim: the console
 * adds nothing to either. The failed build's own words are the most useful thing a failed
 * preview has, so they are one click from its row.
 */
export function PreviewLogsDrawer({ domain, preview, onClose }: { domain: string; preview: Preview | null; onClose: () => void }) {
  const t = useT();
  const [view, setView] = useState<View>("build");
  const number = preview?.number ?? 0;
  const logs = useQuery({ ...previewLogsQuery(domain, number), enabled: preview !== null });
  const name = preview === null ? "" : previewName(preview);
  const build = useMemo(() => linesOf(logs.data?.build), [logs.data?.build]);
  const service = useMemo(() => linesOf(logs.data?.logs), [logs.data?.logs]);
  return (
    <Drawer
      open={preview !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="lg"
      title={t("previewHand.logs.title", { name })}
      description={t("previewHand.logs.description")}
      footer={
        <>
          <Button icon={<RotateCw aria-hidden="true" />} loading={logs.isFetching && !logs.isPending} onClick={() => void logs.refetch()}>
            {t("previewHand.logs.refresh")}
          </Button>
        </>
      }
    >
      <div className="flex min-h-0 flex-1 flex-col gap-3">
        <SegmentedControl<View>
          label={t("previewHand.logs.viewLabel")}
          value={view}
          options={[
            { value: "build", label: t("previewHand.logs.build") },
            { value: "service", label: t("previewHand.logs.service") },
          ]}
          onValueChange={setView}
        />
        {logs.isError ? (
          <ErrorBlock compact error={logs.error} title={t("previewHand.logs.readFailed", { name })} onRetry={() => void logs.refetch()} retrying={logs.isRefetching} />
        ) : view === "build" ? (
          <LogViewer
            lines={build}
            loading={logs.isPending}
            height={420}
            label={t("previewHand.logs.buildLabel", { name })}
            filename={`${preview?.domain ?? "preview"}-build.log`}
            emptyMessage={t("previewHand.logs.noBuild")}
          />
        ) : (
          <LogViewer
            lines={service}
            loading={logs.isPending}
            height={420}
            label={t("previewHand.logs.serviceLabel", { name })}
            filename={`${preview?.domain ?? "preview"}-service.log`}
            emptyMessage={t("previewHand.logs.noService")}
          />
        )}
      </div>
    </Drawer>
  );
}
