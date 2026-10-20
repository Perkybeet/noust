import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { freezesQuery, invalidateHolds } from "../../api/queries/rollout";
import type { Freeze } from "../../api/queries/rollout";
import { announce } from "../../app/Announcer";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Setting } from "../../components/ui/Setting";
import { SkeletonCard } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { formatDateTime, parseTimestamp } from "../../lib/format";
import { useNode } from "../../nodes/useNode";
import { reportActionError } from "../apps/useAppActions";
import { olderServer } from "../app/settings/queries";
import { RowError, RowLoading } from "../app/settings/settingRows";

/** The lengths a freeze is offered for, in hours; `open` lasts until somebody lifts it. */
const LENGTHS = [
  { value: "1", hours: 1, label: "rollout.freeze.length1h" },
  { value: "4", hours: 4, label: "rollout.freeze.length4h" },
  { value: "12", hours: 12, label: "rollout.freeze.length12h" },
  { value: "24", hours: 24, label: "rollout.freeze.length1d" },
  { value: "72", hours: 72, label: "rollout.freeze.length3d" },
  { value: "168", hours: 168, label: "rollout.freeze.length7d" },
  { value: "open", hours: null, label: "rollout.freeze.lengthOpen" },
] as const;

type Length = (typeof LENGTHS)[number]["value"];

interface FormProps {
  /** The app to freeze; absent freezes the whole server. */
  domain: string | undefined;
  formId: string;
  onPending: (pending: boolean) => void;
  onDone: () => void;
}

/** Mounted fresh for every opening, so nothing of an abandoned attempt is there the next time. */
function FreezeForm({ domain, formId, onPending, onDone }: FormProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [later, setLater] = useState(false);
  const [startAt, setStartAt] = useState("");
  const [length, setLength] = useState<Length>("4");
  const [submitted, setSubmitted] = useState(false);

  const create = useMutation({
    mutationFn: () => {
      const hours = LENGTHS.find((option) => option.value === length)?.hours ?? null;
      return request("post", "/api/deploy-freezes", {
        body: {
          reason: reason.trim(),
          ...(domain !== undefined ? { domain } : {}),
          ...(later ? { starts_at: startAt } : {}),
          ...(hours !== null ? { hours } : {}),
        },
      });
    },
    onSuccess: () => {
      invalidateHolds(queryClient);
      announce(t("rollout.freeze.created", { scope: domain ?? t("rollout.freeze.scopeServer") }));
      onDone();
    },
  });

  const reasonMissing = reason.trim() === "" ? t("rollout.freeze.reasonRequired") : null;
  const startMissing = later && startAt === "" ? t("rollout.freeze.startAtRequired") : null;

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (reasonMissing !== null || startMissing !== null || create.isPending) return;
    onPending(true);
    create.mutate(undefined, {
      onSettled: () => {
        onPending(false);
      },
    });
  };

  return (
    <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("rollout.freeze.dialogIntro")}</p>
      <Field label={t("rollout.freeze.reasonLabel")} description={t("rollout.freeze.reasonDescription")} error={submitted ? reasonMissing : null}>
        <Input
          autoComplete="off"
          maxLength={300}
          placeholder={t("rollout.freeze.reasonPlaceholder")}
          value={reason}
          onValueChange={(value: string) => {
            setReason(value);
            if (create.isError) create.reset();
          }}
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("rollout.freeze.startLabel")} nativeLabel={false}>
          <Select
            options={[
              { value: "now", label: t("rollout.freeze.startNow") },
              { value: "later", label: t("rollout.freeze.startLater") },
            ]}
            value={later ? "later" : "now"}
            onValueChange={(value) => {
              setLater(value === "later");
            }}
          />
        </Field>
        <Field label={t("rollout.freeze.lengthLabel")} nativeLabel={false}>
          <Select
            options={LENGTHS.map((option) => ({ value: option.value, label: t(option.label) }))}
            value={length}
            onValueChange={(value) => {
              setLength(value);
            }}
          />
        </Field>
      </div>
      {later ? (
        <Field label={t("rollout.freeze.startAtLabel")} error={submitted ? startMissing : null}>
          <Input type="datetime-local" value={startAt} onValueChange={(value: string) => setStartAt(value)} />
        </Field>
      ) : null}
      {create.isError ? <ErrorBlock live compact error={create.error} title={t("rollout.freeze.notCreated")} /> : null}
    </form>
  );
}

/** The dialog that freezes deploys: one app's, or the whole server's when `domain` is absent. */
export function FreezeDialog({ domain, open, onOpenChange }: { domain: string | undefined; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const formId = useId();
  const [pending, setPending] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && pending) return;
        onOpenChange(next);
      }}
      size="md"
      title={domain !== undefined ? t("rollout.freeze.dialogTitle", { domain }) : t("rollout.freeze.dialogTitleServer")}
      footer={
        <>
          <Button disabled={pending} onClick={() => onOpenChange(false)}>
            {t("appSettings.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={pending}>
            {t("rollout.freeze.create")}
          </Button>
        </>
      }
    >
      {open ? <FreezeForm domain={domain} formId={formId} onPending={setPending} onDone={() => onOpenChange(false)} /> : null}
    </Dialog>
  );
}

/** One freeze: why, over what, for how long, who set it, and lifting it. */
function FreezeItem({ freeze, canLift, onLift }: { freeze: Freeze; canLift: boolean; onLift: (freeze: Freeze) => void }) {
  const t = useT();
  const starts = parseTimestamp(freeze.starts_at);
  const ends = parseTimestamp(freeze.ends_at);
  const from = starts !== null ? formatDateTime(starts, t.locale) : freeze.starts_at;
  const active = freeze.state === "active";
  return (
    <li className="flex min-w-0 flex-col gap-1.5 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <StatusPill state={active ? "warning" : "queued"} size="sm" label={active ? t("rollout.freeze.stateActive") : t("rollout.freeze.stateScheduled")} />
          <span className="min-w-0 text-13 font-medium break-words text-fg">{freeze.reason}</span>
        </div>
        <p className="text-12 text-fg-muted">
          {ends !== null ? t("rollout.freeze.rangeUntil", { from, until: formatDateTime(ends, t.locale) }) : t("rollout.freeze.rangeOpen", { from })}
          {freeze.scope === "server" ? ` · ${t("rollout.freeze.scopeServer")}` : ""}
          {freeze.created_by !== null ? ` · ${t("rollout.freeze.by", { by: freeze.created_by })}` : ""}
        </p>
      </div>
      {canLift ? (
        <Button size="sm" variant="ghost" className="shrink-0 self-start" aria-label={t("rollout.freeze.liftAria", { reason: freeze.reason })} onClick={() => onLift(freeze)}>
          {t("rollout.freeze.lift")}
        </Button>
      ) : null}
    </li>
  );
}

/** The freezes to list: an app's own, or the server's, and the question that lifts one. */
function useFreezes(domain: string | undefined) {
  const query = useQuery({ ...freezesQuery(), retry: false });
  const [making, setMaking] = useState(false);
  const [lifting, setLifting] = useState<Freeze | null>(null);
  const freezes = query.data?.freezes.filter((freeze) => (domain === undefined ? freeze.scope === "server" : freeze.domain === domain)) ?? [];
  return { query, freezes, making, setMaking, lifting, setLifting };
}

/** The dialog that makes a freeze and the question that lifts one; both live beside whatever lists them. */
function FreezeDialogs({ domain, making, setMaking, lifting, setLifting }: { domain: string | undefined } & Pick<ReturnType<typeof useFreezes>, "making" | "setMaking" | "lifting" | "setLifting">) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  return (
    <>
      <FreezeDialog domain={domain} open={making} onOpenChange={setMaking} />
      <ConfirmDialog
        open={lifting !== null}
        onOpenChange={(open) => {
          if (!open) setLifting(null);
        }}
        friction="simple"
        destructive={false}
        server={node}
        title={t("rollout.freeze.liftTitle")}
        description={t("rollout.freeze.liftDescription", { reason: lifting?.reason ?? "" })}
        actionLabel={t("rollout.freeze.liftAction")}
        onConfirm={async () => {
          if (lifting === null) return;
          try {
            await request("delete", "/api/deploy-freezes/{freeze_id}", { params: { freeze_id: lifting.id } });
          } catch (error) {
            reportActionError(t("rollout.freeze.notLifted"), error);
            return;
          }
          invalidateHolds(queryClient);
          announce(t("rollout.freeze.lifted"));
        }}
      />
    </>
  );
}

/**
 * An app's freeze as a row of Automation: what it holds, the button that makes one and the ones in
 * force with the way to lift each. A freeze refuses a deploy somebody asks for unless they say it
 * must go ahead; pushes, tags and schedules wait; a rollback is never held.
 */
export function FreezeSetting({ domain, canDeploy }: { domain: string; canDeploy: boolean }) {
  const t = useT();
  const { query, freezes, making, setMaking, lifting, setLifting } = useFreezes(domain);

  // A server older than 3.4 has no freezes: nothing to show rather than a guess.
  if (query.isError && olderServer(query.error)) return null;
  if (query.isPending) return <RowLoading label={t("rollout.freeze.loading")} />;
  if (query.isError) return <RowError title={t("rollout.freeze.readFailed")} error={query.error} onRetry={() => void query.refetch()} retrying={query.isRefetching} />;
  const inForce = freezes.some((freeze) => freeze.state === "active");

  return (
    <>
      <Setting
        label={t("rollout.freeze.title")}
        help={t("rollout.freeze.help")}
        {...(inForce ? { status: <StatusPill state="warning" size="sm" label={t("rollout.freeze.stateActive")} /> } : {})}
        action={
          canDeploy ? (
            <Button size="sm" onClick={() => setMaking(true)}>
              {t("rollout.freeze.make")}
            </Button>
          ) : (
            <></>
          )
        }
      >
        {freezes.length > 0 ? (
          <ul aria-label={t("rollout.freeze.title")} className="flex flex-col divide-y divide-border">
            {freezes.map((freeze) => (
              <FreezeItem key={freeze.id} freeze={freeze} canLift={canDeploy} onLift={setLifting} />
            ))}
          </ul>
        ) : null}
      </Setting>
      <FreezeDialogs domain={domain} making={making} setMaking={setMaking} lifting={lifting} setLifting={setLifting} />
    </>
  );
}

export interface FreezeCardProps {
  /** Whether the person may freeze and lift (`apps.deploy`); the server decides again. */
  canDeploy: boolean;
  /** The card's title. */
  title: string;
  description?: string;
}

/**
 * The server's freezes, and the way to make one and lift it: every deploy on the machine is held for
 * a stretch, apart from rollbacks. An app's own freezes are a row of its Automation settings.
 */
export function FreezeCard({ canDeploy, title, description }: FreezeCardProps) {
  const t = useT();
  const { query, freezes, making, setMaking, lifting, setLifting } = useFreezes(undefined);

  // A server older than 3.4 has no freezes: nothing to show rather than a guess.
  if (query.isError && olderServer(query.error)) return null;
  if (query.isPending) {
    return (
      <LoadingRegion label={t("rollout.freeze.loading")}>
        <SkeletonCard lines={1} />
      </LoadingRegion>
    );
  }
  if (query.isError) {
    return (
      <Card title={title}>
        <ErrorBlock compact error={query.error} title={t("rollout.freeze.readFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      </Card>
    );
  }

  return (
    <Card
      title={title}
      {...(description !== undefined ? { description } : {})}
      actions={
        canDeploy ? (
          <Button size="sm" onClick={() => setMaking(true)}>
            {t("rollout.freeze.make")}
          </Button>
        ) : undefined
      }
    >
      {freezes.length === 0 ? (
        <EmptyState variant="inline" title={t("rollout.freeze.none")} />
      ) : (
        <ul aria-label={title} className="flex flex-col divide-y divide-border">
          {freezes.map((freeze) => (
            <FreezeItem key={freeze.id} freeze={freeze} canLift={canDeploy} onLift={setLifting} />
          ))}
        </ul>
      )}
      <FreezeDialogs domain={undefined} making={making} setMaking={setMaking} lifting={lifting} setLifting={setLifting} />
    </Card>
  );
}
