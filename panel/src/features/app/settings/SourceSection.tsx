import type { App } from "../../../api/queries/apps";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { Section } from "../../../components/page/Section";
import { appStatus } from "../../../components/page/status";
import { useT } from "../../../i18n";
import { sourceLink } from "../SourceLink";
import { PANEL } from "./panel";

/**
 * What the app is and how it runs: the type, the repository and branch it was deployed from,
 * the build and start commands its deployer runs, and where and as what it runs.
 */
export function SourceSection({ app }: { app: App }) {
  const t = useT();
  const staticSite = appStatus(app.status).state === "static";
  const argv = app.build_command ?? [];
  const noCommand = t("appSettings.source.noCommand");
  const buildCommand = argv.length > 0 ? argv.join(" ") : noCommand;
  const items: KeyValueItem[] = [
    { label: t("appSettings.source.type"), value: app.app_type ?? null },
    ...(staticSite ? [] : [{ label: t("appSettings.source.port"), value: app.port ?? null }]),
    { label: t("appSettings.source.source"), value: sourceLink(app.source ?? null), mono: true, copy: app.source ?? false },
    { label: t("appSettings.source.branch"), value: app.branch ?? null },
    { label: t("appSettings.source.buildCommand"), value: buildCommand, mono: buildCommand !== noCommand, copy: buildCommand === noCommand ? false : buildCommand },
    ...(staticSite ? [] : [{ label: t("appSettings.source.startCommand"), value: app.start_command ?? null }]),
    { label: t("appSettings.source.directory"), value: app.path ?? null },
    {
      label: t("appSettings.source.layout"),
      value: app.layout === "releases" ? t("appSettings.source.layoutReleases") : t("appSettings.source.layoutInPlace"),
      mono: false,
      copy: false,
      hint: app.layout === "releases" ? t("appSettings.source.layoutReleasesHint") : t("appSettings.source.layoutInPlaceHint"),
    },
    ...(staticSite
      ? [{ label: t("appSettings.source.servedBy"), value: t("appSettings.source.servedByStatic"), mono: false, copy: false as const }]
      : [
          { label: t("appSettings.source.unit"), value: app.unit ?? null },
          { label: t("appSettings.source.runsAs"), value: app.run_as ?? null },
          { label: t("appSettings.source.startsAtBoot"), value: app.enabled ? t("appSettings.source.yes") : t("appSettings.source.no"), mono: false, copy: false as const },
        ]),
  ];

  return (
    <Section title={t("appSettings.source.title")} description={t("appSettings.source.description")}>
      <div className={`${PANEL} px-4 py-1`}>
        <KeyValueList empty={t("appSettings.source.notRecorded")} items={items} />
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <p className="text-13 text-pretty text-fg-muted">{t("appSettings.source.branchNote")}</p>
        <CommandHint command={`noust update ${app.domain} --branch <branch>`} label={t("appSettings.fromTerminal")} />
      </div>
    </Section>
  );
}
