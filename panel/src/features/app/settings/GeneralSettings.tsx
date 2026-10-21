import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { Section } from "../../../components/page/Section";
import { Card } from "../../../components/ui/Card";
import { Button } from "../../../components/ui/Button";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { hasUnit } from "../../apps/AppRowActions";
import { useTypeName } from "../../apps/data";
import { useMoveApp } from "../../teams/MoveAppDialog";
import { ownerOf } from "../../teams/organization";
import { useOrganization } from "../../teams/useOrganization";
import { sourceLink } from "../SourceLink";
import { AppTagList, AppTagsActions } from "./AppTags";
import { BranchActions } from "./BranchPin";
import { FollowTagsActions } from "./FollowTags";
import { IdentityCard } from "./Identity";
import { useSettingsApp, useSubsectionIcon, useSubsectionTitle } from "./settingsApp";
import { WorkerCard, isComposeStack } from "./StackSettings";

/**
 * General: what the app is and how it runs, as facts. They are set when the app is deployed;
 * the one changed from here is the branch it deploys from, pinned or unpinned beside it (how
 * deploys work has its own subsection).
 */
export function GeneralSettings() {
  const t = useT();
  const app = useSettingsApp();
  const typeName = useTypeName();
  const org = useOrganization();
  const move = useMoveApp(app);
  useSubsectionTitle("appSettings.nav.general", app.domain);
  const headerIcon = useSubsectionIcon("appSettings.nav.general");
  const unit = hasUnit(app);
  const argv = app.build_command ?? [];
  const buildCommand = argv.length > 0 ? argv.join(" ") : null;
  const releases = app.layout === "releases";
  // Only a git source has branches; a local directory has nothing to pin.
  const git = Boolean(app.source) && !(app.source ?? "").startsWith("/");

  const fact = (label: string, value: KeyValueItem["value"], extra: Omit<KeyValueItem, "label" | "value"> = {}): KeyValueItem => ({ label, value, ...extra });
  const prose = { mono: false, copy: false } as const;
  // Where it lives, only where that tells one place from another: a server nobody organised has one.
  const place = ownerOf(app);
  const where =
    org.available && place.team !== null && place.project !== null && place.environment !== null && (org.teamsMatter || org.scope.where || move.available)
      ? [
          fact(
            t("teams.move.fact"),
            <span className="flex min-w-0 flex-wrap items-center gap-x-2">
              <span>
                {[org.labels.team(place.team), org.labels.project(place.team, place.project), org.labels.environment(place.team, place.project, place.environment)].join(" / ")}
              </span>
              {move.available ? (
                <Button size="sm" variant="ghost" onClick={move.open}>
                  {t("teams.move.action")}
                </Button>
              ) : null}
            </span>,
            { ...prose, copy: false },
          ),
        ]
      : [];
  const items: KeyValueItem[] = [
    fact(t("appSettings.general.type"), typeName(app.app_type) ?? app.app_type ?? null, prose),
    ...where,
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
    ...(git
      ? [
          fact(
            t("appSettings.general.tags"),
            <span className="flex min-w-0 flex-wrap items-center gap-x-2">
              {app.follow_tags ? <Mono>{app.follow_tags}</Mono> : <span>{t("appSettings.general.noTags")}</span>}
              <FollowTagsActions app={app} />
            </span>,
            { ...prose, copy: app.follow_tags ?? false, hint: app.follow_tags ? t("appSettings.general.tagsHint") : t("appSettings.general.noTagsHint") },
          ),
        ]
      : []),
    fact(
      t("appTags.row.label"),
      <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <AppTagList tags={app.tags ?? []} />
        {org.can("apps.manage", app.team ?? null) ? <AppTagsActions app={app} /> : null}
      </span>,
      { ...prose, copy: false, hint: t("appTags.row.hint") },
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
          // Whether it starts at boot is an on/off, told at a glance (item 56).
          fact(
            t("appSettings.general.startsAtBoot"),
            <StatusPill state={app.enabled ? "running" : "stopped"} label={app.enabled ? t("appSettings.general.yes") : t("appSettings.general.no")} size="sm" />,
            prose,
          ),
        ]
      : [fact(t("appSettings.general.servedBy"), t("appSettings.general.servedByStatic"), prose)]),
  ];

  return (
    <Section title={t("appSettings.general.title")} {...headerIcon} description={t("appSettings.general.description")}>
      <Card padding="none">
        <KeyValueList empty={t("appSettings.general.notRecorded")} items={items} className="px-5 py-1" />
      </Card>
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("appSettings.general.readOnlyNote")}</p>
      {isComposeStack(app) && app.port !== null && app.port !== undefined ? <WorkerCard app={app} /> : null}
      {unit ? <IdentityCard domain={app.domain} /> : null}
      <CommandHint command={`noust app branch ${app.domain} <branch>`} label={t("appSettings.fromTerminal")} />
      {move.dialog}
    </Section>
  );
}
