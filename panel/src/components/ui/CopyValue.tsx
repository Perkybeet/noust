import { cx } from "../../lib/cx";
import { CopyButton } from "./CopyButton";

export interface CopyValueProps {
  /** The exact text shown and placed on the clipboard. */
  value: string;
  /** What is being copied, completing "Copy ..." for the copy button's accessible name. */
  label: string;
  /** What this value is, as a screen reader announces the box (a public key, a URL). */
  name?: string;
  className?: string;
}

/**
 * A value the operator runs or pastes elsewhere (a command, a public key, a webhook address):
 * in full, never cut, wrapped at any character so a long key stays on screen, with its copy
 * button. Not for a fact among facts (that is a `KeyValueList` row) and not for output the
 * system printed (that is `SystemOutput`).
 */
export function CopyValue({ value, label, name, className }: CopyValueProps) {
  return (
    <div
      className={cx("flex max-w-full min-w-0 items-center gap-1 rounded-control border border-border bg-bg-sunken py-0.5 pr-0.5 pl-2.5", className)}
      {...(name !== undefined ? { role: "group", "aria-label": name } : {})}
    >
      <code translate="no" className="min-w-0 flex-1 text-12 break-all text-fg">
        {value}
      </code>
      <CopyButton value={value} label={label} />
    </div>
  );
}
