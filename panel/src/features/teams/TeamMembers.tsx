import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut, UserMinus, UserPlus } from "lucide-react";
import { useState } from "react";

import { isTeamRole, removeMember, setMemberRole, teamMembersQuery, TEAM_ROLES } from "../../api/queries/teams";
import type { Member, TeamRole } from "../../api/queries/teams";
import { sessionQuery } from "../../api/queries/auth";
import { announce } from "../../app/Announcer";
import { CommandHint } from "../../components/page/CommandHint";
import { RelativeTime } from "../../components/page/RelativeTime";
import { QueryState } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { useSudoFirst } from "../app/settings/formParts";
import { reportActionError } from "../apps/useAppActions";
import { TabToolbar } from "../server/TabToolbar";
import { roleLabel } from "../settings/accounts/roles";
import { InviteDialog } from "../settings/accounts/InviteDialog";
import { AddMemberDialog } from "./AddMemberDialog";
import { refreshOrganization } from "./refresh";
import { teamRoleDescription, teamRoleLabel } from "./roles";
import { useTeam } from "./TeamLayout";
import { useOrganization } from "./useOrganization";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

/** The role of one member, changed where it is read: a value saved the moment it is chosen. */
function RoleSelect({ member, team, t }: { member: Member; team: string; t: T }) {
  const queryClient = useQueryClient();
  const change = useMutation({
    mutationFn: (role: TeamRole) => setMemberRole(team, member.username, role),
    onSuccess: () => refreshOrganization(queryClient),
    onError: (error) => reportActionError(t("teams.members.roleFailed", { name: member.username }), error),
  });
  return (
    <Select
      size="sm"
      aria-label={t("teams.members.roleOf", { name: member.username })}
      value={isTeamRole(member.role) ? member.role : null}
      disabled={change.isPending}
      options={TEAM_ROLES.map((role) => ({ value: role, label: teamRoleLabel(t, role), hint: teamRoleDescription(t, role) }))}
      onValueChange={(role) => {
        if (role !== member.role) change.mutate(role);
      }}
      className="min-w-36"
    />
  );
}

/**
 * Who is in the team and what each may do there. An owner changes a role in place, takes a
 * person out or leaves; a person who has no account yet is invited through the same invitation
 * the accounts page uses, when the operator may create accounts. Anyone else reads the list.
 * A team keeps at least one owner: the menu says so before the server has to.
 */
export function TeamMembers() {
  const t = useT();
  const team = useTeam();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const org = useOrganization();
  const session = useQuery(sessionQuery());
  const members = useQuery(teamMembersQuery(team.slug));
  const [adding, setAdding] = useState(false);
  const [inviting, setInviting] = useState(false);
  const [removing, setRemoving] = useState<Member | null>(null);
  const sudoFirst = useSudoFirst();

  const governs = org.can("teams.manage", team.slug);
  // An invitation creates a credential, which is the security officer's call and the central's own.
  const mayInvite = governs && node === null && (session.data?.permissions?.includes("accounts.manage") ?? false);
  const me = session.data?.account?.username ?? null;
  const rows = members.data?.members ?? [];
  const owners = rows.filter((member) => member.role === "owner").length;

  const columns: Column<Member>[] = [
    {
      id: "account",
      header: t("teams.members.columnAccount"),
      card: "title",
      sortValue: (member) => member.username,
      cell: (member) => (
        <span className="flex min-w-0 items-center gap-2">
          <Mono tone="default" truncate>
            {member.username}
          </Mono>
          {member.username === me ? <span className="text-12 text-fg-muted">{t("teams.members.you")}</span> : null}
        </span>
      ),
    },
    {
      id: "role",
      header: t("teams.members.columnRole"),
      width: "w-48",
      card: "status",
      sortValue: (member) => TEAM_ROLES.indexOf(member.role as TeamRole),
      cell: (member) => (governs ? <RoleSelect member={member} team={team.slug} t={t} /> : <span className="text-fg-muted">{teamRoleLabel(t, member.role)}</span>),
    },
    {
      id: "server",
      header: t("teams.members.columnServerRole"),
      width: "w-40",
      hideBelow: "md",
      sortValue: (member) => member.instance_role,
      cell: (member) => <span className="text-fg-muted">{roleLabel(t, member.instance_role)}</span>,
    },
    {
      id: "added",
      header: t("teams.members.columnAdded"),
      width: "w-40",
      hideBelow: "sm",
      sortValue: (member) => -member.created_at,
      cell: (member) => <RelativeTime value={member.created_at} className="text-fg-muted" />,
    },
  ];

  const Add = ICONS.add;
  const actions = governs ? (
    <>
      {mayInvite ? (
        <Button icon={<UserPlus aria-hidden="true" />} onClick={() => setInviting(true)}>
          {t("teams.members.invite")}
        </Button>
      ) : null}
      <Button variant="primary" icon={<Add aria-hidden="true" />} onClick={() => setAdding(true)}>
        {t("teams.members.add")}
      </Button>
    </>
  ) : undefined;

  const table = (loading: boolean) => (
    <DataTable
      columns={columns}
      rows={loading ? [] : rows}
      getRowId={(member) => member.username}
      caption={t("teams.members.caption", { name: team.name })}
      loading={loading}
      skeletonRows={3}
      defaultSort={{ column: "role", direction: "descending" }}
      {...(governs
        ? {
            rowActions: (member: Member) => {
              const self = member.username === me;
              const last = member.role === "owner" && owners <= 1;
              return (
                <Menu align="end" trigger={<IconButton label={t("teams.members.actionsFor", { name: member.username })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
                  <MenuItem
                    icon={self ? <LogOut /> : <UserMinus />}
                    destructive
                    disabled={last}
                    {...(last ? { description: t("teams.members.lastOwner") } : {})}
                    onClick={() => sudoFirst(() => setRemoving(member), t("teams.members.removeCouldNotStart", { name: member.username }))}
                  >
                    {self ? t("teams.members.leave") : t("teams.members.remove")}
                  </MenuItem>
                </Menu>
              );
            },
          }
        : {})}
    />
  );

  const content = (
    <QueryState
      query={members}
      label={t("teams.members.queryLabel")}
      skeleton={table(true)}
      isEmpty={() => rows.length === 0}
      empty={
        <EmptyState
          variant="firstUse"
          icon={<OBJECT_ICONS.team />}
          title={t("teams.members.emptyTitle")}
          description={t("teams.members.emptyDescription")}
          command={`noust team add-member ${team.slug} <account> --role owner`}
          level={3}
        />
      }
    >
      {() => table(false)}
    </QueryState>
  );

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <TabToolbar
        summary={members.data === undefined ? t("teams.members.loading") : t("teams.members.summary", { count: rows.length })}
        {...(actions !== undefined ? { actions } : {})}
        loading={members.isPending}
      />
      {!governs && org.available ? <Notice title={t("teams.members.readOnlyTitle")}>{t("teams.members.readOnly")}</Notice> : null}
      {content}
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("teams.members.rolesNote")}</p>
      <CommandHint command={`noust team members ${team.slug}`} label={t("teams.fromTerminal")} />
      <AddMemberDialog open={adding} onOpenChange={setAdding} team={team} />
      {mayInvite ? (
        <InviteDialog
          open={inviting}
          team={team}
          accounts={[]}
          onClose={() => {
            setInviting(false);
            void refreshOrganization(queryClient);
          }}
        />
      ) : null}
      {removing !== null ? (
        <ConfirmDialog
          open
          onOpenChange={(next) => {
            if (!next) setRemoving(null);
          }}
          friction="simple"
          server={node}
          title={removing.username === me ? t("teams.members.leaveTitle", { team: team.name }) : t("teams.members.removeTitle", { name: removing.username, team: team.name })}
          description={removing.username === me ? t("teams.members.leaveDescription", { team: team.name }) : t("teams.members.removeDescription", { name: removing.username, team: team.name })}
          actionLabel={removing.username === me ? t("teams.members.leaveAction") : t("teams.members.removeAction")}
          onConfirm={async () => {
            await removeMember(team.slug, removing.username);
            await refreshOrganization(queryClient);
            announce(removing.username === me ? t("teams.members.left", { team: team.name }) : t("teams.members.removed", { name: removing.username }));
          }}
        />
      ) : null}
    </div>
  );
}
