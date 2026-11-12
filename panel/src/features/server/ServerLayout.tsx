/**
 * The Server area: the header of the section on screen, what is pending on the machine and the job in hand, over the
 * section the second column has open. Status is titled with the machine's own name, its state and the facts that
 * answer "how is it"; every other section is titled with its own name and says which machine it is about on the
 * line under it, so the title is what the page is for and the column is how a person got there.
 *
 * On a central with a node selected every read and action goes to that node through the proxy, and the node is
 * named in the column and in the selector. A node whose Noust predates this area answers none of it: its sections
 * say so, except Services, which every version has.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";
import { RotateCw } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { versionQuery } from "../../api/queries/system";
import { useRenderedPathname } from "../../app/renderedPath";
import { DetailPage } from "../../components/page/DetailPage";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { textLinkClassName } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNodeCapability } from "../../nodes/capability";
import { useHasFleet } from "../../nodes/servers";
import { useNode } from "../../nodes/useNode";
import { useAttention } from "../inbox/useAttention";
import { serverItems, verdictOf } from "./attention";
import type { Verdict } from "./attention";
import { HeaderSlot } from "./headerSlot";
import { ExpiredChanges, useExpiredChanges } from "./ExpiredChanges";
import { PendingChanges } from "./PendingChanges";
import { PowerProvider, ScheduledPowerBanner, usePowerDialog } from "./PowerDialog";
import { SERVER_CAPABILITY, changesQuery, firewallQuery, summaryQuery } from "./queries";
import type { ServerSummary } from "./queries";
import { serverSection } from "./sections";
import { ServerJobProvider, ServerJobSlot, useHasServerJob } from "./serverJob";

function verdictLabel(t: T, verdict: Verdict): string {
  switch (verdict.label) {
    case "healthy":
      return t("server.verdict.healthy");
    case "attention":
      return t("server.verdict.attention");
    case "critical":
      return t("server.verdict.critical");
  }
}

/** The system as its own name and version say it ("Debian 12"): the version is left out where the name already holds it. */
function systemName(summary: ServerSummary): string {
  const { name, version } = summary.os;
  return `${name}${version !== "" && !name.includes(version) ? ` ${version}` : ""}`;
}

/** The Noust this server runs, or a placeholder of its width while the version is not known. */
function NoustVersion() {
  const t = useT();
  const version = useQuery(versionQuery());
  return version.data === undefined ? null : <span>{t.rich("server.header.noust", { version: <Mono key="version">{version.data.current_version}</Mono> })}</span>;
}

/**
 * How many ports answer from outside, and where to look at them: Network is where ports are opened and closed, and a
 * person who comes to the machine to see "what is open" would otherwise look for it here (the walk of the
 * recorded tasks needed a detour through Security before this line).
 */
function OpenPorts() {
  const t = useT();
  const firewall = useQuery(firewallQuery());
  if (firewall.data === undefined || firewall.data.ports_error) return null;
  const open = new Set(firewall.data.ports.filter((port) => port.reachable).map((port) => `${port.proto}:${String(port.port)}`)).size;
  return (
    <Link to="/network/listening" className={textLinkClassName("inline")}>
      {t("server.header.openPorts", { count: open })}
    </Link>
  );
}

/** One line of facts under a section's title: the machine it is about, its system and its Noust. */
function Facts({ summary, name, overview }: { summary: ServerSummary; name: string; overview: boolean }) {
  return (
    <>
      {overview ? null : <Mono>{name}</Mono>}
      <span>{systemName(summary)}</span>
      <NoustVersion />
      {overview ? <OpenPorts /> : null}
    </>
  );
}

function Layout() {
  const t = useT();
  const { node } = useNode();
  const pathname = useRenderedPathname();
  const section = serverSection(pathname);
  const overview = section.title === null && (pathname === "/server" || pathname === "/server/");
  const { data: sessionHostname } = useQuery({ ...sessionQuery(), select: (session) => session.hostname });
  const power = usePowerDialog();
  const hasJob = useHasServerJob();
  const hasFleet = useHasFleet();
  const capability = useNodeCapability(SERVER_CAPABILITY);
  const available = capability.status === "available" || capability.status === "unknown";
  const summary = useQuery({ ...summaryQuery(), enabled: available });
  const attention = useAttention(available);
  const changes = useQuery({ ...changesQuery(), enabled: available });
  // Where the section's own actions are drawn: the page puts them here (`HeaderActions`), and the slot is a box with no
  // size of its own, so the header is the same height before they arrive.
  const [slot, setSlot] = useState<HTMLElement | null>(null);

  const name = node ?? sessionHostname ?? t("server.title");
  // The header says what the machine's slice of the one list says, so it never disagrees with the bell or the Status page.
  const verdict = available && summary.data !== undefined && !attention.loading ? verdictOf(serverItems(attention.items)) : null;
  const pending = (changes.data ?? []).filter((change) => change.status === "pending");
  // A change its timer undid because nobody confirmed it: the banner of the pending one is gone, and this says why.
  const expired = useExpiredChanges(changes.data ?? []);
  const scheduled = summary.data?.power.scheduled ?? null;
  // The reboot is asked for where the machine is looked at as a whole: Status and This machine. Everywhere else it is a
  // distraction from the section's own work.
  const rebootHere = overview || pathname === "/server/machine";
  // Only the central itself is marked: any other server is named by the column and the selector.
  const isCentral = hasFleet && node === null;

  const banner =
    pending.length > 0 || scheduled !== null || expired.shown.length > 0 ? (
      <div className="flex min-w-0 flex-col gap-2">
        {pending.length > 0 ? <PendingChanges changes={pending} /> : null}
        {expired.shown.length > 0 ? <ExpiredChanges {...expired} /> : null}
        {scheduled !== null ? <ScheduledPowerBanner scheduled={scheduled} /> : null}
      </div>
    ) : undefined;

  const meta: ReactNode = !available ? undefined : summary.data !== undefined ? (
    <Facts summary={summary.data} name={name} overview={overview} />
  ) : (
    <span className="flex h-5 w-80 max-w-full items-center">
      <Skeleton className="h-3.5 w-full" />
    </span>
  );

  return (
    <HeaderSlot value={slot}>
      <DetailPage
        header={{
          // Status is named after the machine; any other section after itself, so a heading says what the page is for.
          title: section.title === null ? name : t(section.title),
          mono: section.title === null,
          ...(overview && available
            ? {
                status: verdict !== null ? <StatusPill state={verdict.state} label={verdictLabel(t, verdict)} /> : <Skeleton className="h-6 w-28 rounded-pill" />,
                ...(isCentral ? { statusExtras: <Badge>{t("server.header.central")}</Badge> } : {}),
              }
            : {}),
          ...(section.facts || overview ? (meta !== undefined ? { meta } : {}) : {}),
          ...(section.description !== undefined ? { description: t(section.description) } : {}),
          // A box that holds what the section puts here, and no size when it holds nothing.
          actions: (
            <>
              <span ref={setSlot} className="contents" />
              {available && rebootHere ? (
                <Button icon={<RotateCw aria-hidden="true" />} onClick={() => power.open("reboot")}>
                  {t("server.header.reboot")}
                </Button>
              ) : null}
            </>
          ),
        }}
        banner={banner}
        job={hasJob ? <ServerJobSlot /> : undefined}
      >
        <Outlet />
      </DetailPage>
    </HeaderSlot>
  );
}

/** The Server area's layout route. */
export function ServerLayout() {
  return (
    <ServerJobProvider>
      <PowerProvider>
        <Layout />
      </PowerProvider>
    </ServerJobProvider>
  );
}
