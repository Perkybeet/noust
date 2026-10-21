import { section } from "../../app/navCore";
import type { Dest, Strip } from "../../app/navCore";

const account: Dest = {
  id: "account",
  to: "/account",
  kind: "section",
  scope: "both",
  owner: "account",
  central: true,
  label: "account.title",
  // The first section of its own list, as Server's is "Overview".
  rootLabel: "nav.screens.account.label",
  keywords: "nav.screens.account.keywords",
  command: "nav.screens.account.command",
};

/**
 * The account: the person's, not the machine's. Opened from the account menu, never from the
 * sidebar; Settings holds the instance's and the server's, not this (NV-7). The register is
 * app/nav.ts; this is one group of it.
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
