import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Inbox } from "lucide-react";
import { useMemo, useState } from "react";

import { sendApproved } from "../../api/client";
import { sessionQuery } from "../../api/queries/auth";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section, Sections } from "../../components/page/Section";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { Textarea } from "../../components/ui/Textarea";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { roleLabel } from "../settings/accounts/roles";
import { approvalKeys, approvalPolicyQuery, approvalsQuery, approve, reject } from "./api";
import type { Approval } from "./api";
import { ApprovalStateLabel } from "./ApprovalStateLabel";
import { approvalDescription, callFromSnapshot, inView } from "./data";
import type { ApprovalsSearch, ApprovalsView } from "./data";
import { forgetHeld, heldCall } from "./store";

function who(t: T, actor: Approval["requester"] | null | undefined): string {
  if (actor === null || actor === undefined) return "";
  return actor.role ? t("approvals.fields.actorWithRole", { name: actor.name, role: roleLabel(t, actor.role) }) : actor.name;
}

function columnsFor(t: T): Column<Approval>[] {
  return [
    {
      id: "request",
      header: t("approvals.table.request"),
      card: "title",
      cell: (item) => (
        <span className="flex min-w-0 flex-col">
          <span className="text-13 text-pretty text-fg">{approvalDescription(t, item)}</span>
          {/* The request's number; the call itself, exactly as asked, is in its drawer. */}
          <Mono tone="muted" truncate>
            #{item.id}
          </Mono>
        </span>
      ),
    },
    { id: "state", header: t("approvals.table.state"), card: "status", width: "w-44", cell: (item) => <ApprovalStateLabel t={t} state={item.state} /> },
    { id: "requester", header: t("approvals.table.requester"), hideBelow: "sm", cell: (item) => <span className="text-13">{who(t, item.requester)}</span> },
    {
      id: "reason",
      header: t("approvals.table.reason"),
      hideBelow: "lg",
      cell: (item) => (item.reason ? <span className="line-clamp-2 text-13 text-pretty">{item.reason}</span> : <EmptyCell reason={t("approvals.table.noReason")} />),
    },
    { id: "asked", header: t("approvals.table.asked"), hideBelow: "md", sortValue: (item) => item.created_at, cell: (item) => <RelativeTime value={item.created_at} /> },
  ];
}

/** The detail of one request: what it will do, exactly as asked, and deciding or running it. */
function ApprovalDrawer({ approval, onClose }: { approval: Approval; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [comment, setComment] = useState("");
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: approvalKeys.all });
  };
  const decide = useMutation({
    mutationFn: (verdict: "approve" | "reject") => (verdict === "approve" ? approve(approval.id, comment.trim() || null) : reject(approval.id, comment.trim() || null)),
    onSuccess: (decided) => {
      refresh();
      toast.success(decided.state === "approved" ? t("approvals.drawer.approvedToast", { id: decided.id }) : t("approvals.drawer.rejectedToast", { id: decided.id }));
      onClose();
    },
  });
  const call = heldCall(approval.id) ?? callFromSnapshot(approval);
  const run = useMutation({
    mutationFn: () => {
      if (call === null) throw new Error("no call");
      return sendApproved(call, String(approval.id));
    },
    onSuccess: () => {
      forgetHeld(approval.id);
      refresh();
      // Whatever the call changed is stale now, wherever it is shown.
      void queryClient.invalidateQueries();
      toast.success(t("approvals.drawer.ranToast", { id: approval.id }));
      onClose();
    },
  });
  const canRun = approval.mine && approval.state === "approved";
  const deciding = approval.can_decide && approval.state === "requested";
  const withdrawing = approval.can_decide && approval.state === "approved";

  const items: KeyValueItem[] = [
    { label: t("approvals.fields.request"), value: `#${String(approval.id)}` },
    { label: t("approvals.fields.state"), value: <ApprovalStateLabel t={t} state={approval.state} />, mono: false, copy: false },
    { label: t("approvals.fields.action"), value: approval.action },
    { label: t("approvals.fields.call"), value: `${approval.method} ${approval.path}` },
    { label: t("approvals.fields.requester"), value: who(t, approval.requester), mono: false, copy: false },
    { label: t("approvals.fields.reason"), value: approval.reason ?? t("approvals.table.noReason"), mono: false, copy: false },
    { label: t("approvals.fields.asked"), value: <RelativeTime value={approval.created_at} />, mono: false, copy: false },
    ...(approval.state === "requested"
      ? [{ label: t("approvals.fields.expires"), value: <RelativeTime value={approval.expires_at} />, mono: false, copy: false as const }]
      : []),
    ...(approval.decider
      ? [{ label: t("approvals.fields.decider"), value: who(t, approval.decider), mono: false, copy: false as const }]
      : []),
    ...(approval.decision_comment ? [{ label: t("approvals.fields.comment"), value: approval.decision_comment, mono: false, copy: false as const }] : []),
    ...(approval.execute_by !== null && approval.execute_by !== undefined && approval.state === "approved"
      ? [{ label: t("approvals.fields.runBy"), value: <RelativeTime value={approval.execute_by} />, mono: false, copy: false as const }]
      : []),
    { label: t("approvals.fields.fingerprint"), value: approval.fingerprint },
  ];

  return (
    <Drawer
      open
      onOpenChange={(next) => {
        if (!next && !decide.isPending && !run.isPending) onClose();
      }}
      title={t("approvals.drawer.title", { id: approval.id })}
      description={approvalDescription(t, approval)}
      footer={
        deciding || withdrawing ? (
          <>
            <Button variant="danger" loading={decide.isPending && decide.variables === "reject"} disabled={decide.isPending} onClick={() => decide.mutate("reject")}>
              {withdrawing ? t("approvals.drawer.withdraw") : t("approvals.drawer.reject")}
            </Button>
            {deciding ? (
              <Button variant="primary" loading={decide.isPending && decide.variables === "approve"} disabled={decide.isPending} onClick={() => decide.mutate("approve")}>
                {t("approvals.drawer.approve")}
              </Button>
            ) : null}
          </>
        ) : canRun && call !== null ? (
          <Button variant="primary" loading={run.isPending} onClick={() => run.mutate()}>
            {t("approvals.drawer.runNow")}
          </Button>
        ) : undefined
      }
    >
      <div className="flex flex-col gap-5">
        <KeyValueList items={items} />
        <div className="flex flex-col gap-1.5">
          <p className="text-13 font-medium text-fg">{t("approvals.drawer.callTitle")}</p>
          <p className="text-12 text-fg-muted">{t("approvals.drawer.callHint")}</p>
          <SystemOutput label={t("approvals.drawer.callTitle")} maxHeight="max-h-80">
            {JSON.stringify(approval.parameters, null, 2)}
          </SystemOutput>
        </div>
        {deciding || withdrawing ? (
          <Field label={t("approvals.drawer.commentLabel")} optional description={t("approvals.drawer.commentHint")}>
            <Textarea
              rows={2}
              maxLength={500}
              value={comment}
              onChange={(event) => {
                setComment(event.target.value);
              }}
            />
          </Field>
        ) : null}
        {approval.mine && approval.state === "requested" ? <Notice>{t("approvals.drawer.yours")}</Notice> : null}
        {canRun && call === null ? <Notice tone="warning">{t("approvals.drawer.cannotReplay")}</Notice> : null}
        {decide.isError ? <ErrorBlock live compact error={decide.error} title={t("approvals.drawer.decideFailed")} /> : null}
        {run.isError ? <ErrorBlock live compact error={run.error} title={t("approvals.drawer.runFailed")} /> : null}
      </div>
    </Drawer>
  );
}

export interface ApprovalsPageProps {
  search: ApprovalsSearch;
  onSearchChange: (search: ApprovalsSearch, options?: { replace?: boolean }) => void;
}

/**
 * Settings > Approvals (the central's): the calls that wait for a second person. A decider
 * reads exactly what a request will do, as its requester sent it, and approves or rejects it
 * with a comment; a requester sees where theirs stand and runs an approved one.
 */
export function ApprovalsPage({ search, onSearchChange }: ApprovalsPageProps) {
  const t = useT();
  useDocumentTitle(t("approvals.page.documentTitle"), 1);
  const { data: session } = useQuery(sessionQuery());
  const policy = useQuery(approvalPolicyQuery());
  const view: ApprovalsView = search.view ?? "waiting";
  const mine = search.mine === true;
  const query = useQuery({ ...approvalsQuery(null, mine), refetchInterval: 15_000 });
  const [open, setOpen] = useState<number | null>(null);
  const all = useMemo(() => query.data?.approvals ?? [], [query.data]);
  const shown = useMemo(() => all.filter((item) => inView(item, view)), [all, view]);
  const opened = all.find((item) => item.id === open) ?? null;
  const columns = columnsFor(t);
  const set = (patch: { [K in keyof ApprovalsSearch]?: ApprovalsSearch[K] | undefined }): void => {
    const next = { ...search, ...patch };
    onSearchChange({ ...(next.view !== undefined && next.view !== "waiting" ? { view: next.view } : {}), ...(next.mine === true ? { mine: true } : {}) }, { replace: true });
  };
  const deciders = (policy.data?.approvers ?? []).map((role) => roleLabel(t, role)).join(", ");
  const decider = session?.role !== null && session?.role !== undefined && (policy.data?.approvers ?? []).includes(session.role);

  return (
    <Sections>
      {policy.data !== undefined && !policy.data.enabled ? (
        <Notice title={t("approvals.page.offTitle")}>{t("approvals.page.off")}</Notice>
      ) : null}
      <Section
        title={t("approvals.page.title")}
        description={
          policy.data === undefined ? t("approvals.page.description") : t("approvals.page.descriptionWho", { approvers: deciders })
        }
      >
        <div className="flex min-w-0 flex-col gap-4">
          <FilterBar
            label={t("approvals.page.filterLabel")}
            filters={
              <>
                <SegmentedControl<ApprovalsView>
                  label={t("approvals.page.viewLabel")}
                  options={[
                    { value: "waiting", label: t("approvals.page.viewWaiting") },
                    { value: "decided", label: t("approvals.page.viewDecided") },
                    { value: "all", label: t("approvals.page.viewAll") },
                  ]}
                  value={view}
                  onValueChange={(value) => set({ view: value })}
                />
                {decider ? (
                  <Checkbox
                    label={t("approvals.page.onlyMine")}
                    checked={mine}
                    onCheckedChange={(checked) => {
                      set(checked ? { mine: true } : { mine: undefined });
                    }}
                  />
                ) : null}
              </>
            }
            {...(query.data !== undefined ? { count: t("approvals.page.count", { count: shown.length }) } : {})}
          />
          {query.isError && query.data === undefined ? (
            <ErrorBlock error={query.error} title={t("approvals.page.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
          ) : (
            <DataTable
              caption={t("approvals.page.caption")}
              columns={columns}
              rows={shown}
              getRowId={(item) => String(item.id)}
              mobile="cards"
              loading={query.isPending}
              skeletonRows={2}
              onRowActivate={(item) => setOpen(item.id)}
              rowActions={(item) => (
                <Button size="sm" variant="ghost" aria-label={t("approvals.table.openLabel", { id: item.id })} onClick={() => setOpen(item.id)}>
                  <span className="max-sm:sr-only">{item.can_decide && item.state === "requested" ? t("approvals.table.review") : t("approvals.table.open")}</span>
                </Button>
              )}
              empty={
                <EmptyState
                  variant="inline"
                  title={view === "waiting" ? t("approvals.page.noneWaiting") : t("approvals.page.none")}
                  icon={<Inbox />}
                />
              }
            />
          )}
        </div>
      </Section>
      <CommandHint label={t("approvals.page.fromTerminal")} command="noust approval list" />
      {opened !== null ? <ApprovalDrawer key={opened.id} approval={opened} onClose={() => setOpen(null)} /> : null}
    </Sections>
  );
}
