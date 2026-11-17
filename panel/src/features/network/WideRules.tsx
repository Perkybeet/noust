/**
 * The warning that opening a port "only to these addresses" does nothing while a rule of the firewall lets everyone in on it (item 139).
 * One piece for the one way to open a port: the dialog that opens a service (`OpenPortDialog`) draws it with the choice of taking the
 * rule away in the same step, and the dialog that makes a database public draws it at its review, where the port is known, and points to
 * the firewall's rules for taking the rule away, because that call opens a relay and has no step for a rule it did not write.
 */

import { Checkbox } from "../../components/ui/Checkbox";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { FirewallRule } from "../server/queries";
import { isProtected as isHeldOpen } from "./firewallRules";
import type { WideRule } from "./openPort";

/** What the firewall's own list says of the ports it may not close, as `isProtected` reads it. */
type Guard = Parameters<typeof isHeldOpen>[1];

export interface WideRulesNoticeProps {
  port: number;
  wide: readonly WideRule[];
  guard: Guard;
  /**
   * The rules this dialog can take away in the same step, and whether they are: absent, the notice says where to take them out from
   * and offers no tick, which is what a dialog that cannot do it says.
   */
  removal?: { rules: readonly FirewallRule[]; ticked: boolean; onTick: (next: boolean) => void };
}

/** The rules that leave a port open to everyone, with what is said of each, and the tick that takes away the ones that can go. */
export function WideRulesNotice({ port, wide, guard, removal }: WideRulesNoticeProps) {
  const t = useT();
  if (wide.length === 0) return null;
  const goesAway = (entry: WideRule): boolean => removal !== undefined && entry.removable;
  return (
    <Notice
      tone="warning"
      title={t("network.openPort.wideTitle", { port })}
      // A dialog that cannot take the rule away says where to: the link is the notice's one action, not more words in its body.
      {...(removal === undefined
        ? {
            action: (
              <TextLink to="/network/firewall" size="ui">
                {t("network.openPort.wideRules")}
              </TextLink>
            ),
          }
        : {})}
    >
      <div className="flex flex-col gap-2">
        <p>{t("network.openPort.wideBody", { count: wide.length })}</p>
        <ul className="flex flex-col gap-1">
          {wide.map((entry) => (
            <li key={entry.rule.id}>
              <Mono>{entry.rule.spec}</Mono>
              {/* What its maker wrote about it ("temporary: migration from the old host") says more than the rule does. */}
              {entry.rule.comment.trim() !== "" ? <span className="block text-fg-muted">{entry.rule.comment.trim()}</span> : null}
              {goesAway(entry) ? null : (
                <span className="block text-fg-muted">{isHeldOpen(entry.rule, guard) !== null ? t("network.openPort.wideKept") : t("network.openPort.wideMore")}</span>
              )}
            </li>
          ))}
        </ul>
        {removal !== undefined && removal.rules.length > 0 ? (
          <Checkbox label={t("network.openPort.wideRemove", { count: removal.rules.length })} checked={removal.ticked} onCheckedChange={removal.onTick} />
        ) : null}
      </div>
    </Notice>
  );
}
