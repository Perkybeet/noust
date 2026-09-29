import { useQuery } from "@tanstack/react-query";

import { sessionsQuery } from "../../api/queries/auth";
import { configQuery } from "../../api/queries/config";
import { useDocumentTitle } from "../../app/documentTitle";
import { KeyValueList, KeyValueListSkeleton } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { QueryState } from "../../components/page/QueryState";
import { Sections } from "../../components/page/Section";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatCount } from "../../lib/format";
import { perWindow, readLockoutPolicy, spokenDuration } from "./security";
import type { LockoutPolicy } from "./security";
import { SessionsSection } from "./SessionsSection";
import { SettingsSection } from "./SettingsForm";
import { TwoFactorSection } from "./TwoFactorSection";

function policyItems(t: T, policy: LockoutPolicy): KeyValueItem[] {
  const unknown = t("settings.security.lockout.notSet");
  return [
    {
      label: t("settings.security.lockout.maxFailedAttemptsLabel"),
      value: policy.maxFailedAttempts === null ? unknown : formatCount(policy.maxFailedAttempts, t.locale),
      copy: false,
      mono: false,
      hint: t("settings.security.lockout.maxFailedAttemptsHint"),
    },
    {
      label: t("settings.security.lockout.lockoutLastsLabel"),
      value: policy.lockoutSeconds === null ? unknown : spokenDuration(policy.lockoutSeconds, t.locale),
      copy: false,
      mono: false,
    },
    {
      label: t("settings.security.lockout.requestLimitLabel"),
      value: !policy.rateLimitEnabled
        ? t("settings.security.lockout.requestLimitOff")
        : policy.rateLimitRequests === null || policy.rateLimitWindowSeconds === null
          ? unknown
          : t("settings.security.lockout.requestLimitValue", {
              count: formatCount(policy.rateLimitRequests, t.locale),
              window: perWindow(policy.rateLimitWindowSeconds, t.locale),
            }),
      copy: false,
      mono: false,
      ...(policy.rateLimitEnabled ? { hint: t("settings.security.lockout.requestLimitHint") } : {}),
    },
    {
      label: t("settings.security.lockout.sessionLifetimeLabel"),
      value: policy.sessionHours === null ? unknown : spokenDuration(policy.sessionHours * 3600, t.locale),
      copy: false,
      mono: false,
      hint: t("settings.security.lockout.sessionLifetimeHint"),
    },
    {
      label: t("settings.security.lockout.allowedAddressesLabel"),
      value: policy.ipAllowlist.length === 0 ? t("settings.security.lockout.anyAddress") : policy.ipAllowlist.join(", "),
      mono: policy.ipAllowlist.length > 0,
      copy: policy.ipAllowlist.length === 0 ? false : policy.ipAllowlist.join(", "),
    },
  ];
}

/** The lockout and rate limits, as configured. Read-only: they are edited in config.yaml. */
function LockoutSection() {
  const t = useT();
  const query = useQuery(configQuery());
  return (
    <SettingsSection
      title={t("settings.security.lockout.title")}
      description={t("settings.security.lockout.description")}
      commands={["noust config get web", "noust web restart"]}
    >
      <QueryState query={query} label={t("settings.security.lockout.loadingLabel")} skeleton={
          <div className="rounded-card border border-border bg-surface px-5 py-2 shadow-raised">
            <KeyValueListSkeleton rows={5} hints={[0, 2, 3]} />
          </div>
        }
      >
        {(data) => (
          <div className="rounded-card border border-border bg-surface px-5 py-2 shadow-raised">
            <KeyValueList items={policyItems(t, readLockoutPolicy(data.config))} />
          </div>
        )}
      </QueryState>
    </SettingsSection>
  );
}

/** Settings > Security: the second factor, who is signed in, and the lockout policy. */
export function SecuritySettings() {
  const t = useT();
  useDocumentTitle(t("settings.security.documentTitle"), 1);
  const sessions = useQuery(sessionsQuery());
  return (
    <Sections>
      <TwoFactorSection />
      <SessionsSection />
      {/* After the sessions, whose number is not known until they load: drawn with them, the
          policy never jumps down the page as the list lengthens above it. */}
      {sessions.data !== undefined || sessions.isError ? <LockoutSection /> : null}
    </Sections>
  );
}
