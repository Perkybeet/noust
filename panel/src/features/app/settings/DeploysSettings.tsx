import { useQuery } from "@tanstack/react-query";

import { zeroDowntimeQuery } from "../../../api/queries/zeroDowntime";
import { CommandHint } from "../../../components/page/CommandHint";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { hasUnit } from "../../apps/AppRowActions";
import { useSaveBar } from "./formParts";
import { InstantRollbackCard, useRetention } from "./InstantRollback";
import { useSettingsApp, useSubsectionTitle } from "./settingsApp";
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
  const bar = useSaveBar([releases ? retention.part : null, unit ? check.part : null, drain.part], t("appSettings.deploys.notSaved", { domain }));
  const hasFields = releases || unit;

  return (
    <>
      <Section title={t("appSettings.deploys.title")} description={t("appSettings.deploys.description")}>
        <InstantRollbackCard app={app} retention={retention} />
        {unit ? <StartupCheckCard app={app} check={check} /> : <StaticStartupNote />}
        {unit ? <ZeroDowntimeCard app={app} status={zeroDowntime} drain={drain} /> : null}
        {isComposeStack(app) ? <BackupBeforeUpdateCard app={app} /> : null}
        <CommandHint
          command={releases ? `noust releases list ${domain}` : `noust app migrate ${domain}`}
          label={t("appSettings.fromTerminal")}
        />
      </Section>
      {hasFields ? <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} /> : null}
    </>
  );
}
