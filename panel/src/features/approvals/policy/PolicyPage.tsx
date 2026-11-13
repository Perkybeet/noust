import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { isApiError } from "../../../api/client";
import { callerPolicyKeys, callerPolicyQuery, previewCallerPolicy, saveCallerPolicy } from "../../../api/queries/callerPolicy";
import type { PolicyChange, PolicyPreview, PolicyRow, PolicyTable } from "../../../api/queries/callerPolicy";
import { useDocumentTitle } from "../../../app/documentTitle";
import { PolicyMatrix, PolicyValueMark } from "../../../components/page/PolicyMatrix";
import type { PolicyValue } from "../../../components/page/PolicyMatrix";
import { ReadOnlyLine, useCan } from "../../../components/page/permissions";
import { QueryState } from "../../../components/page/QueryState";
import { Sections } from "../../../components/page/Section";
import { SaveBar } from "../../../components/page/SaveBar";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Disclosure } from "../../../components/ui/Disclosure";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ICONS } from "../../../components/ui/icons";
import { Notice } from "../../../components/ui/Notice";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { PlanPreview } from "../../../components/ui/PlanPreview";
import type { Plan } from "../../../components/ui/PlanPreview";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { SM_UP, useMediaQuery } from "../../../components/ui/useMediaQuery";
import { hasMessage, useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { AREA_ORDER, changeBody, cellKey, effectiveEdits, rowChanged, rowModel } from "./policyModel";
import type { Edits } from "./policyModel";
import type { PolicySearch } from "./search";

const dynamic = (t: T): ((key: string, params?: Record<string, string | number>) => string) => t as unknown as (key: string, params?: Record<string, string | number>) => string;

/** A row's words: this console's own text for the permission, and the server's English only for one it has never heard of. */
function permissionLabel(t: T, row: PolicyRow): string {
  const key = `callerPolicy.permission.${row.permission.replaceAll(".", "_")}`;
  return hasMessage(key) ? dynamic(t)(key) : row.description;
}

function areaTitle(t: T, area: string): string {
  const key = `callerPolicy.areas.${area}`;
  return hasMessage(key) ? dynamic(t)(key) : area;
}

/** The line of the floor that applies to a row the assistant never does: one sentence for the kind of thing it is. */
function floorLine(t: T, table: PolicyTable, row: PolicyRow): string | null {
  if (!table.floor.assistant_never.includes(row.permission)) return null;
  const kind = row.permission === "secrets.reveal" ? "secrets" : row.permission === "root_equivalent" ? "root" : /^(accounts|teams|audit)\./.test(row.permission) ? "people" : /^(security|settings)\./.test(row.permission) ? "self" : "machine";
  return t(`callerPolicy.floor.${kind}`);
}

function Legend({ changed, view, onView, ens }: { changed: number; view: "all" | "changed"; onView: (view: "all" | "changed") => void; ens: boolean }) {
  const t = useT();
  const wide = useMediaQuery(SM_UP);
  const body = (
    <div className="flex flex-col gap-4">
      <dl className="m-0 grid gap-3 sm:grid-cols-3">
        {(["alone", "propose", "never"] as const).map((value) => (
          <div key={value} className="min-w-0">
            <dt className="text-13 font-medium text-fg">
              <PolicyValueMark value={value} />
            </dt>
            <dd className="m-0 pl-6 text-12 text-pretty text-fg-muted">{t(`callerPolicy.valueLine.${value}`)}</dd>
          </div>
        ))}
      </dl>
      <p className="flex items-start gap-1.5 text-12 text-pretty text-fg-muted">
        <ICONS.locked aria-hidden="true" className="mt-0.5 size-icon-xs shrink-0 text-fg-faint" />
        {t("callerPolicy.page.floorLine")}
      </p>
      <p className="flex items-start gap-1.5 text-12 text-pretty text-fg-muted">
        <ICONS.elevated aria-hidden="true" className="mt-0.5 size-icon-xs shrink-0 text-fg-faint" />
        {t("callerPolicy.page.keyLine")}
      </p>
      {ens ? <Notice title={t("callerPolicy.page.ensTitle")}>{t("callerPolicy.page.ensBody")}</Notice> : null}
    </div>
  );
  return (
    <SurfaceSection
      title={t("callerPolicy.page.legendTitle")}
      icon={OBJECT_ICONS.protection}
      description={t("callerPolicy.page.legendDescription")}
      actions={
        <SegmentedControl<"all" | "changed">
          label={t("callerPolicy.page.filter")}
          options={[
            { value: "all", label: t("callerPolicy.page.filterAll") },
            { value: "changed", label: t("callerPolicy.page.filterChanged"), count: changed },
          ]}
          value={view}
          onValueChange={onView}
        />
      }
    >
      {wide ? body : <Disclosure label={t("callerPolicy.page.legendValues")}>{body}</Disclosure>}
    </SurfaceSection>
  );
}

/** What saving would do to the credentials that exist, with Noust's own list of the orders it would run in "See what it will do". */
function SaveDialog({ body, count, onClose, onSaved }: { body: PolicyChange; count: number; onClose: () => void; onSaved: () => void }) {
  const t = useT();
  const preview = useQuery({ queryKey: [...callerPolicyKeys.table, "preview", body] as const, queryFn: () => previewCallerPolicy(body), staleTime: 0 });
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: () => saveCallerPolicy(body),
    onSuccess: (table) => {
      queryClient.setQueryData(callerPolicyKeys.table, table);
      onSaved();
    },
  });
  const data: PolicyPreview | undefined = preview.data;
  const plan: Plan | null = useMemo(
    () =>
      data === undefined
        ? null
        : {
            operation: "approvals.policy",
            subject: null,
            effect: { code: "callerPolicy.save.effect", params: {}, text: t("callerPolicy.save.effect"), hint: null },
            commands: data.commands.map((text) => ({ argv: text.split(" "), text, probe: false, cwd: null, runs_as: null, step: null, writes_to: null, check: false, seconds: null })),
            files: [],
            steps: [],
            findings: [],
            unknown: [{ code: "callerPolicy.save.unknown", params: {}, text: t("callerPolicy.save.unknown"), hint: null }],
            needs: { elevation: true, approval: data.needs_second_person, confirmations: [] },
            blocked: null,
            asked_remote: false,
            omitted: 0,
          },
    [data, t],
  );
  return (
    <ConfirmDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      friction="simple"
      destructive={false}
      size="md"
      title={t("callerPolicy.save.title")}
      description={t("callerPolicy.save.description")}
      actionLabel={t("callerPolicy.save.action")}
      onConfirm={async () => {
        await save.mutateAsync();
      }}
    >
      <div className="flex flex-col gap-4">
        <p className="text-13 text-fg">{t("callerPolicy.save.changes", { count })}</p>
        {data !== undefined ? (
          <>
            {data.credentials.length > 0 ? (
              <section aria-label={t("callerPolicy.save.effectsTitle")} className="flex flex-col gap-2">
                <p className="text-13 font-medium text-fg">{t("callerPolicy.save.effectsTitle")}</p>
                <ul className="m-0 flex list-disc flex-col gap-2 pl-5 text-13 text-pretty text-fg">
                  {data.credentials.map((credential) => (
                    <li key={`${credential.kind}:${String(credential.id)}`}>
                      <span className="font-medium">{credential.kind === "assistant" ? t("callerPolicy.save.assistantEffects") : t("callerPolicy.save.tokenEffects", { name: credential.name ?? "" })}</span>
                      <ul className="m-0 mt-1 flex list-none flex-col gap-0.5 p-0 text-12 text-fg-muted">
                        {credential.effects.map((effect) => (
                          <li key={`${effect.permission}:${effect.environment}`}>
                            {t("callerPolicy.save.effectLine", {
                              permission: effect.permission,
                              environment: t(`callerPolicy.environment.${effect.environment === "other" ? "other" : "production"}`).toLowerCase(),
                              before: t(`callerPolicy.value.${effect.before === "alone" || effect.before === "propose" ? effect.before : "never"}`).toLowerCase(),
                              after: t(`callerPolicy.value.${effect.after === "alone" || effect.after === "propose" ? effect.after : "never"}`).toLowerCase(),
                            })}
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            {data.waiting > 0 ? <p className="text-12 text-fg-muted">{t("callerPolicy.save.waiting", { count: data.waiting })}</p> : null}
            {data.needs_second_person ? <Notice tone="warning">{t("callerPolicy.save.second")}</Notice> : null}
            {plan !== null ? <PlanPreview plan={plan} from={t("callerPolicy.save.from")} defaultOpen /> : null}
          </>
        ) : null}
      </div>
    </ConfirmDialog>
  );
}

function PolicyTables({ table, search, onSearch }: { table: PolicyTable; search: PolicySearch; onSearch: (next: PolicySearch) => void }) {
  const t = useT();
  const canChange = useCan("security.manage");
  const [edits, setEdits] = useState<Edits>({});
  const [saving, setSaving] = useState(false);
  const view = search.view === "changed" ? "changed" : "all";
  const changedRows = table.rows.filter((row) => rowChanged(row, edits));
  const pending = effectiveEdits(table, edits);
  const areas = [...AREA_ORDER, ...new Set(table.rows.map((row) => row.area))].filter((area, index, all) => all.indexOf(area) === index);
  return (
    <Sections>
      {canChange ? null : <ReadOnlyLine permission="security.manage" />}
      <Legend changed={table.chosen} view={view} onView={(next) => onSearch(next === "all" ? {} : { view: next })} ens={table.profile.token_production_approval} />
      {view === "changed" && changedRows.length === 0 ? <p className="text-13 text-fg-muted">{t("callerPolicy.page.noneChanged")}</p> : null}
      {areas.map((area) => {
        const rows = table.rows.filter((row) => row.area === area && (view === "all" || rowChanged(row, edits)));
        if (rows.length === 0) return null;
        return (
          <SurfaceSection key={area} title={areaTitle(t, area)} icon={OBJECT_ICONS.approvalPolicy} flush>
            <PolicyMatrix
              caption={t("callerPolicy.matrix.table", { area: areaTitle(t, area) })}
              rows={rows.map((row) => rowModel(row, edits, permissionLabel(t, row), floorLine(t, table, row)))}
              {...(canChange
                ? {
                    onChange: (row: { permission: string }, cell: { caller: string; environment: string }, value: PolicyValue) =>
                      setEdits((current) => ({ ...current, [cellKey(row.permission, cell.caller, cell.environment)]: value })),
                  }
                : {})}
            />
          </SurfaceSection>
        );
      })}
      {canChange ? <SaveBar changes={pending.length} onDiscard={() => setEdits({})} onSave={() => setSaving(true)} /> : null}
      {saving ? (
        <SaveDialog
          body={changeBody(table, edits)}
          count={pending.length}
          onClose={() => setSaving(false)}
          onSaved={() => {
            setSaving(false);
            setEdits({});
          }}
        />
      ) : null}
    </Sections>
  );
}

/**
 * Settings > Approval policy (item 184): for each permission that changes something, what a token and what the assistant do in
 * production and in the other environments. Reading is not approved, so only changes are here; the legend says what the three values
 * mean, the floor is a padlock, a row that asks you to confirm it is you never offers "Does it", a cell that is not the factory's says
 * what the factory's was, and saving asks to confirm it is you and shows what it does to the credentials that exist.
 */
export function PolicyPage({ search, onSearch }: { search: PolicySearch; onSearch: (next: PolicySearch) => void }) {
  const t = useT();
  useDocumentTitle(t("callerPolicy.page.documentTitle"), 1);
  const query = useQuery(callerPolicyQuery());
  if (query.isError && isApiError(query.error) && query.error.status === 404) {
    // A node that runs an older Noust: the policy is each server's own, and this one has none to show.
    return <EmptyState variant="inline" title={t("callerPolicy.page.noPolicyTitle")} description={t("callerPolicy.page.noPolicyBody")} />;
  }
  return (
    <QueryState query={query} label={t("callerPolicy.page.loadingLabel")} skeleton={<SkeletonCard lines={6} />}>
      {(table) => <PolicyTables table={table} search={search} onSearch={onSearch} />}
    </QueryState>
  );
}


