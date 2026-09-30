import { useQuery } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import { sessionQuery } from "../../api/queries/auth";
import type { SessionInfo } from "../../api/queries/auth";
import { configQuery } from "../../api/queries/config";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList, KeyValueListSkeleton } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section, Sections } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatCount } from "../../lib/format";
import { approvalPolicyQuery } from "../approvals/api";
import { PasskeysSection } from "../auth/PasskeysSection";
import { PasswordDialog } from "../auth/PasswordDialog";
import { roleLabel } from "./accounts/roles";
import { perWindow, readLockoutPolicy, spokenDuration } from "./security";
import { SessionsSection } from "./SessionsSection";
import { TwoFactorSection } from "./TwoFactorSection";

type Baseline = Awaited<ReturnType<typeof profileRequest>>["baseline"][number];

function profileRequest(signal: AbortSignal) {
  return request("get", "/api/ens/profile", { signal });
}

const ENS = "ens-medium";

/** The profile in words: "Standard", "ENS category MEDIUM". */
export function profileLabel(t: T, profile: string | null | undefined): string {
  return profile === ENS ? t("auth.security.profileEns") : t("auth.security.profileStandard");
}

/** Who is signed in: a person's account, or the access token (emergency access). */
function AccountSection({ t, session }: { t: T; session: SessionInfo }) {
  const [changing, setChanging] = useState(false);
  const account = session.account;
  if (account === null || account === undefined) {
    return (
      <Section title={t("auth.security.accountTitle")}>
        <Notice tone="warning" title={t("auth.security.masterTitle")}>
          {session.grant === "compat" ? t("auth.security.masterCompat") : t("auth.security.masterBreakGlass")}
        </Notice>
      </Section>
    );
  }
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

function baselineColumns(t: T, ens: boolean): Column<Baseline>[] {
  return [
    {
      id: "setting",
      header: t("auth.security.baselineSetting"),
      card: "title",
      cell: (item) => (
        <span className="flex min-w-0 flex-col">
          <Mono tone="default">{item.key}</Mono>
          <span className="text-12 text-pretty text-fg-muted">{item.description}</span>
        </span>
      ),
    },
    {
      id: "value",
      header: ens ? t("auth.security.baselineEns") : t("auth.security.baselineStandard"),
      width: "w-40",
      cell: (item) => <Mono tone="default">{ens ? item.ens_value : item.standard_value}</Mono>,
    },
    {
      id: "other",
      header: ens ? t("auth.security.baselineStandard") : t("auth.security.baselineEns"),
      width: "w-40",
      hideBelow: "md",
      cell: (item) => <Mono tone="muted">{ens ? item.standard_value : item.ens_value}</Mono>,
    },
  ];
}

/**
 * What the server enforces on every sign-in, read-only: the security profile and the values it
 * fixes, four-eyes approvals, and the limits per address. They change in the configuration, from
 * a terminal, where a change is audited.
 */
function PolicySection({ t, session }: { t: T; session: SessionInfo }) {
  const profile = useQuery({ queryKey: ["ens", "profile"], queryFn: ({ signal }) => profileRequest(signal), staleTime: 60_000 });
  const approvals = useQuery(approvalPolicyQuery());
  const config = useQuery(configQuery());
  const ens = (profile.data?.profile ?? session.security_profile) === ENS;
  // What the profile fixes is reference, a table as long as the page: behind a button.
  const [showBaseline, setShowBaseline] = useState(false);
  const lockout = config.data !== undefined ? readLockoutPolicy(config.data.config) : null;
  const attempts = lockout?.maxFailedAttempts ?? null;
  const lockSeconds = lockout?.lockoutSeconds ?? null;

  const facts: KeyValueItem[] = [
    { label: t("auth.security.profile"), value: profileLabel(t, profile.data?.profile ?? session.security_profile), mono: false, copy: false },
    ...(session.idle_minutes !== null && session.idle_minutes !== undefined
      ? [{ label: t("auth.security.idle"), value: spokenDuration(session.idle_minutes * 60, t.locale), mono: false, copy: false as const }]
      : []),
    {
      label: t("auth.security.approvals"),
      value:
        approvals.data === undefined
          ? "…"
          : approvals.data.enabled
            ? t("auth.security.approvalsOn", { roles: approvals.data.approvers.map((role) => roleLabel(t, role)).join(", ") })
            : t("auth.security.approvalsOff"),
      mono: false,
      copy: false,
    },
    ...(attempts !== null && lockSeconds !== null
      ? [
          {
            label: t("auth.security.perAddress"),
            value: t("auth.security.perAddressValue", {
              count: formatCount(attempts, t.locale),
              duration: spokenDuration(lockSeconds, t.locale),
            }),
            mono: false,
            copy: false as const,
          },
        ]
      : []),
    ...(lockout !== null && lockout.rateLimitEnabled && lockout.rateLimitRequests !== null && lockout.rateLimitWindowSeconds !== null
      ? [
          {
            label: t("settings.security.lockout.requestLimitLabel"),
            value: t("settings.security.lockout.requestLimitValue", {
              count: formatCount(lockout.rateLimitRequests, t.locale),
              window: perWindow(lockout.rateLimitWindowSeconds, t.locale),
            }),
            mono: false,
            copy: false as const,
          },
        ]
      : []),
    ...(lockout !== null
      ? [
          {
            label: t("settings.security.lockout.allowedAddressesLabel"),
            value: lockout.ipAllowlist.length === 0 ? t("settings.security.lockout.anyAddress") : lockout.ipAllowlist.join(", "),
            mono: lockout.ipAllowlist.length > 0,
            copy: lockout.ipAllowlist.length === 0 ? (false as const) : lockout.ipAllowlist.join(", "),
          },
        ]
      : []),
  ];

  return (
    <Section title={t("auth.security.policyTitle")} description={t("auth.security.policyDescription")}>
      <Card padding="sm">
        {profile.isPending && config.isPending ? <KeyValueListSkeleton rows={4} /> : <KeyValueList items={facts} />}
      </Card>
      <Button size="sm" variant="ghost" className="self-start" aria-expanded={showBaseline} onClick={() => setShowBaseline((value) => !value)}>
        {showBaseline ? t("auth.security.baselineHide") : t("auth.security.baselineShow")}
      </Button>
      {!showBaseline ? null : profile.isError ? (
        <ErrorBlock compact error={profile.error} title={t("auth.security.baselineFailed")} onRetry={() => void profile.refetch()} />
      ) : (
        <DataTable
          caption={t("auth.security.baselineCaption")}
          columns={baselineColumns(t, ens)}
          rows={profile.data?.baseline ?? []}
          getRowId={(item) => item.key}
          density="compact"
          mobile="cards"
          loading={profile.isPending}
          skeletonRows={4}
        />
      )}
      <CommandHint label={t("auth.security.policyCommand")} command={ens ? "noust config get auth" : "noust config set security.profile ens-medium"} />
    </Section>
  );
}

/**
 * Settings > Security (the central's): the signed-in person's own account and password, their
 * authenticator app and passkeys, where they are signed in, and the policy the server
 * enforces on everyone.
 */
export function SecuritySettings() {
  const t = useT();
  useDocumentTitle(t("settings.security.documentTitle"), 1);
  const { data: session } = useQuery(sessionQuery());
  return (
    <Sections>
      {session !== undefined ? <AccountSection t={t} session={session} /> : null}
      <TwoFactorSection session={session} />
      <PasskeysSection />
      <SessionsSection />
      {session !== undefined ? <PolicySection t={t} session={session} /> : null}
    </Sections>
  );
}
