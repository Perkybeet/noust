import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../api/client";
import { siteKeys, siteVersionQuery, siteVersionsQuery } from "../../api/queries/sites";
import type { SiteVersion } from "../../api/queries/sites";
import { useCan } from "../../components/page/permissions";
import { ErrorBlock } from "../../components/page/QueryState";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { RowList } from "../../components/ui/RowList";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import type { Locale } from "../../i18n";
import { formatBytes, formatDateTime } from "../../lib/format";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { namedActor } from "../activity/data";

interface SiteHistoryDialogProps {
  site: string;
  /** The editor holds changes that would be replaced by loading a version. */
  dirty: boolean;
  onClose: () => void;
  /** A version was put back as the file: the editor drops what it held and reads the file again. */
  onRestored: (version: number) => void;
}

/** What a version's source says in a word. */
function sourceLabel(source: string, t: ReturnType<typeof useT>): string {
  switch (source) {
    case "generated":
      return t("domains.siteHistory.sources.generated");
    case "edit":
      return t("domains.siteHistory.sources.edit");
    case "restore":
      return t("domains.siteHistory.sources.restore");
    default:
      return t("domains.siteHistory.sources.found");
  }
}

function Changes({ site, version }: { site: string; version: number }) {
  const t = useT();
  const detail = useQuery(siteVersionQuery(site, version));
  if (detail.isError) return <ErrorBlock compact error={detail.error} title={t("domains.siteHistory.couldNotRead", { version })} />;
  if (detail.data === undefined) {
    return (
      <LoadingRegion label={t("domains.siteHistory.loadingChanges")}>
        <Skeleton className="h-16 w-full" />
      </LoadingRegion>
    );
  }
  return detail.data.diff === "" ? (
    <p className="text-13 text-fg-muted">{t("domains.siteHistory.noDifference", { version })}</p>
  ) : (
    <SystemOutput label={t("domains.siteHistory.changesLabel", { version })} maxHeight="max-h-72">
      {detail.data.diff}
    </SystemOutput>
  );
}

function VersionRow({ site, item, open, onToggle, onUse, locale }: { site: string; item: SiteVersion; open: boolean; onToggle: () => void; onUse: () => void; locale: Locale }) {
  const t = useT();
  // Putting a version back writes the file as the editor's save does: it takes the same permission, and a role without it is not offered the button.
  const mayRestore = useCan("root_equivalent");
  return (
    <RowList.Item className="items-start">
     <div className="flex min-w-0 flex-1 flex-col gap-2">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <Mono>{t("domains.siteHistory.versionNumber", { version: item.version })}</Mono>
          {item.source === "found" ? <StatusPill state="warning" size="sm" appearance="inline" label={sourceLabel(item.source, t)} /> : <Badge>{sourceLabel(item.source, t)}</Badge>}
          {item.current ? <StatusPill state="running" size="sm" appearance="inline" label={t("domains.siteHistory.current")} /> : null}
          <span className="text-13 text-fg-muted">{formatDateTime(new Date(item.created_at), locale)}</span>
          {item.actor !== null && item.actor !== undefined ? <span className="text-13 text-fg-muted">{namedActor(t, item.actor)}</span> : null}
          <span className="text-12 text-fg-muted">{formatBytes(item.size, locale)}</span>
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <Button size="sm" aria-expanded={open} onClick={onToggle}>
            {open ? t("domains.siteHistory.hideChanges") : t("domains.siteHistory.showChanges")}
          </Button>
          {item.current || !mayRestore ? null : (
            <Button size="sm" onClick={onUse}>
              {t("domains.siteHistory.use")}
            </Button>
          )}
        </span>
      </div>
      {open ? <Changes site={site} version={item.version} /> : null}
     </div>
    </RowList.Item>
  );
}

/**
 * The earlier texts of a site's file. Every write keeps one, and a hand edit a deploy was about
 * to overwrite is kept first, so nothing is lost for good. Using a version is the server's restore: it puts the
 * text back as the file after the web server has tested it, with its own words if it refuses, and that is a
 * version too. Unsaved changes in the editor are what it replaces, so it asks once before it does.
 */
export function SiteHistoryDialog({ site, dirty, onClose, onRestored }: SiteHistoryDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const versions = useQuery(siteVersionsQuery(site));
  const items = versions.data?.versions ?? [];
  const [opened, setOpened] = useState<number | null>(null);
  const [asked, setAsked] = useState<number | null>(null);

  // Only a role that may put a version back is offered the button, so this is behind the one `useCan` of the rows.
  const restore = useMutation({
    mutationFn: (version: number) => request("post", "/api/sites/{domain}/config/versions/{version}/restore", { params: { domain: site, version } }),
    onSuccess: (_result, version) => {
      // The file, its structure and its versions all changed: everything read about the site is read again.
      void queryClient.invalidateQueries({ queryKey: siteKeys.detail(site) });
      void queryClient.invalidateQueries({ queryKey: siteKeys.all });
      onRestored(version);
      onClose();
    },
  });

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="lg"
      title={t("domains.siteHistory.title", { site })}
      description={t("domains.siteHistory.description")}
      footer={<Button onClick={onClose}>{t("common.words.close")}</Button>}
    >
      <div className="flex flex-col gap-3">
        {versions.data?.edited_outside ? <p className="text-13 text-warn">{t("domains.siteHistory.editedOutside")}</p> : null}
        {dirty ? <p className="text-13 text-fg-muted">{t("domains.siteHistory.dirtyNote")}</p> : null}
        {versions.isError ? (
          <ErrorBlock error={versions.error} title={t("domains.siteHistory.couldNotList")} onRetry={() => void versions.refetch()} retrying={versions.isRefetching} />
        ) : versions.data === undefined ? (
          <LoadingRegion label={t("domains.siteHistory.loading")}>
            <Skeleton className="h-24 w-full" />
          </LoadingRegion>
        ) : items.length === 0 ? (
          <EmptyState variant="inline" title={t("domains.siteHistory.empty")} />
        ) : (
          <RowList aria-label={t("domains.siteHistory.listLabel", { site })}>
            {items.map((item) => (
              <VersionRow
                key={item.version}
                site={site}
                item={item}
                open={opened === item.version}
                locale={t.locale}
                onToggle={() => setOpened(opened === item.version ? null : item.version)}
                onUse={() => {
                  if (dirty && asked !== item.version) {
                    setAsked(item.version);
                    return;
                  }
                  restore.mutate(item.version);
                }}
              />
            ))}
          </RowList>
        )}
        {asked !== null && !restore.isError ? <p role="status" className="text-13 text-warn">{t("domains.siteHistory.confirmUse", { version: asked })}</p> : null}
        {restore.isError ? <ErrorBlock live compact error={restore.error} title={t("domains.siteHistory.restoreFailed", { version: restore.variables })} /> : null}
      </div>
    </Dialog>
  );
}
