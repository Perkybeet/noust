import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { Section } from "../../../components/page/Section";
import { Card } from "../../../components/ui/Card";
import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import { hasUnit } from "../../apps/AppRowActions";
import { useTypeName } from "../../apps/data";
import { sourceLink } from "../SourceLink";
import { BranchActions } from "./BranchPin";
import { useSettingsApp, useSubsectionTitle } from "./settingsApp";

/**
 * General: what the app is and how it runs, as facts. They are set when the app is deployed;
 * the one changed from here is the branch it deploys from, pinned or unpinned beside it (how
 * deploys work has its own subsection).
 */
export function GeneralSettings() {
  const t = useT();
  const app = useSettingsApp();
  const typeName = useTypeName();
  useSubsectionTitle("appSettings.nav.general", app.domain);
  const unit = hasUnit(app);
  const argv = app.build_command ?? [];
  const buildCommand = argv.length > 0 ? argv.join(" ") : null;
  const releases = app.layout === "releases";
  // Only a git source has branches; a local directory has nothing to pin.
  const git = Boolean(app.source) && !(app.source ?? "").startsWith("/");

  const fact = (label: string, value: KeyValueItem["value"], extra: Omit<KeyValueItem, "label" | "value"> = {}): KeyValueItem => ({ label, value, ...extra });
  const prose = { mono: false, copy: false } as const;
  const items: KeyValueItem[] = [
    fact(t("appSettings.general.type"), typeName(app.app_type) ?? app.app_type ?? null, prose),
    ...(unit ? [fact(t("appSettings.general.port"), app.port ?? null)] : []),
    fact(t("appSettings.general.source"), sourceLink(app.source ?? null), { mono: true, copy: app.source ?? false }),
    app.branch
      ? fact(
          t("appSettings.general.branch"),
          <span className="flex min-w-0 flex-wrap items-center gap-x-2">
            <Mono>{app.branch}</Mono>
            {git ? <BranchActions app={app} /> : null}
          </span>,
          { ...prose, copy: app.branch },
        )
      : fact(
          t("appSettings.general.branch"),
          <span className="flex min-w-0 flex-wrap items-center gap-x-2">
            <span>{t("appSettings.general.noBranch")}</span>
            {git ? <BranchActions app={app} /> : null}
          </span>,
          { ...prose, hint: t("appSettings.general.noBranchHint") },
        ),
    buildCommand !== null
      ? fact(t("appSettings.general.buildCommand"), buildCommand)
      : fact(t("appSettings.general.buildCommand"), t("appSettings.general.noCommand"), prose),
    ...(unit ? [fact(t("appSettings.general.startCommand"), app.start_command ?? null)] : []),
    fact(t("appSettings.general.directory"), app.path ?? null),
    fact(t("appSettings.general.deploysWork"), releases ? t("appSettings.general.layoutReleases") : t("appSettings.general.layoutInPlace"), {
      ...prose,
      hint: releases ? t("appSettings.general.layoutReleasesHint") : t("appSettings.general.layoutInPlaceHint"),
    }),
    ...(unit
      ? [
          fact(t("appSettings.general.service"), app.unit ?? null),
          fact(t("appSettings.general.runsAs"), app.run_as ?? null),
          fact(t("appSettings.general.startsAtBoot"), app.enabled ? t("appSettings.general.yes") : t("appSettings.general.no"), prose),
        ]
      : [fact(t("appSettings.general.servedBy"), t("appSettings.general.servedByStatic"), prose)]),
  ];

  return (
    <Section title={t("appSettings.general.title")} description={t("appSettings.general.description")}>
      <Card padding="none">
        <KeyValueList empty={t("appSettings.general.notRecorded")} items={items} className="px-5 py-1" />
      </Card>
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("appSettings.general.readOnlyNote")}</p>
      <CommandHint command={`noust app branch ${app.domain} <branch>`} label={t("appSettings.fromTerminal")} />
    </Section>
  );
}
