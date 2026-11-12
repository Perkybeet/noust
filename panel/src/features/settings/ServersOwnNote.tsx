import { CommandHint } from "../../components/page/CommandHint";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { useHasFleet } from "../../nodes/servers";

/**
 * Where what each server holds of its own is managed, for the two things a server refuses a central on purpose
 * (`FLEET_REFUSED_PREFIXES`): its accounts and its API tokens. A central that was taken over could otherwise lock an
 * operator out of every server, so no table of servers with a value a row can be drawn for them: it would only say "manage
 * it on the server". One sentence says so, with the command that does it there. Nothing on a lone server, which has
 * nobody else's to point at.
 */
export function ServersOwnNote({ about }: { about: "accounts" | "tokens" }) {
  const t = useT();
  const hasFleet = useHasFleet();
  if (!hasFleet) return null;
  return (
    <Notice title={about === "accounts" ? t("nav.settingsGroups.ownAccountsTitle") : t("nav.settingsGroups.ownTokensTitle")}>
      <div className="flex flex-col gap-2">
        <p className="max-w-measure text-pretty">{t("nav.settingsGroups.ownNote")}</p>
        <CommandHint label={t("nav.settingsGroups.ownCommands")} command={about === "accounts" ? "noust user list" : "noust token list"} reason="otro-servidor" />
      </div>
    </Notice>
  );
}
