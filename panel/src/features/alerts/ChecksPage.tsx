import { useQuery } from "@tanstack/react-query";

import { configQuery, patchConfig } from "../../api/queries/config";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { SaveBar } from "../../components/page/SaveBar";
import { QueryState } from "../../components/page/QueryState";
import { Sections } from "../../components/page/Section";
import { SkeletonCard } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { useSaveBar } from "../app/settings/formParts";
import { useRefreshConfig } from "../settings/SettingsForm";
import { useSettingsForm } from "../settings/useSettingsForm";
import { CHECK_CONFIG_KEYS, CHECK_KEYS, CHECK_SETTINGS, Thresholds, checkPath, checkValue, readChecks } from "../settings/alerts/Thresholds";

/**
 * Alerts > Checks: the limits of what the monitor announces without a rule of yours, as one form saved
 * from the page's save bar. A rule says what matters to you; these say when Noust speaks up by itself.
 */
export function ChecksPage() {
  const t = useT();
  useDocumentTitle(t("alerts.checksPage.documentTitle"), 1);
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
    <Sections>
      <QueryState
        query={config}
        sentences={{ loading: t("alerts.checksPage.loading"), failed: t("alerts.checksPage.loadFailed"), refreshFailed: t("alerts.checksPage.refreshFailed") }}
        skeleton={<SkeletonCard lines={6} />}
      >
        {() => <Thresholds form={form} />}
      </QueryState>
      <SaveBar changes={bar.changes} saving={bar.saving} onSave={bar.onSave} onDiscard={bar.onDiscard} />
      <CommandHint command="noust config get monitor.disk_threshold" label={t("common.words.sameFromTerminal")} />
    </Sections>
  );
}
