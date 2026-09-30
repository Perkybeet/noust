import type { ReactNode } from "react";

import { PageHeader } from "../../app/PageHeader";
import type { PageHeaderProps } from "../../app/PageHeader";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Button } from "../ui/Button";
import { SaveBar } from "./SaveBar";

export interface FileEditorBar {
  /** Lines or fields changed since the last save; 0 says there is nothing to save. */
  changes: number;
  onDiscard: () => void;
  /** Runs the file's own check (nginx -t, systemd-analyze verify) without saving. */
  onTest: () => void;
  /** Checks, and saves only if the check passes. */
  onTestAndSave: () => void;
  testing?: boolean;
  saving?: boolean;
}

export interface FileEditorPageProps {
  /** The file's owner: its name (mono), its state, and its actions (Disable, the overflow). */
  header: PageHeaderProps;
  /** How the file is checked before it is saved, as a Notice. */
  notice?: ReactNode;
  /** Facts about the file on one line: what it serves, its path (mono). */
  meta?: ReactNode;
  /** The editor. It fills the height it is given. */
  children: ReactNode;
  bar: FileEditorBar;
  /** The terminal's way, under the bar: a `CommandHint`. */
  footer?: ReactNode;
  className?: string;
}

/**
 * T6, one file of the system to edit: a site's configuration, a unit file. The editor takes
 * the screen's height; a bar at its foot says what changed and offers Test, and Test and save.
 * Logs and the danger zone live elsewhere, never on this page.
 */
export function FileEditorPage({ header, notice, meta, children, bar, footer, className }: FileEditorPageProps) {
  const t = useT();
  return (
    <div data-template="file-editor" className={cx("flex min-w-0 flex-col", className)}>
      <div data-slot="header">
        <PageHeader {...header} flush />
      </div>
      {notice !== undefined || meta !== undefined ? (
        <div className="mt-4 flex min-w-0 flex-col gap-2 lg:flex-row lg:items-center lg:justify-between lg:gap-6">
          {notice !== undefined ? (
            <div data-slot="notice" className="min-w-0">
              {notice}
            </div>
          ) : null}
          {meta !== undefined ? (
            // One line beside the notice from lg: the facts truncate rather than wrap, so one that
            // arrives late (or a longer language) never pushes the editor down.
            <div data-slot="meta" className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-13 text-fg-muted lg:flex-nowrap">
              {meta}
            </div>
          ) : null}
        </div>
      ) : null}
      <div data-slot="editor" className="mt-4 flex h-editor min-h-0 min-w-0 flex-col">
        {children}
      </div>
      <SaveBar
        changes={bar.changes}
        onDiscard={bar.onDiscard}
        onSave={bar.onTestAndSave}
        saving={bar.saving ?? false}
        saveLabel={t("common.fileEditor.testAndSave")}
        secondary={
          <Button loading={bar.testing ?? false} onClick={bar.onTest}>
            {t("common.fileEditor.test")}
          </Button>
        }
        className="mt-4"
      />
      {footer !== undefined ? (
        <div data-slot="footer" className="mt-6">
          {footer}
        </div>
      ) : null}
    </div>
  );
}
