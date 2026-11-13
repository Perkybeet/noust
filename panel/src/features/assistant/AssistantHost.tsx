import { Drawer } from "../../components/ui/Drawer";
import { LG_UP, useMediaQuery } from "../../components/ui/useMediaQuery";
import { useT } from "../../i18n";
import { AssistantBody } from "./AssistantBody";
import { closePanel, usePanel } from "./store";
import { useAssistantSession } from "./useAssistantSession";

/**
 * From 1792 px the page still has about 900 px for a table once the main bar, the application's own column and the panel are drawn
 * (448 + 384 px); below it the page would be squeezed until its bar overlaps itself, so the panel goes over the page instead.
 */
const DOCK_UP = "(min-width: 112rem)";

/**
 * The three places the panel lives, by width: beside the page and pushing it (from 1792 px, where the page still has room for a table),
 * a drawer over the page's right edge (1024 to 1791: the page would not hold both) and a sheet over a phone, rising from the foot like
 * the one the column and "More" use. The body is the same in the three. It is loaded only when the panel is opened, so the console's
 * first load does not carry it, and it draws nothing for a person who may not use the assistant.
 */
export default function AssistantHost() {
  const t = useT();
  const panel = usePanel();
  const session = useAssistantSession();
  const docked = useMediaQuery(DOCK_UP);
  const side = useMediaQuery(LG_UP);
  if (!panel.open || session === null) return null;
  if (docked) {
    return (
      <aside aria-label={t("assistant.name")} data-frame="assistant" className="sticky top-0 h-dvh w-96 shrink-0 border-l border-border-strong bg-surface">
        <AssistantBody docked onClose={closePanel} />
      </aside>
    );
  }
  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) closePanel();
      }}
      placement={side ? "right" : "bottom"}
      size="md"
      flush
      title={t("assistant.name")}
    >
      <AssistantBody docked={false} onClose={closePanel} />
    </Drawer>
  );
}
