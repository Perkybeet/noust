import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";

export interface GbFieldProps {
  label: string;
  help: string;
  value: string;
  onChange: (value: string) => void;
  error?: string | undefined;
}

/**
 * The most the scheduled backups a policy keeps may weigh together, in gigabytes: the optional
 * third limit beside how many and how old. Empty is no limit; the newest always stays. It goes
 * inside `KeepFields`, which holds the other two.
 */
export function GbField({ label, help, value, onChange, error }: GbFieldProps) {
  return (
    <Field label={label} optional description={help} error={error} className="w-44">
      <Input mono inputMode="decimal" value={value} onValueChange={onChange} suffix="GB" />
    </Field>
  );
}
