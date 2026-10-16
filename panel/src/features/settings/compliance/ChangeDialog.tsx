import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { changeCompliance, complianceKeys, planCompliance } from "../../../api/queries/compliance";
import type { ComplianceChange, ComplianceControl, CompliancePlan } from "../../../api/queries/compliance";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Dialog } from "../../../components/ui/Dialog";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { controlTitle, formatValue, warningText } from "./model";

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
      onClose();
    },
  });
  const blocked = plan === undefined || (warnings.length > 0 && !understood);

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="lg"
      title={title}
      description={lead}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button
            variant={loosens ? "danger" : "primary"}
            disabled={blocked}
            loading={save.isPending}
            onClick={() => {
              save.mutate();
            }}
          >
            {action}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {save.isError ? <ErrorBlock live compact error={save.error} title={t("complianceSetup.dialog.failed")} /> : null}
        {planned.isError ? (
          <ErrorBlock compact error={planned.error} title={t("complianceSetup.dialog.planFailed")} onRetry={() => void planned.refetch()} />
        ) : plan === undefined ? (
          <div aria-busy="true">
            <span className="sr-only">{t("complianceSetup.dialog.planning")}</span>
            <Skeleton className="h-32 w-full rounded-card" />
          </div>
        ) : plan.effects.length === 0 ? (
          <p className="text-13 text-fg-muted">{t("complianceSetup.dialog.nothing")}</p>
        ) : (
          <DataTable
            caption={title}
            columns={effectColumns(t, controls, names)}
            rows={plan.effects}
            getRowId={(effect: Effect) => effect.control}
            density="compact"
            mobile="cards"
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
    </Dialog>
  );
}
