import { useQuery } from "@tanstack/react-query";

import { zeroDowntimeQuery } from "../../../api/queries/zeroDowntime";
import { CommandHint } from "../../../components/page/CommandHint";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { hasUnit } from "../../apps/AppRowActions";
import { previewParentOf } from "../../apps/data";
import { FreezeCard } from "../../freeze/FreezeCard";
import { useOrganization } from "../../teams/useOrganization";
import { useSaveBar } from "./formParts";
import { InstantRollbackCard, useRetention } from "./InstantRollback";
import { AutomaticDeploysCard } from "./AutomaticDeploys";
import { BackupRequiredCard } from "./BackupRequired";
import { RolloutLimitsCard, useRolloutLimits } from "./RolloutLimits";
import { UpdateScheduleCard } from "./UpdateSchedule";
import { useSettingsApp, useSubsectionTitle } from "./settingsApp";
import { ComposeSettingsCard } from "./ComposeSettings";
import { StackNetworksCard } from "./StackNetworks";
import { RegistryLoginsCard, usesRegistries } from "./RegistryLogins";
import { BackupBeforeUpdateCard, isComposeStack } from "./StackSettings";
import { StartupCheckCard, StaticStartupNote, useStartupCheck } from "./StartupCheck";
import { ZeroDowntimeCard, useDrain } from "./ZeroDowntime";

/**
 * Deploys: how a new version reaches production. Instant rollback (on, with how many versions
 * are kept; or off, with the guided way to turn it on), the startup check every new version
 * passes, zero-downtime deploys where the app can have them, and for a Compose stack the copy of
 * its databases each update takes first. The fields of all three are
 * one form, saved from the one save bar.
 */
export function DeploysSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("appSettings.nav.deploys", domain);
  const unit = hasUnit(app);
  const releases = app.layout === "releases";

  const retention = useRetention(app);
  const check = useStartupCheck(app);
  const zeroDowntime = useQuery({ ...zeroDowntimeQuery(domain), enabled: unit });
  const drain = useDrain(domain, zeroDowntime.data);
  const limits = useRolloutLimits(domain);
  const org = useOrganization();
  const isPreview = previewParentOf(app) !== null;
  const bar = useSaveBar([releases ? retention.part : null, unit ? check.part : null, drain.part, limits.part], t("appSettings.deploys.notSaved", { domain }));
  const hasFields = releases || unit || limits.part !== null;

  return (
    <>
      <Section title={t("appSettings.deploys.title")} description={t("appSettings.deploys.description")}>
        <InstantRollbackCard app={app} retention={retention} />
        {unit ? <StartupCheckCard app={app} check={check} /> : <StaticStartupNote />}
        {unit ? <ZeroDowntimeCard app={app} status={zeroDowntime} drain={drain} /> : null}
        {isComposeStack(app) ? <BackupBeforeUpdateCard app={app} /> : null}
        <BackupRequiredCard domain={domain} />
        <AutomaticDeploysCard domain={domain} />
        <FreezeCard domain={domain} canDeploy={org.can("apps.deploy", app.team ?? null)} title={t("rollout.freeze.title")} description={t("rollout.freeze.description")} />
        {isPreview ? null : <UpdateScheduleCard domain={domain} />}
        <RolloutLimitsCard limits={limits} />
        {isComposeStack(app) ? <ComposeSettingsCard domain={domain} /> : null}
        {isComposeStack(app) ? <StackNetworksCard domain={domain} /> : null}
        {usesRegistries(app) ? <RegistryLoginsCard domain={domain} /> : null}
        <CommandHint
          command={releases ? `noust releases list ${domain}` : `noust app migrate ${domain}`}
          label={t("appSettings.fromTerminal")}
        />
      </Section>
      {hasFields ? <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} /> : null}
    </>
  );
}
