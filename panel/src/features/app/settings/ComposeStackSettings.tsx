import { CommandHint } from "../../../components/page/CommandHint";
import { SaveBar } from "../../../components/page/SaveBar";
import { Section } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { ComposeSettingsCard, useComposeOptions } from "./ComposeSettings";
import { useSaveBar } from "./formParts";
import { RegistryLoginsCard, useRegistryChoice, usesRegistries } from "./RegistryLogins";
import { useSettingsApp, useSubsectionIcon, useSubsectionTitle } from "./settingsApp";
import { StackNetworksCard } from "./StackNetworks";

/**
 * Compose: what a Compose stack is built and started with (its file, profiles and the closed list of
 * arguments), the Docker networks it is on and the registry accounts it pulls with. The options and
 * the accounts are one form, saved from the one save bar; the networks are read from Docker.
 */
export function ComposeStackSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("appSettings.nav.compose", domain);
  const headerIcon = useSubsectionIcon("appSettings.nav.compose");
  const options = useComposeOptions(domain);
  const useRegistries = usesRegistries(app);
  const registries = useRegistryChoice(domain, useRegistries);
  const bar = useSaveBar([options.part, useRegistries ? registries.part : null], t("composeSettings.notSaved", { domain }));

  return (
    <>
      <Section title={t("composeSettings.page.title")} {...headerIcon} description={t("composeSettings.page.description")}>
        <ComposeSettingsCard domain={domain} options={options} />
        <StackNetworksCard domain={domain} />
        {useRegistries ? <RegistryLoginsCard choice={registries} /> : null}
        <CommandHint command={`noust app compose show ${domain}`} label={t("common.words.sameFromTerminal")} />
      </Section>
      <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />
    </>
  );
}
