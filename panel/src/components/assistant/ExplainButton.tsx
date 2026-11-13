import { useT } from "../../i18n";
import { Button } from "../ui/Button";
import { OBJECT_ICONS } from "../ui/objectIcons";

export interface ExplainButtonProps {
  /** Opens the panel on a conversation that begins with the block this button sits in as what to read. */
  onExplain: () => void;
  size?: "sm" | "md";
}

/**
 * "Explain this", next to the error it is about: in the head of the block that holds the system's own words (a failed
 * deployment's output, a diagnosis, an alert's detail), never in the header of the page, where it would compete with Retry and
 * Roll back and would not say what "this" is. It is drawn only for who may use the assistant, while it is on: whoever draws it
 * asks the session first, and nothing of it exists otherwise (not a disabled button).
 */
export function ExplainButton({ onExplain, size = "sm" }: ExplainButtonProps) {
  const t = useT();
  return (
    <Button variant="secondary" size={size} icon={<OBJECT_ICONS.assistant aria-hidden="true" />} onClick={onExplain}>
      {t("assistant.explain.button")}
    </Button>
  );
}
