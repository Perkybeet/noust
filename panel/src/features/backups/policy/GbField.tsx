import { useT } from "../../../i18n";
import { LimitField } from "./LimitField";

export interface GbFieldProps {
  label: string;
  help: string;
  value: string;
  onChange: (value: string) => void;
  error?: string | undefined;
}

/**
 * The most the scheduled backups a schedule keeps may weigh together, in gigabytes: the optional
 * third limit beside how many and how old. The newest always stays. It goes inside `KeepFields`,
 * which holds the other two.
 */
export function GbField({ label, help, value, onChange, error }: GbFieldProps) {
  const t = useT();
  return <LimitField label={label} help={help} valueLabel={t("backups.limit.gb")} value={value} onChange={onChange} error={error} suggestion="10" suffix="GB" inputMode="decimal" />;
}
