import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, CircleArrowUp, CircleDashed, CircleHelp, RefreshCw, RotateCw } from "lucide-react";
import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import { configQuery } from "../../api/queries/config";
import { isJobFinished, useFollowedJob } from "../../api/queries/jobs";
import { machineQuery, systemKeys, versionQuery } from "../../api/queries/system";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section, Sections } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { ICONS } from "../../components/ui/icons";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { isHttpUrl } from "../../lib/url";
import { useNode } from "../../nodes/useNode";
import { reportActionError } from "../apps/useAppActions";
import { installMethodWords, readServerIdentity, selfUpdateQuery } from "./GeneralSettings.model";

type UpdateInfo = ResponseOf<"/api/system/version", "get">;

const REPOSITORY = "https://github.com/Perkybeet/noust";

function links(t: T): readonly { label: string; href: string; description: string }[] {
  return [
    { label: t("settings.about.links.documentation.label"), href: `${REPOSITORY}#readme`, description: t("settings.about.links.documentation.description") },
    { label: t("settings.about.links.releaseNotes.label"), href: `${REPOSITORY}/releases`, description: t("settings.about.links.releaseNotes.description") },
    { label: t("settings.about.links.reportProblem.label"), href: `${REPOSITORY}/issues`, description: t("settings.about.links.reportProblem.description") },
    { label: t("settings.about.links.source.label"), href: REPOSITORY, description: t("settings.about.links.source.description") },
  ];
}

/** What the console does, and the command that does it from a terminal. */
function terminalRows(t: T): readonly { task: string; command: string }[] {
  return [
    { task: t("settings.about.terminal.installedVersion"), command: "noust --version" },
    { task: t("settings.about.terminal.showConfig"), command: "noust config show" },
    { task: t("settings.about.terminal.checkMachine"), command: "noust health" },
    { task: t("settings.about.terminal.consoleStatus"), command: "noust web status" },
    { task: t("settings.about.terminal.restartConsole"), command: "noust web restart" },
  ];
}

function ReleaseNotesLink({ url, version }: { url: string | null | undefined; version: string }): ReactNode {
  const t = useT();
  if (!url || !isHttpUrl(url)) return null;
  return <ExternalLink href={url}>{t("settings.about.version.whatIsNew", { version })}</ExternalLink>;
}

/** One line of state: a glyph, a sentence, and what follows from it. */
function StateLine({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <p className="flex items-center gap-2 text-14 font-medium text-fg">
        {icon}
        {title}
      </p>
      {children}
    </div>
  );
}

/**
 * A release this server's package index has not seen yet: the package manager installs what its
 * index lists, and the system refreshes that about once a day. Refreshing it here runs the same
 * job as the Server area's refresh (and `noust server updates refresh`); when it ends the version
 * is asked again, and the update is offered once the index lists it.
 */
function IndexBehind({ info, version }: { info: UpdateInfo; version: string }): ReactNode {
  const t = useT();
  const queryClient = useQueryClient();
  const followed = useFollowedJob();
  const refresh = useMutation({
    mutationFn: () => request("post", "/api/server/updates/refresh"),
    onSuccess: (accepted) => followed.follow(accepted.job_id),
    onError: (error) => reportActionError(t("settings.about.version.refreshFailed"), error),
  });
  const job = followed.job;
  const finished = job !== null && isJobFinished(job);
  const settled = useRef<string | null>(null);
  useEffect(() => {
    if (job === null || !finished || settled.current === job.id) return;
    settled.current = job.id;
    void queryClient.invalidateQueries({ queryKey: systemKeys.version });
  }, [finished, job, queryClient]);
  const running = refresh.isPending || (job !== null && !finished);
  const failed = job !== null && job.status === "failed";
  return (
    <StateLine icon={<CircleArrowUp aria-hidden="true" className="size-icon-md shrink-0" />} title={t("settings.about.version.indexBehind", { version })}>
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("settings.about.version.indexBehindDescription")}</p>
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" variant="primary" icon={<RefreshCw aria-hidden="true" />} loading={running} onClick={() => refresh.mutate()}>
          {running ? t("settings.about.version.refreshingIndex") : t("settings.about.version.refreshIndex")}
        </Button>
        <ReleaseNotesLink url={info.release_url} version={version} />
      </div>
      {failed ? <ErrorBlock live compact error={{ detail: job.error ?? "" }} title={t("settings.about.version.refreshFailed")} /> : null}
      {info.refresh_command ? <CommandHint label={t("settings.about.version.refreshFromTerminal")} command={info.refresh_command} /> : null}
    </StateLine>
  );
}

/**
 * Whether a newer Noust exists, told truthfully: a release on GitHub is only "available" once
 * the package this server installs from has it; until then it is "on the way", with no command
 * that would install nothing.
 */
function UpdateState({ info }: { info: UpdateInfo }): ReactNode {
  const t = useT();
  const icon = "size-icon-md shrink-0";
  if (info.status === "disabled") {
    return (
      <StateLine icon={<CircleHelp aria-hidden="true" className={`${icon} text-fg-muted`} />} title={t("settings.about.version.disabled")}>
        <p className="text-13 text-fg-muted">
          {t.rich("settings.about.version.disabledHint", {
            general: (
              <TextLink key="general" to="/settings">
                {t("settings.about.version.generalLink")}
              </TextLink>
            ),
          })}
        </p>
      </StateLine>
    );
  }
  if (info.update_state === "index_behind") {
    const version = info.announced_version ?? info.published_version ?? info.latest_version;
    if (version) return <IndexBehind info={info} version={version} />;
  }
  if (info.update_state === "on_the_way" && info.published_version) {
    return (
      <StateLine icon={<CircleDashed aria-hidden="true" className={`${icon} text-warn`} />} title={t("settings.about.version.onTheWay", { version: info.published_version })}>
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("settings.about.version.onTheWayDescription")}</p>
        <ReleaseNotesLink url={info.release_url} version={info.published_version} />
      </StateLine>
    );
  }
  if (info.has_update && info.latest_version) {
    return (
      <StateLine icon={<CircleArrowUp aria-hidden="true" className={icon} />} title={t("settings.about.version.available", { version: info.latest_version })}>
        {info.update_command ? <CommandHint label={t("settings.about.version.updateFromTerminal")} command={info.update_command} /> : null}
        <ReleaseNotesLink url={info.release_url} version={info.latest_version} />
      </StateLine>
    );
  }
  if (info.latest_version) {
    return <StateLine icon={<ICONS.success aria-hidden="true" className={`${icon} text-fg-muted`} />} title={t("settings.about.version.upToDate", { version: info.latest_version })} />;
  }
  return (
    <StateLine icon={<CircleHelp aria-hidden="true" className={`${icon} text-fg-muted`} />} title={t("settings.about.version.unknown")}>
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("settings.about.version.unknownHint")}</p>
    </StateLine>
  );
}

function VersionSection() {
  const t = useT();
  const version = useQuery(versionQuery());
  return (
    <Section
      title={t("settings.about.version.title")}
      description={t("settings.about.version.description")}
      loading={version.data === undefined && !version.isError}
      actions={
        <Button size="sm" icon={<RotateCw aria-hidden="true" />} loading={version.isFetching} onClick={() => void version.refetch()}>
          {t("settings.about.version.checkAgain")}
        </Button>
      }
    >
      <Card>
        <div className="flex min-w-0 flex-col gap-5 sm:flex-row sm:items-center sm:gap-10">
          <div className="flex shrink-0 flex-col gap-1">
            <span className="text-13 text-fg-muted">{t("settings.about.version.installedLabel")}</span>
            {version.data === undefined ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <span className="title text-24 tabular-nums">
                <Mono>{version.data.current_version}</Mono>
              </span>
            )}
          </div>
          <div aria-live="polite" className="flex min-w-0 flex-1 items-center sm:border-l sm:border-border sm:pl-10">
            {version.data !== undefined ? (
              <UpdateState info={version.data} />
            ) : version.isError ? (
              <ErrorBlock compact error={version.error} title={t("settings.about.version.checkFailed")} className="w-full" />
            ) : (
              <div aria-busy="true" className="flex flex-col gap-2">
                <span className="sr-only">{t("settings.about.version.checkingLabel")}</span>
                <Skeleton className="h-4 w-56" />
                <Skeleton className="h-3 w-40" />
              </div>
            )}
          </div>
        </div>
      </Card>
    </Section>
  );
}

function InstallationSection() {
  const t = useT();
  const { node } = useNode();
  const machine = useQuery(machineQuery());
  const config = useQuery(configQuery());
  const selfUpdate = useQuery(selfUpdateQuery());
  const publicUrl = config.data === undefined ? "" : readServerIdentity(config.data.config).publicUrl;
  // A node's console is reached through this one; its own address is the one it says it has.
  const address = node === null ? window.location.origin : publicUrl;
  return (
    <Section title={t("settings.about.installation.title")}>
      <Card padding="sm">
        <KeyValueList
          items={[
            { label: t("settings.about.installation.machine"), value: machine.data?.hostname ?? null },
            {
              label: t("settings.about.installation.method"),
              value: selfUpdate.data === undefined ? null : installMethodWords(t, selfUpdate.data.method),
              mono: false,
              copy: false,
            },
            { label: t("settings.about.installation.consoleAddress"), value: address === "" ? null : address },
            { label: t("settings.about.installation.configFile"), value: config.data?.path ?? null },
          ]}
          empty={t("settings.about.installation.notKnown")}
        />
      </Card>
    </Section>
  );
}

/** Where the project lives, its licence, and the name it had before. */
function ProjectSection() {
  const t = useT();
  return (
    <Section title={t("settings.about.project.title")}>
      <Notice title={t("settings.about.rename.title")}>
        <p className="max-w-measure text-pretty">
          {t.rich("settings.about.rename.body", { wasm: <Mono key="wasm">wasm</Mono>, noust: <Mono key="noust">noust</Mono> })}
        </p>
      </Notice>
      <Card padding="none">
        <ul className="flex flex-col divide-y divide-border">
          {links(t).map((link) => (
            <li key={link.href} className="flex min-w-0 flex-col gap-0.5 px-4 py-3 sm:flex-row sm:items-baseline sm:gap-3 sm:px-5">
              <ExternalLink href={link.href} className="text-13">
                {link.label}
              </ExternalLink>
              <span className="text-12 text-fg-muted">{link.description}</span>
            </li>
          ))}
          <li className="flex min-w-0 flex-col gap-1 px-4 py-3 sm:px-5">
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <ExternalLink href={`${REPOSITORY}/blob/main/LICENSE`} className="text-13">
                {t("settings.about.license.label")}
              </ExternalLink>
              <Mono tone="muted">AGPL-3.0-or-later</Mono>
            </span>
            <span className="max-w-measure text-12 text-pretty text-fg-muted">{t("settings.about.license.description")}</span>
          </li>
        </ul>
      </Card>
      <details className="group rounded-card border border-border">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 -outline-offset-2 hover:bg-surface-hover sm:px-5 [&::-webkit-details-marker]:hidden">
          <span className="text-13 font-medium text-fg">{t("settings.about.terminal.title")}</span>
          <ChevronDown aria-hidden="true" className="size-icon-sm text-fg-muted transition-transform duration-(--duration-fast) group-open:rotate-180" />
        </summary>
        <dl className="flex flex-col divide-y divide-border border-t border-border px-4 sm:px-5">
          {terminalRows(t).map((row) => (
            <div key={row.command} className="flex min-w-0 flex-col gap-1 py-2.5 sm:flex-row sm:items-center sm:gap-6">
              <dt className="text-13 text-fg-muted sm:w-64 sm:shrink-0">{row.task}</dt>
              <dd className="min-w-0">
                <CommandHint command={row.command} />
              </dd>
            </div>
          ))}
        </dl>
      </details>
    </Section>
  );
}

/** Settings > About: the version and whether a newer one can be installed, this installation, the project. */
export function AboutSettings() {
  const t = useT();
  useDocumentTitle(t("settings.about.documentTitle"), 1);
  return (
    <Sections>
      <VersionSection />
      <InstallationSection />
      <ProjectSection />
    </Sections>
  );
}
