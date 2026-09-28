import { useQuery } from "@tanstack/react-query";
import { CircleArrowUp, CircleCheck, CircleDashed, CircleHelp, ExternalLink, RotateCw } from "lucide-react";
import type { ReactNode } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { configQuery } from "../../api/queries/config";
import { versionQuery } from "../../api/queries/system";
import type { ResponseOf } from "../../api/client";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section, Sections } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { isHttpUrl } from "../../lib/url";

// published_version and update_state are new in 2.3; the intersection keeps this compiling
// against a schema generated before them, and is a no-op once the schema has them.
type UpdateInfo = ResponseOf<"/api/system/version", "get"> & {
  published_version?: string | null;
  update_state?: "up_to_date" | "update_available" | "on_the_way" | null;
};

const REPOSITORY = "https://github.com/Perkybeet/wasm";

function links(t: T): readonly { label: string; href: string; description: string }[] {
  return [
    { label: t("settings.about.links.documentation.label"), href: `${REPOSITORY}#readme`, description: t("settings.about.links.documentation.description") },
    { label: t("settings.about.links.releaseNotes.label"), href: `${REPOSITORY}/releases`, description: t("settings.about.links.releaseNotes.description") },
    { label: t("settings.about.links.reportProblem.label"), href: `${REPOSITORY}/issues`, description: t("settings.about.links.reportProblem.description") },
    { label: t("settings.about.links.license.label"), href: `${REPOSITORY}/blob/main/LICENSE`, description: t("settings.about.links.license.description") },
  ];
}

/** What the console does, and the command that does it from a terminal. */
function terminalRows(t: T): readonly { task: string; command: string }[] {
  return [
    { task: t("settings.about.terminal.showConfig"), command: "wasm config show" },
    { task: t("settings.about.terminal.readSetting"), command: "wasm config get backup.max_per_app" },
    { task: t("settings.about.terminal.changeSetting"), command: "wasm config set ssl.email ops@example.com" },
    { task: t("settings.about.terminal.configPath"), command: "wasm config path" },
    { task: t("settings.about.terminal.checkMachine"), command: "wasm health" },
    { task: t("settings.about.terminal.consoleStatus"), command: "wasm web status" },
    { task: t("settings.about.terminal.restartConsole"), command: "wasm web restart" },
    { task: t("settings.about.terminal.newToken"), command: "wasm web token --new" },
    { task: t("settings.about.terminal.installedVersion"), command: "wasm --version" },
  ];
}

function ReleaseNotesLink({ t, url, version }: { t: T; url: string | null | undefined; version: string }): ReactNode {
  if (!url || !isHttpUrl(url)) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="flex w-fit items-center gap-1 rounded-[4px] text-13 font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
    >
      {t("settings.about.version.whatIsNew", { version })}
      <ExternalLink aria-hidden="true" className="size-3.5" />
      <span className="sr-only">{t("settings.shared.opensInNewTab")}</span>
    </a>
  );
}

function UpdateState({ t, info }: { t: T; info: UpdateInfo }): ReactNode {
  if (info.update_state === "on_the_way" && info.published_version) {
    // Published on GitHub, but the package this server upgrades from is still being built:
    // offering the command now would send the operator to an upgrade that installs nothing.
    return (
      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <p className="flex items-center gap-2 text-14 font-medium text-fg">
            <CircleDashed aria-hidden="true" className="size-4 shrink-0 text-warn" />
            {t("settings.about.version.onTheWay", { version: info.published_version })}
          </p>
          <p className="text-13 text-fg-muted">{t("settings.about.version.onTheWayDescription")}</p>
        </div>
        <ReleaseNotesLink t={t} url={info.release_url} version={info.published_version} />
      </div>
    );
  }
  if (info.has_update && info.latest_version) {
    return (
      <div className="flex min-w-0 flex-col gap-3">
        <p className="flex items-center gap-2 text-14 font-medium text-fg">
          <CircleArrowUp aria-hidden="true" className="size-4 shrink-0" />
          {t("settings.about.version.available", { version: info.latest_version })}
        </p>
        {info.update_command ? <CommandHint label={t("settings.about.version.updateFromTerminal")} command={info.update_command} /> : null}
        <ReleaseNotesLink t={t} url={info.release_url} version={info.latest_version} />
      </div>
    );
  }
  if (info.latest_version) {
    return (
      <p className="flex items-center gap-2 text-14 text-fg">
        <CircleCheck aria-hidden="true" className="size-4 shrink-0 text-ok" />
        {t("settings.about.version.upToDate", { version: info.latest_version })}
      </p>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <p className="flex items-center gap-2 text-14 text-fg">
        <CircleHelp aria-hidden="true" className="size-4 shrink-0 text-idle" />
        {t("settings.about.version.unknown")}
      </p>
      <p className="text-13 text-fg-muted">{t("settings.about.version.unknownHint")}</p>
    </div>
  );
}

function VersionSection() {
  const t = useT();
  const version = useQuery(versionQuery());
  const { data: session } = useQuery(sessionQuery());
  const installed = version.data?.current_version ?? session?.version;
  return (
    <Section
      title={t("settings.about.version.title")}
      description={t("settings.about.version.description")}
      actions={
        <Button
          size="sm"
          icon={<RotateCw aria-hidden="true" />}
          loading={version.isFetching}
          onClick={() => void version.refetch()}
        >
          {t("settings.about.version.checkAgain")}
        </Button>
      }
    >
      <div className="grid gap-x-10 gap-y-5 rounded-card border border-border bg-surface p-5 shadow-raised sm:grid-cols-[auto_minmax(0,1fr)]">
        <div className="flex flex-col gap-1">
          <span className="text-13 text-fg-muted">{t("settings.about.version.installedLabel")}</span>
          {installed === undefined ? (
            <Skeleton className="h-8 w-24" />
          ) : (
            <span translate="no" className="mono text-24 leading-8 text-fg tabular-nums">
              {installed}
            </span>
          )}
        </div>
        <div aria-live="polite" className="flex min-w-0 items-center sm:border-l sm:border-border sm:pl-10">
          {version.data !== undefined ? (
            <UpdateState t={t} info={version.data} />
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
    </Section>
  );
}

function InstallationSection() {
  const t = useT();
  const { data: session } = useQuery(sessionQuery());
  const config = useQuery(configQuery());
  return (
    <Section title={t("settings.about.installation.title")}>
      <div className="rounded-card border border-border bg-surface px-5 py-2 shadow-raised">
        <KeyValueList
          items={[
            { label: t("settings.about.installation.machine"), value: session?.hostname ?? "" },
            { label: t("settings.about.installation.consoleAddress"), value: window.location.origin },
            { label: t("settings.about.installation.configFile"), value: config.data?.path ?? "" },
          ]}
          empty={t("settings.about.installation.loading")}
        />
      </div>
    </Section>
  );
}

function LinksSection() {
  const t = useT();
  return (
    <Section title={t("settings.about.links.title")}>
      <ul className="grid gap-3 sm:grid-cols-2">
        {links(t).map((link) => (
          <li key={link.href}>
            <a
              href={link.href}
              target="_blank"
              rel="noreferrer"
              className="group flex h-full flex-col gap-0.5 rounded-card border border-border bg-surface px-4 py-3 shadow-raised hover:bg-surface-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            >
              <span className="flex items-center gap-1.5 text-14 font-medium text-fg">
                {link.label}
                <ExternalLink aria-hidden="true" className="size-3.5 text-fg-faint group-hover:text-fg-muted" />
                <span className="sr-only">{t("settings.shared.opensInNewTab")}</span>
              </span>
              <span className="text-13 text-fg-muted">{link.description}</span>
            </a>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function TerminalSection() {
  const t = useT();
  return (
    <Section title={t("settings.about.terminal.title")} description={t("settings.about.terminal.description")}>
      <dl className="flex flex-col divide-y divide-border rounded-card border border-border bg-surface px-5 py-1 shadow-raised">
        {terminalRows(t).map((row) => (
          <div key={row.command} className="grid min-w-0 items-center gap-x-6 gap-y-1 py-2.5 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <dt className="text-13 text-fg-muted">{row.task}</dt>
            <dd className="min-w-0">
              <CommandHint command={row.command} />
            </dd>
          </div>
        ))}
      </dl>
    </Section>
  );
}

/** Settings > About: the version, whether a newer one exists, where to read more. */
export function AboutSettings() {
  const t = useT();
  useDocumentTitle(t("settings.about.documentTitle"), 1);
  return (
    <Sections>
      <VersionSection />
      <InstallationSection />
      <TerminalSection />
      <LinksSection />
    </Sections>
  );
}
