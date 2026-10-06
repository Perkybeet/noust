import { Eye, EyeOff } from "lucide-react";
import { useState } from "react";

import { IconButton } from "../../components/ui/IconButton";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";

export interface SecretFieldProps {
  /** What the secret is called, for the show and hide button: "registry password". */
  name: string;
  value: string;
  onValueChange: (value: string) => void;
  /** Whether a value is already kept on the server: the field says so instead of showing nothing. */
  kept?: boolean;
  placeholder?: string;
  disabled?: boolean;
}

/**
 * A write-only field: what is typed can be shown while it is typed, and what the server keeps
 * never comes back. It never fills itself in, so a browser's password manager cannot leave a
 * value in it that the operator did not choose, and an empty field means "leave it as it is".
 */
export function SecretField({ name, value, onValueChange, kept = false, placeholder, disabled = false }: SecretFieldProps) {
  const t = useT();
  const [shown, setShown] = useState(false);
  return (
    <Input
      mono
      type={shown ? "text" : "password"}
      autoComplete="new-password"
      autoCapitalize="off"
      spellCheck={false}
      disabled={disabled}
      value={value}
      onValueChange={onValueChange}
      {...(kept ? { placeholder: t("containers.secret.kept") } : placeholder !== undefined ? { placeholder } : {})}
      suffix={
        <IconButton
          label={t(shown ? "newApp.env.hide" : "newApp.env.show", { name })}
          icon={shown ? <EyeOff /> : <Eye />}
          size="sm"
          pressed={shown}
          onClick={() => setShown(!shown)}
        />
      }
    />
  );
}
