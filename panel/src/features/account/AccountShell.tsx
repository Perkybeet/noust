import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { DetailPage } from "../../components/page/DetailPage";
import { SettingsLayout } from "../../components/page/SettingsLayout";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";

/**
 * The account, under the title "Account" (its two sections are the column's): the signed-in person's own profile and password, and
 * how they sign in. It is the central's (a node refuses a central's sign-in on purpose), so it never
 * carries a server, and it is not part of Settings: it follows the person, not the machine (NV-7).
 * Theme and language are preferences of the browser and live in the account menu, not here (NV-8).
 *
 * Someone who signed in with the access token has no account to show: the shell says so once, above
 * whichever page is open, rather than each page finding its own words for it.
 */
export function AccountShell({ children }: { children: ReactNode }) {
  const t = useT();
  const { data: session } = useQuery(sessionQuery());
  const withoutAccount = session !== undefined && (session.account === null || session.account === undefined);
  return (
    <DetailPage measure="settings" header={{ title: t("account.title"), description: t("account.description") }}>
      <SettingsLayout label={t("nav.landmarks.accountSections")} items={[]} carried>
        <div className="flex min-w-0 flex-col gap-8">
          {withoutAccount ? (
            <Notice tone="warning" title={t("auth.security.masterTitle")}>
              {session.grant === "compat" ? t("auth.security.masterCompat") : t("auth.security.masterBreakGlass")}
            </Notice>
          ) : null}
          {children}
        </div>
      </SettingsLayout>
    </DetailPage>
  );
}
