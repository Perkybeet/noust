import { ExplainButton } from "../../components/assistant/ExplainButton";
import { MenuItem } from "../../components/ui/Menu";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import type { PageRef } from "./model";
import { pageLabel } from "./pageContext";
import { openPanel } from "./store";
import { useAssistantSession } from "./useAssistantSession";

/** Opens the panel on this page and asks it to explain it. */
function useExplain(page: PageRef): (() => void) | null {
  const t = useT();
  const session = useAssistantSession();
  if (session === null) return null;
  return () => openPanel({ page, label: pageLabel(t, page), question: t("assistant.explain.question") });
}

/**
 * "Explain this", in the head of the block that holds the system's own words (a failed deployment's error, a diagnosis). It draws
 * nothing while the assistant is off or for a person who may not use it: not a disabled button, nothing.
 */
export function ExplainThis({ page }: { page: PageRef }) {
  const explain = useExplain(page);
  if (explain === null) return null;
  return <ExplainButton onExplain={explain} />;
}

/** The same, as an item of a row's menu (an alert that is firing). */
export function ExplainMenuItem({ page }: { page: PageRef }) {
  const t = useT();
  const explain = useExplain(page);
  if (explain === null) return null;
  return (
    <MenuItem icon={<OBJECT_ICONS.assistant />} onClick={explain}>
      {t("assistant.explain.button")}
    </MenuItem>
  );
}
