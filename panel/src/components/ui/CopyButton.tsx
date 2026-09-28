import { Check, Copy } from "lucide-react";

import { useT } from "../../i18n";
import { IconButton } from "./IconButton";
import { useCopyState } from "./useCopyState";

export interface CopyButtonProps {
  /** The exact text placed on the clipboard, or a function that produces it on press. */
  value: string | (() => string);
  /** What is being copied, completing "Copy ..." for the accessible name. */
  label?: string;
  size?: "sm" | "md";
  className?: string;
  /** An element (usually visually hidden) describing what pressing it copies. */
  "aria-describedby"?: string;
}

/** Copies a value and confirms it in place: the icon becomes a check and the change is announced. */
export function CopyButton({ value, label, size = "sm", className, ["aria-describedby"]: describedBy }: CopyButtonProps) {
  const t = useT();
  const { state, copy } = useCopyState();

  return (
    <>
      <IconButton
        label={label ?? t("common.copyButton.label")}
        size={size}
        icon={state === "copied" ? <Check className="text-ok" /> : <Copy />}
        onClick={() => void copy(typeof value === "function" ? value() : value)}
        {...(className ? { className } : {})}
        {...(describedBy !== undefined ? { "aria-describedby": describedBy } : {})}
      />
      <span role="status" className="sr-only">
        {state === "copied" ? t("common.copyButton.copied") : state === "failed" ? t("common.copyButton.failed") : ""}
      </span>
    </>
  );
}
