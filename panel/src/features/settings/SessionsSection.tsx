import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { useState } from "react";

import { isApiError } from "../../api/client";
import { authKeys, revokeOtherSessions, revokeSession, sessionsQuery } from "../../api/queries/auth";
import type { ActiveSession } from "../../api/queries/auth";
import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Dialog } from "../../components/ui/Dialog";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { RowAction } from "./RowAction";
import { SettingsSection } from "./SettingsForm";

/** How many sessions the server reports revoked, from `POST /api/auth/sessions/revoke-others`'s own words. */
function countRevoked(message: string): number | null {
  const match = /(\d+)/.exec(message);
  return match ? Number(match[1]) : null;
}

/** Everyone signed in to this console right now, and signing them out. */
export function SessionsSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery(sessionsQuery());
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: authKeys.sessions });
  };

  const revokeOne = useMutation({
    mutationFn: (session: ActiveSession) => revokeSession(session.sid_prefix),
    onSuccess: (_, session) => {
      toast.success(t("settings.security.sessions.signedOutToast", { sid: session.sid_prefix }));
      refresh();
    },
    onError: (error, session) => {
      reportActionError(t("settings.security.sessions.signOutFailed", { sid: session.sid_prefix }), error);
      refresh();
    },
  });

  const others = (query.data?.sessions ?? []).filter((session) => !session.is_current);
  const [confirming, setConfirming] = useState(false);
  const [failure, setFailure] = useState<{ hint: string; detail: string } | null>(null);

  const revokeOthers = useMutation({
    mutationFn: revokeOtherSessions,
    onSuccess: (result) => {
      refresh();
      setConfirming(false);
      const count = countRevoked(result.message);
      toast.success(count === null ? result.message : t("settings.security.sessions.signedOutCount", { count }));
    },
    onError: (error: unknown) => {
      // The one credential this cannot apply to: a Bearer or the master token, which never
      // had a browser tab of its own. The backend answers a bare 400 with no hint of its own.
      if (isApiError(error) && error.status === 400) {
        setFailure({
          hint: t("settings.security.sessions.tokenCredentialHint"),
          detail: error.detail,
        });
        return;
      }
      setConfirming(false);
      reportActionError(t("settings.security.sessions.revokeOthersFailed"), error);
    },
  });

  const closeConfirm = (next: boolean): void => {
    if (!next && revokeOthers.isPending) return;
    setConfirming(next);
    if (!next) setFailure(null);
  };

  const columns: Column<ActiveSession>[] = [
    {
      id: "session",
      header: t("settings.security.sessions.columnSession"),
      cell: (session) => (
        <span className="flex items-center gap-2">
          <span translate="no" className="mono text-12">
            {session.sid_prefix}
          </span>
          {session.is_current ? <Badge>{t("settings.security.sessions.thisBrowser")}</Badge> : null}
        </span>
      ),
    },
    { id: "address", header: t("settings.security.sessions.columnAddress"), mono: true, hideBelow: "sm", cell: (session) => session.client_ip },
    {
      id: "signed-in",
      header: t("settings.security.sessions.columnSignedIn"),
      hideBelow: "md",
      sortValue: (session) => session.created_at,
      cell: (session) => <RelativeTime value={session.created_at} />,
    },
    {
      id: "last-seen",
      header: t("settings.security.sessions.columnLastActive"),
      sortValue: (session) => session.last_seen,
      cell: (session) => <RelativeTime value={session.last_seen} />,
    },
    {
      id: "expires",
      header: t("settings.security.sessions.columnExpires"),
      hideBelow: "sm",
      sortValue: (session) => session.expires_at,
      cell: (session) => <RelativeTime value={session.expires_at} />,
    },
  ];

  return (
    <SettingsSection
      title={t("settings.security.sessions.title")}
      description={t("settings.security.sessions.description")}
    >
      <div className="flex min-w-0 flex-col gap-3">
        <QueryState
          query={query}
          label={t("settings.security.sessions.loadingLabel")}
          // One row, the least there can be (this one), compact like the loaded table; the line
          // under it has its place held too.
          skeleton={
            <div className="flex min-w-0 flex-col gap-3">
              <DataTable
                caption={t("settings.security.sessions.tableCaption")}
                columns={columns}
                rows={[]}
                getRowId={(s) => s.sid_prefix}
                density="compact"
                rowActions={() => null}
                loading
                skeletonRows={1}
              />
              <div aria-hidden="true" className="h-8" />
            </div>
          }
        >
          {(data) => (
            <DataTable
              caption={t("settings.security.sessions.tableCaption")}
              columns={columns}
              rows={data.sessions}
              getRowId={(session) => session.sid_prefix}
              density="compact"
              rowActions={(session) =>
                session.is_current ? null : (
                  <RowAction
                    label={t("settings.security.sessions.signOutLabel", { sid: session.sid_prefix })}
                    text={t("settings.security.sessions.signOutText")}
                    icon={<LogOut />}
                    loading={revokeOne.isPending && revokeOne.variables.sid_prefix === session.sid_prefix}
                    onClick={() => {
                      revokeOne.mutate(session);
                    }}
                  />
                )
              }
            />
          )}
        </QueryState>
        {query.data !== undefined ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-13 text-fg-muted tabular-nums">
              {t("settings.security.sessions.activeCount", { count: query.data.active_sessions })}
            </p>
            <Button
              icon={<LogOut aria-hidden="true" />}
              disabled={others.length === 0}
              onClick={() => {
                setFailure(null);
                setConfirming(true);
              }}
            >
              {t("settings.security.sessions.signOutOthers")}
            </Button>
          </div>
        ) : null}
        <Dialog
          open={confirming}
          onOpenChange={closeConfirm}
          size="sm"
          title={t("settings.security.sessions.confirmTitle")}
          description={
            others.length > 0
              ? t("settings.security.sessions.confirmDescriptionWithCount", {
                  count: t("settings.security.sessions.othersCount", { count: others.length }),
                })
              : t("settings.security.sessions.confirmDescriptionNoOthers")
          }
          footer={
            <>
              <Button disabled={revokeOthers.isPending} onClick={() => closeConfirm(false)}>
                {t("settings.shared.cancel")}
              </Button>
              <Button
                variant="primary"
                loading={revokeOthers.isPending}
                onClick={() => {
                  revokeOthers.mutate();
                }}
              >
                {t("settings.security.sessions.signOutOthers")}
              </Button>
            </>
          }
        >
          {failure !== null ? (
            <div role="alert" className="flex flex-col gap-2 rounded-control border border-fail/30 bg-fail-soft p-3">
              <p className="text-13 font-medium text-fail">{failure.hint}</p>
              <SystemOutput label={t("settings.security.sessions.serverSaidLabel")} maxHeight="max-h-40">
                {failure.detail}
              </SystemOutput>
            </div>
          ) : undefined}
        </Dialog>
      </div>
    </SettingsSection>
  );
}
