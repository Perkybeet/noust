import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { useState } from "react";

import { isApiError } from "../../api/client";
import { authKeys, revokeOtherSessions, revokeSession, sessionsQuery } from "../../api/queries/auth";
import type { ActiveSession } from "../../api/queries/auth";
import { ErrorBlock, QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Dialog } from "../../components/ui/Dialog";
import { Mono } from "../../components/ui/Mono";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { RowAction } from "./RowAction";

/** How many sessions show before "Show all": the recent ones are the ones to check. */
const RECENT = 5;

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
  const [showAll, setShowAll] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);

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
        setFailure(error);
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
          <Mono tone="default" className="text-12">
            {session.sid_prefix}
          </Mono>
          {session.is_current ? <Badge>{t("settings.security.sessions.thisBrowser")}</Badge> : null}
        </span>
      ),
    },
    {
      id: "address",
      header: t("settings.security.sessions.columnAddress"),
      hideBelow: "sm",
      mono: true,
      cell: (session) => session.client_ip,
    },
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
    <Section title={t("settings.security.sessions.title")} description={t("settings.security.sessions.description")}>
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
              // Newest activity first; beyond the recent few, the rest wait behind "Show all".
              rows={[...data.sessions].sort((a, b) => b.last_seen - a.last_seen).slice(0, showAll ? undefined : RECENT)}
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
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-13 text-fg-muted tabular-nums">
                {t("settings.security.sessions.activeCount", { count: query.data.active_sessions })}
              </p>
              {query.data.sessions.length > RECENT ? (
                <Button size="sm" variant="ghost" onClick={() => setShowAll((value) => !value)}>
                  {showAll ? t("auth.security.showRecent") : t("auth.security.showAllSessions", { count: query.data.sessions.length })}
                </Button>
              ) : null}
            </div>
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
            <ErrorBlock live compact error={failure} title={t("settings.security.sessions.revokeOthersFailed")} hint={t("settings.security.sessions.tokenCredentialHint")} />
          ) : undefined}
        </Dialog>
      </div>
    </Section>
  );
}
