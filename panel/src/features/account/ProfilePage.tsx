import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { KeyRound } from "lucide-react";
import { useState } from "react";

import { sessionQuery } from "../../api/queries/auth";
import type { SessionInfo } from "../../api/queries/auth";
import { useDocumentTitle } from "../../app/documentTitle";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section, Sections } from "../../components/page/Section";
import { Button, buttonClassName } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { PasswordDialog } from "../auth/PasswordDialog";
import { roleLabel } from "../settings/accounts/roles";

/** Who is signed in, when it is a person's account. */
function ProfileSection({ t, session }: { t: T; session: SessionInfo }) {
  const [changing, setChanging] = useState(false);
  const account = session.account;
  // The access token has no account: the shell says so above this page (AccountShell).
  if (account === null || account === undefined) return null;
  const items: KeyValueItem[] = [
    { label: t("auth.security.username"), value: account.username },
    { label: t("auth.security.role"), value: roleLabel(t, account.role), mono: false, copy: false },
    ...(account.person_ref ? [{ label: t("auth.security.person"), value: account.person_ref }] : []),
    {
      label: t("auth.security.passwordChanged"),
      value: <RelativeTime value={account.password_changed_at} fallback={t("auth.security.never")} />,
      mono: false,
      copy: false,
    },
  ];
  return (
    <Section
      title={t("auth.security.accountTitle")}
      icon={OBJECT_ICONS.account}
      description={t("auth.security.accountDescription")}
      actions={
        <Button size="sm" icon={<KeyRound aria-hidden="true" />} onClick={() => setChanging(true)}>
          {t("auth.password.change")}
        </Button>
      }
    >
      <Card padding="sm">
        <KeyValueList items={items} />
      </Card>
      {changing ? <PasswordDialog onClose={() => setChanging(false)} /> : null}
    </Section>
  );
}

/**
 * Account > Profile and password: who is signed in and the password. API tokens are not here
 * (they are the instance's, with their owner, in Settings): this page says so and leads there.
 */
export function ProfilePage() {
  const t = useT();
  useDocumentTitle(t("account.documentTitle"), 1);
  const { data: session } = useQuery(sessionQuery());
  return (
    <Sections>
      {session !== undefined ? <ProfileSection t={t} session={session} /> : null}
      <Section title={t("account.tokens.title")} icon={OBJECT_ICONS.token} description={t("account.tokens.description")}>
        <Link to="/settings/tokens" className={cx(buttonClassName("secondary", "md"), "self-start")}>
          {t("account.tokens.open")}
        </Link>
      </Section>
    </Sections>
  );
}
