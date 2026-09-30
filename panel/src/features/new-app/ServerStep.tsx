import { Radio } from "@base-ui/react/radio";
import { RadioGroup } from "@base-ui/react/radio-group";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";

import type { WizardActionsProps } from "../../components/page/Wizard";
import { Badge } from "../../components/ui/Badge";
import { Mono } from "../../components/ui/Mono";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { NODE_STATE, nodeStatus, useHasFleet, useServerList } from "../../nodes/servers";
import { useNode } from "../../nodes/useNode";
import { useCentral } from "../central/central";
import { accessOf, fleetViewQuery, originOf } from "../fleet/data";
import type { AccessCeiling } from "../fleet/data";
import type { Step } from "./wizard";

declare module "@tanstack/history" {
  interface HistoryState {
    /**
     * The server the New application wizard's Server step chose, set by the navigation that
     * took the wizard there: it starts on its next step instead of asking again.
     */
    newAppServer?: string;
  }
}

/** This server, in the step's choice; no node name can be it (names are [a-z0-9-]). */
const THIS_SERVER = "@this";

interface ServerOption {
  value: string;
  name: string;
  /** Why it cannot take a new application, when it cannot. */
  unavailable: string | null;
  state: { state: "running" | "failed" | "unknown"; label: string } | null;
  version: string | null;
  access: AccessCeiling | null;
  thisServer: boolean;
}

/** The steps of a start, with Server first when the console holds a fleet. */
export function withServerStep(steps: readonly Step[], enabled: boolean): readonly Step[] {
  return enabled ? ["server", ...steps] : steps;
}

function unavailableWhy(t: T, name: string, reachable: boolean, access: AccessCeiling | null): string | null {
  if (!reachable) return t("fleet.newApp.notAnswering", { name });
  // Creating an application is what a central held to read, or to deploy, may not do there.
  if (access?.level === "read") return t("fleet.newApp.readOnly", { name });
  if (access?.level === "deploy") return t("fleet.newApp.deployOnly", { name });
  return null;
}

function ServerChoices({ t, options, value, onChange }: { t: T; options: readonly ServerOption[]; value: string; onChange: (value: string) => void }) {
  const base = useId();
  return (
    <div className="flex flex-col gap-2">
      <p id={`${base}-legend`} className="text-13 font-medium text-fg">
        {t("fleet.newApp.legend")}
      </p>
      <RadioGroup
        aria-labelledby={`${base}-legend`}
        value={value}
        onValueChange={(next: unknown) => {
          if (typeof next === "string") onChange(next);
        }}
        className="flex flex-col gap-2"
      >
        {options.map((option) => {
          const labelId = `${base}-${option.value}-label`;
          const descriptionId = `${base}-${option.value}-description`;
          const chosen = option.value === value;
          const disabled = option.unavailable !== null;
          return (
            <Radio.Root
              key={option.value}
              value={option.value}
              disabled={disabled}
              aria-labelledby={labelId}
              aria-describedby={descriptionId}
              className={cx(
                "flex items-start gap-2.5 rounded-control border px-3 py-2.5 text-left",
                "transition-[background-color,border-color] duration-(--duration-fast) ease-out",
                disabled ? "cursor-not-allowed border-border bg-bg-sunken" : "cursor-pointer",
                !disabled && (chosen ? "border-accent bg-accent-soft" : "border-border bg-surface hover:bg-surface-hover"),
              )}
            >
              <span
                aria-hidden="true"
                className={cx(
                  "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-pill border",
                  chosen ? "border-accent bg-accent" : "border-border-strong bg-surface",
                )}
              >
                <Radio.Indicator className="size-1.5 rounded-pill bg-on-accent" />
              </span>
              <span className="flex min-w-0 flex-col gap-0.5">
                <span id={labelId} className="flex min-w-0 flex-wrap items-center gap-x-2 text-13 font-medium text-fg">
                  <Mono>{option.name}</Mono>
                  {option.thisServer ? <span className="text-12 font-normal text-fg-muted">{t("fleet.selector.thisServer")}</span> : null}
                </span>
                <span id={descriptionId} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-12 text-fg-muted">
                  {option.state !== null ? (
                    <span className={cx("inline-flex items-center gap-1", stateTextClass(option.state.state))}>
                      <StatusGlyph state={option.state.state} size={10} />
                      <span className="text-fg-muted">{option.state.label}</span>
                    </span>
                  ) : null}
                  {option.version !== null ? <Badge mono>{option.version}</Badge> : null}
                  {option.access !== null ? <span>{t(`fleet.access.short.${option.access.level}`)}</span> : null}
                  {option.unavailable !== null ? <span className="text-fg">{option.unavailable}</span> : null}
                </span>
              </span>
            </Radio.Root>
          );
        })}
      </RadioGroup>
    </div>
  );
}

export interface ServerStepView {
  title: string;
  description: ReactNode;
  content: ReactNode;
  actions: WizardActionsProps;
}

export interface ServerStep {
  /** The console holds a fleet: the wizard asks on which server first. */
  enabled: boolean;
  /** The wizard begins on it: it was not answered on the way here. */
  startHere: boolean;
  /** The step itself, or null when there is no fleet. */
  view: ServerStepView | null;
}

export interface ServerStepHandlers {
  /** The server chosen is the one on screen: on to the next step. */
  onContinue: () => void;
  /** The fleet became known after the wizard opened, still untouched: it can start here now. */
  onArrive: () => void;
}

/**
 * The New application wizard's first step on a fleet: which server deploys it, so any server can
 * be deployed to from one place (spec 4.2). Each server says whether it answers, which Noust it
 * runs and what it lets this central do; one that cannot take a new application (not answering,
 * or holding this central to read or to deploy) is shown, disabled, with the reason.
 *
 * Choosing another server moves the wizard there (`/n/<server>/apps/new`): every later step
 * reads that server (its domains, its ports, its GitHub App), so the wizard must be on it. The
 * navigation says the step was answered, so the wizard starts on the next one.
 */
export function useServerStep(handlers: ServerStepHandlers): ServerStep {
  const t = useT();
  const navigate = useNavigate();
  const hasFleet = useHasFleet();
  const { node } = useNode();
  const { role } = useCentral();
  const servers = useServerList();
  const fleet = useQuery({ ...fleetViewQuery("servers"), enabled: hasFleet });
  const answered = useRouterState({ select: (state) => state.location.state.newAppServer });
  const here = node ?? THIS_SERVER;
  const [choice, setChoice] = useState<string | null>(null);

  const latest = useRef(handlers);
  useEffect(() => {
    latest.current = handlers;
  });
  const startHere = hasFleet && answered !== here;
  const initially = useRef(startHere);
  useEffect(() => {
    if (!startHere || initially.current) return;
    initially.current = true;
    latest.current.onArrive();
  }, [startHere]);

  const accessByNode = new Map((fleet.data?.items ?? []).filter((row) => !originOf(row).local).map((row) => [originOf(row).node, accessOf(row)]));
  const options: ServerOption[] = [
    // A hub deploys nothing itself: it is not a place for an application.
    ...(role === "hub"
      ? []
      : [
          {
            value: THIS_SERVER,
            name: servers.hostname ?? t("fleet.selector.thisServer"),
            unavailable: null,
            state: null,
            version: servers.version,
            access: null,
            thisServer: true,
          },
        ]),
    ...servers.nodes.map((record): ServerOption => {
      const status = nodeStatus(record);
      const view = NODE_STATE[status];
      const access = accessByNode.get(record.name) ?? null;
      const reachable = status === "reachable" || status === "unknown";
      return {
        value: record.name,
        name: record.name,
        unavailable: unavailableWhy(t, record.name, reachable, access),
        state: { state: view.state === "running" ? "running" : view.state === "failed" ? "failed" : "unknown", label: t(view.label) },
        version: record.version ?? null,
        access,
        thisServer: false,
      };
    }),
  ];
  const usable = options.filter((option) => option.unavailable === null);
  const fallback = options.some((option) => option.value === here && option.unavailable === null) ? here : (usable[0]?.value ?? null);
  const chosen = choice ?? fallback;

  if (!hasFleet) return { enabled: false, startHere: false, view: null };

  const next = (): void => {
    if (chosen === null) return;
    if (chosen === here) {
      latest.current.onContinue();
      return;
    }
    void navigate({
      to: "/apps/new",
      search: { node: chosen === THIS_SERVER ? undefined : chosen },
      state: (previous) => ({ ...previous, newAppServer: chosen }),
    });
  };

  return {
    enabled: true,
    startHere,
    view: {
      title: t("fleet.newApp.title"),
      description: t("fleet.newApp.description"),
      content:
        options.length === 0 ? (
          <p className="text-14 text-pretty text-fg-muted">{t("fleet.newApp.none")}</p>
        ) : (
          <ServerChoices t={t} options={options} value={chosen ?? ""} onChange={setChoice} />
        ),
      actions: {
        next: { onClick: next },
        ...(chosen === null ? { missing: usable.length === 0 ? t("fleet.newApp.noneUsable") : t("fleet.newApp.missing") } : {}),
      },
    },
  };
}
