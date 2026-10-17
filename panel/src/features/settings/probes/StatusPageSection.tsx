/**
 * The public status page: the probes marked public as one static page that any web server can
 * serve. Here it is switched on, titled, written now, and put on a name of its own (an nginx
 * site and a certificate, made by a job and recorded so that taking it off removes exactly that).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { ElevationCancelledError } from "../../../api/client";
import { isJobFinished, jobKeys, useFollowedJob } from "../../../api/queries/jobs";
import { probeKeys, probesQuery, publishStatusPage, saveStatusPage, unpublishStatusPage, writeStatusPage } from "../../../api/queries/probes";
import type { StatusPage } from "../../../api/queries/probes";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { ExternalLink } from "../../../components/ui/ExternalLink";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Switch } from "../../../components/ui/Switch";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { jobState } from "../../databases/jobs";
import { JobLogPanel } from "../../jobs/JobLogPanel";
import { reportActionError } from "../../apps/useAppActions";

type Doing = "publish" | "unpublish";

/**
 * Putting the page on a name, or taking it off: the name and the checks first, then the job with
 * its output. The refusals - a name that is an application's or a site Noust did not write, a name
 * that does not point here - are shown as the server wrote them.
 */
function PageJobDialog({ doing, open, onClose, domain }: { doing: Doing; open: boolean; onClose: () => void; domain: string | null }) {
  const t = useT();
  const queryClient = useQueryClient();
  const followed = useFollowedJob();
  const [name, setName] = useState("");
  const [skipDns, setSkipDns] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  const start = useMutation({
    mutationFn: () => (doing === "publish" ? publishStatusPage({ domain: name.trim(), skip_dns_check: skipDns }) : unpublishStatusPage()),
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      followed.follow(accepted.job_id);
    },
    onError: (error) => {
      if (error instanceof ElevationCancelledError) start.reset();
    },
  });
  const job = followed.job;
  const finished = job !== null && isJobFinished(job);
  const settled = useRef<string | null>(null);
  useEffect(() => {
    if (job === null || !isJobFinished(job) || settled.current === job.id) return;
    settled.current = job.id;
    void queryClient.invalidateQueries({ queryKey: probeKeys.all });
  }, [job, queryClient]);

  const close = (): void => {
    if (start.isPending) return;
    onClose();
    setName("");
    setSkipDns(false);
    followed.dismiss();
    start.reset();
  };
  const shown = doing === "publish" ? name.trim() : (domain ?? "");
  const state = job === null ? "queued" : jobState(job.status);
  const running = start.isPending || (followed.id !== null && !finished);
  const asking = followed.id === null;

  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value) close();
      }}
      size="lg"
      initialFocus={nameRef}
      title={doing === "publish" ? t("probes.page.publish.title") : t("probes.page.unpublish.title")}
      description={doing === "publish" ? t("probes.page.publish.description") : t("probes.page.unpublish.description", { domain: domain ?? "" })}
      footer={
        asking ? (
          <>
            <Button variant="ghost" disabled={start.isPending} onClick={close}>
              {t("settings.shared.cancel")}
            </Button>
            <Button
              variant={doing === "publish" ? "primary" : "danger"}
              loading={start.isPending}
              disabled={doing === "publish" && name.trim() === ""}
              onClick={() => {
                start.mutate();
              }}
            >
              {doing === "publish" ? t("probes.page.publish.submit") : t("probes.page.unpublish.submit")}
            </Button>
          </>
        ) : (
          <>
            {state === "succeeded" && doing === "publish" ? (
              <ExternalLink href={`https://${shown}`} button="secondary">
                {t("probes.page.open", { domain: shown })}
              </ExternalLink>
            ) : null}
            <Button variant="primary" disabled={running} onClick={close}>
              {finished ? t("probes.page.done") : t("probes.page.close")}
            </Button>
          </>
        )
      }
    >
      <div className="flex min-w-0 flex-col gap-5">
        {asking ? (
          doing === "publish" ? (
            <form
              noValidate
              className="flex min-w-0 flex-col gap-5"
              onSubmit={(event) => {
                event.preventDefault();
                if (name.trim() !== "" && !start.isPending) start.mutate();
              }}
            >
              <Field label={t("probes.page.publish.domainLabel")} description={t("probes.page.publish.domainDescription")}>
                <Input
                  ref={nameRef}
                  mono
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  placeholder="status.example.com"
                  value={name}
                  onValueChange={(next: string) => {
                    setName(next);
                    start.reset();
                  }}
                />
              </Field>
              <Checkbox checked={skipDns} onCheckedChange={setSkipDns} label={t("probes.page.publish.skipDns")} description={t("probes.page.publish.skipDnsDescription")} />
              {start.isError && !(start.error instanceof ElevationCancelledError) ? <ErrorBlock live compact error={start.error} title={t("probes.page.publish.failed")} /> : null}
            </form>
          ) : (
            <>
              <p className="text-13 text-pretty text-fg-muted">{t("probes.page.unpublish.body")}</p>
              {start.isError && !(start.error instanceof ElevationCancelledError) ? <ErrorBlock live compact error={start.error} title={t("probes.page.unpublish.failed")} /> : null}
            </>
          )
        ) : (
          <JobLogPanel
            job={job}
            words={{
              queued: t("probes.page.job.running", { domain: shown }),
              running: t("probes.page.job.running", { domain: shown }),
              succeeded: t(doing === "publish" ? "probes.page.job.published" : "probes.page.job.unpublished", { domain: shown }),
              failed: t("probes.page.job.failed", { domain: shown }),
            }}
            label={t("probes.page.job.logLabel")}
          />
        )}
      </div>
    </Dialog>
  );
}

function PageCard({ page }: { page: StatusPage }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState<string | null>(null);
  const [doing, setDoing] = useState<Doing | null>(null);
  const shownTitle = title ?? page.title;
  const dirty = title !== null && title.trim() !== page.title;

  const refresh = (): Promise<void> => queryClient.invalidateQueries({ queryKey: probeKeys.all });
  const change = useMutation({
    mutationFn: (body: { enabled?: boolean; title?: string }) => saveStatusPage(body),
    onSuccess: async () => {
      setTitle(null);
      await refresh();
    },
    onError: (error) => {
      reportActionError(t("probes.page.saveFailed"), error);
    },
  });
  const write = useMutation({
    mutationFn: () => writeStatusPage(),
    onSuccess: async (result) => {
      toast.success(t("probes.page.written", { count: result.files.length }));
      await refresh();
    },
    onError: (error) => {
      reportActionError(t("probes.page.writeFailed"), error);
    },
  });
  const checked = change.isPending && change.variables.enabled !== undefined ? change.variables.enabled : page.enabled;

  return (
    <Card>
      <div className="flex min-w-0 flex-col gap-5">
        <Switch
          label={t("probes.page.enabledLabel")}
          checked={checked}
          disabled={change.isPending}
          onCheckedChange={(next) => {
            change.mutate({ enabled: next });
          }}
        />
        <p className="text-12 text-pretty text-fg-muted">
          {t.rich("probes.page.files", { directory: <Mono key="dir">{page.directory}</Mono>, count: page.components })}
        </p>
        <Field label={t("probes.page.titleLabel")} description={t("probes.page.titleDescription")}>
          <Input
            maxLength={80}
            autoComplete="off"
            value={shownTitle}
            disabled={change.isPending}
            onValueChange={(next: string) => {
              setTitle(next);
            }}
            suffix={
              <Button
                size="sm"
                variant={dirty ? "primary" : "ghost"}
                disabled={!dirty || change.isPending}
                onClick={() => {
                  change.mutate({ title: shownTitle.trim() });
                }}
              >
                {t("probes.page.saveTitle")}
              </Button>
            }
          />
        </Field>
        <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2">
          {page.domain !== null && page.domain !== undefined && page.domain !== "" ? (
            <>
              <p className="min-w-0 flex-1 basis-60 text-13 text-fg">
                {t.rich("probes.page.publishedOn", { domain: <Mono key="domain">{page.domain}</Mono> })}
              </p>
              <ExternalLink href={`https://${page.domain}`} button="secondary">
                {t("probes.page.open", { domain: page.domain })}
              </ExternalLink>
              <Button
                variant="ghost"
                onClick={() => {
                  setDoing("unpublish");
                }}
              >
                {t("probes.page.unpublish.button")}
              </Button>
            </>
          ) : (
            <>
              <p className="min-w-0 flex-1 basis-60 text-13 text-fg-muted">{t("probes.page.notPublished")}</p>
              <Button
                variant="secondary"
                onClick={() => {
                  setDoing("publish");
                }}
              >
                {t("probes.page.publish.button")}
              </Button>
            </>
          )}
          <Button size="sm" loading={write.isPending} onClick={() => write.mutate()}>
            {t("probes.page.write")}
          </Button>
        </div>
      </div>
      {doing !== null ? (
        <PageJobDialog
          doing={doing}
          open
          domain={page.domain ?? null}
          onClose={() => {
            setDoing(null);
          }}
        />
      ) : null}
    </Card>
  );
}

/** The status page section: whether it is kept, its title and where it is published. */
export function StatusPageSection() {
  const t = useT();
  const query = useQuery(probesQuery());
  if (query.data === undefined) return null;
  return (
    <Section title={t("probes.page.title")} description={t("probes.page.description")}>
      <PageCard page={query.data.status_page} />
    </Section>
  );
}
