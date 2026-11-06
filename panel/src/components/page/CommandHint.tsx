import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { CopyButton } from "../ui/CopyButton";

/**
 * Why a page shows a command at all. The console does what the CLI does, so a command that repeats a button is noise
 * (owner item 123: the terminal is the terminal and the web is the web); one earns its place only for one of these:
 *
 * - `solo-terminal`: the console cannot do it and the terminal can (sealing a central, a command run on the machine).
 * - `otro-servidor`: it is to be run on another server than the one on screen (a node that is not reached from here).
 * - `salida-del-sistema`: the system's own output names it, and the operator is shown what the system said to run.
 */
export type CommandHintReason = "solo-terminal" | "otro-servidor" | "salida-del-sistema";

export interface CommandHintProps {
  /** The exact command, without the prompt: `noust status example.com`. */
  command: string;
  /** Leads into the command for context: "From a terminal". Omit where the page already says it. */
  label?: string;
  /**
   * Why this page shows a command (see `CommandHintReason`). It leads the command, in the words of its reason, when no
   * `label` does, and it is the declaration the design rules count: a command with none is on the way out (F4).
   */
  reason?: CommandHintReason;
  className?: string;
}

const REASON_WORDS = {
  "solo-terminal": "common.commandHint.reason.terminalOnly",
  "otro-servidor": "common.commandHint.reason.otherServer",
  "salida-del-sistema": "common.commandHint.reason.systemOutput",
} as const;

/**
 * The CLI command that does what this part of the console does, for operators who live in a
 * terminal: set in mono behind a prompt the copy button leaves out.
 */
export function CommandHint({ command, label, reason, className }: CommandHintProps) {
  const t = useT();
  const lead = label ?? (reason !== undefined ? t(REASON_WORDS[reason]) : undefined);
  return (
    <div {...(reason !== undefined ? { "data-reason": reason } : {})} className={cx("flex max-w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-1", className)}>
      {lead !== undefined ? <span className="text-12 text-fg-faint">{lead}</span> : null}
      <div className="flex max-w-full min-w-0 items-center gap-1 rounded-control border border-border bg-bg-sunken py-0.5 pr-0.5 pl-2.5">
        <code translate="no" className="truncate text-12 text-fg-muted" title={command}>
          <span aria-hidden="true" className="text-fg-faint select-none">
            ${" "}
          </span>
          {command}
        </code>
        <CopyButton value={command} label={t("common.copyButton.copyCommand")} />
      </div>
    </div>
  );
}
