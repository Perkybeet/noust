import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Meter } from "../ui/Progress";

export interface ResourceMeterProps {
  /** The resource: "Memory", "CPU". */
  label: string;
  /** The reading now, in the same unit as `limit`; null when there is no reading. */
  value: number | null;
  /** The limit it runs under (MemoryMax, CPUQuota); null or omitted when it has none. */
  limit?: number | null;
  /** Writes a value in its unit: formatBytes, formatPercent. */
  format: (value: number) => string;
  /** Why there is no reading, when there is none: "Not running". */
  missing?: string;
  className?: string;
}

/**
 * Use of one resource against its limit. With a limit it is a meter whose fill turns amber
 * then red as the limit nears; without one the reading stands alone and says so, because a
 * bar with no end would imply a ceiling that does not exist.
 */
export function ResourceMeter({ label, value, limit = null, format, missing, className }: ResourceMeterProps) {
  const t = useT();
  if (value !== null && limit !== null && limit > 0) {
    return (
      <Meter
        label={label}
        value={Math.min(value, limit)}
        max={limit}
        valueText={t("common.resourceMeter.ofLimit", { value: format(value), limit: format(limit) })}
        {...(className !== undefined ? { className } : {})}
      />
    );
  }
  return (
    <div className={cx("flex min-w-0 flex-col gap-1.5", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="truncate text-13 text-fg-muted">{label}</span>
        {value !== null ? (
          <span className="mono text-13 text-fg">{format(value)}</span>
        ) : (
          <span className="text-13 text-fg-faint">{missing ?? t("common.resourceMeter.missing")}</span>
        )}
      </div>
      <p className="text-12 text-fg-faint">
        {limit !== null && limit > 0 ? t("common.resourceMeter.limit", { value: format(limit) }) : t("common.resourceMeter.noLimit")}
      </p>
    </div>
  );
}
