import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { formatBytes } from "../../../lib/format";

/** A binary cell as the browser returns it: its length and its first bytes. */
export interface BinaryCell {
  bytes: number;
  hex: string;
}

export function isBinaryCell(value: unknown): value is BinaryCell {
  return typeof value === "object" && value !== null && "bytes" in value && "hex" in value;
}

/** Whether a column's cells are numbers, drawn at the right like every figure. */
export function isNumericKind(kind: string | undefined): boolean {
  return kind === "numeric";
}

/** A cell as plain text, for copying and for the row's detail. */
export function cellText(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (isBinaryCell(value)) return `\\x${value.hex}`;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

export interface CellProps {
  value: unknown;
  kind?: string | undefined;
  /** The engine cut it at 2,000 characters. */
  truncated?: boolean;
  /** One line, cut with an ellipsis (a grid); otherwise wrapped (a row's detail). */
  inline?: boolean;
}

/**
 * One value from a database, told apart by what it is: NULL is a word of its own, never an
 * empty string; binary shows its size and first bytes; a value the engine cut says so; numbers
 * sit at the right. Every value is a system value, in mono.
 */
export function Cell({ value, kind, truncated = false, inline = true }: CellProps) {
  const t = useT();
  if (value === null || value === undefined) {
    return (
      <Mono tone="faint" className="select-none">
        NULL
      </Mono>
    );
  }
  if (isBinaryCell(value)) {
    return (
      <span className="inline-flex max-w-full items-center gap-1.5">
        <span className="shrink-0 rounded-chip bg-bg-sunken px-1 text-12 text-fg-muted">{t("databases.grid.binary", { size: formatBytes(value.bytes, t.locale) })}</span>
        <Mono tone="muted" truncate={inline}>{`\\x${value.hex}${value.bytes * 2 > value.hex.length ? "…" : ""}`}</Mono>
      </span>
    );
  }
  const text = cellText(value);
  return (
    <span className={cx("inline-flex max-w-full items-center gap-1.5", !inline && "items-start")}>
      <Mono
        tone={typeof value === "boolean" || kind === "json" ? "muted" : "default"}
        truncate={inline}
        className={cx(!inline && "break-all whitespace-pre-wrap", text === "" && "text-fg-faint")}
      >
        {text === "" ? t("databases.grid.emptyText") : text}
      </Mono>
      {truncated ? <span className="shrink-0 rounded-chip bg-bg-sunken px-1 text-12 text-fg-muted">{t("databases.grid.cut")}</span> : null}
    </span>
  );
}
