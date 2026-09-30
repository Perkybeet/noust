import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { createContext, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { ElevationCancelledError } from "../../api/client";
import { ErrorBlock } from "../../components/page/QueryState";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Stepper } from "../../components/page/Stepper";
import { WizardActions } from "../../components/page/Wizard";
import { Badge } from "../../components/ui/Badge";
import { Checkbox } from "../../components/ui/Checkbox";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, StatusPill, stateTextClass } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import {
  ACTION_ORDER,
  ACTION_WORDS,
  NODE_JOB_VIEW,
  batchOf,
  OUTCOME_VIEW,
  SKIP_REASONS,
  accessOf,
  fleetActionsQuery,
  fleetKeys,
  fleetViewQuery,
  labelsOf,
  matchesLabels,
  nodeJobState,
  originOf,
  outcomeKind,
  parseLabelSelector,
  runFleetAction,
} from "./data";
import type { FleetActionBody, FleetNodeState, FleetPlan, KnownAction } from "./data";

// ---------------------------------------------------------------------------------------
// Opening it from anywhere on the fleet's pages

export interface BulkActionRequest {
  /** The action already chosen (a tab's own: Renew certificates on Certificates). */
  action?: KnownAction | undefined;
  /** The servers already chosen (the Servers tab's selection). */
  nodes?: readonly string[] | undefined;
}

interface FleetActionsApi {
  open: (request?: BulkActionRequest) => void;
}

const FleetActionsContext = createContext<FleetActionsApi>({ open: () => undefined });

/** Opens the bulk action dialog, from any page under the fleet's layout. */
export function useFleetActions(): FleetActionsApi {
  return useContext(FleetActionsContext);
}

/** Holds the one bulk action dialog of the fleet's pages. */
export function FleetActionsProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<BulkActionRequest | null>(null);
  const [opened, setOpened] = useState(0);
  const api = useMemo<FleetActionsApi>(
    () => ({
      open: (next = {}) => {
        setRequest(next);
        setOpened((count) => count + 1);
      },
    }),
    [],
  );
  return (
    <FleetActionsContext value={api}>
      {children}
      {request !== null ? (
        <BulkActionDialog
          key={opened}
          initial={request}
          onClose={() => {
            setRequest(null);
          }}
        />
      ) : null}
    </FleetActionsContext>
  );
}

// ---------------------------------------------------------------------------------------
// The dialog

type Step = "action" | "servers" | "plan";
type TargetMode = "name" | "label";

/** A batch size as typed: a number of servers, or a share of them ("25%"); empty is the action's. */
export function parseSerial(value: string): number | string | null | undefined {
  const text = value.trim();
  if (text === "") return null;
  if (/^\d{1,3}%$/.test(text)) {
    const percent = Number(text.slice(0, -1));
    return percent >= 1 && percent <= 100 ? text : undefined;
  }
  if (/^\d{1,4}$/.test(text) && Number(text) >= 1) return Number(text);
  return undefined;
}

/** How many failures stop the rest, as typed; empty is the action's own default. */
export function parseMaxFailures(value: string): number | null | undefined {
  const text = value.trim();
  if (text === "") return null;
  return /^\d{1,4}$/.test(text) ? Number(text) : undefined;
}

interface Options {
  force: boolean;
  verify: boolean;
  scope: "security" | "all";
}

const DEFAULT_OPTIONS: Options = { force: false, verify: true, scope: "security" };

/** The options each action takes, as the central checks them. */
function optionsFor(action: KnownAction, options: Options): Record<string, unknown> {
  switch (action) {
    case "certs_renew":
      return { force: options.force };
    case "backups_run":
      return { verify: options.verify };
    case "apps_update":
      return { force: options.force };
    case "os_updates":
      return { scope: options.scope };
    default:
      return {};
  }
}

function skipWords(t: T, node: FleetNodeState): string {
  const key = node.reason !== null && node.reason !== undefined ? SKIP_REASONS[node.reason] : undefined;
  if (key !== undefined) return t(key);
  return node.reason ?? t("fleet.jobs.state.skipped");
}

/** What the plan will do to one server: run in a batch, or be skipped and why. */
function planColumns(t: T, canary: unknown): Column<FleetNodeState>[] {
  return [
    {
      id: "server",
      header: t("fleet.column.server"),
      card: "title",
      cell: (node) => <Mono className="font-medium">{node.node}</Mono>,
    },
    {
      id: "what",
      header: t("fleet.bulk.plan.column"),
      card: "status",
      cell: (node) => {
        const state = nodeJobState(node.state);
        if (state === "queued") {
          const batch = batchOf(node, canary);
          return (
            <span className="inline-flex items-center gap-1.5 text-13 text-fg">
              <StatusGlyph state="queued" size={12} className="text-warn" />
              {batch.canary ? t("fleet.bulk.plan.canary") : t("fleet.bulk.plan.batch", { batch: batch.number })}
            </span>
          );
        }
        const view = NODE_JOB_VIEW[state];
        return (
          <span className="flex min-w-0 flex-col">
            <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />
            <span className="text-12 text-fg-muted">{skipWords(t, node)}</span>
          </span>
        );
      },
    },
    {
      id: "sudo",
      header: t("fleet.bulk.plan.sudo"),
      hideBelow: "sm",
      card: "meta",
      cell: (node) =>
        node.requires_elevation ? <span className="text-13 text-fg">{t("fleet.bulk.plan.sudoYes")}</span> : <span className="text-13 text-fg-muted">{t("fleet.bulk.plan.sudoNo")}</span>,
    },
  ];
}

export function PlanView({ t, plan, action }: { t: T; plan: FleetPlan; action: KnownAction }) {
  const run = plan.summary["run"] ?? 0;
  const skipped = plan.summary["skipped"] ?? 0;
  const label = t(ACTION_WORDS[action].label);
  return (
    <div className="flex flex-col gap-4">
      <p className="text-14 text-pretty text-fg">
        {run > 0 ? t("fleet.bulk.plan.summary", { action: label, count: run }) : t("fleet.bulk.plan.nothing", { action: label })}
        {skipped > 0 ? ` ${t("fleet.bulk.plan.skipped", { count: skipped })}` : ""}
      </p>
      {plan.batches.length > 0 ? (
        <ol aria-label={t("fleet.bulk.plan.batchesLabel")} className="flex flex-col gap-1">
          {plan.batches.map((batch, index) => (
            <li key={batch.join(",")} className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-13">
              <span className="shrink-0 text-fg-muted">
                {plan.strategy["canary"] !== null && plan.strategy["canary"] !== undefined && index === 0
                  ? t("fleet.bulk.plan.canaryFirst")
                  : t("fleet.bulk.plan.batchLine", { batch: index + 1 })}
              </span>
              <Mono>{batch.join(", ")}</Mono>
            </li>
          ))}
        </ol>
      ) : null}
      <DataTable caption={t("fleet.bulk.plan.caption")} columns={planColumns(t, plan.strategy["canary"])} rows={plan.nodes} getRowId={(node) => node.node} density="compact" mobile="cards" />
      {plan.requires_elevation ? <Notice>{t("fleet.bulk.plan.elevation")}</Notice> : null}
      {action === "noust_update" ? <Notice>{t("fleet.bulk.plan.centralLast")}</Notice> : null}
      {action === "os_updates" ? <Notice tone="warning">{t("fleet.bulk.plan.noReboot")}</Notice> : null}
    </div>
  );
}

export interface BulkActionDialogProps {
  initial: BulkActionRequest;
  onClose: () => void;
}

/**
 * One action on several servers, as a job of the central (T5 in a dialog): what to run, on which
 * servers - chosen by hand or by label - and how (a batch at a time, a canary first, stop after
 * so many failures), then the plan the central makes before anything runs: which servers run it,
 * in which batch, which are skipped and why. Running it opens the job, server by server.
 */
export function BulkActionDialog({ initial, onClose }: BulkActionDialogProps) {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const actions = useQuery(fleetActionsQuery());
  const servers = useQuery(fleetViewQuery("servers"));

  const [step, setStep] = useState<Step>(initial.action !== undefined ? "servers" : "action");
  const [action, setAction] = useState<KnownAction | null>(initial.action ?? null);
  const [options, setOptions] = useState<Options>(DEFAULT_OPTIONS);
  const [mode, setMode] = useState<TargetMode>("name");
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set(initial.nodes ?? []));
  const [labelText, setLabelText] = useState("");
  const [serial, setSerial] = useState("");
  const [maxFailures, setMaxFailures] = useState("");
  const [canary, setCanary] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Partial<Record<"labels" | "serial" | "maxFailures", string>>>({});
  const serialRef = useRef<HTMLInputElement>(null);

  const nodes = useMemo(() => (servers.data?.items ?? []).filter((row) => !originOf(row).local), [servers.data]);
  const selector = parseLabelSelector(labelText);
  const byLabel = selector === null ? [] : nodes.filter((row) => Object.keys(selector).length > 0 && matchesLabels(labelsOf(row), selector)).map((row) => originOf(row).node);
  const targets = mode === "name" ? [...selected] : byLabel;
  const spec = actions.data?.actions.find((candidate) => candidate.name === action);

  const body = (plan: boolean): FleetActionBody | null => {
    if (action === null) return null;
    const parsedSerial = parseSerial(serial);
    const parsedMax = parseMaxFailures(maxFailures);
    if (parsedSerial === undefined || parsedMax === undefined) return null;
    return {
      action,
      targets: mode === "name" ? { nodes: [...selected], labels: {} } : { nodes: [], labels: selector ?? {} },
      strategy: { serial: parsedSerial, max_failures: parsedMax, canary: canary === "" ? null : canary },
      options: optionsFor(action, options),
      plan,
    };
  };

  const planning = useMutation({
    mutationFn: (request: FleetActionBody) => runFleetAction(request),
  });
  const running = useMutation({
    mutationFn: (request: FleetActionBody) => runFleetAction(request),
    onSuccess: (answer) => {
      void queryClient.invalidateQueries({ queryKey: fleetKeys.jobs });
      const job = answer.job;
      if (job === null || job === undefined) return;
      onClose();
      void navigate({ to: "/fleet/jobs/$id", params: { id: job.job_id } });
    },
  });
  const pending = planning.isPending || running.isPending;

  const close = (next: boolean): void => {
    // "Confirm it's you" opens over this dialog while the job starts: it is not a way out.
    if (next || running.isPending) return;
    onClose();
  };

  const goToPlan = (): void => {
    const errors: typeof fieldErrors = {};
    if (parseSerial(serial) === undefined) errors.serial = t("fleet.bulk.how.serialInvalid");
    if (parseMaxFailures(maxFailures) === undefined) errors.maxFailures = t("fleet.bulk.how.maxFailuresInvalid");
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) {
      serialRef.current?.focus();
      return;
    }
    const request = body(true);
    if (request === null) return;
    setStep("plan");
    planning.mutate(request);
  };

  // What each step still needs, said when Continue is pressed without it.
  let missing: string | undefined;
  let onMissing: (() => void) | undefined;
  if (step === "action" && action === null) {
    missing = t("fleet.bulk.action.missing");
    onMissing = () => document.querySelector<HTMLElement>('[data-slot="bulk-actions"] [role="radio"]')?.focus();
  } else if (step === "servers") {
    if (mode === "label" && selector === null) {
      missing = t("fleet.bulk.servers.labelsInvalid");
      onMissing = () => document.querySelector<HTMLElement>("[data-bulk-labels]")?.focus();
    } else if (targets.length === 0) {
      missing = mode === "name" ? t("fleet.bulk.servers.noneChosen") : t("fleet.bulk.servers.noneMatch");
      onMissing = () => document.querySelector<HTMLElement>(mode === "label" ? "[data-bulk-labels]" : '[data-slot="bulk-servers"] button[role="checkbox"]')?.focus();
    }
  } else if (step === "plan" && planning.data !== undefined && (planning.data.plan.summary["run"] ?? 0) === 0) {
    missing = t("fleet.bulk.plan.nothingToRun");
  }

  const stepLabels = [
    { id: "action", label: t("fleet.bulk.steps.action") },
    { id: "servers", label: t("fleet.bulk.steps.servers") },
    { id: "plan", label: t("fleet.bulk.steps.plan") },
  ];

  let content: ReactNode;
  if (step === "action") {
    content = (
      <div data-slot="bulk-actions" className="flex flex-col gap-4">
        {actions.isError ? <ErrorBlock compact error={actions.error} title={t("fleet.bulk.action.loadFailed")} onRetry={() => void actions.refetch()} /> : null}
        <ChoiceCards
          legend={t("fleet.bulk.action.legend")}
          value={action ?? ("" as KnownAction)}
          onValueChange={(value) => {
            setAction(value);
          }}
          options={ACTION_ORDER.filter((name) => actions.data === undefined || actions.data.actions.some((candidate) => candidate.name === name)).map((name) => ({
            value: name,
            label: t(ACTION_WORDS[name].label),
            description: t(ACTION_WORDS[name].description),
          }))}
        />
      </div>
    );
  } else if (step === "servers" && action !== null) {
    const allChosen = nodes.length > 0 && nodes.every((row) => selected.has(originOf(row).node));
    content = (
      <div data-slot="bulk-servers" className="flex flex-col gap-6">
        <p className="text-14 text-pretty text-fg">{t("fleet.bulk.servers.intro", { action: t(ACTION_WORDS[action].label) })}</p>
        <OptionFields t={t} action={action} options={options} onChange={setOptions} />
        <SegmentedControl
          label={t("fleet.bulk.servers.modeLabel")}
          value={mode}
          onValueChange={setMode}
          options={[
            { value: "name", label: t("fleet.bulk.servers.byName") },
            { value: "label", label: t("fleet.bulk.servers.byLabel") },
          ]}
        />
        {mode === "name" ? (
          servers.isPending ? (
            <div aria-busy="true" className="flex flex-col gap-2">
              <span className="sr-only">{t("fleet.bulk.servers.loading")}</span>
              <Skeleton className="h-control-md w-full" />
              <Skeleton className="h-control-md w-full" />
            </div>
          ) : (
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-2 text-13 font-medium text-fg">{t("fleet.bulk.servers.legend")}</legend>
              <Checkbox
                label={t("fleet.bulk.servers.all", { count: nodes.length })}
                checked={allChosen}
                indeterminate={!allChosen && selected.size > 0}
                onCheckedChange={(checked) => {
                  setSelected(checked ? new Set(nodes.map((row) => originOf(row).node)) : new Set());
                }}
              />
              <ul className="flex flex-col gap-1.5 border-t border-border pt-2">
                {nodes.map((row) => {
                  const origin = originOf(row);
                  const outcome = servers.data?.nodes.find((candidate) => candidate.name === origin.node);
                  const view = outcome === undefined ? null : OUTCOME_VIEW[outcomeKind(outcome)];
                  const access = accessOf(row);
                  const labels = labelsOf(row);
                  return (
                    <li key={origin.node}>
                      <Checkbox
                        label={<Mono className="font-medium">{origin.node}</Mono>}
                        description={
                          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                            {view !== null ? (
                              <span className={`inline-flex items-center gap-1 ${stateTextClass(view.state)}`}>
                                <StatusGlyph state={view.state} size={10} />
                                {t(view.label)}
                              </span>
                            ) : null}
                            {access !== null ? <span>{t(`fleet.access.short.${access.level}`)}</span> : null}
                            {labels.map(([key, value]) => (
                              <Badge key={key} mono>{`${key}=${value}`}</Badge>
                            ))}
                          </span>
                        }
                        checked={selected.has(origin.node)}
                        onCheckedChange={(checked) => {
                          setSelected((current) => {
                            const next = new Set(current);
                            if (checked) next.add(origin.node);
                            else next.delete(origin.node);
                            return next;
                          });
                        }}
                      />
                    </li>
                  );
                })}
              </ul>
            </fieldset>
          )
        ) : (
          <Field
            label={t("fleet.bulk.servers.labelsLabel")}
            description={
              byLabel.length > 0 ? t("fleet.bulk.servers.labelsMatch", { count: byLabel.length, names: byLabel.join(", ") }) : t("fleet.bulk.servers.labelsHelp")
            }
            error={fieldErrors.labels}
          >
            <Input
              data-bulk-labels=""
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              placeholder="env=prod"
              value={labelText}
              onValueChange={(value: string) => {
                setLabelText(value);
              }}
            />
          </Field>
        )}
        <fieldset className="flex flex-col gap-5">
          <legend className="mb-1 text-13 font-medium text-fg">{t("fleet.bulk.how.legend")}</legend>
          <div className="grid gap-5 sm:grid-cols-3">
            <Field label={t("fleet.bulk.how.serial")} description={t("fleet.bulk.how.serialHelp", { value: String(spec?.serial ?? 1) })} error={fieldErrors.serial} optional>
              <Input ref={serialRef} mono inputMode="text" autoComplete="off" placeholder={String(spec?.serial ?? 1)} value={serial} onValueChange={(value: string) => setSerial(value)} />
            </Field>
            <Field
              label={t("fleet.bulk.how.maxFailures")}
              description={
                spec?.max_failures === null || spec?.max_failures === undefined
                  ? t("fleet.bulk.how.maxFailuresHelpNever")
                  : t("fleet.bulk.how.maxFailuresHelp", { value: String(spec.max_failures) })
              }
              error={fieldErrors.maxFailures}
              optional
            >
              <Input mono inputMode="numeric" autoComplete="off" value={maxFailures} onValueChange={(value: string) => setMaxFailures(value)} />
            </Field>
            <Field label={t("fleet.bulk.how.canary")} description={t("fleet.bulk.how.canaryHelp")} optional>
              <Select
                value={canary === "" ? "@none" : canary}
                onValueChange={(value) => {
                  setCanary(value === "@none" ? "" : value);
                }}
                options={[{ value: "@none", label: t("fleet.bulk.how.noCanary") }, ...targets.map((name) => ({ value: name, label: name }))]}
              />
            </Field>
          </div>
        </fieldset>
      </div>
    );
  } else if (action !== null) {
    content = planning.isPending ? (
      <div aria-busy="true" className="flex flex-col gap-3">
        <span className="sr-only">{t("fleet.bulk.plan.loading")}</span>
        <Skeleton className="h-4 w-80 max-w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    ) : planning.isError ? (
      <ErrorBlock
        live
        error={planning.error}
        title={t("fleet.bulk.plan.failed")}
        onRetry={() => {
          const request = body(true);
          if (request !== null) planning.mutate(request);
        }}
      />
    ) : planning.data !== undefined ? (
      <div className="flex flex-col gap-4">
        <PlanView t={t} plan={planning.data.plan} action={action} />
        {running.isError && !(running.error instanceof ElevationCancelledError) ? (
          <ErrorBlock live error={running.error} title={t("fleet.bulk.run.failed", { action: t(ACTION_WORDS[action].label) })} />
        ) : null}
      </div>
    ) : null;
  }

  const runCount = planning.data?.plan.summary["run"] ?? 0;
  const next =
    step === "action"
      ? { label: t("fleet.bulk.next"), onClick: () => setStep("servers") }
      : step === "servers"
        ? { label: t("fleet.bulk.showPlan"), onClick: goToPlan }
        : {
            label: t("fleet.bulk.run.button", { count: runCount }),
            loading: running.isPending,
            onClick: () => {
              const request = body(false);
              if (request !== null) running.mutate(request);
            },
          };

  return (
    <Dialog
      open
      onOpenChange={close}
      size="lg"
      // The same name through every step: a dialog that renames itself loses a screen reader.
      title={t("fleet.bulk.title")}
      description={t("fleet.bulk.description")}
      footer={
        <WizardActions
          className="w-full"
          {...(step !== "action"
            ? {
                back: {
                  label: t("fleet.bulk.back"),
                  onClick: () => {
                    if (pending) return;
                    if (step === "plan") {
                      planning.reset();
                      running.reset();
                      setStep("servers");
                    } else setStep("action");
                  },
                },
              }
            : {})}
          next={next}
          {...(missing !== undefined ? { missing } : {})}
          {...(onMissing !== undefined ? { onMissing } : {})}
        />
      }
    >
      <div className="flex flex-col gap-5">
        <Stepper orientation="horizontal" steps={stepLabels} current={step} />
        {content}
      </div>
    </Dialog>
  );
}

/** An action's own options: those that change what it does on each server. */
function OptionFields({ t, action, options, onChange }: { t: T; action: KnownAction; options: Options; onChange: (options: Options) => void }) {
  if (action === "certs_renew" || action === "apps_update") {
    return (
      <Checkbox
        label={t(action === "certs_renew" ? "fleet.bulk.options.forceRenew" : "fleet.bulk.options.forceUpdate")}
        description={t(action === "certs_renew" ? "fleet.bulk.options.forceRenewHelp" : "fleet.bulk.options.forceUpdateHelp")}
        checked={options.force}
        onCheckedChange={(checked) => {
          onChange({ ...options, force: checked });
        }}
      />
    );
  }
  if (action === "backups_run") {
    return (
      <Checkbox
        label={t("fleet.bulk.options.verify")}
        description={t("fleet.bulk.options.verifyHelp")}
        checked={options.verify}
        onCheckedChange={(checked) => {
          onChange({ ...options, verify: checked });
        }}
      />
    );
  }
  if (action === "os_updates") {
    return (
      <ChoiceCards
        legend={t("fleet.bulk.options.scope")}
        value={options.scope}
        onValueChange={(scope) => {
          onChange({ ...options, scope });
        }}
        options={[
          { value: "security", label: t("fleet.bulk.options.scopeSecurity"), description: t("fleet.bulk.options.scopeSecurityHelp") },
          { value: "all", label: t("fleet.bulk.options.scopeAll"), description: t("fleet.bulk.options.scopeAllHelp") },
        ]}
      />
    );
  }
  return null;
}

