import { CommandHint } from "../../../components/page/CommandHint";
import { Section } from "../../../components/page/Section";
import { SettingsGroup } from "../../../components/page/SettingsGroup";
import { useT } from "../../../i18n";
import { previewParentOf } from "../../apps/data";
import { FreezeSetting } from "../../freeze/FreezeCard";
import { useOrganization } from "../../teams/useOrganization";
import { AutomaticDeploysRows } from "./AutomaticDeploys";
import { BackupRequiredRow } from "./BackupRequired";
import { RolloutLimitsSetting } from "./RolloutLimits";
import { useSettingsApp, useSubsectionTitle } from "./settingsApp";
import { BackupBeforeUpdateRow, isComposeStack } from "./StackSettings";
import { UpdateScheduleSetting } from "./UpdateSchedule";

/**
 * Automation: what deploys the app without a person asking, and what holds, plans and protects its
 * updates. Every row applies the moment it is changed (a switch) or opens a drawer or a dialog with
 * its own footer (a freeze, a schedule, the deploy limits), so the page has no save bar: a
 * subsection saves in one way (docs/DESIGN.md, FM-1).
 */
export function AutomationSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("appSettings.nav.automation", domain);
  const org = useOrganization();
  const isPreview = previewParentOf(app) !== null;

  return (
    <Section title={t("appSettings.automation.title")} description={t("appSettings.automation.description")}>
      <SettingsGroup title={t("appSettings.automation.deploysGroup")}>
        <AutomaticDeploysRows domain={domain} />
      </SettingsGroup>
      <SettingsGroup title={t("appSettings.automation.backupsGroup")}>
        <BackupRequiredRow domain={domain} />
        {isComposeStack(app) ? <BackupBeforeUpdateRow app={app} /> : null}
      </SettingsGroup>
      <SettingsGroup title={t("appSettings.automation.planGroup")}>
        <FreezeSetting domain={domain} canDeploy={org.can("apps.deploy", app.team ?? null)} />
        {isPreview ? null : <UpdateScheduleSetting domain={domain} />}
        <RolloutLimitsSetting domain={domain} />
      </SettingsGroup>
      <CommandHint command={`noust deploy schedule show ${domain}`} label={t("appSettings.fromTerminal")} />
    </Section>
  );
}
