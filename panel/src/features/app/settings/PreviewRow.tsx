import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Play, RotateCw, Square, Tag, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { request } from "../../../api/client";
import { ElevationCancelledError } from "../../../api/errors";
import { appKeys } from "../../../api/queries/apps";
import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import type { Job } from "../../../api/queries/jobs";
import { previewsKey } from "../../../api/queries/previews";
import type { Preview } from "../../../api/queries/previews";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { useNow } from "../../../components/page/clock";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { ExternalLink } from "../../../components/ui/ExternalLink";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { parseTimestamp } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { useConfirmItsYou } from "../useDeleteApp";
import { previewName, previewStatus } from "./previews";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

/** When a preview goes: "Expires in 5d", or "Expired 2h ago" while its removal is pending. */
function Expiry({ value }: { value: string }) {
  const t = useT();
  const date = parseTimestamp(value);
  // The clock every time label shares, stepping each minute: enough to flip the word on time.
  const now = useNow(() => 60_000);
  const past = date !== null && date.getTime() <= now;
  return <span>{t.rich(past ? "appSettings.previews.expired" : "appSettings.previews.expires", { time: <RelativeTime value={value} className="text-fg" /> })}</span>;
}

function KindIcon({ kind }: { kind: string }) {
  const t = useT();
  const Icon = kind === "tag" ? Tag : kind === "branch" ? OBJECT_ICONS.source : OBJECT_ICONS.preview;
  const label = kind === "tag" ? t("previewHand.list.kind.tag") : kind === "branch" ? t("previewHand.list.kind.branch") : t("previewHand.list.kind.pr");
  return <Icon role="img" aria-label={label} className="size-icon-md shrink-0 text-fg-muted" />;
}

/** What the signed-in principal may do to a preview: the server decides again, this only draws what would be allowed. */
export interface PreviewAbilities {
  /** Build it again (`apps.deploy`). */
  deploy: boolean;
  /** Stop and start it (`apps.operate`). */
  operate: boolean;
  /** Remove it (`apps.manage`). */
  manage: boolean;
}

export interface PreviewRowProps {
  /** The application the preview is of. */
  domain: string;
  preview: Preview;
  can: PreviewAbilities;
  /** Opens the preview's logs beside the list. */
  onLogs: (preview: Preview) => void;
}

/**
 * One preview: what it was made from, its state, where it answers, when it goes, and what can be
 * done to it one at a time. The daily verbs are on the row (its logs, building it again); stopping
 * and starting it, and removing it, are in its menu. Removing is a job, followed here until it
 * ends; every other action answers at once and the list is read again.
 */
export function PreviewRow({ domain, preview, can, onLogs }: PreviewRowProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const confirmItsYou = useConfirmItsYou();
  const followed = useFollowedJob();
  const notifiedRef = useRef<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [approving, setApproving] = useState(false);
  const number = preview.number;
  const name = previewName(preview);

  const job = followed.job;
  const removingNow = (job !== null && !isJobFinished(job)) || preview.status === "removing";
  const removalFailed = job !== null && isJobFinished(job) && job.status !== "completed";

  useEffect(() => {
    if (job === null || !isJobFinished(job) || notifiedRef.current === job.id) return;
    notifiedRef.current = job.id;
    void queryClient.invalidateQueries({ queryKey: previewsKey(domain) });
    void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
    if (job.status === "completed") announce(t("previewHand.list.removed", { name }));
  }, [job, domain, name, queryClient, t]);

  const view = removingNow && preview.status !== "removing" ? previewStatus("removing", t.locale) : previewStatus(preview.status, t.locale);
  const busy = removingNow || preview.status === "pending" || preview.status === "deploying";
  const stopped = preview.status === "stopped";
  // A pull request from a fork waits for a person; nothing of it has been built, so there is nothing to rebuild.
  const awaiting = preview.status === "awaiting";

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: previewsKey(domain) });
  };

  const redeploy = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/previews/{number}/redeploy", { params: { domain, number } }),
    onSuccess: () => {
      refresh();
      announce(t("previewHand.list.redeployQueued", { name }));
    },
    onError: (error: unknown) => {
      reportActionError(t("previewHand.list.redeployFailed", { name }), error);
    },
  });
  const stop = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/previews/{number}/stop", { params: { domain, number } }),
    onSuccess: () => {
      refresh();
      announce(t("previewHand.list.stopped", { name }));
    },
  });
  const start = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/previews/{number}/start", { params: { domain, number } }),
    onSuccess: () => {
      refresh();
      announce(t("previewHand.list.started", { name }));
    },
    onError: (error: unknown) => {
      reportActionError(t("previewHand.list.startFailed", { name }), error);
    },
  });

  const approve = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/previews/{number}/approve", { params: { domain, number } }),
    onSuccess: () => {
      refresh();
      announce(t("rollout.previews.approved", { name }));
    },
  });
  const reject = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/previews/{number}/reject", { params: { domain, number } }),
    onSuccess: () => {
      refresh();
      announce(t("rollout.previews.rejected", { name }));
    },
    onError: (error: unknown) => {
      reportActionError(t("rollout.previews.rejectFailed", { name }), error);
    },
  });

  const askToRemove = (): void => {
    followed.dismiss();
    confirmItsYou().then(
      () => {
        setRemoving(true);
      },
      (error: unknown) => {
        if (!(error instanceof ElevationCancelledError)) reportActionError(t("previewHand.list.notRemoved", { name }), error);
      },
    );
  };

  return (
    <li className="flex min-w-0 flex-col gap-3 px-5 py-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <StatusPill state={view.state} label={view.label} size="sm" />
            <span className="flex items-center gap-1.5 text-14 font-medium text-fg">
              <KindIcon kind={preview.kind} />
              {preview.kind === "pr" ? <span translate="no">{name}</span> : <Mono>{name}</Mono>}
            </span>
            {/* A pull request is named by its number, so its branch is said beside it; a branch or a tag is its own name. */}
            {preview.kind === "pr" ? (
              <Mono tone="muted" className="min-w-0 break-all">
                {preview.branch}
              </Mono>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <TextLink to="/apps/$domain" params={{ domain: preview.domain }} translate="no" size="ui" className="break-all">
              {preview.domain}
            </TextLink>
            <ExternalLink href={preview.url} label={t("previewHand.list.openAria", { name })}>
              {t("appSettings.previews.openPreview")}
            </ExternalLink>
          </div>
          <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-12 text-fg-muted">
            {preview.head_sha ? <span>{t.rich("appSettings.previews.commit", { commit: <Mono>{preview.head_sha.slice(0, 7)}</Mono> })}</span> : null}
            <span translate="no">{preview.repository ? `${preview.provider} ${preview.repository}` : preview.provider}</span>
            <Expiry value={preview.expires_at} />
            {(preview.databases ?? []).length > 0 ? (
              <span>{t.rich("previewHand.list.ownDatabase", { names: <Mono>{(preview.databases ?? []).join(", ")}</Mono> })}</span>
            ) : null}
          </p>
          {awaiting ? <p className="text-12 text-fg-muted">{t.rich("rollout.previews.awaitingNote", { commit: <Mono>{(preview.head_sha ?? "").slice(0, 7)}</Mono> })}</p> : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button size="sm" variant="ghost" icon={<OBJECT_ICONS.log aria-hidden="true" />} aria-label={`${t("previewHand.list.logs")} ${name}`} onClick={() => onLogs(preview)}>
            {t("previewHand.list.logs")}
          </Button>
          {awaiting && can.deploy ? (
            <>
              <Button size="sm" variant="primary" icon={<Check aria-hidden="true" />} aria-label={t("rollout.previews.approveAria", { name })} onClick={() => setApproving(true)}>
                {t("rollout.previews.approve")}
              </Button>
              <Button size="sm" variant="ghost" icon={<X aria-hidden="true" />} loading={reject.isPending} aria-label={t("rollout.previews.rejectAria", { name })} onClick={() => reject.mutate()}>
                {t("rollout.previews.reject")}
              </Button>
            </>
          ) : null}
          {can.deploy && !awaiting ? (
            <Button size="sm" variant="ghost" icon={<RotateCw aria-hidden="true" />} loading={redeploy.isPending} disabled={busy} aria-label={`${t("common.words.update")} ${name}`} onClick={() => redeploy.mutate()}>
              {t("common.words.update")}
            </Button>
          ) : null}
          {can.operate || can.manage ? (
            <Menu align="end" trigger={<IconButton size="sm" label={t("previewHand.list.actionsAria", { name })} icon={<ICONS.more />} tooltip={false} />}>
              {can.operate ? (
                stopped ? (
                  <MenuItem icon={<Play />} disabled={start.isPending || busy} onClick={() => start.mutate()}>
                    {t("common.words.start")}
                  </MenuItem>
                ) : (
                  <MenuItem icon={<Square />} disabled={stop.isPending || busy} onClick={() => setStopping(true)}>
                    {t("common.words.stop")}
                  </MenuItem>
                )
              ) : null}
              {can.operate && can.manage ? <MenuSeparator /> : null}
              {can.manage ? (
                <MenuItem icon={<ICONS.delete />} destructive disabled={removingNow} onClick={askToRemove}>
                  {t("common.words.remove")}
                </MenuItem>
              ) : null}
            </Menu>
          ) : null}
        </div>
      </div>

      {preview.status === "failed" ? (
        <ErrorBlock
          compact
          error={{ detail: preview.error ?? t("previewHand.list.buildFailedDefault") }}
          title={t("previewHand.list.lastBuildFailed", { name })}
          hint={t("previewHand.list.tryAgainHint")}
          action={
            <Button size="sm" onClick={() => onLogs(preview)}>
              {t("previewHand.list.logs")}
            </Button>
          }
        />
      ) : null}
      {removalFailed ? <ErrorBlock live compact error={{ detail: job.error ?? t("appSettings.jobFailedSilently") }} title={t("previewHand.list.notRemoved", { name })} /> : null}

      <ConfirmDialog
        open={approving}
        onOpenChange={setApproving}
        friction="simple"
        destructive={false}
        server={node}
        title={t("rollout.previews.approveTitle", { commit: (preview.head_sha ?? "").slice(0, 7), name })}
        description={t("rollout.previews.approveDescription")}
        actionLabel={t("rollout.previews.approveAction")}
        onConfirm={async () => {
          try {
            await approve.mutateAsync();
          } catch (error: unknown) {
            reportActionError(t("rollout.previews.approveFailed", { name }), error);
          }
        }}
      />
      <ConfirmDialog
        open={stopping}
        onOpenChange={setStopping}
        friction="simple"
        server={node}
        title={t("previewHand.list.stopTitle", { name })}
        description={t("previewHand.list.stopDescription", { domain: preview.domain })}
        actionLabel={t("previewHand.list.stopAction")}
        onConfirm={async () => {
          try {
            await stop.mutateAsync();
          } catch (error: unknown) {
            reportActionError(t("previewHand.list.stopFailed", { name }), error);
          }
        }}
      />
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        friction="simple"
        server={node}
        title={t("previewHand.list.removeTitle", { name })}
        description={t("appSettings.previews.removeConfirmDescription", { domain: preview.domain })}
        actionLabel={t("appSettings.previews.removePreview")}
        onConfirm={async () => {
          const result = await request("delete", "/api/apps/{domain}/previews/{number}", { params: { domain, number } });
          followed.follow(result.job as Job);
          refresh();
        }}
      />
    </li>
  );
}
