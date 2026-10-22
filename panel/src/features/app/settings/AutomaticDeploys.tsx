/**
 * What deploys the app without a person asking (`noust deploy pause|resume|pause-on-rollback`,
 * `GET /api/apps/{domain}/rollout` and its `pause`, `resume` and `PUT`). Paused, a push, a tag or a
 * schedule is held and counted, while an update somebody starts still runs; resuming says how many
 * arrived meanwhile. After a rollback the app pauses by itself unless this is turned off, so the
 * next push cannot undo the rollback.
 *
 * Two rows of Automation: the switch is "deploys on its own", off while paused, and turning it off
 * asks for the reason the pause is kept with.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { rolloutKey, rolloutQuery } from "../../../api/queries/rollout";
import type { Rollout } from "../../../api/queries/rollout";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Setting } from "../../../components/ui/Setting";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { useResumeDeploys } from "../useResumeDeploys";
import { olderServer } from "./queries";
import { RowError, RowLoading } from "./settingRows";

/** The longest reason the backend keeps with a pause. */
const MAX_REASON = 300;

/** What can deploy the app by itself, in words: the backend names the sources. */
function sourceWords(t: T, sources: readonly string[]): string[] {
  return sources.map((source) => {
    if (source === "webhook") return t("rollout.auto.sourceWebhook");
    if (source === "github") return t("rollout.auto.sourceGithub");
    if (source === "tags") return t("rollout.auto.sourceTags");
    return source;
  });
}

function PauseDialog({ domain, open, onOpenChange }: { domain: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const formId = useId();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const pause = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/rollout/pause", { params: { domain }, body: { reason: reason.trim() === "" ? null : reason.trim() } }),
    onSuccess: (result) => {
      queryClient.setQueryData<Rollout>(rolloutKey(domain), result);
      announce(t("rollout.auto.paused", { domain }));
      setReason("");
      onOpenChange(false);
    },
  });
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!pause.isPending) pause.mutate();
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && pause.isPending) return;
        if (!next) pause.reset();
        onOpenChange(next);
      }}
      size="sm"
      title={t("rollout.auto.pauseTitle", { domain })}
      description={t("rollout.auto.pauseDescription")}
      footer={
        <>
          <Button disabled={pause.isPending} onClick={() => onOpenChange(false)}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={pause.isPending}>
            {t("rollout.auto.pauseAction")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field label={t("rollout.auto.reasonLabel")} description={t("rollout.auto.reasonDescription")} optional>
          <Input autoComplete="off" maxLength={MAX_REASON} placeholder={t("rollout.auto.reasonPlaceholder")} value={reason} onValueChange={(value: string) => setReason(value)} />
        </Field>
        {pause.isError ? <ErrorBlock live compact error={pause.error} title={t("rollout.auto.pauseFailed", { domain })} /> : null}
      </form>
    </Dialog>
  );
}

/** The two rows: whether the app deploys on its own (and the pause that holds it), and what a rollback does to that. */
export function AutomaticDeploysRows({ domain }: { domain: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery({ ...rolloutQuery(domain), retry: false });
  const [pausing, setPausing] = useState(false);

  const resume = useResumeDeploys(domain);
  const onRollback = useMutation({
    mutationFn: (pause_on_rollback: boolean) => request("put", "/api/apps/{domain}/rollout", { params: { domain }, body: { pause_on_rollback } }),
    onSuccess: (result) => {
      queryClient.setQueryData<Rollout>(rolloutKey(domain), result);
      announce(result.pause_on_rollback ? t("rollout.auto.rollbackOn", { domain }) : t("rollout.auto.rollbackOff", { domain }));
    },
    onError: (error) => {
      reportActionError(t("rollout.auto.rollbackFailed", { domain }), error);
    },
  });

  if (query.isPending) return <RowLoading label={t("rollout.auto.loading")} rows={[1, 2]} />;
  // A server older than 3.4 has no such setting: nothing to show rather than a guess.
  if (query.isError && olderServer(query.error)) return null;
  if (query.isError) return <RowError title={t("rollout.auto.readFailed")} error={query.error} onRetry={() => void query.refetch()} retrying={query.isRefetching} />;

  const data = query.data;
  const paused = data.paused;
  const sources = sourceWords(t, data.automatic);
  const pauseOnRollback = onRollback.isPending && typeof onRollback.variables === "boolean" ? onRollback.variables : data.pause_on_rollback;

  // Running: what can deploy it. Paused: since when, by whom, why and what has been held meanwhile.
  const help =
    paused === null ? (
      <span data-testid="automatic-sources">
        {sources.length > 0 ? t.rich("rollout.auto.sources", { list: <Mono>{sources.join(", ")}</Mono> }) : t("rollout.auto.nothingAutomatic")}
      </span>
    ) : (
      <span>
        {t.rich("rollout.hold.pausedSince", { time: <RelativeTime value={paused.at} /> })}
        {paused.by !== null ? ` ${t("rollout.hold.pausedBy", { by: paused.by })}` : ""}
        {paused.reason !== null ? ` ${t("rollout.hold.pausedReason", { reason: paused.reason })}` : ""}
        {data.held > 0 ? ` ${t("rollout.auto.heldNow", { count: data.held })}` : ""}
      </span>
    );

  return (
    <>
      <Setting
        label={t("rollout.auto.title")}
        help={help}
        {...(paused !== null ? { status: <StatusPill state="stopped" size="sm" label={t("rollout.auto.pausedPill")} /> } : {})}
        checked={paused === null}
        busy={resume.isPending}
        onCheckedChange={(next) => {
          if (next) resume.mutate();
          else setPausing(true);
        }}
      />
      <Setting
        label={t("rollout.auto.rollbackLabel")}
        help={t("rollout.auto.rollbackDescription")}
        checked={pauseOnRollback}
        busy={onRollback.isPending}
        onCheckedChange={(next) => onRollback.mutate(next)}
      />
      <PauseDialog domain={domain} open={pausing} onOpenChange={setPausing} />
    </>
  );
}
