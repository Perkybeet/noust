import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleArrowUp, CircleDashed, CircleHelp, RotateCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import { configQuery } from "../../api/queries/config";
import { isJobFinished, useFollowedJob } from "../../api/queries/jobs";
import { machineQuery, systemKeys, versionQuery } from "../../api/queries/system";
import { useDocumentTitle } from "../../app/documentTitle";
import { autoUpdateQuery } from "../../api/queries/autoUpdate";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section, Sections } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Disclosure } from "../../components/ui/Disclosure";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { ICONS } from "../../components/ui/icons";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { isHttpUrl } from "../../lib/url";
import { useNode } from "../../nodes/useNode";
import { reportActionError } from "../apps/useAppActions";
import { AutoUpdateSection } from "./AutoUpdateSection";
import { installMethodWords, readServerIdentity, selfUpdateQuery } from "./GeneralSettings.model";
import { InstallCheck } from "./InstallCheck";
import { SupportReport } from "./SupportReport";
import { UpdateNotes, WhatsNew } from "./WhatsNew";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

type UpdateInfo = ResponseOf<"/api/system/version", "get">;

const REPOSITORY = "https://github.com/Perkybeet/noust";

function links(t: T): readonly { label: string; href: string; description: string }[] {
  return [
    { label: t("settings.about.links.documentation.label"), href: `${REPOSITORY}#readme`, description: t("settings.about.links.documentation.description") },
    { label: t("settings.about.links.releaseNotes.label"), href: `${REPOSITORY}/releases`, description: t("settings.about.links.releaseNotes.description") },
    { label: t("settings.about.links.reportProblem.label"), href: `${REPOSITORY}/issues`, description: t("settings.about.links.reportProblem.description") },
    { label: t("settings.about.links.getHelp.label"), href: `${REPOSITORY}/blob/main/docs/SUPPORT.md`, description: t("settings.about.links.getHelp.description") },
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
        <Button size="sm" variant="primary" icon={<OBJECT_ICONS.update aria-hidden="true" />} loading={running} onClick={() => refresh.mutate()}>
          {running ? t("settings.about.version.refreshingIndex") : t("settings.about.version.refreshIndex")}
        </Button>
        <ReleaseNotesLink url={info.release_url} version={version} />
      </div>
      {failed ? <ErrorBlock live compact error={{ detail: job.error ?? "" }} title={t("settings.about.version.refreshFailed")} /> : null}
      <UpdateNotes version={version} />
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
        <UpdateNotes version={info.published_version} />
      </StateLine>
    );
  }
  if (info.has_update && info.latest_version) {
    return (
      <StateLine icon={<CircleArrowUp aria-hidden="true" className={icon} />} title={t("settings.about.version.available", { version: info.latest_version })}>
        {info.update_command ? <CommandHint label={t("settings.about.version.updateFromTerminal")} command={info.update_command} /> : null}
        <ReleaseNotesLink url={info.release_url} version={info.latest_version} />
        <UpdateNotes version={info.latest_version} />
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

/**
 * A release newer than the running one fixes a vulnerability: said apart from the update, which
 * is still offered below with its command. The advisory link is only a link when it is http(s).
 */
function SecurityRelease({ info }: { info: UpdateInfo }): ReactNode {
  const t = useT();
  if (!info.security_update || !info.security_version) return null;
  const version = info.security_version;
  return (
    <StateLine icon={<ICONS.warning aria-hidden="true" className="size-icon-md shrink-0 text-warn" />} title={t("settings.about.version.securityRelease", { version })}>
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("settings.about.version.securityReleaseDescription")}</p>
      {info.advisory_url && isHttpUrl(info.advisory_url) ? (
        <ExternalLink href={info.advisory_url}>{t("settings.about.version.whatItFixes", { version })}</ExternalLink>
      ) : null}
    </StateLine>
  );
}

function VersionSection() {
  const t = useT();
  const version = useQuery(versionQuery());
  return (
    <Section
      level={3}
      title={t("settings.about.version.title")}
      loading={version.isPending}
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
              <div className="flex min-w-0 flex-col gap-5">
                <SecurityRelease info={version.data} />
                <UpdateState info={version.data} />
              </div>
            ) : version.isError ? (
              <ErrorBlock compact error={version.error} title={t("settings.about.version.checkFailed")} className="w-full" />
            ) : (
              <LoadingRegion label={t("settings.about.version.checkingLabel")} className="flex flex-col gap-2">
                <Skeleton className="h-4 w-56" />
                <Skeleton className="h-3 w-40" />
              </LoadingRegion>
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
    <Section level={3} title={t("settings.about.installation.title")}>
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

/**
 * The notices the AGPL-3.0 asks an interactive interface to display (section 5, d): who holds the
 * copyright, that the licence lets anyone use, change and share the work, that there is no
 * warranty, where to read the licence, and (section 13) where the source of this version is.
 */
function LegalNotice() {
  const t = useT();
  return (
    <Card level={4} title={t("settings.about.legal.title")} padding="sm">
      <div className="flex max-w-measure min-w-0 flex-col gap-2 text-13 text-fg-muted">
        <p className="font-medium text-fg">{t("settings.about.legal.copyright")}</p>
        <p>
          {t.rich("settings.about.legal.license", {
            license: (
              <ExternalLink key="license" inline href={`${REPOSITORY}/blob/main/LICENSE`}>
                {t("settings.about.legal.licenseName")}
              </ExternalLink>
            ),
            id: <Mono key="id">AGPL-3.0-or-later</Mono>,
          })}
        </p>
        <p>{t("settings.about.legal.warranty")}</p>
        <p>
          {t.rich("settings.about.legal.source", {
            repository: (
              <ExternalLink key="repository" inline href={REPOSITORY}>
                {t("settings.about.legal.repositoryName")}
              </ExternalLink>
            ),
          })}
        </p>
      </div>
    </Card>
  );
}

/** Where the project lives and its legal notices. */
function ProjectSection() {
  const t = useT();
  return (
    <Section level={3} title={t("settings.about.project.title")}>
      <Card padding="sm">
        <ul className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
          {links(t).map((link) => (
            <li key={link.href} className="flex min-w-0 flex-col gap-0.5">
              <ExternalLink href={link.href} className="text-13">
                {link.label}
              </ExternalLink>
              <span className="text-12 text-fg-muted">{link.description}</span>
            </li>
          ))}
        </ul>
      </Card>
      <LegalNotice />
      <Disclosure variant="card" label={t("settings.about.terminal.title")}>
        <dl className="flex flex-col divide-y divide-border">
          {terminalRows(t).map((row) => (
            <div key={row.command} className="flex min-w-0 flex-col gap-1 py-2.5 sm:flex-row sm:items-center sm:gap-6">
              <dt className="text-13 text-fg-muted sm:w-64 sm:shrink-0">{row.task}</dt>
              <dd className="min-w-0">
                <CommandHint command={row.command} />
              </dd>
            </div>
          ))}
        </dl>
      </Disclosure>
    </Section>
  );
}

/**
 * The page waits for the two answers that decide the height of what is on screen. The update state
 * is one line or a paragraph and a disclosure, the automatic updates are a sentence or a sentence
 * and six facts, and each lands on its own: drawn as they arrived, each pushed every section under
 * it, and the page scored twice what the layout gate allows when the server had them on. Both are
 * read once (the sections below find them in the cache), and a failed one counts as read, for its
 * section to say so.
 */
function useAboutRead(): boolean {
  const version = useQuery(versionQuery());
  const automatic = useQuery(autoUpdateQuery());
  // Once, and for good: a section that mounts on a failed read asks again, which makes the query
  // pending for a moment, and a page that went back to its skeleton for that would unmount the
  // section and mount it again, asking again, for as long as the answer is a refusal.
  const [read, setRead] = useState(false);
  const answered = !version.isPending && !automatic.isPending;
  if (answered && !read) setRead(true);
  return read || answered;
}

function AboutSkeleton() {
  const t = useT();
  return (
    <LoadingRegion label={t("settings.about.loading")} className="flex flex-col gap-8">
      <div className="flex flex-col gap-4">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-40 w-full rounded-card" />
      </div>
      <div className="flex flex-col gap-4">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-52 w-full rounded-card" />
      </div>
    </LoadingRegion>
  );
}

/** Settings > About, in three sections: the version and its updates, this installation and its support, and what is new. */
export function AboutSettings() {
  const t = useT();
  useDocumentTitle(t("settings.about.documentTitle"), 1);
  const read = useAboutRead();
  if (!read) return <AboutSkeleton />;
  return (
    <Sections>
      <Section title={t("settings.about.groups.updates.title")} description={t("settings.about.groups.updates.description")}>
        <div className="flex min-w-0 flex-col gap-6">
          <VersionSection />
          <AutoUpdateSection />
        </div>
      </Section>
      <Section title={t("settings.about.groups.support.title")} description={t("settings.about.groups.support.description")}>
        <div className="flex min-w-0 flex-col gap-6">
          <InstallationSection />
          <InstallCheck />
          <SupportReport />
          <ProjectSection />
        </div>
      </Section>
      <WhatsNew />
    </Sections>
  );
}
