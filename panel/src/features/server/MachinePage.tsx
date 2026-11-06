import { useQuery } from "@tanstack/react-query";
import { Outlet } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode } from "react";

import type { PageHeaderProps } from "../../app/PageHeader";
import { DetailPage } from "../../components/page/DetailPage";
import { useNodeCapability } from "../../nodes/capability";
import { HeaderSlot } from "./headerSlot";
import { PendingChanges } from "./PendingChanges";
import { PowerProvider } from "./PowerDialog";
import { SERVER_CAPABILITY, changesQuery } from "./queries";
import { ServerJobProvider, ServerJobSlot, useHasServerJob } from "./serverJob";

/**
 * What the pages of the machine's own areas outside Server (Network and Security) need from the Server's layout,
 * which they left in 4.2: the job in hand (a run of the checks, a fix) and the power dialog that some fixes end in.
 * It is the layout route of those areas.
 */
export function MachineProviders() {
  return (
    <ServerJobProvider>
      <PowerProvider>
        <Outlet />
      </PowerProvider>
    </ServerJobProvider>
  );
}

/**
 * The frame of a page of the machine outside the Server's layout: its title and what it is for, what is
 * pending on the machine (a security change that waits to be confirmed) and the job in hand, over the page.
 * The page itself is the component the section had under the Server, as it was. A page's own actions and the state
 * beside its title are the `header`'s; a section drawn under it puts its actions at the right of the title too
 * (`HeaderActions`), as the sections of the Server do.
 */
export function MachinePage({
  title,
  description,
  status,
  header,
  children,
}: {
  title: string;
  description?: ReactNode;
  status?: ReactNode;
  header?: Omit<PageHeaderProps, "title" | "description">;
  children: ReactNode;
}) {
  const capability = useNodeCapability(SERVER_CAPABILITY);
  const available = capability.status === "available" || capability.status === "unknown";
  const changes = useQuery({ ...changesQuery(), enabled: available });
  const hasJob = useHasServerJob();
  const pending = (changes.data ?? []).filter((change) => change.status === "pending");
  // Where the page's own actions are drawn: a box with no size of its own, so the header is the same height before they arrive.
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  return (
    <HeaderSlot value={slot}>
      <DetailPage
        header={{
          title,
          ...(description !== undefined ? { description } : {}),
          ...(status !== undefined ? { status } : {}),
          ...header,
          actions: (
            <>
              {header?.actions}
              <span ref={setSlot} className="contents" />
            </>
          ),
        }}
        {...(pending.length > 0 ? { banner: <PendingChanges changes={pending} /> } : {})}
        {...(hasJob ? { job: <ServerJobSlot /> } : {})}
      >
        {children}
      </DetailPage>
    </HeaderSlot>
  );
}
