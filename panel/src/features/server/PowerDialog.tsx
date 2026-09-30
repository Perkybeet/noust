/**
 * Rebooting and shutting the server down, now or later, and the banner that says one is on
 * its way.
 *
 * Neither is a job: a job lives in the process a reboot kills. The API keeps who asked and
 * systemd keeps the schedule, which survives the console and can be cancelled. The dialog
 * shows the pre-checks first (what is running, whether the console and every application come
 * back, fstab, the newest kernel's ramdisk) and the operator schedules with them in view: a
 * warning turns the button into "Reboot anyway" and the request says it was read (`force`).
 *
 * A reboot asks once. A shutdown asks for the host name, as the API does: a powered-off VPS
 * can only be started again from the provider's panel.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Power, RotateCw } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../api/client";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatMoment, parseTimestamp } from "../../lib/format";
import { useNode } from "../../nodes/useNode";
import { ServerErrorBlock, explainServerError } from "./errors";
import { identityQuery, powerQuery, serverKeys } from "./queries";
import type { Power as PowerStatus, ScheduledPower } from "./queries";
import { toast } from "../../components/ui/toast";

export type PowerKind = "reboot" | "shutdown";

interface PowerApi {
  /** Opens the reboot or shutdown dialog. */
  open: (kind: PowerKind) => void;
}

const PowerContext = createContext<PowerApi>({ open: () => undefined });

/** Opens the Server area's reboot and shutdown dialog from anywhere under it. */
export function usePowerDialog(): PowerApi {
  return useContext(PowerContext);
}

/** Holds the dialog for every tab of the Server area. */
export function PowerProvider({ children }: { children: ReactNode }) {
  const [kind, setKind] = useState<PowerKind | null>(null);
  const open = useCallback((next: PowerKind) => setKind(next), []);
  const api = useMemo(() => ({ open }), [open]);
  return (
    <PowerContext value={api}>
      {children}
      {kind !== null ? <PowerDialog kind={kind} onClose={() => setKind(null)} /> : null}
    </PowerContext>
  );
}

type When = "soon" | "later" | "at";

const DELAYS = [5, 15, 30, 60, 120] as const;

/** A pre-check's name, by the id the API gives it; an unknown one is named by its id. */
export function checkLabel(t: T, id: string): string {
  switch (id) {
    case "jobs":
      return t("server.power.checks.jobs");
    case "console":
      return t("server.power.checks.console");
    case "apps":
      return t("server.power.checks.apps");
    case "fstab":
      return t("server.power.checks.fstab");
    case "ramdisk":
      return t("server.power.checks.ramdisk");
    default:
      return id;
  }
}

function PreChecks({ power, t }: { power: PowerStatus | undefined; t: T }) {
  if (power === undefined) {
    return (
      <div aria-busy="true" className="flex flex-col gap-2">
        <span className="sr-only">{t("server.power.checksLoading")}</span>
        {Array.from({ length: 5 }, (_, index) => (
          <Skeleton key={index} className="h-5 w-full" />
        ))}
      </div>
    );
  }
  return (
    <ul aria-label={t("server.power.checksLabel")} className="flex flex-col divide-y divide-border">
      {power.checks.map((check) => {
        const state = check.status === "ok" ? "running" : "warning";
        return (
          <li key={check.id} className="flex items-start gap-2.5 py-2">
            <StatusGlyph state={state} size={12} className={`mt-1 shrink-0 ${stateTextClass(state)}`} />
            <div className="flex min-w-0 flex-col">
              <span className="text-13 text-fg">
                {checkLabel(t, check.id)}
                <span className="sr-only">{check.status === "ok" ? t("server.power.checkOk") : t("server.power.checkWarn")}</span>
              </span>
              {/* Noust's own finding, as the API words it. */}
              <span className="text-12 text-fg-muted">{check.message}</span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function PowerDialog({ kind, onClose }: { kind: PowerKind; onClose: () => void }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const power = useQuery(powerQuery());
  const identity = useQuery({ ...identityQuery(), enabled: kind === "shutdown" });
  const [when, setWhen] = useState<When>("soon");
  const [delay, setDelay] = useState<string>("15");
  const [at, setAt] = useState("");
  const [hostname, setHostname] = useState("");
  const [missing, setMissing] = useState<string | null>(null);

  const warnings = power.data?.checks.filter((check) => check.status !== "ok") ?? [];

  const schedule = useMutation({
    mutationFn: () => {
      const timing = when === "soon" ? {} : when === "later" ? { in_minutes: Number(delay) } : { at };
      const body = { ...timing, force: warnings.length > 0 };
      return kind === "reboot"
        ? request("post", "/api/server/power/reboot", { body })
        : request("post", "/api/server/power/shutdown", { body: { ...body, confirm_hostname: hostname } });
    },
    onSuccess: (scheduled) => {
      void queryClient.invalidateQueries({ queryKey: serverKeys.summary });
      void queryClient.invalidateQueries({ queryKey: serverKeys.power });
      const moment = parseTimestamp(scheduled.scheduled_for);
      toast.success(
        kind === "reboot"
          ? t("server.power.rebootScheduledToast", { when: moment ? formatMoment(moment, t.locale) : scheduled.scheduled_for })
          : t("server.power.shutdownScheduledToast", { when: moment ? formatMoment(moment, t.locale) : scheduled.scheduled_for }),
      );
      onClose();
    },
  });

  const submit = (): void => {
    if (when === "at" && at === "") {
      setMissing(t("server.power.atMissing"));
      return;
    }
    if (kind === "shutdown" && hostname.trim() === "") {
      setMissing(t("server.power.hostnameMissing"));
      return;
    }
    setMissing(null);
    schedule.mutate();
  };

  const fieldError = schedule.error !== null && "fields" in schedule.error ? (schedule.error.fields as Record<string, string> | null) : null;
  const shownHost = identity.data?.hostname.hostname ?? "";
  const rebooting = kind === "reboot";
  const actionLabel =
    warnings.length > 0
      ? rebooting
        ? t("server.power.rebootAnyway")
        : t("server.power.shutdownAnyway")
      : rebooting
        ? t("server.power.reboot")
        : t("server.power.shutdown");

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !schedule.isPending) onClose();
      }}
      size="md"
      title={rebooting ? t("server.power.rebootTitle") : t("server.power.shutdownTitle")}
      description={rebooting ? t("server.power.rebootDescription") : t("server.power.shutdownDescription")}
      footer={
        <>
          <Button disabled={schedule.isPending} onClick={onClose}>
            {t("server.power.cancel")}
          </Button>
          <Button
            variant="danger"
            icon={rebooting ? <RotateCw aria-hidden="true" /> : <Power aria-hidden="true" />}
            loading={schedule.isPending}
            onClick={submit}
          >
            {actionLabel}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        {node !== null ? <p className="text-13 text-fg-muted">{t.rich("server.power.onServer", { server: <Mono>{node}</Mono> })}</p> : null}
        {/* A radio group names itself: inside a Field every radio would take the field's name. */}
        <div className="flex flex-col gap-1.5">
          <p aria-hidden="true" className="text-13 font-medium text-fg">
            {t("server.power.whenLabel")}
          </p>
          <SegmentedControl<When>
            label={t("server.power.whenLabel")}
            value={when}
            onValueChange={setWhen}
            options={[
              { value: "soon", label: t("server.power.whenSoon") },
              { value: "later", label: t("server.power.whenLater") },
              { value: "at", label: t("server.power.whenAt") },
            ]}
            className="self-start"
          />
        </div>
        {when === "later" ? (
          <Field label={t("server.power.delayLabel")} nativeLabel={false}>
            <Select
              value={delay}
              onValueChange={setDelay}
              options={DELAYS.map((minutes) => ({ value: String(minutes), label: t("server.power.delayMinutes", { count: minutes }) }))}
            />
          </Field>
        ) : null}
        {when === "at" ? (
          <Field label={t("server.power.atLabel")} description={t("server.power.atDescription")} error={fieldError?.["at"] ?? null}>
            <Input type="datetime-local" value={at} onValueChange={(value: string) => setAt(value)} />
          </Field>
        ) : null}
        <div className="flex flex-col gap-2">
          <p className="text-13 font-medium text-fg">{t("server.power.checksTitle")}</p>
          <PreChecks power={power.data} t={t} />
          {power.isError ? <ServerErrorBlock compact error={power.error} title={t("server.power.checksFailed")} onRetry={() => void power.refetch()} /> : null}
          {warnings.length > 0 ? <p className="text-12 text-fg-muted">{t("server.power.warningsRead")}</p> : null}
        </div>
        {!rebooting ? (
          <Field
            label={t("server.power.hostnameLabel")}
            description={shownHost !== "" ? t.rich("server.power.hostnameDescription", { host: <Mono>{shownHost}</Mono> }) : undefined}
            error={fieldError?.["confirm_hostname"] ?? null}
          >
            <Input mono value={hostname} onValueChange={(value: string) => setHostname(value)} autoComplete="off" spellCheck={false} />
          </Field>
        ) : null}
        {missing !== null ? (
          <Notice tone="warning" live>
            {missing}
          </Notice>
        ) : null}
        {schedule.isError && fieldError === null ? (
          <ServerErrorBlock live compact error={explainServerError(t, schedule.error, node)} title={rebooting ? t("server.power.rebootFailed") : t("server.power.shutdownFailed")} />
        ) : null}
      </div>
    </Dialog>
  );
}

/** "A reboot is scheduled for 04:00", with the way to call it off, on every tab while it is. */
export function ScheduledPowerBanner({ scheduled }: { scheduled: ScheduledPower }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const cancel = useMutation({
    mutationFn: () => request("delete", "/api/server/power/scheduled"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: serverKeys.summary });
    },
  });
  const moment = parseTimestamp(scheduled.scheduled_for);
  const when = moment ? formatMoment(moment, t.locale) : scheduled.scheduled_for;
  const reboot = scheduled.action === "reboot";
  return (
    <Notice
      variant="banner"
      tone="warning"
      title={reboot ? t("server.power.bannerReboot", { when }) : t("server.power.bannerShutdown", { when })}
      action={
        <Button size="sm" loading={cancel.isPending} onClick={() => cancel.mutate()}>
          {reboot ? t("server.power.cancelReboot") : t("server.power.cancelShutdown")}
        </Button>
      }
    >
      {scheduled.requested_by ? t("server.power.bannerRequestedBy", { actor: scheduled.requested_by }) : t("server.power.bannerNoActor")}
      {cancel.isError ? <ServerErrorBlock live compact error={explainServerError(t, cancel.error, node)} title={t("server.power.cancelFailed")} className="mt-2" /> : null}
    </Notice>
  );
}
