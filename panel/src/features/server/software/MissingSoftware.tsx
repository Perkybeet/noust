/**
 * "X is missing": the one notice of the console for a feature that lacks the tool it runs on (4.2, item 118).
 * It reads the same registry as the Software page, so the two never disagree about what is missing: it says which
 * piece, what the feature loses without it, and offers to install it right there when the person may. When they
 * may not, it says what it takes and to whom to ask; on a server that did not give the central access to its
 * system, it says that, with the command to run there. The install is a task, which the task centre follows.
 *
 * A page puts it where its own warning was and gives it what to say when the registry has nothing to add (the
 * piece is installed, or the server is too old to say): `fallback`.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

import { isJobFinished, useFollowedJob } from "../../../api/queries/jobs";
import type { FollowedJob } from "../../../api/queries/jobs";
import { JobProgress } from "../../../components/page/JobProgress";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Button } from "../../../components/ui/Button";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { VERB_ICONS } from "../../../components/ui/objectIcons";
import { ServerText } from "../../../components/ui/ServerText";
import { Skeleton } from "../../../components/ui/Skeleton";
import { TextLink } from "../../../components/ui/TextLink";
import { useCan } from "../../../components/page/permissions";
import { useT } from "../../../i18n";
import { useNodeCapability } from "../../../nodes/capability";
import { ServerErrorBlock } from "../errors";
import { SOFTWARE_CAPABILITY, installSoftware, softwareKeys, softwareQuery } from "./queries";
import type { SoftwareItem } from "./queries";
import { installPermissions, whyKey } from "./registry";
import { componentName, purposeOf } from "./words";

export interface MissingSoftwareProps {
  /** The registry's id of what is missing: `nginx_stream_module`, `cloudflared`. */
  component: string;
  /** What to show when the registry has nothing to say: the piece is installed, or the server cannot tell. */
  fallback?: ReactNode;
  /** The install this notice started ended well: the page reads again what it read. */
  onInstalled?: () => void;
  className?: string;
}

/** What the feature loses without the piece, in the console's words; the piece's own purpose where it has none. */
function Why({ item }: { item: SoftwareItem }) {
  const t = useT();
  const key = whyKey(item.id);
  if (key !== null) return <>{t(key)}</>;
  const { coded, text } = purposeOf(item);
  return <ServerText coded={coded} text={text} />;
}

function Waiting() {
  const t = useT();
  return (
    <LoadingRegion label={t("software.missing.reading")}>
      <Skeleton className="h-14 w-full rounded-control" />
    </LoadingRegion>
  );
}

/** What the notice offers in place of the button when the person cannot install: why, and who can. */
function CannotInstall({ item, managed, permissions }: { item: SoftwareItem; managed: boolean; permissions: readonly string[] }) {
  const t = useT();
  if (!managed) return <>{t("software.unmanaged")}</>;
  if (!item.installable) return <>{t("software.missing.notHere", { name: componentName(t, item.id) })}</>;
  const list = permissions.map((permission) => <Mono key={permission}>{permission}</Mono>);
  return permissions.length === 1
    ? t.rich("software.missing.cannot", { permission: list[0] })
    : t.rich("software.missing.cannotMany", { permission: list.flatMap((entry, index) => (index === 0 ? [entry] : [" + ", entry])) });
}

/**
 * The install in hand: waiting, running, done or failed with the tool's own words, and the way to its task. It does not
 * announce itself: the task centre says how a task of yours ended, once.
 */
function Installing({ name, followed, retry, className }: { name: string; followed: FollowedJob; retry: () => void; className?: string | undefined }) {
  const t = useT();
  const job = followed.job;
  if (followed.id === null) return null;
  const task = (
    <TextLink to="/activity/tasks/$id" params={{ id: followed.id }} size="ui">
      {t("software.missing.seeTask")}
    </TextLink>
  );
  if (job === null || !isJobFinished(job)) {
    return <JobProgress {...(className !== undefined ? { className } : {})} announce={false} state={job?.status === "running" ? "running" : "queued"} title={t("software.missing.installing", { name })} step={job?.current_step ?? null} action={task} />;
  }
  if (job.status === "completed") {
    return <JobProgress {...(className !== undefined ? { className } : {})} announce={false} state="succeeded" title={t("software.missing.installed", { name })} onDismiss={followed.dismiss} />;
  }
  return (
    <JobProgress
      {...(className !== undefined ? { className } : {})}
      announce={false}
      state="failed"
      title={t("software.missing.failed", { name })}
      error={{ detail: job.error ?? "" }}
      action={
        <>
          <Button size="sm" onClick={retry}>
            {t("software.missing.retry")}
          </Button>
          {task}
        </>
      }
      onDismiss={followed.dismiss}
    />
  );
}

/** "X is missing", read from the registry; see the module's note. */
export function MissingSoftware({ component, fallback, onInstalled, className }: MissingSoftwareProps) {
  const t = useT();
  const can = useCan();
  const queryClient = useQueryClient();
  const capability = useNodeCapability(SOFTWARE_CAPABILITY);
  // A server that predates the registry has nothing to read: the page says what it said before.
  const readable = capability.status === "available" || capability.status === "unknown";
  const query = useQuery({ ...softwareQuery(), enabled: readable });
  const followed = useFollowedJob();
  const start = useMutation({
    mutationFn: () => installSoftware(component, false),
    onSuccess: (accepted) => followed.follow(accepted.job_id),
  });
  // The one place the install is started; the three ways to it below (the button, the retry of the task, the retry of the refusal) are the same press.
  const install = (): void => {
    start.mutate();
  };
  const item = query.data?.items?.find((candidate) => candidate.id === component);
  const name = componentName(t, component);
  const job = followed.job;
  const succeeded = job !== null && isJobFinished(job) && job.status === "completed";

  // What the install changed is read again once, when it ends well: the registry, and whatever the page read.
  const settled = useRef<string | null>(null);
  useEffect(() => {
    if (!succeeded || followed.id === null || settled.current === followed.id) return;
    settled.current = followed.id;
    void queryClient.invalidateQueries({ queryKey: softwareKeys.list });
    onInstalled?.();
  }, [succeeded, followed.id, queryClient, onInstalled]);

  // The install in hand stays on screen to its end, whatever the registry says half-way through it.
  if (followed.id !== null) return <Installing name={name} followed={followed} retry={install} className={className} />;
  if (capability.status === "checking" || (readable && query.isPending)) return <Waiting />;
  // Installed, or not a piece the registry has, or it could not be read: the page's own words stand.
  if (item === undefined || item.installed) return <>{fallback ?? null}</>;
  if (start.isError) {
    return <ServerErrorBlock live error={start.error} title={t("software.install.failed", { name })} onRetry={install} retrying={start.isPending} />;
  }
  const managed = query.data?.managed ?? false;
  if (!managed && fallback !== undefined) return <>{fallback}</>;
  const permissions = installPermissions(item.id);
  const allowed = managed && item.installable && permissions.every(can);
  return (
    <Notice
      tone="warning"
      {...(className !== undefined ? { className } : {})}
      title={t("software.missing.title", { name })}
      action={
        allowed ? (
          <Button size="sm" icon={<VERB_ICONS.install aria-hidden="true" />} loading={start.isPending} onClick={install}>
            {t("software.missing.install")}
          </Button>
        ) : (
          <span className="max-w-72 text-12 text-fg">
            <CannotInstall item={item} managed={managed} permissions={permissions} />
          </span>
        )
      }
    >
      <Why item={item} />{" "}
      <TextLink to="/server/software" size="ui">
        {t("software.missing.seeInSoftware")}
      </TextLink>
    </Notice>
  );
}
