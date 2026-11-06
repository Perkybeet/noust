import { useQuery } from "@tanstack/react-query";

import { sessionsQuery } from "../../../api/queries/auth";
import { complianceQuery } from "../../../api/queries/compliance";
import type { ComplianceControl, ComplianceState } from "../../../api/queries/compliance";
import { configQuery } from "../../../api/queries/config";
import { useDocumentTitle } from "../../../app/documentTitle";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Sections } from "../../../components/page/Section";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { ReadOnlyLine, useCan } from "../../../components/page/permissions";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Setting } from "../../../components/ui/Setting";
import { Skeleton } from "../../../components/ui/Skeleton";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { MANAGE } from "../compliance/FrameworkCards";
import { useControlEditing } from "../compliance/ControlsSection";
import { readLockoutPolicy } from "../security";

/** The controls of one section, in the order it lists them, leaving out one this server does not have (an older one). */
function pick(state: ComplianceState, ids: readonly string[]): ComplianceControl[] {
  return ids.flatMap((id) => state.controls.filter((control) => control.id === id));
}

/**
 * Settings > Access security: how people get into the console, in three things (what is asked at the door, how long a
 * session lasts, and the token for when nothing else works), each setting one of the controls the compliance
 * frameworks are made of: a person who turns one on here is doing what a framework would, and one a framework has on is
 * locked here with the framework's name, so the two never disagree. The addresses that may reach the console are the
 * configuration's, shown and not edited. What a person does with their own sign-in (their factor, their passkeys)
 * is theirs, on the account page.
 */
export function AccessSecuritySettings() {
  const t = useT();
  useDocumentTitle(t("nav.settingsTabs.accessSecurity.label"), 1);
  const query = useQuery(complianceQuery());
  const canManage = useCan(MANAGE);
  const canReadSessions = useCan("accounts.read");
  const sessions = useQuery({ ...sessionsQuery(), enabled: canReadSessions });
  const config = useQuery(configQuery());
  const state = query.data;
  const editing = useControlEditing(state, canManage);
  const allowed = config.data !== undefined ? readLockoutPolicy(config.data.config).ipAllowlist : null;

  return (
    <Sections surfaces>
      <ReadOnlyLine permission={MANAGE} />
      {query.isError && state === undefined ? (
        <ErrorBlock error={query.error} title={t("accessSecurity.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      ) : state === undefined ? (
        <LoadingRegion label={t("accessSecurity.loading")} className="flex flex-col gap-6">
          <Skeleton className="h-56 w-full rounded-card" />
          <Skeleton className="h-40 w-full rounded-card" />
        </LoadingRegion>
      ) : (
        <>
          {editing.failure}
          <SurfaceSection title={t("accessSecurity.door.title")} icon={OBJECT_ICONS.signIn} description={t("accessSecurity.door.description")} flush>
            <div className="flex flex-col divide-y divide-border">
              {pick(state, ["second_factor", "password_min_length", "lockout_threshold", "lockout_minutes"]).map((control) => editing.row(control))}
              <Setting
                label={t("accessSecurity.door.addresses")}
                help={t("accessSecurity.door.addressesHelp")}
                action={
                  allowed === null ? (
                    <Skeleton className="h-4 w-24" />
                  ) : allowed.length === 0 ? (
                    <span className="text-13 text-fg">{t("accessSecurity.door.anyAddress")}</span>
                  ) : (
                    <Mono tone="default" truncate className="max-w-64">
                      {allowed.join(", ")}
                    </Mono>
                  )
                }
              />
            </div>
          </SurfaceSection>
          <SurfaceSection
            title={t("accessSecurity.sessions.title")}
            icon={OBJECT_ICONS.session}
            description={t("accessSecurity.sessions.description")}
            flush
            actions={
              <TextLink to="/account/security" size="ui">
                {t("accessSecurity.sessions.yours")}
              </TextLink>
            }
          >
            <div className="flex flex-col divide-y divide-border">
              {pick(state, ["session_idle_minutes", "session_absolute_hours", "sudo_password", "sudo_idle_minutes", "sudo_max_minutes"]).map((control) => editing.row(control))}
              {/* The shell may have read the sessions already (the cache is shared): who may not read them is shown none. */}
              {canReadSessions && sessions.data !== undefined ? (
                <Setting
                  label={t("accessSecurity.sessions.open")}
                  action={<span className="text-13 text-fg">{t("accessSecurity.sessions.openCount", { count: sessions.data.active_sessions })}</span>}
                />
              ) : null}
            </div>
          </SurfaceSection>
          {pick(state, ["master_token"]).length > 0 ? (
            <SurfaceSection title={t("accessSecurity.master.title")} icon={OBJECT_ICONS.token} description={t("accessSecurity.master.description")} help={t("accessSecurity.master.help")} flush>
              <div className="flex flex-col divide-y divide-border">{pick(state, ["master_token"]).map((control) => editing.row(control))}</div>
            </SurfaceSection>
          ) : null}
        </>
      )}
      {editing.dialogs}
    </Sections>
  );
}
