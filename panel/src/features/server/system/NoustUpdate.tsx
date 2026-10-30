/**
 * Noust's own update, beside the system's (task 12: update the system or the tool itself). One
 * line says which Noust runs here and whether a newer one is out; the button starts the one
 * command this installation updates with, as a job the page follows. An installation that is not
 * updated from the console (a source checkout, a container image) says why and what to run instead.
 */

import { useMutation, useQuery } from "@tanstack/react-query";

import { request } from "../../../api/client";
import { versionQuery } from "../../../api/queries/system";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { CopyValue } from "../../../components/ui/CopyValue";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import { useNodeCapability } from "../../../nodes/capability";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { explainServerError } from "../errors";
import { useServerJob } from "../serverJob";
import { selfUpdateQuery } from "../../settings/GeneralSettings.model";

/** A server whose Noust predates `GET /api/system/version` shows nothing here rather than a failure. */
const NOUST_CAPABILITY = "/api/system/version";

function Panel() {
  const t = useT();
  const { node } = useNode();
  const jobs = useServerJob();
  const version = useQuery(versionQuery());
  const update = useQuery(selfUpdateQuery());
  const start = useMutation({
    mutationFn: () => request("post", "/api/system/update"),
    onSuccess: (accepted) => jobs.track(accepted.job_id, "noust"),
    onError: (error) => reportActionError(t("server.job.noust.failed"), explainServerError(t, error, node)),
  });
  const known = version.data;
  if (known === undefined) {
    return (
      <Card level={2} title={t("server.noust.title")} icon={OBJECT_ICONS.noust} padding="sm" loading>
        <Skeleton className="h-5 w-64" />
      </Card>
    );
  }
  const latest = known.latest_version ?? null;
  const available = known.has_update && latest !== null;
  const supported = update.data?.supported !== false;
  const refusal = update.data;
  return (
    <Card
      level={2}
      title={t("server.noust.title")}
      icon={OBJECT_ICONS.noust}
      padding="sm"
      status={
        available ? (
          <StatusPill state="warning" label={known.security_update ?  t("server.noust.securityAvailable") : t("server.noust.available", { version: latest })} size="sm" />
        ) : (
          <StatusPill state="running" label={t("server.noust.upToDate")} size="sm" />
        )
      }
      actions={
        available && supported ? (
          <Button icon={<OBJECT_ICONS.update aria-hidden="true" />} loading={start.isPending || jobs.busy} onClick={() => start.mutate()}>
            {t("server.noust.update")}
          </Button>
        ) : undefined
      }
    >
      <div className="flex min-w-0 flex-col gap-2">
        <p className="text-13 text-fg-muted">
          {t.rich("server.noust.running", { version: <Mono key="version">{known.current_version}</Mono> })}
        </p>
        {available && !supported && refusal !== undefined ? (
          <div className="flex min-w-0 flex-col gap-1.5">
            <p className="max-w-measure text-13 text-pretty text-fg">{refusal.reason ?? t("server.noust.notFromHere")}</p>
            {refusal.hint !== null && refusal.hint !== undefined ? <CopyValue value={refusal.hint} label={t("server.noust.copyCommand")} /> : null}
          </div>
        ) : null}
      </div>
    </Card>
  );
}

/** Noust's update card; a server too old to say which Noust it runs shows nothing. */
export function NoustUpdate() {
  const capability = useNodeCapability(NOUST_CAPABILITY);
  return capability.status === "available" || capability.status === "unknown" ? <Panel /> : null;
}
