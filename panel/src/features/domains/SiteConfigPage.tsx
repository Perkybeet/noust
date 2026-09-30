import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useBlocker, useNavigate } from "@tanstack/react-router";
import { CornerDownRight, FileX, Play, RotateCw, Square } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { ReactNode } from "react";

import { ElevationCancelledError, isApiError, request } from "../../api/client";
import { appsQuery } from "../../api/queries/apps";
import { siteConfigQuery, siteKeys, siteQuery } from "../../api/queries/sites";
import type { SiteConfig } from "../../api/queries/sites";
import { CommandHint } from "../../components/page/CommandHint";
import { FileEditorPage } from "../../components/page/FileEditorPage";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button, buttonClassName } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { MenuItem } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { TextLink } from "../../components/ui/TextLink";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { ConfigEditor } from "./ConfigEditor";
import type { ConfigEditorHandle } from "./ConfigEditor";
import { configRejection, failingLine } from "./configErrors";
import type { ConfigRejection } from "./configErrors";
import { SiteState, Tls } from "./SitesPage";
import { useSiteActions } from "./useSiteActions";

type Outcome =
  | { kind: "saved"; webserver: string }
  | { kind: "rejected"; rejection: ConfigRejection }
  | { kind: "tested"; ok: boolean; output: string; line: number | null }
  | null;

/** How many lines differ from what is saved: the save bar's count of unsaved changes. */
export function changedLines(saved: string, draft: string): number {
  const before = saved.split("\n");
  const after = draft.split("\n");
  let changed = 0;
  for (let index = 0; index < Math.max(before.length, after.length); index += 1) {
    if (before[index] !== after[index]) changed += 1;
  }
  return changed;
}

/**
 * The web server refused the text: the console's usual error block with its own words
 * verbatim, and the line it names one click away.
 */
function Rejected({ rejection, id, onGoToLine }: { rejection: ConfigRejection; id: string; onGoToLine: (line: number) => void }) {
  const t = useT();
  return (
    <div id={id} className="flex min-w-0 flex-col gap-2">
      <ErrorBlock
        live
        compact
        title={t("domains.siteConfigPage.nothingSavedTestFailed")}
        hint={t("domains.siteConfigPage.fixLineNote", { summary: rejection.summary })}
        error={{ detail: rejection.output.trim() === "" ? rejection.summary : rejection.output.trimEnd() }}
      />
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

/** A check that passed, or a save: said with the web server's own confirmation. */
function Passed({ id, title, output, action }: { id: string; title: string; output?: string; action?: ReactNode }) {
  const t = useT();
  return (
    <div id={id}>
      <Notice tone="success" live title={title} {...(action !== undefined ? { action } : {})}>
        {output !== undefined ? (
          <SystemOutput label={t("domains.siteConfigPage.whatTestSaid")} maxHeight="max-h-28">
            {output}
          </SystemOutput>
        ) : undefined}
      </Notice>
    </div>
  );
}

/**
 * One web server site's configuration file, in the file editor (T6): the editor takes the
 * screen's height and the bar at its foot tests, or tests and saves. Saving always tests the
 * text with the web server first; a refusal is shown in the server's own words, the line it
 * names is marked, and the file on disk is left as it was. Deleting the site is behind "More
 * actions", never on the page beside the editor.
 */
export function SiteConfigPage({ site }: { site: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const info = useQuery(siteQuery(site));
  const config = useQuery(siteConfigQuery(site));
  const apps = useQuery(appsQuery());
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
  const app = apps.data?.apps.find((candidate) => candidate.domain === site) ?? null;
  const breadcrumbs = [
    { label: t("nav.domains.label"), to: "/domains" },
    { label: t("domains.page.sitesTab"), to: "/domains/sites" },
  ];

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
      <ListPage header={{ title: site, mono: true, breadcrumbs }}>
        <EmptyState
          variant="firstUse"
          icon={<FileX />}
          title={t("domains.siteConfigPage.noSiteTitle")}
          description={t("domains.siteConfigPage.noSiteDescription")}
          action={
            <Link to="/domains/sites" className={buttonClassName("secondary")}>
              {t("domains.siteConfigPage.allSites")}
            </Link>
          }
          command="noust site list"
        />
      </ListPage>
    );
  }

  const goToLine = (line: number): void => editor.current?.goToLine(line);
  let result = null;
  if (outcome?.kind === "rejected") {
    result = <Rejected rejection={outcome.rejection} id={outcomeId} onGoToLine={goToLine} />;
  } else if (outcome?.kind === "tested") {
    result = outcome.ok ? (
      <Passed
        id={outcomeId}
        title={t("domains.siteConfigPage.testPassedNotSaved", { webserver })}
        output={outcome.output.trim() === "" ? t("domains.siteConfigPage.printedNothing", { webserver }) : outcome.output.trimEnd()}
      />
    ) : (
      <Rejected
        rejection={{ summary: t("domains.siteConfigPage.webserverRejectedThisConfiguration", { webserver }), output: outcome.output, line: outcome.line }}
        id={outcomeId}
        onGoToLine={goToLine}
      />
    );
  } else if (outcome?.kind === "saved") {
    result = reload.isError ? (
      <ErrorBlock live compact error={reload.error} title={t("domains.siteConfigPage.webserverNotReloaded", { webserver })} />
    ) : (
      <Passed
        id={outcomeId}
        title={t("domains.siteConfigPage.savedPassedTest", { webserver })}
        action={
          <Button size="sm" icon={<RotateCw aria-hidden="true" />} loading={reload.isPending} onClick={() => reload.mutate()}>
            {t("domains.siteConfigPage.reloadWebserver", { webserver })}
          </Button>
        }
      />
    );
  } else if (save.error instanceof ElevationCancelledError) {
    result = (
      <p role="status" className="text-13 text-fg-muted">
        {save.error.detail}
      </p>
    );
  } else if (save.isError) {
    result = <ErrorBlock live compact error={save.error} title={t("domains.siteConfigPage.configNotSaved")} />;
  } else if (test.isError) {
    result = <ErrorBlock live compact error={test.error} title={t("domains.siteConfigPage.configNotTested")} />;
  }

  const describedBy = [pathId, outcome ? outcomeId : null].filter(Boolean).join(" ");

  return (
    <>
      <FileEditorPage
        header={{
          title: site,
          mono: true,
          breadcrumbs,
          ...(info.data ? { status: <SiteState enabled={info.data.enabled} size="md" /> } : {}),
          meta: info.data ? (
            <>
              <Mono tone="muted">{info.data.webserver}</Mono>
              <Tls secure={info.data.has_ssl} />
              {app !== null ? (
                <span>
                  {t.rich("domains.siteConfigPage.belongsTo", {
                    app: (
                      <TextLink to="/apps/$domain" params={{ domain: app.domain }}>
                        {app.domain}
                      </TextLink>
                    ),
                  })}
                </span>
              ) : null}
            </>
          ) : (
            // A line as tall as the loaded one.
            <span aria-hidden="true" className="flex h-5 items-center gap-3">
              <Skeleton className="h-3 w-12" />
              <Skeleton className="h-3 w-16" />
            </span>
          ),
          ...(info.data
            ? {
                secondaryActions: info.data.enabled ? (
                  <Button icon={<Square aria-hidden="true" />} loading={disable.isPending} onClick={() => disable.mutate(site)}>
                    {t("domains.sitesTab.disableAction")}
                  </Button>
                ) : (
                  <Button icon={<Play aria-hidden="true" />} loading={enable.isPending} onClick={() => enable.mutate(site)}>
                    {t("domains.sitesTab.enableAction")}
                  </Button>
                ),
                overflow: (
                  <MenuItem icon={<ICONS.delete />} destructive onClick={() => setDeleting(true)}>
                    {t("domains.deleteSite")}
                  </MenuItem>
                ),
              }
            : {}),
        }}
        notice={
          <Notice>{t("domains.siteConfigPage.testedBefore", { command: webserver === "apache" ? "apache2ctl -t" : "nginx -t" })}</Notice>
        }
        meta={
          config.data ? (
            <>
              {info.data && info.data.server_names.length > 0 ? (
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="shrink-0">{t("domains.siteConfigPage.servesLabel")}</span>
                  <Mono truncate title={info.data.server_names.join(", ")}>
                    {info.data.server_names.join(", ")}
                  </Mono>
                </span>
              ) : null}
              <span id={pathId} className="flex min-w-0 items-center gap-1.5">
                <span className="shrink-0">{t("domains.siteConfigPage.fileLabel")}</span>
                <Mono truncate title={config.data.path}>
                  {config.data.path}
                </Mono>
              </span>
            </>
          ) : undefined
        }
        bar={{
          changes: dirty ? changedLines(saved, text) : 0,
          onDiscard: () => {
            setDraft(null);
            setOutcome(null);
          },
          onTest: () => test.mutate(text),
          onTestAndSave: () => save.mutate(text),
          testing: test.isPending,
          saving: save.isPending,
        }}
        footer={<CommandHint command={`noust site show ${site}`} label={t("domains.fromTerminal")} />}
      >
        {config.isError && config.data === undefined ? (
          <ErrorBlock error={config.error} title={t("domains.siteConfigPage.couldNotReadConfig", { site })} onRetry={() => void config.refetch()} retrying={config.isRefetching} />
        ) : config.data === undefined ? (
          <div aria-busy="true" className="h-full">
            <span className="sr-only">{t("domains.siteConfigPage.loadingConfigurationSr")}</span>
            <Skeleton className="h-full rounded-control" />
          </div>
        ) : (
          <div className="flex h-full min-h-0 flex-col gap-3">
            {result}
            <ConfigEditor
              ref={editor}
              value={text}
              onChange={onChange}
              label={t("domains.siteConfigPage.configurationOfAriaLabel", { site })}
              describedBy={describedBy}
              errorLine={outcome?.kind === "rejected" ? outcome.rejection.line : outcome?.kind === "tested" ? outcome.line : null}
              disabled={save.isPending || test.isPending}
              className="flex-1"
            />
          </div>
        )}
      </FileEditorPage>

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
          void navigate({ to: "/domains/sites" });
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
