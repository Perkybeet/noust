import { useQuery } from "@tanstack/react-query";

import { configQuery, patchConfig } from "../../../api/queries/config";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { SaveBar } from "../../../components/page/SaveBar";
import { Sections } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { useSaveBar } from "../../app/settings/formParts";
import { useRefreshConfig } from "../SettingsForm";
import { useSettingsForm } from "../useSettingsForm";
import { RuleList } from "./RuleList";
import { CHECK_CONFIG_KEYS, CHECK_KEYS, CHECK_SETTINGS, Thresholds, checkPath, checkValue, readChecks } from "./Thresholds";

/** The monitor's built-in checks as one form, saved from the page's save bar. */
function Checks() {
  const t = useT();
  const refresh = useRefreshConfig();
  const config = useQuery(configQuery());
  const server = config.data === undefined ? undefined : readChecks(config.data.config);
  const form = useSettingsForm<Record<string, string>>({
    server,
    names: CHECK_KEYS,
    configKeys: CHECK_CONFIG_KEYS,
    save: async (values) => {
      // Only what changed is written: a setting the operator never touched stays whatever the file says.
      for (const setting of CHECK_SETTINGS) {
        if (server !== undefined && values[setting.key] !== server[setting.key]) await patchConfig(checkPath(setting), checkValue(values[setting.key] ?? ""));
      }
      await refresh();
    },
  });
  const bar = useSaveBar([form.part], t("alerts.notSaved"));
  return (
    <>
      <Thresholds form={form} />
      <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />
    </>
  );
}

/**
 * Settings > Alerts: the rules on the metrics Noust records and which of them are firing, then
 * the limits of the checks the monitor makes without a rule.
 */
export function AlertSettings() {
  const t = useT();
  useDocumentTitle(t("alerts.documentTitle"), 1);
  return (
    <Sections>
      <RuleList />
      <Checks />
      <CommandHint command="noust alert list" label={t("alerts.fromTerminal")} />
    </Sections>
  );
}
