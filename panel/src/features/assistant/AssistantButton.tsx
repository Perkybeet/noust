import { IconButton } from "../../components/ui/IconButton";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { togglePanel, usePanel } from "./store";
import { useAssistantSession } from "./useAssistantSession";

/**
 * The assistant's button in the top bar. It is drawn only while the assistant is on and for a person who may use it: with it off, or for
 * a person who may not, there is no button, not a disabled one. It opens and closes the panel and says whether it is open.
 */
export function AssistantButton() {
  const t = useT();
  const session = useAssistantSession();
  const panel = usePanel();
  if (session === null) return null;
  return <IconButton label={t("assistant.name")} variant="secondary" icon={<OBJECT_ICONS.assistant />} pressed={panel.open} onClick={togglePanel} tooltip={false} />;
}
