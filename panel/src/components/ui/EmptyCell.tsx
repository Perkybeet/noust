import { useT } from "../../i18n";

export interface EmptyCellProps {
  /** Why there is nothing, for a screen reader: "No certificate", "Never deployed". */
  reason?: string;
}

/**
 * A table cell with nothing to report: an en dash on screen and the reason for screen readers,
 * which would otherwise hear nothing, or "dash", and not know whether the value is missing,
 * zero or still loading.
 */
export function EmptyCell({ reason }: EmptyCellProps) {
  const t = useT();
  return (
    <>
      <span aria-hidden="true" className="text-fg-faint">
        –
      </span>
      <span className="sr-only">{reason ?? t("common.emptyCell.none")}</span>
    </>
  );
}
