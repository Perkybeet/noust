import { useQuery } from "@tanstack/react-query";

import type { Freeze } from "../../api/queries/rollout";
import { rolloutQuery } from "../../api/queries/rollout";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { formatDateTime, parseTimestamp } from "../../lib/format";
import { useResumeDeploys } from "./useResumeDeploys";

/**
 * The freeze that holds an app's deploys, in the words the person who froze them gave. A freeze
 * of the whole server and one of the app are told apart by their title; both refuse the same.
 */
export function FrozenNotice({ domain, freeze }: { domain: string; freeze: Freeze }) {
  const t = useT();
  const ends = parseTimestamp(freeze.ends_at);
  return (
    <Notice
      tone="warning"
      title={freeze.scope === "server" ? t("rollout.hold.frozenServerTitle") : t("rollout.hold.frozenAppTitle", { domain })}
    >
      <p>
        {ends !== null
          ? t("rollout.hold.frozenUntil", { until: formatDateTime(ends, t.locale), reason: freeze.reason })
          : t("rollout.hold.frozenOpen", { reason: freeze.reason })}
      </p>
      <p className="mt-1">{t("rollout.hold.frozenRule")}</p>
    </Notice>
  );
}

/**
 * What holds an app's deploys now: a freeze over it, or the pause of what deploys it by
 * itself, which is lifted here with "Resume" (a rollback pauses them, so the next push does not
 * undo it). Nothing is drawn when nothing holds them, and nothing when the server is older than
 * the setting, because a banner that is not there cannot be wrong.
 */
export function DeployHold({ domain }: { domain: string }) {
  const t = useT();
  const query = useQuery({ ...rolloutQuery(domain), retry: false });
  const resume = useResumeDeploys(domain);
  const data = query.data;
  if (data === undefined || (data.freeze === null && data.paused === null)) return null;
  const paused = data.paused;
  return (
    <div className="flex flex-col gap-3">
      {data.freeze !== null ? <FrozenNotice domain={domain} freeze={data.freeze} /> : null}
      {paused !== null ? (
        <Notice
          title={t("rollout.hold.pausedTitle")}
          action={
            <Button size="sm" loading={resume.isPending} onClick={() => resume.mutate()}>
              {t("rollout.auto.resume")}
            </Button>
          }
        >
          <p>{t.rich("rollout.hold.pausedSince", { time: <RelativeTime value={paused.at} /> })}</p>
          {paused.by !== null ? <p className="mt-1">{t("rollout.hold.pausedBy", { by: paused.by })}</p> : null}
          {paused.reason !== null ? <p className="mt-1">{t("rollout.hold.pausedReason", { reason: paused.reason })}</p> : null}
          {data.held > 0 ? <p className="mt-1">{t("rollout.auto.heldNow", { count: data.held })}</p> : null}
        </Notice>
      ) : null}
    </div>
  );
}
