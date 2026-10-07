import { useQueryClient } from "@tanstack/react-query";
import { Pencil } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { createEnvironment, deleteEnvironment, renameEnvironment } from "../../api/queries/teams";
import type { Project, ProjectEnvironment } from "../../api/queries/teams";
import { announce } from "../../app/Announcer";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { ICONS } from "../../components/ui/icons";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { useSudoFirst } from "../app/settings/formParts";
import { NameDialog } from "./NameDialog";
import { refreshOrganization } from "./refresh";
import type { Resources } from "./useResources";

export interface EnvironmentActions {
  /** "New environment" opens its dialog: a name, and the environment is there. */
  create: () => void;
  /** The menu of one environment: rename it, delete it. */
  menu: (environment: ProjectEnvironment, environments: readonly ProjectEnvironment[]) => ReactNode;
  dialogs: ReactNode;
}

/**
 * Everything the console does to an environment: create, rename, delete. A project always keeps
 * one (the server refuses to delete the last), and one that holds something is not deleted; the
 * menu says both before the operator asks, as a disabled item with its reason.
 */
export function useEnvironmentActions(
  project: Project | undefined,
  resources: Pick<Resources, "held">,
  { governs, onCreated, afterDelete }: { governs: boolean; onCreated: (environment: ProjectEnvironment) => void; afterDelete: () => void },
): EnvironmentActions {
  const t = useT();
  const queryClient = useQueryClient();
  const { node } = useNode();
  // Sudo mode first, so "Confirm it's you" never opens on top of the question that deletes.
  const sudoFirst = useSudoFirst();
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<ProjectEnvironment | null>(null);
  const [deleting, setDeleting] = useState<ProjectEnvironment | null>(null);

  const menu = (environment: ProjectEnvironment, environments: readonly ProjectEnvironment[]): ReactNode => {
    if (!governs) return null;
    const count = resources.held.get(`${environment.team}/${environment.project}/${environment.slug}`) ?? 0;
    const onlyOne = environments.length <= 1;
    const isDefault = environment.team === "default" && environment.project === "default" && environment.slug === "production";
    const reason = onlyOne ? t("teams.environments.deleteLast") : isDefault ? t("teams.environments.deleteDefault") : count > 0 ? t("teams.environments.deleteHeld", { count }) : undefined;
    return (
      <Menu align="end" trigger={<IconButton label={t("teams.environments.actionsFor", { name: environment.name })} icon={<ICONS.more />} tooltip={false} />}>
        <MenuItem icon={<Pencil />} onClick={() => setRenaming(environment)}>
          {t("teams.environments.rename")}
        </MenuItem>
        <MenuSeparator />
        <MenuItem icon={<ICONS.delete />} destructive disabled={reason !== undefined} {...(reason !== undefined ? { description: reason } : {})} onClick={() => sudoFirst(() => setDeleting(environment), t("teams.environments.deleteCouldNotStart", { name: environment.name }))}>
          {t("teams.environments.delete")}
        </MenuItem>
      </Menu>
    );
  };

  const dialogs =
    project === undefined ? null : (
      <>
        <NameDialog
          open={creating}
          onOpenChange={setCreating}
          title={t("teams.environments.newTitle", { project: project.name })}
          description={t("teams.environments.newDescription")}
          nameLabel={t("teams.environments.nameLabel")}
          nameHelp={t("teams.environments.nameHelp")}
          submitLabel={t("teams.environments.create")}
          slug={{ label: t("teams.shortName"), help: t("teams.shortNameHelp") }}
          onSubmit={async ({ name, slug }) => {
            const created = await createEnvironment(project.team, project.slug, name, slug === "" ? undefined : slug);
            await refreshOrganization(queryClient);
            announce(t("teams.environments.created", { name: created.name }));
            onCreated(created);
          }}
        />
        <NameDialog
          open={renaming !== null}
          onOpenChange={(next) => {
            if (!next) setRenaming(null);
          }}
          title={t("teams.environments.renameTitle", { name: renaming?.name ?? "" })}
          description={t.rich("teams.environments.renameDescription", { slug: <Mono>{renaming?.slug ?? ""}</Mono> })}
          nameLabel={t("teams.environments.nameLabel")}
          submitLabel={t("teams.environments.renameSubmit")}
          initialName={renaming?.name ?? ""}
          unchanged={({ name }) => name === renaming?.name}
          onSubmit={async ({ name }) => {
            if (renaming === null) return;
            await renameEnvironment(renaming.team, renaming.project, renaming.slug, name);
            await refreshOrganization(queryClient);
          }}
        />
        {deleting !== null ? (
          <ConfirmDialog
            open
            onOpenChange={(next) => {
              if (!next) setDeleting(null);
            }}
            friction="simple"
            server={node}
            title={t("teams.environments.deleteTitle", { name: deleting.name })}
            description={t("teams.environments.deleteDescription", { name: deleting.name })}
            actionLabel={t("teams.environments.deleteAction")}
            onConfirm={async () => {
              await deleteEnvironment(deleting.team, deleting.project, deleting.slug);
              await refreshOrganization(queryClient);
              announce(t("teams.environments.deleted", { name: deleting.name }));
              afterDelete();
            }}
          />
        ) : null}
      </>
    );

  return { create: () => setCreating(true), menu, dialogs };
}
