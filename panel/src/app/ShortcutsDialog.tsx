import { Fragment } from "react";

import { Dialog } from "../components/ui/Dialog";
import { Kbd } from "../components/ui/Kbd";
import { useT } from "../i18n";
import { modKeyLabel } from "./shortcuts";

export interface ShortcutHelp {
  /** Keys pressed one after the other; a chord is written as one key, "Ctrl K". */
  keys: readonly string[];
  description: string;
}

export interface ShortcutsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The key sequences; the palette's chord is listed first without being passed. */
  shortcuts: readonly ShortcutHelp[];
}

/** Every shortcut the console answers to, opened with `?`. */
export function ShortcutsDialog({ open, onOpenChange, shortcuts }: ShortcutsDialogProps) {
  const t = useT();
  const rows: readonly ShortcutHelp[] = [
    { keys: [`${modKeyLabel()} K`], description: t("shell.shortcuts.openPalette") },
    ...shortcuts,
  ];
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("shell.session.keyboardShortcuts")}
      description={t("shell.shortcuts.dialogDescription")}
      size="sm"
    >
      <dl className="flex flex-col">
        {rows.map((shortcut) => (
          <div
            key={shortcut.keys.join(" ")}
            className="flex items-center justify-between gap-4 border-b border-border py-2 text-13 last:border-b-0"
          >
            <dt className="text-fg">{shortcut.description}</dt>
            <dd className="flex shrink-0 items-center gap-1 text-12 text-fg-faint">
              {shortcut.keys.map((key, index) => (
                <Fragment key={`${key}-${String(index)}`}>
                  {index > 0 ? <span>{t("shell.shortcuts.then")}</span> : null}
                  <span className="flex gap-0.5">
                    {key.split(" ").map((part) => (
                      <Kbd key={part}>{part}</Kbd>
                    ))}
                  </span>
                </Fragment>
              ))}
            </dd>
          </div>
        ))}
      </dl>
    </Dialog>
  );
}
