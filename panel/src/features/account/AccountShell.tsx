import { useRouterState } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { DetailPage } from "../../components/page/DetailPage";
import { SettingsLayout } from "../../components/page/SettingsLayout";
import type { SettingsNavItem } from "../../components/page/SettingsLayout";
import { useT } from "../../i18n";
import { ACCOUNT_SCREENS } from "../nav/account";

/**
 * The account, T3 under the title "Account": the signed-in person's own profile and password, and
 * how they sign in. It is the central's (a node refuses a central's sign-in on purpose), so it never
 * carries a server, and it is not part of Settings: it follows the person, not the machine (NV-7).
 * Theme and language are preferences of the browser and live in the account menu, not here (NV-8).
 */
export function AccountShell({ children }: { children: ReactNode }) {
  const t = useT();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const items: SettingsNavItem[] = ACCOUNT_SCREENS.map((entry) => ({
    to: entry.to,
    label: t(entry.to === "/account" ? (entry.rootLabel ?? entry.label) : entry.label),
    keywords: entry.keywords !== undefined ? t(entry.keywords) : "",
    ...(entry.to === "/account" ? { exact: true } : {}),
  }));
  return (
    <DetailPage header={{ title: t("account.title"), description: t("account.description") }}>
      <SettingsLayout label={t("nav.landmarks.accountSections")} items={items} index={pathname === "/account"} backTo="/account">
        {children}
      </SettingsLayout>
    </DetailPage>
  );
}
