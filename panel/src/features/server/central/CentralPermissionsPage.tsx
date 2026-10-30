/**
 * The central's permissions on a server (task: give the central permissions over a server). What
 * the server lets the central do is the first thing shown, as three levels with the current one
 * marked; the only way to change it is the command the server runs for itself, which is built as
 * the person chooses and read back with "Check again". The console cannot raise it: that is the
 * point of it, so it says so, and says what no central may ever do whatever the level.
 */

import { useQuery } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { useState } from "react";

import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { CopyValue } from "../../../components/ui/CopyValue";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { useNode } from "../../../nodes/useNode";
import { ServerQueryState } from "../errors";
import { TabToolbar } from "../TabToolbar";
import { LEVELS, LEVEL_WORDS, accessCommand, isLevel } from "./data";
import type { Level } from "./data";
import { FLEET_SELF_CAPABILITY, fleetSelfQuery } from "./queries";
import type { FleetSelf } from "./queries";

function Permissions({ node, self, onCheck, checking }: { node: string; self: FleetSelf; onCheck: () => void; checking: boolean }) {
  const t = useT();
  const current: Level | null = isLevel(self.level) ? self.level : null;
  const [wanted, setWanted] = useState<Level>(current ?? "admin");
  const [hostAccess, setHostAccess] = useState(self.host_access);
  const central = self.central ?? null;
  const changed = current === null || wanted !== current || (wanted === "admin" && hostAccess !== self.host_access);
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <TabToolbar
        summary={current === null ? t("serverCentral.summaryUnknown", { node }) : t("serverCentral.summary", { node, level: t(LEVEL_WORDS[current].label) })}
        actions={
          <Button icon={<RotateCw aria-hidden="true" />} loading={checking} onClick={onCheck}>
            {t("serverCentral.check")}
          </Button>
        }
      />
      <Card level={2} title={t("serverCentral.title")} description={central === null ? t("serverCentral.description") : t("serverCentral.descriptionOf", { central })} padding="md">
        <div className="flex min-w-0 flex-col gap-5">
          <ChoiceCards<Level>
            legend={t("serverCentral.choose", { node })}
            columns={1}
            value={wanted}
            onValueChange={setWanted}
            options={LEVELS.map((level) => ({
              value: level,
              label: t(LEVEL_WORDS[level].label),
              description: t(LEVEL_WORDS[level].description),
              ...(level === current ? { badge: t("serverCentral.now") } : {}),
            }))}
          />
          <Checkbox
            checked={wanted === "admin" && hostAccess}
            disabled={wanted !== "admin"}
            onCheckedChange={setHostAccess}
            label={t("serverCentral.hostAccess")}
            description={wanted === "admin" ? t("serverCentral.hostAccessHelp") : t("serverCentral.hostAccessOnlyAdmin")}
          />
        </div>
      </Card>
      {changed ? (
        <Card level={2} title={t("serverCentral.changeTitle", { node })} description={t("serverCentral.changeDescription")} padding="md">
          <div className="flex min-w-0 flex-col gap-3">
            <CopyValue value={accessCommand(wanted, hostAccess)} label={t("serverCentral.copy")} />
            <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("serverCentral.afterIt")}</p>
          </div>
        </Card>
      ) : (
        <InlineMessage tone="success">{t("serverCentral.inForce", { node })}</InlineMessage>
      )}
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t.rich("serverCentral.never", { command: <Mono key="command">noust fleet access</Mono> })}</p>
    </div>
  );
}

function CentralPermissions() {
  const t = useT();
  const { node } = useNode();
  const self = useQuery({ ...fleetSelfQuery(node ?? ""), enabled: node !== null });
  if (node === null) {
    // The central's own page: it is no server's to drive, so there is nothing a central may do on it.
    return <InlineMessage>{t("serverCentral.thisCentral")}</InlineMessage>;
  }
  return (
    <ServerQueryState query={self} label={t("serverCentral.label")} skeleton={<SkeletonCard lines={4} />}>
      {(data) => <Permissions node={node} self={data} checking={self.isFetching} onCheck={() => void self.refetch()} />}
    </ServerQueryState>
  );
}

/** What the central that manages this server may do on it. */
export function CentralPermissionsPage() {
  return (
    <NodeCapabilityGate capability={FLEET_SELF_CAPABILITY}>
      <CentralPermissions />
    </NodeCapabilityGate>
  );
}
