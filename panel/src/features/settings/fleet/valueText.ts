import { useT } from "../../../i18n";
import { summarize } from "./summary";

type Value = Record<string, unknown>;

/**
 * The line a row says for the value of a setting of this kind. Most kinds say theirs in names and numbers
 * (`summarize`); the policy of a server is four facts of different kinds and is said in words of the console's own.
 */
export function useValueText(resource: string): (value: Value) => string {
  const t = useT();
  return (value) => {
    if (resource !== "policy") return summarize(resource, value);
    const email = typeof value["ssl_email"] === "string" ? value["ssl_email"] : "";
    return t("fleetSettings.policy.summary", {
      updates: value["updates_check"] === false ? t("fleetSettings.policy.off") : t("fleetSettings.policy.on"),
      email: email === "" ? t("fleetSettings.policy.noEmail") : email,
      keep: typeof value["backup_max_per_app"] === "number" ? value["backup_max_per_app"] : 0,
      minutes: typeof value["delivery_max_minutes"] === "number" ? value["delivery_max_minutes"] : 0,
    });
  };
}
