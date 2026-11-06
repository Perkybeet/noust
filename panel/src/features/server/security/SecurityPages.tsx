/**
 * The five sections of Security, each the page it draws: the checks, SSH, the blocks, the web firewall and who has
 * entered. They share a frame (`MachinePage`: the title, what it is for, what is pending on the machine and the job in
 * hand) and the server's capability, which a node older than the area does not have: its sections say so.
 */

import type { ReactNode } from "react";

import { NodeCapabilityGate } from "../../../nodes/capability";
import { useT } from "../../../i18n";
import { MachinePage } from "../MachinePage";
import { SERVER_CAPABILITY } from "../queries";
import { AccessView } from "./AccessView";
import { BansView } from "./BansView";
import { ChecksView } from "./ChecksView";
import { RunChecks, SecuritySummary } from "./SecuritySummary";
import { SshView } from "./SshView";
import { WafState, WafView } from "./WafView";

function Gate({ children }: { children: ReactNode }) {
  return <NodeCapabilityGate capability={SERVER_CAPABILITY}>{children}</NodeCapabilityGate>;
}

/** Security > Checks. */
export function ChecksPage() {
  const t = useT();
  return (
    <MachinePage title={t("server.security.views.checks")} description={<SecuritySummary />}>
      <Gate>
        <RunChecks />
        <ChecksView />
      </Gate>
    </MachinePage>
  );
}

/** Security > SSH. */
export function SshPage() {
  const t = useT();
  return (
    <MachinePage title={t("server.security.views.ssh")} description={t("server.ssh.pageDescription")}>
      <Gate>
        <SshView />
      </Gate>
    </MachinePage>
  );
}

/** Security > Blocks. */
export function BlocksPage() {
  const t = useT();
  return (
    <MachinePage title={t("server.security.views.bans")} description={t("server.bans.pageDescription")}>
      <Gate>
        <BansView />
      </Gate>
    </MachinePage>
  );
}

/** Security > Web firewall. */
export function WafPage() {
  const t = useT();
  return (
    <MachinePage title={t("server.security.views.waf")} description={t("server.waf.pageDescription")} status={<WafState />}>
      <Gate>
        <WafView />
      </Gate>
    </MachinePage>
  );
}

/** Security > Access and sessions. */
export function AccessPage() {
  const t = useT();
  return (
    <MachinePage title={t("server.security.views.access")} description={t("server.access.pageDescription")}>
      <Gate>
        <AccessView />
      </Gate>
    </MachinePage>
  );
}
