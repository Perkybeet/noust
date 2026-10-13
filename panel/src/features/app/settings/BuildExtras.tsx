import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { announce } from "../../../app/Announcer";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { useAppActions } from "../../apps/useAppActions";
import { buildCacheQuery, processTypesQuery, settingsKeys } from "./queries";

/**
 * The cache the application's builds share: how much it holds, emptying it, and building the
 * live version again from nothing. The same as `noust app cache` and `noust update --no-cache`.
 */
export function BuildCacheCard({ domain }: { domain: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const view = useQuery(buildCacheQuery(domain));
  const { rebuildFresh } = useAppActions(domain);
  const [emptying, setEmptying] = useState(false);
  const clear = useMutation({
    mutationFn: () => request("delete", "/api/apps/{domain}/build-cache", { params: { domain } }),
    onSuccess: (freed) => {
      void queryClient.invalidateQueries({ queryKey: settingsKeys.buildCache(domain) });
      announce(freed.size_bytes > 0 ? t("buildExtras.cache.emptied", { domain }) : t("buildExtras.cache.nothingFreed", { domain }));
    },
  });
  const title = t("buildExtras.cache.title");
  const description = t("buildExtras.cache.description");

  if (view.isPending) {
    return (
      <Card title={title} description={description} loading>
        <span className="sr-only">{t("buildExtras.cache.loading")}</span>
        <Skeleton className="h-12 w-full rounded-control" />
      </Card>
    );
  }
  if (view.isError) {
    return (
      <Card title={title} description={description}>
        <ErrorBlock compact error={view.error} title={t("buildExtras.cache.readFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      </Card>
    );
  }
  const cache = view.data;
  return (
    <Card title={title} description={description}>
      <div className="flex flex-col gap-4">
        <KeyValueList
          items={[
            { label: t("buildExtras.cache.size"), value: cache.exists && cache.size_bytes > 0 ? formatBytes(cache.size_bytes, t.locale) : t("buildExtras.cache.none"), mono: false, copy: false },
            { label: t("buildExtras.cache.path"), value: cache.path },
          ]}
        />
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("buildExtras.cache.rebuildDescription")}</p>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" loading={rebuildFresh.isPending} onClick={() => rebuildFresh.mutate()}>
            {t("buildExtras.cache.rebuild")}
          </Button>
          <Button size="sm" variant="ghost" disabled={!cache.exists || cache.size_bytes === 0} onClick={() => setEmptying(true)}>
            {t("buildExtras.cache.empty")}
          </Button>
        </div>
        <CommandHint command={`noust app cache show ${domain}`} label={t("buildExtras.cache.fromTerminal")} />
      </div>
      <ConfirmDialog
        open={emptying}
        onOpenChange={setEmptying}
        friction="simple"
        server={node}
        title={t("buildExtras.cache.emptyTitle", { domain })}
        description={t("buildExtras.cache.emptyDescription")}
        actionLabel={t("buildExtras.cache.emptyAction")}
        onConfirm={async () => {
          await clear.mutateAsync();
        }}
      />
    </Card>
  );
}

/**
 * The process types the code declares and which of them a deployment uses (`web` as the unit's
 * command, `release` as a pre-deploy hook), with the reason when one that looks as if it should
 * apply does not. Shown only when the repository declares something, or a Procfile that cannot
 * be used: an application that declares nothing has nothing to explain.
 */
export function ProcessesCard({ domain }: { domain: string }) {
  const t = useT();
  const view = useQuery(processTypesQuery(domain));
  const title = t("buildExtras.processes.title");
  const description = t("buildExtras.processes.description");

  if (view.isPending) {
    return (
      <Card title={title} description={description} loading>
        <span className="sr-only">{t("buildExtras.processes.loading")}</span>
        <Skeleton className="h-12 w-full rounded-control" />
      </Card>
    );
  }
  if (view.isError) {
    return (
      <Card title={title} description={description}>
        <ErrorBlock compact error={view.error} title={t("buildExtras.processes.readFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      </Card>
    );
  }
  const report = view.data;
  if (report.processes.length === 0 && report.problem === "") return null;
  return (
    <Card title={title} description={description}>
      <div className="flex flex-col gap-3">
        {report.problem !== "" ? (
          <Notice tone="warning" title={t("buildExtras.processes.problem")}>
            {report.problem}
          </Notice>
        ) : null}
        {report.processes.length > 0 ? <p className="text-13 text-fg-muted">{t("buildExtras.processes.source", { source: report.source })}</p> : null}
        <ul className="flex flex-col gap-2">
          {report.processes.map((process) => (
            <li key={process.name} className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <Mono>{process.name}</Mono>
              <StatusPill state={process.in_effect ? "running" : "stopped"} label={process.in_effect ? t("buildExtras.processes.used") : t("buildExtras.processes.declared")} />
              <Mono className="min-w-0 break-all text-fg-muted">{process.command}</Mono>
            </li>
          ))}
        </ul>
        {report.notes.map((note) => (
          <p key={note} className="max-w-measure text-13 text-pretty text-fg-muted">
            {note}
          </p>
        ))}
        <CommandHint command={`noust app process-types show ${domain}`} label={t("buildExtras.processes.fromTerminal")} />
      </div>
    </Card>
  );
}
