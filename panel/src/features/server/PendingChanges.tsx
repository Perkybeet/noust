/**
 * A change to how the server is reached (sshd, the firewall) that waits to be kept.
 *
 * Such a change undoes itself unless it is confirmed, so a mistake cannot lock the operator
 * out: a timer on the server reverts it when its window closes. Confirming needs a new SSH
 * login since the change, which proves the way in still works; a session that was already open
 * proves nothing, because reloading sshd or changing the firewall leaves open connections be.
 *
 * The banner says that calmly, on every tab, with the time left and the two ways out, and it
 * shows whether that login has happened yet: the server reports it with each change
 * (`proof_seen`, from the very check Keep makes), the changes are read again every few seconds
 * while one is pending, and Keep stays off, with its reason next to it, until the login is on
 * record. Asking the operator to press a button that is certain to be refused is what this
 * replaces.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import { request } from "../../api/client";
import { CommandHint } from "../../components/page/CommandHint";
import { useNow } from "../../components/page/clock";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import { formatClock } from "../../lib/format";
import { useNode } from "../../nodes/useNode";
import { ServerErrorBlock, explainServerError } from "./errors";
import { serverKeys } from "./queries";
import type { PendingChange } from "./queries";

/** "1:42": the minutes and seconds left, never negative. */
export function countdown(expiresAt: number, now: number): string {
  const left = Math.max(0, Math.ceil(expiresAt - now / 1000));
  const minutes = Math.floor(left / 60);
  const seconds = left % 60;
  return `${String(minutes)}:${String(seconds).padStart(2, "0")}`;
}

/** Where the proof stands: the login that keeps the change, none yet, or no way to tell. */
type Proof =
  | { kind: "seen"; user: string; source: string; at: number }
  | { kind: "waiting" }
  | { kind: "unreadable"; error: string };

function proofOf(change: PendingChange): Proof {
  const login = change.proof_login ?? null;
  if (change.proof_seen && login !== null) return { kind: "seen", ...login };
  if (!change.proof_readable) return { kind: "unreadable", error: change.proof_error };
  return { kind: "waiting" };
}

/**
 * The proof as a step: its state in a shape and a word (a waiting dashed ring, a green dot, a
 * warning triangle), and under it what that means for Keep. A polite status, because it changes
 * once, when the login arrives: the operator who is in another terminal hears that Keep is on.
 */
function ProofStep({ id, proof }: { id: string; proof: Proof }) {
  const t = useT();
  const state: Status = proof.kind === "seen" ? "running" : proof.kind === "waiting" ? "queued" : "warning";
  return (
    <div id={id} role="status" className="flex min-w-0 items-start gap-2.5">
      <StatusGlyph state={state} size={12} className={`mt-1 shrink-0 ${stateTextClass(state)}`} />
      <div className="flex min-w-0 flex-col gap-0.5">
        {proof.kind === "seen" ? (
          <>
            <p className="font-medium text-fg">
              {t.rich("server.pending.proofSeen", {
                user: <Mono>{proof.user}</Mono>,
                source: <Mono>{proof.source}</Mono>,
                time: formatClock(new Date(proof.at * 1000), t.locale),
              })}
            </p>
            <p>{t("server.pending.proofSeenDetail")}</p>
          </>
        ) : proof.kind === "waiting" ? (
          <>
            <p className="font-medium text-fg">{t("server.pending.proofWaiting")}</p>
            <p>{t("server.pending.proofWaitingDetail")}</p>
          </>
        ) : (
          <>
            <p className="font-medium text-fg">{t("server.pending.proofUnreadable")}</p>
            <p>{t("server.pending.proofUnreadableDetail")}</p>
            {/* Why, in the system's own words. */}
            {proof.error !== "" ? <SystemOutput label={t("server.pending.proofUnreadableLabel")}>{proof.error}</SystemOutput> : null}
          </>
        )}
      </div>
    </div>
  );
}

function PendingChangeBanner({ change }: { change: PendingChange }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const now = useNow(() => 1_000);
  const proofId = useId();
  const [revertOpen, setRevertOpen] = useState(false);
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: serverKeys.changes });
    void queryClient.invalidateQueries({ queryKey: serverKeys.security });
  };
  const confirm = useMutation({
    mutationFn: () => request("post", "/api/server/security/changes/{change_id}/confirm", { params: { change_id: change.id } }),
    onSuccess: refresh,
  });
  const { reset: forgetConfirmError } = confirm;
  const proof = proofOf(change);
  // "No new SSH login" is obsolete the moment one arrives; leaving it up reads as a fresh refusal.
  useEffect(() => {
    if (proof.kind === "seen") forgetConfirmError();
  }, [proof.kind, forgetConfirmError]);
  const revert = async (): Promise<void> => {
    try {
      await request("post", "/api/server/security/changes/{change_id}/revert", { params: { change_id: change.id } });
    } catch (error: unknown) {
      throw explainServerError(t, error, node);
    }
    // Keep's refusal is about a path the operator has just left.
    forgetConfirmError();
    refresh();
  };
  const expired = now / 1000 >= change.expires_at;
  const firewall = change.kind.startsWith("firewall");
  const canKeep = proof.kind === "seen";
  return (
    <Notice
      variant="banner"
      tone="warning"
      title={
        expired
          ? t("server.pending.titleExpired")
          : firewall
            ? t("server.pending.titleFirewall", { left: countdown(change.expires_at, now) })
            : t("server.pending.titleSsh", { left: countdown(change.expires_at, now) })
      }
      action={
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={() => {
              // Undo does not look at logins: a Keep that was refused says nothing about it.
              forgetConfirmError();
              setRevertOpen(true);
            }}
            disabled={confirm.isPending}
          >
            {t("server.pending.undo")}
          </Button>
          <Button
            size="sm"
            loading={confirm.isPending}
            disabled={!canKeep}
            // A disabled button does not say why: the step beside it does.
            aria-describedby={canKeep ? undefined : proofId}
            onClick={() => confirm.mutate()}
          >
            {t("server.pending.confirm")}
          </Button>
        </div>
      }
    >
      <div className="flex min-w-0 flex-col gap-2">
        {/* The change's own name, as Noust recorded it. */}
        <p>{t.rich("server.pending.what", { change: <span className="font-medium">{change.title}</span> })}</p>
        {/* What to do comes before where it stands, and is gone once it is done. */}
        {proof.kind === "waiting" ? <p>{firewall ? t("server.pending.howFirewall") : t("server.pending.howSsh")}</p> : null}
        <ProofStep id={proofId} proof={proof} />
        <CommandHint command={`noust server security confirm ${change.id}`} label={t("server.pending.fromTerminal")} />
        {confirm.isError ? <ServerErrorBlock live compact error={confirm.error} title={t("server.pending.confirmFailed")} /> : null}
        <p className="text-12 text-fg-muted">{t.rich("server.pending.id", { id: <Mono>{change.id}</Mono> })}</p>
      </div>
      <ConfirmDialog
        open={revertOpen}
        onOpenChange={setRevertOpen}
        friction="simple"
        server={node}
        title={t("server.pending.undoTitle")}
        description={t("server.pending.undoDescription")}
        actionLabel={t("server.pending.undoAction")}
        destructive={false}
        onConfirm={revert}
      />
    </Notice>
  );
}

/** Every change waiting for confirmation, one banner each (there is usually one). */
export function PendingChanges({ changes }: { changes: readonly PendingChange[] }) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      {changes.map((change) => (
        <PendingChangeBanner key={change.id} change={change} />
      ))}
    </div>
  );
}
