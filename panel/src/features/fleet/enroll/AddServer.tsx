import { useState } from "react";

import { AddServerDialog } from "../../settings/servers/AddServerDialog";
import { EnrollServerDialog } from "./EnrollServerDialog";

export interface AddServerProps {
  open: boolean;
  onClose: () => void;
}

/**
 * "Add a server": the guided way first (an address and a credential used once, the host key
 * confirmed), and, one link away, the way that never gives this central a credential at all (a
 * command run on the server, whose join code is pasted here). Only one of the two is open at a
 * time; both end on the same server list.
 */
export function AddServer({ open, onClose }: AddServerProps) {
  const [manual, setManual] = useState(false);
  return (
    <>
      <EnrollServerDialog open={open && !manual} onClose={onClose} onManual={() => setManual(true)} />
      <AddServerDialog
        open={open && manual}
        onClose={() => {
          setManual(false);
          onClose();
        }}
      />
    </>
  );
}
