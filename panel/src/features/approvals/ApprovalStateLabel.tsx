import type { Status } from "../../components/ui/StatusPill";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import type { T } from "../../i18n";

/** A request's state in the state language: waiting, approved, rejected, expired, carried out. */
export function approvalStatus(t: T, state: string): { status: Status; label: string } {
  switch (state) {
    case "requested":
      return { status: "queued", label: t("approvals.state.requested") };
    case "approved":
      return { status: "running", label: t("approvals.state.approved") };
    case "rejected":
      return { status: "failed", label: t("approvals.state.rejected") };
    case "expired":
      return { status: "stopped", label: t("approvals.state.expired") };
    case "executed":
      return { status: "running", label: t("approvals.state.executed") };
    default:
      return { status: "unknown", label: state };
  }
}

export function ApprovalStateLabel({ t, state }: { t: T; state: string }) {
  const { status, label } = approvalStatus(t, state);
  return (
    <span className="inline-flex items-center gap-1.5 text-13 text-fg">
      <StatusGlyph state={status} className={stateTextClass(status)} />
      {label}
    </span>
  );
}
