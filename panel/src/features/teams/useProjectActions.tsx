import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Pencil } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { createProject, deleteProject, renameProject } from "../../api/queries/teams";
import type { Project } from "../../api/queries/teams";
import { announce } from "../../app/Announcer";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { ICONS } from "../../components/ui/icons";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Select } from "../../components/ui/Select";
import { Field } from "../../components/ui/Field";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { useSudoFirst } from "../app/settings/formParts";
import { NameDialog } from "./NameDialog";
import { projectPath } from "./organization";
import { refreshOrganization } from "./refresh";
import { useOrganization } from "./useOrganization";
import type { Resources } from "./useResources";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

/** The default project of the default team receives what nobody assigned: the server will not delete it. */
export function isDefaultProject(project: Pick<Project, "team" | "slug">): boolean {
  return project.team === "default" && project.slug === "default";
}

export interface ProjectActions {
  /** The menu at the end of a project's row: open it, rename it, delete it. */
  rowMenu: (project: Project) => ReactNode;
  /** The same two verbs as header menu items, for the project's own page. */
  menuItems: (project: Project) => ReactNode;
  /** "New project" opens the dialog; the teams it may go to are the ones the principal governs. */
  create: () => void;
  /** Where a new project may go: the teams the principal may govern. */
  teams: readonly { slug: string; name: string }[];
  dialogs: ReactNode;
}

/**
 * Everything the console does to a project, in one place for the list, a team's page and the
 * project's own: create, rename, delete. The guards are the server's (an owner governs a team;
 * a project that holds something is not deleted); the menu says them before the operator
 * asks, as a disabled item with its reason, and the server's refusal is shown if it still comes.
 */
export function useProjectActions(resources: Pick<Resources, "held" | "known">, { afterDelete }: { afterDelete?: () => void } = {}): ProjectActions {
  const t = useT();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { node } = useNode();
  const org = useOrganization();
  // Sudo mode first, so "Confirm it's you" never opens on top of the question that deletes.
  const sudoFirst = useSudoFirst();
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState<Project | null>(null);
  const [team, setTeam] = useState<string | null>(null);

  const governed = org.teams.filter((candidate) => org.can("teams.manage", candidate.slug));
  const target = team !== null && governed.some((candidate) => candidate.slug === team) ? team : (governed.find((candidate) => candidate.slug === org.active)?.slug ?? governed[0]?.slug ?? null);

  const held = (project: Project): number => resources.held.get(`${project.team}/${project.slug}`) ?? 0;

  const menuItems = (project: Project): ReactNode => {
    const governs = org.can("teams.manage", project.team);
    if (!governs) return null;
    const count = held(project);
    const reason = isDefaultProject(project) ? t("teams.projects.deleteDefault") : count > 0 ? t("teams.projects.deleteHeld", { count }) : undefined;
    return (
      <>
        <MenuItem icon={<Pencil />} onClick={() => setRenaming(project)}>
          {t("teams.projects.rename")}
        </MenuItem>
        <MenuSeparator />
        <MenuItem icon={<ICONS.delete />} destructive disabled={reason !== undefined} {...(reason !== undefined ? { description: reason } : {})} onClick={() => sudoFirst(() => setDeleting(project), t("teams.projects.deleteCouldNotStart", { name: project.name }))}>
          {t("teams.projects.delete")}
        </MenuItem>
      </>
    );
  };

  const rowMenu = (project: Project): ReactNode => (
    <Menu align="end" trigger={<IconButton label={t("teams.projects.actionsFor", { name: project.name })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
      <MenuItem icon={<OBJECT_ICONS.project />} onClick={() => void navigate(projectPath(project))}>
        {t("teams.projects.open")}
      </MenuItem>
      {menuItems(project)}
    </Menu>
  );

  const dialogs = (
    <>
      <NameDialog
        open={creating}
        onOpenChange={setCreating}
        title={t("teams.projects.newTitle")}
        description={t("teams.projects.newDescription")}
        nameLabel={t("teams.projects.nameLabel")}
        nameHelp={t("teams.projects.nameHelp")}
        submitLabel={t("teams.projects.create")}
        slug={{ label: t("teams.shortName"), help: t("teams.shortNameHelp") }}
        onSubmit={async ({ name, slug }) => {
          if (target === null) return;
          const created = await createProject(target, name, slug === "" ? undefined : slug);
          await refreshOrganization(queryClient);
          announce(t("teams.projects.created", { name: created.name }));
          void navigate({ ...projectPath(created), search: {} });
        }}
      >
        {governed.length > 1 ? (
          <Field label={t("teams.teamLabel")} nativeLabel={false} description={t("teams.projects.teamHelp")}>
            <Select aria-label={t("teams.teamLabel")} value={target ?? ""} options={governed.map((candidate) => ({ value: candidate.slug, label: candidate.name }))} onValueChange={setTeam} />
          </Field>
        ) : null}
      </NameDialog>
      <NameDialog
        open={renaming !== null}
        onOpenChange={(next) => {
          if (!next) setRenaming(null);
        }}
        title={t("teams.projects.renameTitle", { name: renaming?.name ?? "" })}
        description={t.rich("teams.projects.renameDescription", { slug: <Mono>{renaming?.slug ?? ""}</Mono> })}
        nameLabel={t("teams.projects.nameLabel")}
        submitLabel={t("teams.projects.renameSubmit")}
        initialName={renaming?.name ?? ""}
        unchanged={({ name }) => name === renaming?.name}
        onSubmit={async ({ name }) => {
          if (renaming === null) return;
          await renameProject(renaming.team, renaming.slug, name);
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
          title={t("teams.projects.deleteTitle", { name: deleting.name })}
          description={t("teams.projects.deleteDescription", { name: deleting.name })}
          actionLabel={t("teams.projects.deleteAction")}
          onConfirm={async () => {
            await deleteProject(deleting.team, deleting.slug);
            await refreshOrganization(queryClient);
            announce(t("teams.projects.deleted", { name: deleting.name }));
            afterDelete?.();
          }}
        />
      ) : null}
    </>
  );

  return {
    rowMenu,
    menuItems,
    create: () => setCreating(true),
    teams: governed.map((candidate) => ({ slug: candidate.slug, name: candidate.name })),
    dialogs,
  };
}
