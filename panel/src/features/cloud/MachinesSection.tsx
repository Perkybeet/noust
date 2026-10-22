import { useQuery } from "@tanstack/react-query";
import { Plus, Power, Unlink } from "lucide-react";
import { useState } from "react";

import { CommandHint } from "../../components/page/CommandHint";
import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { RowMenu } from "../../components/page/RowMenu";
import { ICONS } from "../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cloudMachinesQuery, machineStatus } from "./data";
import type { CloudMachine } from "./data";
import { useCloudActions } from "./useCloudActions";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

function columnsFor(t: T): readonly Column<CloudMachine>[] {
  return [
    {
      id: "node",
      header: t("cloud.machines.column.server"),
      sortValue: (machine) => machine.node,
      cell: (machine) => (
        <span className="flex min-w-0 flex-col gap-0.5">
          <Mono className="font-medium">{machine.node}</Mono>
          {machine.state === "failed" && machine.error ? <span className="max-w-prose text-12 text-pretty text-fg-muted">{machine.error}</span> : null}
        </span>
      ),
    },
    {
      id: "state",
      header: t("cloud.machines.column.state"),
      cell: (machine) => <StatusPill state={machineStatus(machine.state)} label={t(`cloud.machines.state.${machine.state as "creating"}`)} appearance="inline" size="sm" />,
    },
    { id: "address", header: t("cloud.machines.column.address"), hideBelow: "md", cell: (machine) => (machine.address ? <Mono tone="muted">{machine.address}</Mono> : <EmptyCell reason={t("cloud.machines.noAddress")} />) },
    { id: "plan", header: t("cloud.machines.column.plan"), hideBelow: "lg", cell: (machine) => <Mono tone="muted">{[machine.plan, machine.location].filter(Boolean).join(" · ")}</Mono> },
    { id: "account", header: t("cloud.machines.column.account"), hideBelow: "lg", cell: (machine) => <span className="text-13 text-fg-muted">{machine.account}</span> },
    { id: "created", header: t("cloud.machines.column.created"), hideBelow: "sm", cell: (machine) => <RelativeTime value={machine.created_at} className="text-13 text-fg-muted" /> },
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
      <RowMenu name={machine.node}>
        <MenuItem icon={<Power />} disabled={gone || powerOn.isPending} onClick={() => powerOn.mutate(machine.node)}>
          {t("cloud.machines.powerOn")}
        </MenuItem>
        <MenuItem icon={<Unlink />} disabled={unlinkMachine.isPending} onClick={() => unlinkMachine.mutate(machine.node)}>
          {t("cloud.machines.unlink")}
        </MenuItem>
        <MenuSeparator />
        <MenuItem icon={<ICONS.delete />} destructive disabled={gone} onClick={() => setDeleting(true)}>
          {t("cloud.machines.delete")}
        </MenuItem>
      </RowMenu>
      {deleting ? <DeleteMachineDialog machine={machine} open onOpenChange={setDeleting} /> : null}
    </>
  );
}

/** The machines this central created at a provider, how far each got, and what to do with one that did not make it. */
export function MachinesSection({ canCreate, onCreate }: { canCreate: boolean; onCreate: () => void }) {
  const t = useT();
  const query = useQuery(cloudMachinesQuery());
  const columns = columnsFor(t);
  const create = (
    <Button variant="primary" icon={<Plus aria-hidden="true" />} onClick={onCreate}>
      {t("cloud.machines.create")}
    </Button>
  );
  return (
    <Section title={t("cloud.machines.title")} icon={OBJECT_ICONS.server} description={t("cloud.machines.description")} actions={query.data !== undefined && query.data.length > 0 ? create : undefined}>
      <QueryState
        query={query}
        label={t("cloud.machines.loadingLabel")}
        skeleton={<DataTable caption={t("cloud.machines.caption")} columns={columns} rows={[]} getRowId={(machine) => machine.node} loading skeletonRows={2} />}
        isEmpty={(data) => data.length === 0}
        empty={
          <EmptyState
            variant="inline"
            title={t("cloud.machines.empty.title")}
            description={canCreate ? t("cloud.machines.empty.description") : t("cloud.machines.empty.needsAccount")}
            action={canCreate ? create : undefined}
          />
        }
      >
        {(machines) => <DataTable caption={t("cloud.machines.caption")} columns={columns} rows={machines} getRowId={(machine) => machine.node} rowActions={(machine) => <MachineActions machine={machine} />} />}
      </QueryState>
      <CommandHint label={t("common.words.fromTerminal")} command="noust fleet cloud server list" />
    </Section>
  );
}
