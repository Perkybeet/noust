import { useT } from "../../../i18n";
import { SecretManagers } from "../../variables/SecretManagers";
import { DeployKeysSettings } from "../DeployKeysSettings";
import { IntegrationsSettings } from "../IntegrationsSettings";
import { LogsSettings } from "../LogsSettings";
import { OwnByServer } from "./OwnByServer";

/** Settings > Integrations: the code hosts, each server's own, and on a central a table of the other servers' (item 166). */
export function IntegrationsByServer() {
  const t = useT();
  return (
    <OwnByServer section="integrations" label={t("nav.settingsTabs.integrations.label")} valueHeader={t("fleetSettings.own.value.integrations")}>
      <IntegrationsSettings />
    </OwnByServer>
  );
}

/** Settings > Log shipping, with the table of the other servers' under it on a central. */
export function LogShippingByServer() {
  const t = useT();
  return (
    <OwnByServer section="log-shipping" label={t("nav.settingsTabs.logShipping.label")} valueHeader={t("fleetSettings.own.value.logShipping")}>
      <LogsSettings />
    </OwnByServer>
  );
}

/** Settings > Secret managers, with the table of the other servers' under it on a central. */
export function SecretManagersByServer() {
  const t = useT();
  return (
    <OwnByServer section="secret-managers" label={t("variables.providers.nav.label")} valueHeader={t("fleetSettings.own.value.secretManagers")}>
      <SecretManagers />
    </OwnByServer>
  );
}

/** Settings > Deploy keys, with the table of the other servers' under it on a central. */
export function DeployKeysByServer() {
  const t = useT();
  return (
    <OwnByServer section="deploy-keys" label={t("connections.deployKeys.nav.label")} valueHeader={t("fleetSettings.own.value.deployKeys")}>
      <DeployKeysSettings />
    </OwnByServer>
  );
}
