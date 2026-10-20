import { section } from "../../app/navCore";
import type { Dest, Strip } from "../../app/navCore";

const account: Dest = {
  id: "account",
  to: "/account",
  kind: "section",
  scope: "both",
  owner: "account",
  central: true,
  label: "nav.screens.account.label",
  keywords: "nav.screens.account.keywords",
  command: "nav.screens.account.command",
};

/**
 * The account: the person's, not the machine's. A route before it is a page (app/navCore.ts,
 * INTERIM), so no list offers it yet. The register is app/nav.ts; this is one group of it.
 */
export const ACCOUNT_SCREENS: Strip = [
  account,
  section(account, "security", "nav.screens.accountSecurity.label", {
    owner: "account",
    central: true,
    keywords: "nav.screens.accountSecurity.keywords",
    command: "nav.screens.accountSecurity.command",
  }),
];
