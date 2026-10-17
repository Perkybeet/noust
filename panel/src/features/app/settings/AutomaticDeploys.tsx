/**
 * What deploys the app without a person asking (`noust deploy pause|resume|pause-on-rollback`,
 * `GET /api/apps/{domain}/rollout` and its `pause`, `resume` and `PUT`). Paused, a push, a tag or a
 * schedule is held and counted, while an update somebody starts still runs; resuming says how many
 * arrived meanwhile. After a rollback the app pauses by itself unless this is turned off, so the
 * next push cannot undo the rollback.
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
import { Card } from "../../../components/ui/Card";
import { Dialog } from "../../../components/ui/Dialog";
import { FeatureState } from "../../../components/ui/FeatureState";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { olderServer } from "./queries";

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
            {t("appSettings.cancel")}
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

/** The card: paused or running, the pause and the resume, and what a rollback does to it. */
export function AutomaticDeploysCard({ domain }: { domain: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery({ ...rolloutQuery(domain), retry: false });
  const [pausing, setPausing] = useState(false);
  const title = t("rollout.auto.title");

  const resume = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/rollout/resume", { params: { domain } }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: rolloutKey(domain) });
      announce(result.held > 0 ? t("rollout.auto.resumedHeld", { domain, count: result.held }) : t("rollout.auto.resumed", { domain }));
    },
    onError: (error) => {
      reportActionError(t("rollout.auto.resumeFailed", { domain }), error);
    },
  });
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

  if (query.isPending) {
    return (
      <Card title={title}>
        <div aria-busy="true">
          <span className="sr-only">{t("rollout.auto.loading")}</span>
          <Skeleton className="h-10 w-full rounded-control" />
        </div>
      </Card>
    );
  }
  // A server older than 3.4 has no such setting: nothing to show rather than a guess.
  if (query.isError && olderServer(query.error)) return null;
  if (query.isError) {
    return (
      <Card title={title}>
        <ErrorBlock compact error={query.error} title={t("rollout.auto.readFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      </Card>
    );
  }
  const data = query.data;
  const paused = data.paused;
  const sources = sourceWords(t, data.automatic);
  const pauseOnRollback = onRollback.isPending && typeof onRollback.variables === "boolean" ? onRollback.variables : data.pause_on_rollback;

  return (
    <Card title={title} description={t("rollout.auto.description")}>
      <div className="flex flex-col gap-4">
        {paused === null ? (
          <FeatureState
            state="on"
            title={t("rollout.auto.on")}
            action={
              <Button size="sm" onClick={() => setPausing(true)}>
                {t("rollout.auto.pause")}
              </Button>
            }
          >
            {t("rollout.auto.onBody")}
          </FeatureState>
        ) : (
          <FeatureState
            state="off"
            title={t("rollout.auto.off")}
            action={
              <Button size="sm" loading={resume.isPending} onClick={() => resume.mutate()}>
                {t("rollout.auto.resume")}
              </Button>
            }
          >
            <span className="flex flex-col gap-0.5">
              <span>{t.rich("rollout.hold.pausedSince", { time: <RelativeTime value={paused.at} /> })}</span>
              {paused.by !== null ? <span>{t("rollout.hold.pausedBy", { by: paused.by })}</span> : null}
              {paused.reason !== null ? <span>{t("rollout.hold.pausedReason", { reason: paused.reason })}</span> : null}
              {data.held > 0 ? <span>{t("rollout.auto.heldNow", { count: data.held })}</span> : null}
            </span>
          </FeatureState>
        )}
        <p className="max-w-measure text-13 text-pretty text-fg-muted" data-testid="automatic-sources">
          {sources.length > 0 ? t.rich("rollout.auto.sources", { list: <Mono>{sources.join(", ")}</Mono> }) : t("rollout.auto.nothingAutomatic")}
        </p>
        <Switch
          label={t("rollout.auto.rollbackLabel")}
          description={t("rollout.auto.rollbackDescription")}
          checked={pauseOnRollback}
          disabled={onRollback.isPending}
          onCheckedChange={(next) => onRollback.mutate(next)}
        />
      </div>
      <PauseDialog domain={domain} open={pausing} onOpenChange={setPausing} />
    </Card>
  );
}
