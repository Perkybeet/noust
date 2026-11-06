import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { isApiError, request } from "../../../api/client";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import { noustDnsKeys } from "../../../api/queries/noustDns";
import type { NoustServer } from "../../../api/queries/noustDns";
import { JobProgress } from "../../../components/page/JobProgress";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { FieldGroup } from "../../../components/ui/Field";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { ServerText } from "../../../components/ui/ServerText";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";

/** The packages a 409 `confirmation_required` says the install needs EPEL for, when it names them. */
function packagesOf(error: unknown): string {
  if (!isApiError(error)) return "";
  const required = error.extra["required"];
  const packages = typeof required === "object" && required !== null ? (required as Record<string, unknown>)["packages"] : null;
  return Array.isArray(packages) ? packages.map(String).join(", ") : "";
}

/**
 * The first thing a server's Noust DNS shows: what running a name server takes on (its
 * availability and the amplification of attacks), said by the server, and one acceptance in front of
 * the install. Pressing Install without it says what is missing and puts the focus on it, because a
 * button that is merely disabled says nothing. The install is a job, followed here until it ends.
 */
export function InstallPanel({ server }: { server: NoustServer }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const followed = useFollowedJob();
  const [accepted, setAccepted] = useState(false);
  const [missing, setMissing] = useState(false);
  const [epel, setEpel] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  const install = useMutation({
    mutationFn: (withEpel: boolean) => request("post", "/api/dns/noust/server/install", { body: { epel: withEpel } }),
    onSuccess: (job) => followed.follow(job.job_id),
    onError: (error) => {
      if (isApiError(error) && error.error === "confirmation_required") setEpel(packagesOf(error));
    },
  });

  useEffect(() => {
    if (followed.job !== null && isJobFinished(followed.job)) void queryClient.invalidateQueries({ queryKey: noustDnsKeys.server });
  }, [followed.job, queryClient]);

  const press = (): void => {
    if (!accepted) {
      setMissing(true);
      box.current?.querySelector<HTMLElement>("button, input")?.focus();
      return;
    }
    install.mutate(false);
  };

  const job = followed.job;
  const running = followed.id !== null && (job === null || !isJobFinished(job));
  const failed = job !== null && isJobFinished(job) && job.status !== "completed";

  return (
    <div className="flex flex-col gap-4">
      <Card level={2} title={t("noustDns.install.title")} icon={OBJECT_ICONS.dns} description={t("noustDns.install.description")}>
        <div className="flex flex-col gap-5">
          <ul className="flex flex-col gap-3">
            {(server.warnings ?? []).map((code) => (
              <li key={code} className="flex items-start gap-2 text-14 text-pretty">
                <ICONS.warning aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-warn" />
                <span className="min-w-0">
                  <ServerText coded={{ code, params: {} }} text={code} />
                </span>
              </li>
            ))}
          </ul>
          <div ref={box}>
            <FieldGroup legend={t("noustDns.install.acceptGroup")} error={missing && !accepted ? t("noustDns.install.acceptMissing") : null}>
              <Checkbox
                label={t("noustDns.install.accept")}
                checked={accepted}
                onCheckedChange={(next) => {
                  setAccepted(next);
                  if (next) setMissing(false);
                }}
              />
            </FieldGroup>
          </div>
          {running ? (
            <JobProgress state={job?.status === "running" ? "running" : "queued"} title={t("noustDns.install.running")} step={job?.current_step ?? null} />
          ) : failed ? (
            <JobProgress state="failed" title={t("noustDns.install.failed")} error={{ detail: job.error ?? "" }} onDismiss={followed.dismiss} />
          ) : install.isError && !(isApiError(install.error) && install.error.error === "confirmation_required") ? (
            <JobProgress
              state="failed"
              title={isApiError(install.error) && install.error.status === 501 ? t("noustDns.install.unsupported") : t("noustDns.install.failed")}
              error={install.error}
              onDismiss={() => install.reset()}
            />
          ) : null}
          <div>
            <Button variant="primary" icon={<OBJECT_ICONS.dns aria-hidden="true" />} loading={install.isPending || running} onClick={press}>
              {t("noustDns.install.action")}
            </Button>
          </div>
        </div>
      </Card>
      <ConfirmDialog
        open={epel !== null}
        onOpenChange={(next) => {
          if (!next) setEpel(null);
        }}
        friction="simple"
        server={node}
        title={t("noustDns.install.epelTitle")}
        description={t.rich("noustDns.install.epelBody", { packages: <Mono>{epel ?? ""}</Mono> })}
        actionLabel={t("noustDns.install.epelAction")}
        onConfirm={async () => {
          const job = await request("post", "/api/dns/noust/server/install", { body: { epel: true } });
          setEpel(null);
          followed.follow(job.job_id);
        }}
      />
    </div>
  );
}
