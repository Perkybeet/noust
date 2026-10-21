/**
 * The public status page: the probes marked public as one static page that any web server can
 * serve. Here it is switched on, titled, written now, and put on a name of its own (an nginx
 * site and a certificate, made by a job and recorded so that taking it off removes exactly that).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Globe, Pencil, Unlink } from "lucide-react";
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
import { Setting } from "../../../components/ui/Setting";
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
  const [lacksName, setLacksName] = useState(false);
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

  // Pressing "Publish" with no name says so beside the box and goes there; it is never a dead button.
  const begin = (): void => {
    if (start.isPending) return;
    if (doing === "publish" && name.trim() === "") {
      setLacksName(true);
      nameRef.current?.focus();
      return;
    }
    start.mutate();
  };
  const close = (): void => {
    if (start.isPending) return;
    onClose();
    setLacksName(false);
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
            <Button variant="primary" loading={start.isPending} onClick={begin}>
              {doing === "publish" ? t("probes.page.publish.submit") : t("probes.page.unpublish.submit")}
            </Button>
          </>
        ) : (
          <>
            {state === "succeeded" && doing === "publish" ? (
              <ExternalLink href={`https://${shown}`} button="secondary">
                {t("probes.page.open")}
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
                begin();
              }}
            >
              <Field label={t("probes.page.publish.domainLabel")} description={t("probes.page.publish.domainDescription")} error={lacksName && name.trim() === "" ? t("probes.page.publish.nameMissing") : undefined}>
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
                    setLacksName(false);
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

/** The page's title, asked in a dialog of its own: a field and a button that saves it, not a Save in the card. */
function TitleDialog({ page, open, onClose }: { page: StatusPage; open: boolean; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(page.title);
  const save = useMutation({
    mutationFn: () => saveStatusPage({ title: title.trim() }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: probeKeys.all });
      onClose();
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="sm"
      title={t("probes.page.titleDialog")}
      description={t("probes.page.titleDescription")}
      footer={
        <>
          <Button variant="ghost" disabled={save.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>
            {t("probes.page.saveTitle")}
          </Button>
        </>
      }
    >
      <form
        noValidate
        className="flex min-w-0 flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (!save.isPending) save.mutate();
        }}
      >
        <Field label={t("probes.page.titleLabel")}>
          <Input
            maxLength={80}
            autoComplete="off"
            value={title}
            onValueChange={(next: string) => {
              setTitle(next);
              save.reset();
            }}
          />
        </Field>
        {save.isError ? <ErrorBlock live compact error={save.error} title={t("probes.page.saveFailed")} /> : null}
      </form>
    </Dialog>
  );
}

function PageCard({ page }: { page: StatusPage }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [titling, setTitling] = useState(false);
  const [doing, setDoing] = useState<Doing | null>(null);

  const refresh = (): Promise<void> => queryClient.invalidateQueries({ queryKey: probeKeys.all });
  const change = useMutation({
    mutationFn: (enabled: boolean) => saveStatusPage({ enabled }),
    onSuccess: refresh,
    onError: (error) => {
      reportActionError(t("probes.page.saveFailed"), error);
    },
  });
  const write = useMutation({
    mutationFn: () => writeStatusPage(),
    onSuccess: async (result) => {
      // design-exception: toast-success the files are written to disk, which is not on this screen
      toast.success(t("probes.page.written", { count: result.files.length }));
      await refresh();
    },
    onError: (error) => {
      reportActionError(t("probes.page.writeFailed"), error);
    },
  });
  const checked = change.isPending ? change.variables : page.enabled;
  const published = page.domain !== null && page.domain !== "";

  return (
    <Card padding="none">
      <div className="flex flex-col divide-y divide-border">
        <Setting
          label={t("probes.page.enabledLabel")}
          help={t.rich("probes.page.files", { directory: <Mono key="dir">{page.directory}</Mono>, count: page.components })}
          checked={checked}
          busy={change.isPending}
          onCheckedChange={(next) => {
            change.mutate(next);
          }}
        />
        <Setting
          label={t("probes.page.titleLabel")}
          help={page.title}
          action={
            <Button
              size="sm"
              icon={<Pencil aria-hidden="true" />}
              onClick={() => {
                setTitling(true);
              }}
            >
              {t("probes.page.changeTitle")}
            </Button>
          }
        />
        <Setting
          label={t("probes.page.addressLabel")}
          help={published ? t.rich("probes.page.publishedOn", { domain: <Mono key="domain">{page.domain}</Mono> }) : t("probes.page.notPublished")}
          action={
            published ? (
              <div className="flex items-center gap-2">
                <ExternalLink href={`https://${page.domain ?? ""}`} button="secondary" size="sm">
                  {t("probes.page.open")}
                </ExternalLink>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<Unlink aria-hidden="true" />}
                  onClick={() => {
                    setDoing("unpublish");
                  }}
                >
                  {t("probes.page.unpublish.button")}
                </Button>
              </div>
            ) : (
              <Button
                size="sm"
                icon={<Globe aria-hidden="true" />}
                onClick={() => {
                  setDoing("publish");
                }}
              >
                {t("probes.page.publish.button")}
              </Button>
            )
          }
        />
        <Setting
          label={t("probes.page.writeLabel")}
          help={t("probes.page.writeHelp")}
          action={
            <Button size="sm" icon={<FileText aria-hidden="true" />} loading={write.isPending} onClick={() => write.mutate()}>
              {t("probes.page.write")}
            </Button>
          }
        />
      </div>
      {titling ? (
        <TitleDialog
          page={page}
          open
          onClose={() => {
            setTitling(false);
          }}
        />
      ) : null}
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
