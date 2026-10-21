/**
 * A change to how the server is reached (sshd, the firewall) that waits to be kept.
 *
 * Such a change undoes itself unless it is confirmed, so a mistake cannot lock the operator
 * out: a timer on the server reverts it when its window closes. Confirming needs a new SSH
 * login since the change, which proves the way in still works; a session that was already open
 * proves nothing, because reloading sshd or changing the firewall leaves open connections be.
 *
 * The banner is one sentence, the time left and the two ways out: the change, and where the proof
 * stands. The server reports the proof with each change (`proof_seen`, from the very check Keep
 * makes), the changes are read again every few seconds while one is pending, and Keep stays off,
 * with its reason described, until the login is on record. Asking the operator to press a button
 * that is certain to be refused is what this replaces. How to keep it, what the proof means and
 * the terminal's way are behind "Details", in a drawer, so the banner never grows.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import { request } from "../../api/client";
import { CommandHint } from "../../components/page/CommandHint";
import { useNow } from "../../components/page/clock";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Drawer } from "../../components/ui/Drawer";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { textLinkClassName } from "../../components/ui/TextLink";
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

/**
 * Where the proof stands: the login that keeps the change, none yet, no way to tell, or the
 * server not reporting it at all (`unknown`).
 */
type Proof =
  | { kind: "seen"; user: string; source: string; at: number }
  | { kind: "waiting" }
  | { kind: "unreadable"; error: string }
  | { kind: "unknown" };

/**
 * The proof of a change, as its server reported it.
 *
 * The schema types the proof fields as always present, but a node older than this console
 * (3.3.0 and before) leaves them out, and a newer central proxies it as it is. Absent is
 * "unknown", not "unreadable": Keep stays on, because the server's own confirm is authoritative
 * and answers with its own refusal, rather than the console shutting a button off for a reason
 * it never checked. Only a server that says the history cannot be read
 * (`proof_readable === false`) turns Keep off for that.
 */
export function proofOf(change: PendingChange): Proof {
  const reported: Partial<Pick<PendingChange, "proof_seen" | "proof_readable" | "proof_error" | "proof_login">> = change;
  const login = reported.proof_login ?? null;
  if (reported.proof_seen === true && login !== null) return { kind: "seen", ...login };
  if (reported.proof_readable === false) return { kind: "unreadable", error: reported.proof_error ?? "" };
  if (reported.proof_seen === undefined && reported.proof_readable === undefined) return { kind: "unknown" };
  return { kind: "waiting" };
}

/**
 * Where the proof stands, in one line: a still dashed ring while it is awaited, a green dot once
 * it is seen, a warning triangle when it cannot be read. A polite status, because it changes
 * once, when the login arrives: the operator who is in another terminal hears that Keep is on.
 * The banner's one sentence, and the description of Keep while Keep is off.
 */
function ProofLine({ id, change, proof }: { id: string; change: PendingChange; proof: Proof }) {
  const t = useT();
  const state: Status = proof.kind === "seen" ? "running" : proof.kind === "unknown" ? "queued" : proof.kind === "waiting" ? "queued" : "warning";
  return (
    <span id={id} role="status" className="inline-flex min-w-0 items-start gap-1.5">
      <StatusGlyph state={state} size={12} className={`mt-1 shrink-0 ${stateTextClass(state)}`} />
      <span className="min-w-0">
        {/* The change's own name, as Noust recorded it. */}
        <span className="font-medium text-fg">{change.title}.</span>{" "}
        {proof.kind === "seen"
          ? t.rich("server.pending.proofSeen", {
              user: <Mono>{proof.user}</Mono>,
              source: <Mono>{proof.source}</Mono>,
              time: formatClock(new Date(proof.at * 1000), t.locale),
            })
          : proof.kind === "waiting"
            ? t("server.pending.proofWaiting")
            : proof.kind === "unreadable"
              ? t("server.pending.proofUnreadable")
              : t("server.pending.proofUnknown")}
      </span>
    </span>
  );
}

/** What the banner leaves out: how to keep the change, what the proof means, and the terminal's way. */
function PendingDetails({ change, proof, open, onOpenChange }: { change: PendingChange; proof: Proof; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const firewall = change.kind.startsWith("firewall");
  return (
    <Drawer open={open} onOpenChange={onOpenChange} size="md" title={t("server.pending.detailsTitle")} description={change.title}>
      <div className="flex flex-col gap-4 text-13 text-fg-muted">
        {/* What to do comes before where it stands, and is gone once it is done. */}
        {proof.kind !== "seen" ? <p>{firewall ? t("server.pending.howFirewall") : t("server.pending.howSsh")}</p> : null}
        {proof.kind === "seen" ? <p>{t("server.pending.proofSeenDetail")}</p> : null}
        {proof.kind === "waiting" ? <p>{t("server.pending.proofWaitingDetail")}</p> : null}
        {proof.kind === "unreadable" ? (
          <>
            <p>{t("server.pending.proofUnreadableDetail")}</p>
            {/* Why, in the system's own words. */}
            {proof.error !== "" ? <SystemOutput label={t("server.pending.proofUnreadableLabel")}>{proof.error}</SystemOutput> : null}
          </>
        ) : null}
        <CommandHint command={`noust server security confirm ${change.id}`} label={t("server.pending.fromTerminal")} />
        <p className="text-12">{t.rich("server.pending.id", { id: <Mono>{change.id}</Mono> })}</p>
      </div>
    </Drawer>
  );
}

function PendingChangeBanner({ change }: { change: PendingChange }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const now = useNow(() => 1_000);
  const proofId = useId();
  const [revertOpen, setRevertOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
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
  // Only a proof known to be missing turns Keep off; when the server does not say, its confirm decides.
  const canKeep = proof.kind === "seen" || proof.kind === "unknown";
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
            // A disabled button does not say why: the line beside it does.
            aria-describedby={canKeep ? undefined : proofId}
            onClick={() => confirm.mutate()}
          >
            {t("server.pending.confirm")}
          </Button>
        </div>
      }
    >
      <div className="flex min-w-0 flex-col gap-2">
        <p>
          <ProofLine id={proofId} change={change} proof={proof} />{" "}
          <button type="button" className={textLinkClassName("inline")} onClick={() => setDetailsOpen(true)}>
            {t("server.pending.details")}
          </button>
        </p>
        {confirm.isError ? <ServerErrorBlock live compact error={confirm.error} title={t("server.pending.confirmFailed")} /> : null}
      </div>
      <PendingDetails change={change} proof={proof} open={detailsOpen} onOpenChange={setDetailsOpen} />
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
