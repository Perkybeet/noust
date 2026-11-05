import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { Card } from "../../../components/ui/Card";
import { Button } from "../../../components/ui/Button";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { hasUnit } from "../../apps/AppRowActions";
import { useMoveApp } from "../../teams/MoveAppDialog";
import { ownerOf } from "../../teams/organization";
import { useOrganization } from "../../teams/useOrganization";
import { AppTagList, AppTagsActions } from "./AppTags";
import { IdentityCard } from "./Identity";
import { PartSection } from "./saveHost";
import { useSettingsApp, useSubsectionIcon, useSubsectionTitle } from "./settingsApp";
import { WorkerCard, isComposeStack } from "./StackSettings";

/**
 * How the application runs, as facts: where it lives (its team, project and environment, and where it is moved
 * from), its tags, its folder, how its deploys work and the service that runs it with the account it runs as. The
 * type and the port are the header's, where the code comes from and how it is built are Code and build, and the
 * branch pinned or the tags followed are beside the source; what is changed from here is the move and the tags.
 */
export function GeneralSettings() {
  const t = useT();
  const app = useSettingsApp();
  const org = useOrganization();
  const move = useMoveApp(app);
  useSubsectionTitle("appSettings.nav.general", app.domain);
  const headerIcon = useSubsectionIcon("appSettings.nav.general");
  const unit = hasUnit(app);
  const releases = app.layout === "releases";

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
    ...where,
    fact(
      t("appTags.row.label"),
      <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <AppTagList tags={app.tags ?? []} />
        {org.can("apps.manage", app.team ?? null) ? <AppTagsActions app={app} /> : null}
      </span>,
      { ...prose, copy: false, hint: t("appTags.row.hint") },
    ),
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
            <StatusPill state={app.enabled ? "running" : "stopped"} label={app.enabled ? t("common.words.yes") : t("common.words.no")} size="sm" />,
            prose,
          ),
        ]
      : [fact(t("appSettings.general.servedBy"), t("appSettings.general.servedByStatic"), prose)]),
  ];

  return (
    <PartSection title={t("appSettings.general.title")} {...headerIcon} description={t("appSettings.general.description")}>
      <Card padding="none">
        <KeyValueList empty={t("appSettings.general.notRecorded")} items={items} className="px-5 py-1" />
      </Card>
      {isComposeStack(app) && app.port !== null && app.port !== undefined ? <WorkerCard app={app} /> : null}
      {unit ? <IdentityCard domain={app.domain} /> : null}
      {move.dialog}
    </PartSection>
  );
}
