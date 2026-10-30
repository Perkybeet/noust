import { useQuery } from "@tanstack/react-query";

import { configQuery } from "../../api/queries/config";
import { Notice } from "../../components/ui/Notice";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { readNotificationSettings } from "../settings/notifications";

/**
 * Whether anybody will hear an alert: with notifications off a rule fires and nothing says so outside
 * the console, which is the one thing a rule is for. It says it once above the rules, with the way to
 * turn them on, and draws nothing while they are on (or while the settings are still being read).
 */
export function AlertsReach() {
  const t = useT();
  const config = useQuery(configQuery());
  if (config.data === undefined || readNotificationSettings(config.data.config).enabled) return null;
  return (
    <Notice
      tone="warning"
      title={t("alerts.reach.title")}
      action={
        <TextLink to="/settings/notifications" size="ui">
          {t("alerts.reach.action")}
        </TextLink>
      }
    >
      {t("alerts.reach.body")}
    </Notice>
  );
}
