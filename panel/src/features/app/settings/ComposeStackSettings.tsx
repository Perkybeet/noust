import { useT } from "../../../i18n";
import { ComposeSettingsCard, useComposeOptions } from "./ComposeSettings";
import { useSaveBar } from "./formParts";
import { RegistryLoginsCard, useRegistryChoice, usesRegistries } from "./RegistryLogins";
import { useSettingsApp, useSubsectionIcon, useSubsectionTitle } from "./settingsApp";
import { StackNetworksCard } from "./StackNetworks";
import { PartSaveBar, PartSection } from "./saveHost";

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
      <PartSection title={t("composeSettings.page.title")} {...headerIcon} description={t("composeSettings.page.description")}>
        <ComposeSettingsCard domain={domain} options={options} />
        <StackNetworksCard domain={domain} />
        {useRegistries ? <RegistryLoginsCard choice={registries} /> : null}
      </PartSection>
      <PartSaveBar bar={bar} />
    </>
  );
}
