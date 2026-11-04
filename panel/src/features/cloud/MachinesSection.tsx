import { useQuery } from "@tanstack/react-query";
import { Power, Unlink } from "lucide-react";
import { useState } from "react";

import { QueryState } from "../../components/page/QueryState";
import { RowMenu } from "../../components/page/RowMenu";
import { SaidLines } from "../../components/page/SaidLines";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { useCan } from "../../components/page/permissions";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cloudMachinesQuery, machineStatus } from "./data";
import type { CloudMachine } from "./data";
import { providerName } from "./providerName";
import { useCloudActions } from "./useCloudActions";

function columnsFor(t: T): readonly Column<CloudMachine>[] {
  return [
    { id: "node", header: t("cloud.machines.column.server"), sortValue: (machine) => machine.node, cell: (machine) => <Mono className="font-medium">{machine.node}</Mono> },
    { id: "account", header: t("cloud.machines.column.account"), hideBelow: "md", cell: (machine) => <span className="text-13 text-fg-muted">{machine.account}</span> },
    {
      id: "place",
      header: t("cloud.machines.column.place"),
      hideBelow: "md",
      cell: (machine) => <span className="text-13 text-fg">{[providerName(t, machine.provider), machine.location].filter(Boolean).join(", ")}</span>,
    },
    { id: "plan", header: t("cloud.machines.column.plan"), hideBelow: "lg", cell: (machine) => (machine.plan ? <Mono tone="muted">{machine.plan}</Mono> : <EmptyCell reason={t("cloud.machines.noPlan")} />) },
    {
      id: "state",
      header: t("cloud.machines.column.state"),
      cell: (machine) => <StatusPill state={machineStatus(machine.state)} label={t(`cloud.machines.state.${machine.state as "creating"}`)} appearance="inline" size="sm" />,
    },
    { id: "address", header: t("cloud.machines.column.address"), hideBelow: "lg", cell: (machine) => (machine.address ? <Mono tone="muted">{machine.address}</Mono> : <EmptyCell reason={t("cloud.machines.noAddress")} />) },
  ];
}

/** Deleting a machine destroys it and what is on it: the server's name must be typed. */
function DeleteMachineDialog({ machine, open, onOpenChange }: { machine: CloudMachine; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const { deleteMachine } = useCloudActions();
  const [removeNode, setRemoveNode] = useState(false);
  return (
    <ConfirmDialog
      friction="type"
      destructive
      confirmText={machine.node}
      open={open}
      onOpenChange={(next) => {
        if (!next) setRemoveNode(false);
        onOpenChange(next);
      }}
      title={t("cloud.machines.deleteTitle", { name: machine.node })}
      description={t("cloud.machines.deleteBody", { name: machine.node })}
      actionLabel={t("cloud.machines.deleteAction")}
      onConfirm={async () => {
        // design-exception: write-without-permission opened only from the machine's row, which is drawn only for fleet.manage
        await deleteMachine.mutateAsync({ node: machine.node, removeNode });
      }}
    >
      <Checkbox checked={removeNode} onCheckedChange={setRemoveNode} label={t("cloud.machines.removeNode")} description={t("cloud.machines.removeNodeHelp")} />
    </ConfirmDialog>
  );
}

function MachineActions({ machine }: { machine: CloudMachine }) {
  const t = useT();
  const { powerOn, unlinkMachine } = useCloudActions();
  const [deleting, setDeleting] = useState(false);
  const gone = machine.state === "deleted";
  return (
    <>
      {/* A machine that did not make it is the row's one thing to do: it is billed until it is deleted, so the way to do it is on the row. */}
      {machine.state === "failed" ? (
        <Button size="sm" icon={<ICONS.delete aria-hidden="true" />} aria-label={t("cloud.machines.deleteShortLabel", { name: machine.node })} onClick={() => setDeleting(true)}>
          {t("common.words.delete")}
        </Button>
      ) : (
        <RowMenu name={machine.node}>
          {/* design-exception: write-without-permission the row's menu is drawn only where the table has actions, for fleet.manage */}
          <MenuItem icon={<Power />} disabled={gone || powerOn.isPending} onClick={() => powerOn.mutate(machine.node)}>
            {t("cloud.machines.powerOn")}
          </MenuItem>
          {/* design-exception: write-without-permission the row's menu is drawn only where the table has actions, for fleet.manage */}
          <MenuItem icon={<Unlink />} disabled={unlinkMachine.isPending} onClick={() => unlinkMachine.mutate(machine.node)}>
            {t("cloud.machines.unlink")}
          </MenuItem>
          <MenuSeparator />
          <MenuItem icon={<ICONS.delete />} destructive disabled={gone} onClick={() => setDeleting(true)}>
            {t("cloud.machines.delete")}
          </MenuItem>
        </RowMenu>
      )}
      {deleting ? <DeleteMachineDialog machine={machine} open onOpenChange={setDeleting} /> : null}
    </>
  );
}

/**
 * What the provider said about the machines that did not make it, as it said it, and what that costs: they stay on
 * record, billed, until they are deleted. The system's words are the system's: in mono, never paraphrased.
 */
function Refusals({ machines }: { machines: readonly CloudMachine[] }) {
  const t = useT();
  const lines = machines
    .filter((machine) => machine.state === "failed" && machine.error)
    .map((machine) => ({
      id: machine.node,
      said: t.rich("cloud.machines.providerSaid", {
        name: (
          <span key="name" translate="no" className="font-medium">
            {machine.node}
          </span>
        ),
        said: <Mono key="said">{machine.error}</Mono>,
      }),
      note: t("cloud.machines.stillBilled"),
    }));
  return <SaidLines label={t("cloud.machines.refusalsLabel")} lines={lines} />;
}

/** The machines this central created at a provider, how far each got, and what to do with one that did not make it. */
export function MachinesSection({ canCreate }: { canCreate: boolean }) {
  const t = useT();
  const canManage = useCan("fleet.manage");
  const query = useQuery(cloudMachinesQuery());
  const columns = columnsFor(t);
  const rows = query.data !== undefined && query.data.length > 0;
  return (
    <SurfaceSection
      title={t("cloud.machines.title")}
      icon={OBJECT_ICONS.server}
      description={t("cloud.machines.description")}
      flush={query.data === undefined || rows}
    >
      <QueryState
        query={query}
        label={t("cloud.machines.loadingLabel")}
        skeleton={<DataTable embedded caption={t("cloud.machines.caption")} columns={columns} rows={[]} getRowId={(machine) => machine.node} loading skeletonRows={2} />}
        isEmpty={(data) => data.length === 0}
        empty={<EmptyState variant="inline" title={t("cloud.machines.empty.title")} description={canCreate ? t("cloud.machines.empty.description") : t("cloud.machines.empty.needsAccount")} />}
      >
        {(machines) => (
          <>
            <DataTable
              embedded
              caption={t("cloud.machines.caption")}
              columns={columns}
              rows={machines}
              getRowId={(machine) => machine.node}
              {...(canManage ? { rowActions: (machine: CloudMachine) => <MachineActions machine={machine} /> } : {})}
            />
            <Refusals machines={machines} />
          </>
        )}
      </QueryState>
    </SurfaceSection>
  );
}
