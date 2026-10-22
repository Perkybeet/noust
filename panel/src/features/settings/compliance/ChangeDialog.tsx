import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { changeCompliance, complianceKeys, planCompliance } from "../../../api/queries/compliance";
import type { ComplianceChange, ComplianceControl, CompliancePlan } from "../../../api/queries/compliance";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { controlTitle, formatValue, warningText } from "./model";
import { EmptyState } from "../../../components/ui/EmptyState";

type Effect = CompliancePlan["effects"][number];

export interface ChangeDialogProps {
  /** What is to be changed; the server plans it, shows it here, and makes it on confirmation. */
  request: ComplianceChange;
  title: string;
  /** The sentence above the effects. */
  lead: string;
  action: string;
  /** Turning protection down: the confirmation is a danger button. */
  loosens?: boolean;
  /** What is said when it is done. */
  done: string;
  /** The controls, to word each effect's values (units, choices). */
  controls: readonly ComplianceControl[];
  /** Frameworks' names, to say who asks for each control. */
  names: ReadonlyMap<string, string>;
  /** A plan the caller already has (a control's change that raised a warning), so it is not asked twice. */
  plan?: CompliancePlan;
  onClose: () => void;
}

function effectColumns(t: T, controls: readonly ComplianceControl[], names: ReadonlyMap<string, string>): Column<Effect>[] {
  const byId = new Map(controls.map((control) => [control.id, control]));
  const word = (effect: Effect, value: unknown): string => {
    const control = byId.get(effect.control);
    return control === undefined ? String(value) : formatValue(t, control, value);
  };
  return [
    {
      id: "control",
      header: t("complianceSetup.dialog.control"),
      card: "title",
      cell: (effect) => <span className="text-13 text-fg">{controlTitle(t, effect.control)}</span>,
    },
    { id: "now", header: t("complianceSetup.dialog.now"), width: "w-36", cell: (effect) => <span className="text-13 text-fg-muted">{word(effect, effect.before)}</span> },
    { id: "then", header: t("complianceSetup.dialog.then"), width: "w-36", cell: (effect) => <span className="text-13 text-fg">{word(effect, effect.after)}</span> },
    {
      id: "because",
      header: t("complianceSetup.dialog.because"),
      hideBelow: "md",
      cell: (effect) => (
        <span className="flex min-w-0 flex-col gap-0.5">
          {effect.forced_by.map((id) => (
            <span key={id} className="text-12 text-fg-muted">
              {names.get(id) ?? id}: <Mono tone="default">{(effect.clauses[id] ?? []).join(", ")}</Mono>
            </span>
          ))}
        </span>
      ),
    },
  ];
}

/**
 * The confirmation of a change to the frameworks or the controls: what would change (control by
 * control, with the clause each one serves), what about this server the person must see first, and
 * the button. The plan is the server's, asked when the dialog opens and writing nothing; turning
 * protection down opens the usual "Confirm it's you" when the button is pressed.
 */
export function ChangeDialog({ request, title, lead, action, loosens = false, done, controls, names, plan: given, onClose }: ChangeDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [understood, setUnderstood] = useState(false);
  const planned = useQuery({
    queryKey: [...complianceKeys.all, "plan", request],
    queryFn: () => planCompliance(request),
    ...(given !== undefined ? { initialData: given } : {}),
    enabled: given === undefined,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
  const plan = planned.data;
  const warnings = plan?.warnings ?? [];
  const save = useMutation({
    mutationFn: () => changeCompliance({ ...request, acknowledge: understood }),
    onSuccess: () => {
      toast.success(done);
      void queryClient.invalidateQueries({ queryKey: complianceKeys.all });
      // The session says which profile is on, and the policy the rest of the console reads.
      void queryClient.invalidateQueries({ queryKey: ["ens"] });
      void queryClient.invalidateQueries({ queryKey: ["auth"] });
    },
  });
  // Nothing to confirm until the plan is here, and nothing to go on with until the warnings are read.
  const ready = plan !== undefined && (warnings.length === 0 || understood);

  return (
    <ConfirmDialog
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      friction="simple"
      destructive={loosens}
      size="lg"
      ready={ready}
      title={title}
      description={lead}
      actionLabel={action}
      onConfirm={async () => {
        await save.mutateAsync();
      }}
    >
      <div className="flex flex-col gap-4">
        {planned.isError ? (
          <ErrorBlock compact error={planned.error} title={t("complianceSetup.dialog.planFailed")} onRetry={() => void planned.refetch()} />
        ) : plan === undefined ? (
          <LoadingRegion label={t("complianceSetup.dialog.planning")}>
            <Skeleton className="h-32 w-full rounded-card" />
          </LoadingRegion>
        ) : plan.effects.length === 0 ? (
          <EmptyState variant="inline" title={t("complianceSetup.dialog.nothing")} />
        ) : (
          <DataTable
            caption={title}
            columns={effectColumns(t, controls, names)}
            rows={plan.effects}
            getRowId={(effect: Effect) => effect.control}
            density="compact"
          />
        )}
        {warnings.length > 0 ? (
          <Notice tone="warning" title={t("complianceSetup.warning.heading")}>
            <ul className="flex list-disc flex-col gap-1 pl-4">
              {warnings.map((warning) => (
                <li key={warning.id}>{warningText(t, warning)}</li>
              ))}
            </ul>
          </Notice>
        ) : null}
        {warnings.length > 0 ? (
          <Checkbox
            checked={understood}
            onCheckedChange={(on) => {
              setUnderstood(on);
            }}
            label={t("complianceSetup.dialog.understood")}
          />
        ) : null}
      </div>
    </ConfirmDialog>
  );
}
