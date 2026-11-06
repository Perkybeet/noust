/**
 * Noust's own update, beside the system's (task 12: update the system or the tool itself). One line says which Noust
 * runs here and whether a newer one is out, with what the new one brings one press away; the button starts the one
 * command this installation updates with, as a job the page follows. An installation that is not updated from the
 * console (a source checkout, a container image) says why and what to run instead.
 */

import { useMutation, useQuery } from "@tanstack/react-query";

import { request } from "../../../api/client";
import { versionQuery } from "../../../api/queries/system";
import { Can } from "../../../components/page/permissions";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Button } from "../../../components/ui/Button";
import { CopyValue } from "../../../components/ui/CopyValue";
import { ExternalLink } from "../../../components/ui/ExternalLink";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { useNodeCapability } from "../../../nodes/capability";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { selfUpdateQuery } from "../../settings/GeneralSettings.model";
import { explainServerError } from "../errors";
import { useServerJob } from "../serverJob";

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
      <SurfaceSection title={t("server.noust.title")} icon={OBJECT_ICONS.noust} loading>
        <Skeleton className="h-5 w-64" />
      </SurfaceSection>
    );
  }
  const latest = known.latest_version ?? null;
  const available = known.has_update && latest !== null;
  const supported = update.data?.supported !== false;
  const refusal = update.data;
  return (
    <SurfaceSection
      title={t("server.noust.title")}
      icon={OBJECT_ICONS.noust}
      status={
        available ? (
          <StatusPill state="warning" label={known.security_update ? t("server.noust.securityAvailable") : t("server.noust.available", { version: latest })} size="sm" />
        ) : (
          <StatusPill state="running" label={t("server.noust.upToDate")} size="sm" />
        )
      }
      actions={
        available && supported ? (
          <Can permission="server.manage">
            <Button icon={<OBJECT_ICONS.update aria-hidden="true" />} loading={start.isPending || jobs.busy} onClick={() => start.mutate()}>
              {t("server.noust.update")}
            </Button>
          </Can>
        ) : known.release_url !== null && known.release_url !== undefined ? (
          <ExternalLink href={known.release_url}>{t("server.noust.whatsNew")}</ExternalLink>
        ) : undefined
      }
    >
      <div className="flex min-w-0 flex-col gap-2">
        <p className="text-13 text-fg-muted">
          {available ? t.rich("server.noust.running", { version: <Mono key="version">{known.current_version}</Mono> }) : t.rich("server.noust.runningLatest", { version: <Mono key="version">{known.current_version}</Mono> })}
        </p>
        {available && known.release_url !== null && known.release_url !== undefined ? <ExternalLink href={known.release_url}>{t("server.noust.whatsNew")}</ExternalLink> : null}
        {available && !supported && refusal !== undefined ? (
          <div className="flex min-w-0 flex-col gap-1.5">
            <p className="max-w-measure text-13 text-pretty text-fg">{refusal.reason ?? t("server.noust.notFromHere")}</p>
            {refusal.hint !== null && refusal.hint !== undefined ? <CopyValue value={refusal.hint} label={t("server.noust.copyCommand")} /> : null}
          </div>
        ) : null}
      </div>
    </SurfaceSection>
  );
}

/** Noust's update slab; a server too old to say which Noust it runs shows nothing. */
export function NoustSection() {
  const capability = useNodeCapability(NOUST_CAPABILITY);
  return capability.status === "available" || capability.status === "unknown" ? <Panel /> : null;
}
