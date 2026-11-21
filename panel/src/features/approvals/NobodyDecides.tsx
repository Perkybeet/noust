import type { ReactNode } from "react";

import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { roleLabel } from "../settings/accounts/roles";
import type { Deciders } from "./deciders";

/** The roles that decide, as people read them: "Security officer", or two of them. */
export function decidingRoles(t: T, deciders: Deciders): string {
  return deciders.roles.map((role) => roleLabel(t, role)).join(", ");
}

/** A place of Settings, in words ("Settings > Accounts") and, where there is a router to follow it, as the link to it. */
function Place({ label, to, link }: { label: string; to: "/settings/accounts" | "/settings/approvals"; link: boolean }): ReactNode {
  return link ? (
    <TextLink to={to} size="ui">
      {label}
    </TextLink>
  ) : (
    <span className="font-medium text-fg">{label}</span>
  );
}

export interface NobodyDecidesProps {
  /** Who could decide: the sentence is for the case where nobody else could. */
  deciders: Deciders;
  /**
   * `waiting` is a request that already waits (the page of approvals, the dialog of the request): it says the way out in two
   * steps. `turnOn` is the moment before approvals are turned on, when the way is to give the role first.
   */
  moment: "waiting" | "turnOn";
  /** The places are links where a router is there to follow them; the dialog of a request is mounted above it and names them in words. */
  links: boolean;
}

/**
 * Says that nobody could decide an approval, why that is a dead end (giving the role to someone is itself a change that waits for
 * approval while approvals are on), and the way out. Whether it is drawn is the caller's: it asks `useDeciders` and only says it
 * when `others` is exactly 0, which is when the server's own numbers say it.
 */
export function NobodyDecides({ deciders, moment, links }: NobodyDecidesProps) {
  const t = useT();
  const settings = t("nav.settings.label");
  const key =
    moment === "turnOn"
      ? deciders.you
        ? "approvals.nobody.turnOnOnlyYou"
        : "approvals.nobody.turnOnNone"
      : deciders.you
        ? "approvals.nobody.onlyYou"
        : "approvals.nobody.none";
  return (
    <>
      {t.rich(key, {
        roles: decidingRoles(t, deciders),
        accounts: <Place key="accounts" label={`${settings} > ${t("accounts.nav.label")}`} to="/settings/accounts" link={links} />,
        approvals: <Place key="approvals" label={`${settings} > ${t("approvals.nav.label")}`} to="/settings/approvals" link={links} />,
      })}
    </>
  );
}
