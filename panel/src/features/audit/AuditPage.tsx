import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ScrollText } from "lucide-react";
import { useId, useMemo, useState } from "react";

import { auditPagesQuery } from "../../api/queries/audit";
import type { AuditEntry } from "../../api/queries/audit";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section, Sections } from "../../components/page/Section";
import { Button, buttonClassName } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Dialog } from "../../components/ui/Dialog";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { Textarea } from "../../components/ui/Textarea";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatCount } from "../../lib/format";
import { roleLabel } from "../settings/accounts/roles";
import { splitErrors } from "../settings/formErrors";
import { catalogQuery, recordReview, reviewsQuery, statusQuery, trailKeys, verifyQuery } from "./api";
import { RESULTS, actorName, isFiltered, matches, outcomeView } from "./data";
import type { AuditSearch } from "./data";

const PAGE = 50;
const ALL = "all";

function Outcome({ t, result }: { t: T; result: string }) {
  const { status, label } = outcomeView(t, result);
  return (
    <span className="inline-flex items-center gap-1.5 text-13 text-fg">
      <StatusGlyph state={status} className={stateTextClass(status)} />
      {label}
    </span>
  );
}

function Actor({ t, entry }: { t: T; entry: AuditEntry }) {
  const role = entry.who?.role;
  return (
    <span className="flex min-w-0 flex-col">
      <Mono tone="default" truncate>
        {actorName(entry)}
      </Mono>
      {role ? <span className="truncate text-12 text-fg-muted">{roleLabel(t, role)}</span> : null}
    </span>
  );
}

function columnsFor(t: T): Column<AuditEntry>[] {
  return [
    {
      id: "event",
      header: t("audit.table.event"),
      card: "title",
      cell: (entry) => (
        <span className="flex min-w-0 flex-col">
          <Mono tone="default" truncate>
            {entry.action}
          </Mono>
          {entry.detail ? <span className="line-clamp-1 text-12 text-fg-muted">{entry.detail}</span> : null}
        </span>
      ),
    },
    { id: "outcome", header: t("audit.table.outcome"), card: "status", width: "w-32", cell: (entry) => <Outcome t={t} result={entry.result} /> },
    { id: "actor", header: t("audit.table.actor"), width: "w-44", cell: (entry) => <Actor t={t} entry={entry} /> },
    {
      id: "target",
      header: t("audit.table.target"),
      hideBelow: "md",
      cell: (entry) => (entry.resource ? <Mono tone="default" truncate>{entry.resource}</Mono> : <EmptyCell reason={t("audit.table.noTarget")} />),
    },
    {
      id: "source",
      header: t("audit.table.source"),
      hideBelow: "lg",
      width: "w-36",
      cell: (entry) => {
        const source = entry.client_ip ?? entry.who?.source ?? entry.who?.via ?? null;
        return source === null ? <EmptyCell reason={t("audit.table.noSource")} /> : <Mono tone="muted">{source}</Mono>;
      },
    },
    { id: "when", header: t("audit.table.when"), width: "w-32", cell: (entry) => <RelativeTime value={entry.timestamp} /> },
  ];
}

/** One event, every field of it, and the way to its siblings of the same request. */
function EntryDrawer({ entry, onClose, onCorrelate }: { entry: AuditEntry; onClose: () => void; onCorrelate: (id: string) => void }) {
  const t = useT();
  const items: KeyValueItem[] = [
    { label: t("audit.fields.action"), value: entry.action },
    { label: t("audit.fields.outcome"), value: <Outcome t={t} result={entry.result} />, mono: false, copy: false },
    { label: t("audit.fields.when"), value: entry.timestamp },
    { label: t("audit.fields.actor"), value: actorName(entry) },
    ...(entry.who?.role ? [{ label: t("audit.fields.role"), value: roleLabel(t, entry.who.role), mono: false, copy: false as const }] : []),
    ...(entry.who?.via ? [{ label: t("audit.fields.via"), value: entry.who.via }] : []),
    ...(entry.client_ip ? [{ label: t("audit.fields.source"), value: entry.client_ip }] : []),
    ...(entry.resource ? [{ label: t("audit.fields.target"), value: entry.resource }] : []),
    ...(entry.category ? [{ label: t("audit.fields.category"), value: entry.category }] : []),
    ...(entry.correlation_id ? [{ label: t("audit.fields.correlation"), value: entry.correlation_id }] : []),
    ...(entry.seq !== null && entry.seq !== undefined ? [{ label: t("audit.fields.seq"), value: String(entry.seq) }] : []),
  ];
  return (
    <Drawer
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={entry.action}
      description={entry.detail ?? undefined}
      {...(entry.correlation_id
        ? {
            footer: (
              <Button
                onClick={() => {
                  if (entry.correlation_id) onCorrelate(entry.correlation_id);
                }}
              >
                {t("audit.drawer.sameRequest")}
              </Button>
            ),
          }
        : {})}
    >
      <div className="flex flex-col gap-5">
        {entry.sensitive ? <Notice>{t("audit.drawer.sensitive")}</Notice> : null}
        <KeyValueList items={items} />
        {entry.details && Object.keys(entry.details).length > 0 ? (
          <div className="flex flex-col gap-1.5">
            <p className="text-13 font-medium text-fg">{t("audit.drawer.details")}</p>
            <SystemOutput label={t("audit.drawer.details")} maxHeight="max-h-80">
              {JSON.stringify(entry.details, null, 2)}
            </SystemOutput>
          </div>
        ) : null}
      </div>
    </Drawer>
  );
}

/**
 * Whether the log can be trusted, said once above it: the chain verified end to end (or the
 * first broken link, where it is), and whether events are being written and shipped.
 */
function TrailNotice({ t }: { t: T }) {
  const verify = useQuery(verifyQuery());
  const status = useQuery(statusQuery());
  if (verify.data === undefined && status.data === undefined) return null;
  const broken = verify.data !== undefined && !verify.data.ok;
  const problems = status.data?.problems ?? [];
  const degraded = (status.data?.sinks ?? []).filter((sink) => sink.degraded || sink.error);
  if (broken) {
    const link = verify.data.broken;
    return (
      <Notice tone="error" variant="banner" title={t("audit.trail.brokenTitle")}>
        <div className="flex flex-col gap-2">
          <p className="max-w-measure">{t("audit.trail.broken")}</p>
          {link ? <SystemOutput label={t("audit.trail.brokenLabel")}>{`${link.file}:${String(link.line)}${link.seq !== null && link.seq !== undefined ? ` seq ${String(link.seq)}` : ""}: ${link.reason}`}</SystemOutput> : null}
        </div>
      </Notice>
    );
  }
  if (problems.length > 0 || degraded.length > 0 || status.data?.failing === true) {
    return (
      <Notice tone="warning" variant="banner" title={t("audit.trail.problemsTitle")}>
        <div className="flex flex-col gap-2">
          <p className="max-w-measure">{t("audit.trail.problems")}</p>
          <SystemOutput label={t("audit.trail.problemsLabel")}>
            {[
              ...problems,
              ...(status.data?.last_failure ? [status.data.last_failure] : []),
              ...degraded.map((sink) => `${sink.sink_id}: ${sink.error ?? t("audit.trail.degraded")}`),
            ].join("\n")}
          </SystemOutput>
        </div>
      </Notice>
    );
  }
  if (verify.data !== undefined) {
    return (
      <Notice tone="success" title={t("audit.trail.intactTitle")}>
        {t("audit.trail.intact", {
          count: verify.data.checked,
          checked: formatCount(verify.data.checked, t.locale),
          sinks: formatCount(status.data?.sinks?.length ?? 0, t.locale),
        })}
      </Notice>
    );
  }
  return null;
}

/** Recording that the log was reviewed for a period: the evidence the review happened. */
function ReviewDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  // The last seven days, the period a weekly review covers, as the default.
  const [start, setStart] = useState(() => new Date(Date.now() - 7 * 86_400_000).toISOString().slice(0, 10));
  const [end, setEnd] = useState(() => new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState("");
  const formId = useId();
  const record = useMutation({
    mutationFn: () => recordReview({ period_start: start, period_end: end, notes: notes.trim() }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: trailKeys.reviews });
      toast.success(t("audit.review.recordedToast"));
      onClose();
    },
  });
  const errors = splitErrors(record.error, ["period_start", "period_end", "notes"] as const);
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !record.isPending) onClose();
      }}
      size="md"
      title={t("audit.review.title")}
      description={t("audit.review.description")}
      footer={
        <>
          <Button disabled={record.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={record.isPending}>
            {t("audit.review.submit")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          if (!record.isPending) record.mutate();
        }}
      >
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("audit.review.failed")} /> : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("audit.review.from")} error={errors.fields.period_start}>
            <Input type="date" value={start} onValueChange={(value: string) => setStart(value)} />
          </Field>
          <Field label={t("audit.review.to")} error={errors.fields.period_end}>
            <Input type="date" value={end} onValueChange={(value: string) => setEnd(value)} />
          </Field>
        </div>
        <Field label={t("audit.review.notes")} optional description={t("audit.review.notesHint")} error={errors.fields.notes}>
          <Textarea
            rows={4}
            maxLength={4000}
            value={notes}
            onChange={(event) => {
              setNotes(event.target.value);
            }}
          />
        </Field>
      </form>
    </Dialog>
  );
}

function ReviewsSection({ t }: { t: T }) {
  const query = useQuery(reviewsQuery());
  const items = query.data?.items ?? [];
  return (
    <Section title={t("audit.reviews.title")} description={t("audit.reviews.description")}>
      {query.isError ? (
        <ErrorBlock compact error={query.error} title={t("audit.reviews.loadFailed")} onRetry={() => void query.refetch()} />
      ) : (
        <DataTable
          caption={t("audit.reviews.caption")}
          columns={[
            { id: "when", header: t("audit.reviews.when"), card: "title", cell: (item) => <RelativeTime value={item.timestamp} /> },
            { id: "reviewer", header: t("audit.reviews.reviewer"), cell: (item) => <Mono tone="default">{item.reviewer}</Mono> },
            {
              id: "period",
              header: t("audit.reviews.period"),
              hideBelow: "sm",
              cell: (item) => (
                <Mono tone="default">
                  {item.period_start ?? ""} – {item.period_end ?? ""}
                </Mono>
              ),
            },
            {
              id: "chain",
              header: t("audit.reviews.chain"),
              hideBelow: "md",
              cell: (item) =>
                item.chain_ok === null || item.chain_ok === undefined ? (
                  <EmptyCell reason={t("audit.reviews.chainUnknown")} />
                ) : (
                  <Outcome t={t} result={item.chain_ok ? "ok" : "failure"} />
                ),
            },
          ]}
          rows={items}
          getRowId={(item) => item.timestamp}
          density="compact"
          mobile="cards"
          loading={query.isPending}
          skeletonRows={1}
          empty={<EmptyState variant="inline" title={t("audit.reviews.none")} />}
        />
      )}
    </Section>
  );
}

export interface AuditPageProps {
  search: AuditSearch;
  onSearchChange: (search: AuditSearch, options?: { replace?: boolean }) => void;
}

/**
 * Settings > Audit log (the central's): every privileged action, who did it, with which role,
 * from where, on what, and how it ended - newest first, a page at a time. The chain that makes
 * a rewrite detectable is verified above the list; an auditor records their review of it.
 */
export function AuditPage({ search, onSearchChange }: AuditPageProps) {
  const t = useT();
  useDocumentTitle(t("audit.page.documentTitle"), 1);
  const catalog = useQuery(catalogQuery());
  const filters = {
    limit: PAGE,
    ...(search.category !== undefined ? { category: search.category } : {}),
    ...(search.result !== undefined ? { result: search.result } : {}),
    ...(search.correlation !== undefined ? { correlation_id: search.correlation } : {}),
  };
  const query = useInfiniteQuery(auditPagesQuery(filters));
  const [open, setOpen] = useState<AuditEntry | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const loaded = useMemo(() => (query.data?.pages ?? []).flatMap((page) => page.items), [query.data]);
  const shown = useMemo(() => loaded.filter((entry) => matches(entry, search.q)), [loaded, search.q]);
  const columns = columnsFor(t);
  const set = (patch: { [K in keyof AuditSearch]?: AuditSearch[K] | undefined }, replace = false): void => {
    const next = { ...search, ...patch };
    onSearchChange(Object.fromEntries(Object.entries(next).filter(([, value]) => value !== undefined && value !== "")), { replace });
  };

  return (
    <Sections>
      <TrailNotice t={t} />
      <Section
        title={t("audit.page.title")}
        description={t("audit.page.description")}
        actions={
          <>
            <a href="/api/audit/export" download className={buttonClassName("secondary", "md")}>
              <Download aria-hidden="true" className="size-icon-md" />
              {t("audit.page.export")}
            </a>
            <Button variant="primary" icon={<ScrollText aria-hidden="true" />} onClick={() => setReviewing(true)}>
              {t("audit.page.review")}
            </Button>
          </>
        }
      >
        <div className="flex min-w-0 flex-col gap-4">
          <FilterBar
            label={t("audit.page.filterLabel")}
            search={{
              value: search.q ?? "",
              onChange: (value) => set({ q: value }, true),
              label: t("audit.page.searchLabel"),
              placeholder: t("audit.page.searchPlaceholder"),
            }}
            filters={
              <>
                <Select
                  aria-label={t("audit.page.categoryFilter")}
                  value={search.category ?? ALL}
                  onValueChange={(value) => set({ category: value === ALL ? undefined : value })}
                  options={[{ value: ALL, label: t("audit.page.everyCategory") }, ...(catalog.data?.categories ?? []).map((category) => ({ value: category, label: category }))]}
                  mono
                  className="min-w-36"
                />
                <Select
                  aria-label={t("audit.page.outcomeFilter")}
                  value={search.result ?? ALL}
                  onValueChange={(value) => set({ result: value === ALL ? undefined : value })}
                  options={[{ value: ALL, label: t("audit.page.everyOutcome") }, ...RESULTS.map((result) => ({ value: result, label: result }))]}
                  mono
                  className="min-w-36"
                />
              </>
            }
            {...(query.data !== undefined ? { count: t("audit.page.count", { count: shown.length }) } : {})}
            {...(isFiltered(search)
              ? {
                  actions: (
                    <Button variant="ghost" onClick={() => onSearchChange({})}>
                      {t("audit.page.clearFilters")}
                    </Button>
                  ),
                }
              : {})}
          />
          {search.correlation !== undefined ? (
            <Notice
              title={t("audit.page.correlationTitle")}
              action={
                <Button size="sm" onClick={() => set({ correlation: undefined })}>
                  {t("audit.page.correlationClear")}
                </Button>
              }
            >
              {t.rich("audit.page.correlation", { id: <Mono key="id">{search.correlation}</Mono> })}
            </Notice>
          ) : null}
          {query.isError && query.data === undefined ? (
            <ErrorBlock error={query.error} title={t("audit.page.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
          ) : (
            <DataTable
              caption={t("audit.page.caption")}
              columns={columns}
              rows={shown}
              getRowId={(entry) => entry.id ?? `${entry.timestamp}-${entry.action}-${entry.actor}`}
              density="compact"
              mobile="cards"
              loading={query.isPending}
              skeletonRows={8}
              onRowActivate={setOpen}
              empty={<EmptyState variant="inline" title={isFiltered(search) ? t("audit.page.noMatch") : t("audit.page.none")} />}
            />
          )}
          {query.hasNextPage ? (
            <Button className="self-center" loading={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>
              {t("audit.page.loadMore")}
            </Button>
          ) : null}
        </div>
      </Section>
      <ReviewsSection t={t} />
      <CommandHint label={t("audit.page.fromTerminal")} command="noust audit verify && noust audit list" />
      {open !== null ? (
        <EntryDrawer
          entry={open}
          onClose={() => setOpen(null)}
          onCorrelate={(id) => {
            setOpen(null);
            set({ correlation: id, q: undefined });
          }}
        />
      ) : null}
      {reviewing ? <ReviewDialog onClose={() => setReviewing(false)} /> : null}
    </Sections>
  );
}
