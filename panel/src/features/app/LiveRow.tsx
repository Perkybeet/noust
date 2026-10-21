import { StatusGlyph } from "../../components/ui/StatusPill";
import { Card } from "../../components/ui/Card";
import { Mono } from "../../components/ui/Mono";
import { RelativeTime } from "../../components/page/RelativeTime";
import { useT } from "../../i18n";
import { formatDuration } from "../../lib/format";
import type { Deployment } from "../apps/data";
import { shortCommit, triggerWords } from "./deployments/words";

/**
 * The version that serves, in one row: what changed (the commit's message), where it came from,
 * who or what started it, when, and how long it took to build. It has no buttons: going back is
 * the header's "Roll back" and the list's row menus, which ask the one question, and an address
 * is the header's fact line. The state is told by the section's title ("Live now") and the dot
 * beside the message, a still green: it serves (a ring in the idle colour when the service is down).
 */
export function LiveRow({ deployment, down = false }: { deployment: Deployment; down?: boolean }) {
  const t = useT();
  const commit = shortCommit(deployment.git_commit);
  const trigger = triggerWords(t, deployment.triggered_by);
  const Trigger = trigger.icon;
  const message = deployment.commit_message ?? t("appPages.deployments.page.heading", { id: String(deployment.id) });
  return (
    <Card padding="sm">
      <div className="flex min-w-0 flex-col gap-1.5">
        <p className="flex min-w-0 items-center gap-2 text-14 font-medium text-fg">
          <StatusGlyph state={down ? "stopped" : "running"} size={12} className={down ? "text-idle" : "text-ok"} />
          <span title={message} className="min-w-0 truncate">
            {message}
          </span>
        </p>
        <p className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-12 text-fg-muted">
          {commit !== null ? <Mono>{commit}</Mono> : null}
          {deployment.git_branch ? (
            <Mono tone="muted" truncate>
              {deployment.git_branch}
            </Mono>
          ) : null}
          <span className="flex items-center gap-1.5">
            <Trigger aria-hidden="true" className="size-icon-sm shrink-0" />
            {trigger.label}
          </span>
          <RelativeTime value={deployment.finished_at ?? deployment.started_at} />
          {deployment.duration_s !== null && deployment.duration_s !== undefined ? (
            <span>{t("appPages.overview.builtIn", { duration: formatDuration(deployment.duration_s, t.locale) })}</span>
          ) : null}
        </p>
      </div>
    </Card>
  );
}
