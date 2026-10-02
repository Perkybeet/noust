/**
 * The account an application runs as (spec 3.2, section 4): its own (`noust-app-<name>`, its
 * tree and its `.env` its own) or a shared one, and the move to its own account. Moving it is
 * root-equivalent and runs as a job, which restarts the app once and puts everything back
 * exactly (unit, file owners, pool) when it does not answer afterwards.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { appKeys } from "../../../api/queries/apps";
import type { Job } from "../../../api/queries/jobs";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { useSudoFirst } from "./formParts";
import { JobFailedError, identityQuery, olderServer, settingsKeys, waitForJob } from "./queries";

export function IdentityCard({ domain }: { domain: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const sudoFirst = useSudoFirst();
  const identity = useQuery(identityQuery(domain));
  const [moving, setMoving] = useState(false);
  const title = t("appSettings.identity.title");

  if (identity.isPending) {
    return (
      <Card title={title}>
        <div aria-busy="true">
          <span className="sr-only">{t("appSettings.identity.loading")}</span>
          <Skeleton className="h-10 w-full rounded-control" />
        </div>
      </Card>
    );
  }
  // A server older than 3.2 has no accounts per application: nothing to say or do here.
  if (identity.isError && olderServer(identity.error)) return null;
  if (identity.isError) {
    return (
      <Card title={title}>
        <ErrorBlock compact error={identity.error} title={t("appSettings.identity.readFailed")} onRetry={() => void identity.refetch()} retrying={identity.isRefetching} />
      </Card>
    );
  }
  const data = identity.data;
  const proposed = data.proposed ?? null;
  return (
    <Card title={title} description={t("appSettings.identity.description")}>
      <div className="flex flex-col gap-3">
        <p className="text-13 text-pretty text-fg">
          {data.own
            ? t.rich("appSettings.identity.own", { account: <Mono>{data.account}</Mono> })
            : t.rich("appSettings.identity.shared", { account: <Mono>{data.account}</Mono>, group: <Mono>{data.group}</Mono> })}
        </p>
        {!data.own && data.eligible && proposed !== null ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <Button size="sm" onClick={() => sudoFirst(() => setMoving(true), t("appSettings.identity.notMoved", { domain }))}>
              {t("appSettings.identity.move")}
            </Button>
            <p className="text-12 text-fg-muted">{t.rich("appSettings.identity.moveNote", { account: <Mono>{proposed}</Mono> })}</p>
          </div>
        ) : null}
        {!data.own && !data.eligible && data.reason ? <p className="text-13 text-pretty text-fg-muted">{data.reason}</p> : null}
      </div>
      <ConfirmDialog
        open={moving}
        onOpenChange={setMoving}
        friction="simple"
        destructive={false}
        server={node}
        title={t("appSettings.identity.moveTitle", { domain })}
        description={t("appSettings.identity.moveDescription", { account: proposed ?? "" })}
        actionLabel={t("appSettings.identity.moveAction")}
        onConfirm={async () => {
          const queued = await request("post", "/api/apps/{domain}/identity/migrate", { params: { domain } });
          const job = await waitForJob(queryClient, queued.job as unknown as Job);
          void queryClient.invalidateQueries({ queryKey: settingsKeys.identity(domain) });
          void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
          if (job.status !== "completed") throw new JobFailedError(job.error ?? t("appSettings.jobFailedSilently"));
          announce(t("appSettings.identity.moved", { domain }));
        }}
      />
    </Card>
  );
}
