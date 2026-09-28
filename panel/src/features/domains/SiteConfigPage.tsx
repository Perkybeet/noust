import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useBlocker, useNavigate } from "@tanstack/react-router";
import { CircleAlert, CornerDownRight, FileX, FlaskConical, MoreHorizontal, Play, RotateCw, ShieldCheck, Square, Trash2, Undo2 } from "lucide-react";
import { useId, useRef, useState } from "react";

import { ElevationCancelledError, isApiError, request } from "../../api/client";
import { siteConfigQuery, siteKeys, siteQuery } from "../../api/queries/sites";
import type { SiteConfig } from "../../api/queries/sites";
import { PageHeader } from "../../app/PageHeader";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section, Sections } from "../../components/page/Section";
import { Button, buttonClassName } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem } from "../../components/ui/Menu";
import { Skeleton } from "../../components/ui/Skeleton";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { ConfigEditor } from "./ConfigEditor";
import type { ConfigEditorHandle } from "./ConfigEditor";
import { SiteState, Tls } from "./SitesTab";
import { configRejection, failingLine } from "./configErrors";
import type { ConfigRejection } from "./configErrors";
import { useSiteActions } from "./useSiteActions";

function useBreadcrumbs(): readonly { label: string; to: "/domains" }[] {
  const t = useT();
  return [{ label: t("nav.domains.label"), to: "/domains" }] as const;
}

type Outcome =
  | { kind: "saved"; webserver: string }
  | { kind: "rejected"; rejection: ConfigRejection }
  | { kind: "tested"; ok: boolean; output: string; line: number | null }
  | null;

function Rejected({ rejection, id, onGoToLine }: { rejection: ConfigRejection; id: string; onGoToLine: (line: number) => void }) {
  const t = useT();
  return (
    <div id={id} role="alert" className="flex min-w-0 flex-col gap-2 rounded-card border border-fail/30 bg-fail-soft/50 p-4">
      <div className="flex items-start gap-2">
        <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fail" />
        <div className="flex min-w-0 flex-col gap-1">
          <p className="text-13 font-medium text-fg">{t("domains.siteConfigPage.nothingSavedTestFailed")}</p>
          <p className="text-13 text-pretty text-fg-muted">{t("domains.siteConfigPage.fixLineNote", { summary: rejection.summary })}</p>
        </div>
      </div>
      <SystemOutput label={t("domains.siteConfigPage.whatTestSaid")} maxHeight="max-h-56" className="rounded-control border border-border bg-surface px-3 py-2">
        {rejection.output.trim() === "" ? rejection.summary : rejection.output.trimEnd()}
      </SystemOutput>
      {rejection.line !== null ? (
        <div>
          <Button size="sm" icon={<CornerDownRight aria-hidden="true" />} onClick={() => onGoToLine(rejection.line ?? 1)}>
            {t("domains.siteConfigPage.goToLine", { line: rejection.line })}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function Saved({ webserver, id, onReload, reloading }: { webserver: string; id: string; onReload: () => void; reloading: boolean }) {
  const t = useT();
  return (
    <div id={id} role="status" className="flex flex-col gap-3 rounded-card border border-ok/30 bg-ok-soft/40 p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-2">
        <ShieldCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ok" />
        <p className="text-13 text-pretty text-fg">{t("domains.siteConfigPage.savedPassedTest", { webserver })}</p>
      </div>
      <Button icon={<RotateCw aria-hidden="true" />} loading={reloading} onClick={onReload} className="self-start sm:self-auto">
        {t("domains.siteConfigPage.reloadWebserver", { webserver })}
      </Button>
    </div>
  );
}

/** The reload after a save's own test passed, itself refused by the web server: rare (the
 * configuration changed again between the two calls), but its output is shown verbatim, the
 * same as any other test failure. */
function ReloadFailed({ webserver, output }: { webserver: string; output: string }) {
  const t = useT();
  return (
    <div role="alert" className="flex flex-col gap-2 rounded-card border border-fail/30 bg-fail-soft/50 p-4">
      <div className="flex items-start gap-2">
        <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fail" />
        <p className="text-13 text-pretty text-fg">{t("domains.siteConfigPage.webserverNotReloaded", { webserver })}</p>
      </div>
      <SystemOutput label={t("domains.siteConfigPage.whatReloadSaid", { webserver })} maxHeight="max-h-56" className="rounded-control border border-border bg-surface px-3 py-2">
        {output.trim() === "" ? t("domains.siteConfigPage.printedNothing", { webserver }) : output.trimEnd()}
      </SystemOutput>
    </div>
  );
}

/** A candidate configuration tested without saving it, and found acceptable. Nothing on disk
 * changed: the web server's own confirmation is shown so the operator knows it is safe to save. */
function TestPassed({ webserver, output, id }: { webserver: string; output: string; id: string }) {
  const t = useT();
  return (
    <div id={id} role="status" className="flex flex-col gap-2 rounded-card border border-ok/30 bg-ok-soft/40 p-4">
      <div className="flex items-start gap-2">
        <ShieldCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ok" />
        <p className="text-13 text-pretty text-fg">{t("domains.siteConfigPage.testPassedNotSaved", { webserver })}</p>
      </div>
      <SystemOutput label={t("domains.siteConfigPage.whatTestSaid")} maxHeight="max-h-56" className="rounded-control border border-border bg-surface px-3 py-2">
        {output.trim() === "" ? t("domains.siteConfigPage.printedNothing", { webserver }) : output.trimEnd()}
      </SystemOutput>
    </div>
  );
}

/**
 * The loaded view's shape, line for line: the "Serves" and "File" lines, the editor at its own
 * height, and the row of buttons. Anything shorter pushed the terminal hint down the page when
 * the configuration arrived.
 */
function EditorSkeleton() {
  const t = useT();
  return (
    <div aria-busy="true" className="flex flex-col gap-3">
      <span className="sr-only">{t("domains.siteConfigPage.loadingConfigurationSr")}</span>
      <div aria-hidden="true" className="flex flex-col gap-3">
        <div className="flex h-4 items-center">
          <Skeleton className="h-3 w-48" />
        </div>
        <div className="flex h-4 items-center">
          <Skeleton className="h-3 w-72 max-w-full" />
        </div>
        <div className="flex h-[26rem] min-w-0 flex-col gap-2 rounded-control border border-border p-3 sm:h-[34rem]">
          {["w-2/5", "w-2/3", "w-1/2", "w-3/4", "w-1/3", "w-3/5", "w-1/2", "w-2/3"].map((width, index) => (
            <Skeleton key={index} className={`h-3 ${width}`} />
          ))}
        </div>
        <div className="h-8" />
      </div>
    </div>
  );
}

/**
 * One web server site: its state, and its configuration file in an editor. Saving always tests
 * the text with the web server first; a refusal is shown in the server's own words, the line it
 * names is marked, and the file on disk is left as it was.
 */
export function SiteConfigPage({ site }: { site: string }) {
  const t = useT();
  const BREADCRUMBS = useBreadcrumbs();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const info = useQuery(siteQuery(site));
  const config = useQuery(siteConfigQuery(site));
  const { enable, disable, reload, refresh } = useSiteActions();
  const editor = useRef<ConfigEditorHandle>(null);
  const outcomeId = useId();
  const pathId = useId();

  const [draft, setDraft] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [deleting, setDeleting] = useState(false);

  const saved = config.data?.config ?? "";
  const text = draft ?? saved;
  const dirty = draft !== null && draft !== saved;
  const webserver = config.data?.webserver ?? info.data?.webserver ?? "nginx";

  // Set once the site is deleted: there is nothing left to lose by leaving.
  const gone = useRef(false);
  const blocker = useBlocker({
    // Signing in again after the session expired is not leaving: the draft could not be saved.
    shouldBlockFn: ({ next }) => dirty && !gone.current && next.pathname !== "/login",
    enableBeforeUnload: () => dirty && !gone.current,
    withResolver: true,
  });

  const save = useMutation({
    mutationFn: (next: string) => request("put", "/api/sites/{domain}/config", { params: { domain: site }, body: { config: next } }),
    onMutate: () => {
      setOutcome(null);
      reload.reset();
    },
    onSuccess: (_result, next) => {
      queryClient.setQueryData<SiteConfig>(siteKeys.config(site), (current) => (current ? { ...current, config: next } : current));
      setDraft(null);
      setOutcome({ kind: "saved", webserver });
      refresh(site);
    },
    onError: (error) => {
      const rejection = configRejection(error);
      if (rejection !== null) setOutcome({ kind: "rejected", rejection });
    },
  });

  // A candidate configuration tried against the web server without saving it: same test the
  // save path runs, without the side effect. The endpoint never touches the file on disk.
  const test = useMutation({
    mutationFn: (content: string) => request("post", "/api/sites/{domain}/config/test", { params: { domain: site }, body: { content } }),
    onMutate: () => {
      setOutcome(null);
    },
    onSuccess: (result) => {
      setOutcome({ kind: "tested", ok: result.ok, output: result.output, line: result.ok ? null : failingLine(result.output) });
    },
  });

  const onChange = (value: string): void => {
    setDraft(value);
    // A refusal (from testing or saving) stays up while the operator fixes the line it names;
    // an affirmative result about to be outdated by the edit does not.
    if (outcome?.kind === "saved" || (outcome?.kind === "tested" && outcome.ok)) setOutcome(null);
  };

  if ((info.isError && isApiError(info.error) && info.error.status === 404) || (config.isError && isApiError(config.error) && config.error.status === 404)) {
    return (
      <>
        <PageHeader title={site} breadcrumbs={BREADCRUMBS} />
        <EmptyState
          level={2}
          icon={<FileX />}
          title={t("domains.siteConfigPage.noSiteTitle")}
          description={t("domains.siteConfigPage.noSiteDescription")}
          action={
            <Link to="/domains" search={{ tab: "sites" }} className={buttonClassName("secondary")}>
              {t("domains.siteConfigPage.allSites")}
            </Link>
          }
          command="wasm site list"
          className="py-16"
        />
      </>
    );
  }

  const facts = info.data ? (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      <SiteState enabled={info.data.enabled} />
      <span translate="no" className="mono text-12 text-fg-muted">
        {info.data.webserver}
      </span>
      <span className="text-13">
        <Tls secure={info.data.has_ssl} />
      </span>
    </span>
  ) : (
    // A line as tall as the loaded one: its tallest part is the 13px TLS word.
    <span aria-hidden="true" className="flex h-5 items-center gap-3">
      <Skeleton className="h-3 w-16" />
      <Skeleton className="h-3 w-12" />
    </span>
  );

  const actions = info.data ? (
    <>
      {info.data.enabled ? (
        <Button icon={<Square aria-hidden="true" />} loading={disable.isPending} onClick={() => disable.mutate(site)}>
          {t("domains.sitesTab.disableAction")}
        </Button>
      ) : (
        <Button icon={<Play aria-hidden="true" />} loading={enable.isPending} onClick={() => enable.mutate(site)}>
          {t("domains.sitesTab.enableAction")}
        </Button>
      )}
      <Menu align="end" trigger={<IconButton variant="secondary" label={t("domains.siteConfigPage.moreActions")} icon={<MoreHorizontal />} tooltip={false} />}>
        <MenuItem icon={<Trash2 />} destructive onClick={() => setDeleting(true)}>
          {t("domains.deleteSite")}
        </MenuItem>
      </Menu>
    </>
  ) : undefined;

  const describedBy = [pathId, outcome ? outcomeId : null].filter(Boolean).join(" ");

  return (
    <>
      <PageHeader title={site} breadcrumbs={BREADCRUMBS} description={facts} actions={actions} />
      <Sections>
        <Section
          title={t("domains.siteConfigPage.configurationSectionTitle")}
          description={t.rich("domains.siteConfigPage.configurationSectionDescription", {
            command: <code className="text-12">{webserver === "apache" ? "apache2ctl -t" : "nginx -t"}</code>,
          })}
        >
          {config.isError && config.data === undefined ? (
            <ErrorBlock error={config.error} title={t("domains.siteConfigPage.couldNotReadConfig", { site })} onRetry={() => void config.refetch()} retrying={config.isRefetching} />
          ) : config.data === undefined ? (
            <EditorSkeleton />
          ) : (
            <div className="flex flex-col gap-3">
              {info.data && info.data.server_names.length > 0 ? (
                <p className="flex min-w-0 items-center gap-2 text-12 text-fg-muted">
                  <span className="shrink-0">{t("domains.siteConfigPage.servesLabel")}</span>
                  <span translate="no" className="mono truncate text-fg" title={info.data.server_names.join(", ")}>
                    {info.data.server_names.join(", ")}
                  </span>
                </p>
              ) : null}
              <p id={pathId} className="flex min-w-0 items-center gap-2 text-12 text-fg-muted">
                <span className="shrink-0">{t("domains.siteConfigPage.fileLabel")}</span>
                <code translate="no" className="truncate text-fg" title={config.data.path}>
                  {config.data.path}
                </code>
              </p>
              <ConfigEditor
                ref={editor}
                value={text}
                onChange={onChange}
                label={t("domains.siteConfigPage.configurationOfAriaLabel", { site })}
                describedBy={describedBy}
                errorLine={outcome?.kind === "rejected" ? outcome.rejection.line : outcome?.kind === "tested" ? outcome.line : null}
                disabled={save.isPending || test.isPending}
              />
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p role="status" className="text-13 text-fg-muted">
                  {dirty ? t("domains.siteConfigPage.unsavedChanges") : outcome?.kind === "saved" ? "" : t("domains.siteConfigPage.noChanges")}
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="ghost"
                    icon={<Undo2 aria-hidden="true" />}
                    disabled={!dirty || save.isPending || test.isPending}
                    onClick={() => {
                      setDraft(null);
                      setOutcome(null);
                    }}
                  >
                    {t("domains.discardChanges")}
                  </Button>
                  <Button variant="secondary" icon={<FlaskConical aria-hidden="true" />} disabled={save.isPending} loading={test.isPending} onClick={() => test.mutate(text)}>
                    {t("domains.siteConfigPage.test")}
                  </Button>
                  <Button variant="primary" disabled={!dirty || test.isPending} loading={save.isPending} onClick={() => save.mutate(text)}>
                    {t("domains.siteConfigPage.testAndSave")}
                  </Button>
                </div>
              </div>
              {outcome?.kind === "rejected" ? (
                <Rejected rejection={outcome.rejection} id={outcomeId} onGoToLine={(line) => editor.current?.goToLine(line)} />
              ) : outcome?.kind === "tested" ? (
                outcome.ok ? (
                  <TestPassed webserver={webserver} output={outcome.output} id={outcomeId} />
                ) : (
                  <Rejected
                    rejection={{ summary: t("domains.siteConfigPage.webserverRejectedThisConfiguration", { webserver }), output: outcome.output, line: outcome.line }}
                    id={outcomeId}
                    onGoToLine={(line) => editor.current?.goToLine(line)}
                  />
                )
              ) : outcome?.kind === "saved" ? (
                <>
                  <Saved webserver={outcome.webserver} id={outcomeId} onReload={() => reload.mutate()} reloading={reload.isPending} />
                  {reload.isError ? (
                    isApiError(reload.error) && reload.error.output ? (
                      <ReloadFailed webserver={webserver} output={reload.error.output} />
                    ) : (
                      <ErrorBlock live error={reload.error} title={t("domains.siteConfigPage.reloadNotReloadedTitle", { webserver })} />
                    )
                  ) : null}
                </>
              ) : save.isError && !(save.error instanceof ElevationCancelledError) ? (
                <ErrorBlock live error={save.error} title={t("domains.siteConfigPage.configNotSaved")} />
              ) : save.error instanceof ElevationCancelledError ? (
                <p role="status" className="text-13 text-fg-muted">{save.error.detail}</p>
              ) : test.isError ? (
                <ErrorBlock live error={test.error} title={t("domains.siteConfigPage.configNotTested")} />
              ) : null}
            </div>
          )}
        </Section>
        <CommandHint command={`wasm site show ${site}`} label={t("domains.fromTerminal")} />
      </Sections>

      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={t("domains.deleteNamedTitle", { name: site })}
        description={t("domains.deleteSiteDescription")}
        confirmText={site}
        actionLabel={t("domains.deleteSite")}
        onConfirm={async () => {
          await request("delete", "/api/sites/{domain}", { params: { domain: site } });
          gone.current = true;
          refresh(site);
          toast.success(t("domains.sitesTab.deletedToast", { site }));
          void navigate({ to: "/domains", search: { tab: "sites" } });
        }}
      />
      <Dialog
        open={blocker.status === "blocked"}
        onOpenChange={(open) => {
          if (!open) blocker.reset?.();
        }}
        size="sm"
        title={t("domains.siteConfigPage.leaveDialogTitle")}
        description={t("domains.siteConfigPage.leaveDialogDescription", { site })}
        footer={
          <>
            <Button onClick={() => blocker.reset?.()}>{t("domains.siteConfigPage.keepEditing")}</Button>
            <Button variant="danger" onClick={() => blocker.proceed?.()}>
              {t("domains.discardChanges")}
            </Button>
          </>
        }
      />
    </>
  );
}
