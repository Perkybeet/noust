import { useState } from "react";

import { AddServerDialog } from "../../settings/servers/AddServerDialog";
import { LinkServerDialog } from "../../settings/servers/LinkServerDialog";
import { EnrollServerDialog } from "./EnrollServerDialog";

export interface AddServerProps {
  open: boolean;
  onClose: () => void;
}

type Way = "guided" | "manual" | "link";

/**
 * "Add a server": the guided way first (an address and a credential used once, the host key
 * confirmed), and, one link away, the way that never gives this central a credential at all (a
 * command run on the server, whose join code is pasted here), and the way for a server this
 * central cannot reach (behind NAT or a carrier's CGNAT) that dials in instead. Only one of the
 * three is open at a time; all end on the same server list.
 */
export function AddServer({ open, onClose }: AddServerProps) {
  const [way, setWay] = useState<Way>("guided");
  const finished = (): void => {
    setWay("guided");
    onClose();
  };
  return (
    <>
      <EnrollServerDialog open={open && way === "guided"} onClose={onClose} onManual={() => setWay("manual")} onLink={() => setWay("link")} />
      <AddServerDialog open={open && way === "manual"} onClose={finished} />
      <LinkServerDialog open={open && way === "link"} onClose={finished} />
    </>
  );
}
