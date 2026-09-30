import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { cx } from "../../lib/cx";
import type { CertTone } from "./certificates";

/** A certificate's tone in the console's state language: its glyph and its colour. */
export const CERT_STATE: Readonly<Record<CertTone, Status>> = { ok: "running", warn: "warning", fail: "failed", idle: "stopped", busy: "deploying" };

/**
 * A certificate's state as glyph and words: a dot for valid, a warning sign for expiring or
 * not covering a name, a cross for expired, an arc while a job works on it, a ring for none.
 * The shape carries the state as much as the colour does; the words are in the text colour,
 * except an expired certificate's, which is a failure and reads as one.
 */
export function CertificateStatus({ tone, label, className }: { tone: CertTone; label: string; className?: string }) {
  const state = CERT_STATE[tone];
  return (
    <span data-tone={tone} className={cx("inline-flex min-w-0 items-center gap-1.5 text-13", className)}>
      <StatusGlyph state={state} size={12} className={stateTextClass(state)} />
      <span className={cx("truncate", tone === "idle" ? "text-fg-muted" : tone === "fail" ? cx("font-medium", stateTextClass(state)) : "text-fg")}>{label}</span>
    </span>
  );
}
