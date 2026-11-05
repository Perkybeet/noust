import { useQuery } from "@tanstack/react-query";
import { Outlet } from "@tanstack/react-router";
import type { ReactNode } from "react";

import type { PageHeaderProps } from "../../app/PageHeader";
import { DetailPage } from "../../components/page/DetailPage";
import { useNodeCapability } from "../../nodes/capability";
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
 * beside its title are the `header`'s.
 */
export function MachinePage({ title, description, header, children }: { title: string; description?: ReactNode; header?: Omit<PageHeaderProps, "title" | "description">; children: ReactNode }) {
  const capability = useNodeCapability(SERVER_CAPABILITY);
  const available = capability.status === "available" || capability.status === "unknown";
  const changes = useQuery({ ...changesQuery(), enabled: available });
  const hasJob = useHasServerJob();
  const pending = (changes.data ?? []).filter((change) => change.status === "pending");
  return (
    <DetailPage
      header={{ title, ...(description !== undefined ? { description } : {}), ...header }}
      {...(pending.length > 0 ? { banner: <PendingChanges changes={pending} /> } : {})}
      {...(hasJob ? { job: <ServerJobSlot /> } : {})}
    >
      {children}
    </DetailPage>
  );
}
