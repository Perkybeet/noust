import { BUILTIN_IDS } from "../../features/overview/panels";
import { useHomeAddress } from "../../features/overview/search";
import { usePanels } from "../../features/overview/usePanels";

/**
 * The name of the panel Home is on, which is what the phone's selector says under "Home" (the panel is the section
 * of Home). It is a chunk of its own, with the panels it reads, which come with Home's page and not with the
 * script every page loads: until it arrives the selector says what the address already says, the server's or the fleet's.
 */
export default function HomePanelName() {
  const address = useHomeAddress();
  const { panels } = usePanels();
  const open = address.panel ?? BUILTIN_IDS[address.scope];
  const panel = panels.find((candidate) => candidate.id === open);
  return <span translate={panel === undefined || panel.builtin ? undefined : "no"}>{panel?.name ?? ""}</span>;
}
