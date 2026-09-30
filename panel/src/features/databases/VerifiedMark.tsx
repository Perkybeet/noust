import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { cx } from "../../lib/cx";

const VIEWS: Readonly<Record<string, { state: Status; label: PlainKey }>> = {
  ok: { state: "running", label: "databases.verify.ok" },
  failed: { state: "failed", label: "databases.verify.failed" },
};

/**
 * What checking a dump found, in the state language: checked and sound, checked and broken, or
 * not checked yet (neutral words, no colour: nothing is known to be wrong).
 */
export function VerifiedMark({ status, compact = false }: { status: string; compact?: boolean }) {
  const t = useT();
  const view = VIEWS[status];
  if (view === undefined) return <span className={cx("text-fg-faint", compact ? "text-12" : "text-13")}>{t("databases.verify.unverified")}</span>;
  return (
    <span className={cx("inline-flex items-center gap-1.5", compact ? "text-12" : "text-13")}>
      <StatusGlyph state={view.state} size={compact ? 10 : 12} className={stateTextClass(view.state)} />
      <span className={view.state === "failed" ? "text-fail" : "text-fg-muted"}>{t(view.label)}</span>
    </span>
  );
}
