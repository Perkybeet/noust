import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Eye, KeyRound, Trash2, UserCog } from "lucide-react";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { accessQuery, databaseKeys, databaseOverviewQuery } from "../../../api/queries/databases";
import type { AccessEntry } from "../../../api/queries/databases";
import { jobKeys } from "../../../api/queries/jobs";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import { Dialog } from "../../../components/ui/Dialog";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Input } from "../../../components/ui/Input";
import { Menu, MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { useDatabaseJob } from "../jobs";
import { TEXT_LINK } from "../ui";
import { PROFILES, isProfile, profileHelp, profileLabel } from "./profiles";
import type { Profile } from "./profiles";
import { SecretShown } from "./SecretShown";
import type { SecretShownProps } from "./SecretShown";

const USERNAME = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;

function ProfileSelect({ value, onChange }: { value: Profile; onChange: (profile: Profile) => void }) {
  const t = useT();
  return (
    <Select<Profile>
      value={value}
      onValueChange={onChange}
      options={PROFILES.map((profile) => ({ value: profile, label: t(profileLabel(profile)), hint: t(profileHelp(profile)) }))}
    />
  );
}

function NewUserDialog({
  engine,
  name,
  hosts,
  open,
  onOpenChange,
  onCreated,
}: {
  engine: string;
  name: string;
  hosts: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (username: string, password: string) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const [username, setUsername] = useState("");
  const [profile, setProfile] = useState<Profile>("read_write");
  const [host, setHost] = useState("localhost");
  const [errors, setErrors] = useState<Partial<Record<"username" | "host", string>>>({});
  const create = useMutation({
    mutationFn: () => request("post", "/api/databases/users", { body: { engine, username: username.trim(), password: null, database: name, host: host.trim() || "localhost", profile } }),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.access(engine, name) });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.overview(engine, name) });
      onOpenChange(false);
      setUsername("");
      onCreated(created.username, created.password);
    },
    onError: (error) => {
      if (isApiError(error) && error.fields) setErrors({ ...(error.fields["username"] ? { username: error.fields["username"] } : {}), ...(error.fields["host"] ? { host: error.fields["host"] } : {}) });
    },
  });
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: typeof errors = {};
    if (!USERNAME.test(username.trim())) found.username = t("databases.users.usernameInvalid");
    setErrors(found);
    if (Object.keys(found).length === 0) create.mutate();
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title={t("databases.users.newTitle")}
      description={t("databases.users.newDescription", { name })}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("databases.common.cancel")}</Button>
          <Button type="submit" form="new-user-form" variant="primary" loading={create.isPending}>
            {t("databases.users.create")}
          </Button>
        </>
      }
    >
      <form id="new-user-form" noValidate onSubmit={submit} className="flex flex-col gap-5">
        <Field label={t("databases.fields.username")} error={errors.username}>
          <Input mono value={username} onValueChange={(value: string) => setUsername(value)} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Field label={t("databases.users.profile")} nativeLabel={false} description={t(profileHelp(profile))}>
          <ProfileSelect value={profile} onChange={setProfile} />
        </Field>
        {hosts ? (
          <Field label={t("databases.fields.host")} description={t("databases.users.hostHelp")} error={errors.host}>
            <Input mono value={host} onValueChange={(value: string) => setHost(value)} autoComplete="off" spellCheck={false} />
          </Field>
        ) : null}
        <p className="text-13 text-fg-muted">{t("databases.users.passwordGenerated")}</p>
        {create.isError && !(isApiError(create.error) && create.error.fields) ? <ErrorBlock live compact error={create.error} title={t("databases.users.createFailed")} /> : null}
      </form>
    </Dialog>
  );
}

function ProfileDialog({ engine, name, entry, onClose }: { engine: string; name: string; entry: AccessEntry | null; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [profile, setProfile] = useState<Profile>(entry !== null && isProfile(entry.profile) ? entry.profile : "read_write");
  const save = useMutation({
    mutationFn: () =>
      request("put", "/api/databases/databases/{engine}/{name}/access/{username}", {
        params: { engine, name, username: entry?.username ?? "" },
        body: { profile, host: entry?.host ?? "localhost" },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.access(engine, name) });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.overview(engine, name) });
      onClose();
    },
  });
  return (
    <Dialog
      open={entry !== null}
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="sm"
      title={t("databases.users.profileTitle", { user: entry?.username ?? "" })}
      description={t("databases.users.profileDescription", { name })}
      footer={
        <>
          <Button onClick={onClose}>{t("databases.common.cancel")}</Button>
          <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>
            {t("databases.users.profileSave")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label={t("databases.users.profile")} nativeLabel={false} description={t(profileHelp(profile))}>
          <ProfileSelect value={profile} onChange={setProfile} />
        </Field>
        {save.isError ? <ErrorBlock live compact error={save.error} title={t("databases.users.profileFailed")} /> : null}
      </div>
    </Dialog>
  );
}

function rotateDescription(t: T, entry: AccessEntry): string {
  if ((entry.apps ?? []).length === 0) return t("databases.users.rotateDescription", { user: entry.username });
  return t("databases.users.rotateDescriptionApps", { user: entry.username, apps: (entry.apps ?? []).join(", ") });
}

/**
 * Who can reach a database, each account with its profile (owner, read and write, read only),
 * the applications that sign in as it and when Noust last set its password; the engine's own
 * accounts and the read-only console's are hidden unless asked for, and never changed. A new
 * account's password is shown once; a rotation restarts the applications that use it behind
 * their startup check.
 */
export function UsersTab({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const job = useDatabaseJob();
  const access = useQuery(accessQuery(engine, name));
  const overview = useQuery(databaseOverviewQuery(engine, name));
  const [showSystem, setShowSystem] = useState(false);
  const [creating, setCreating] = useState(false);
  const [changing, setChanging] = useState<AccessEntry | null>(null);
  const [rotating, setRotating] = useState<AccessEntry | null>(null);
  const [deleting, setDeleting] = useState<AccessEntry | null>(null);
  const [secret, setSecret] = useState<SecretShownProps["secret"]>(null);

  const entries = access.data?.access ?? [];
  const shown = showSystem ? entries : entries.filter((entry) => !entry.internal);
  const hidden = entries.length - entries.filter((entry) => !entry.internal).length;
  const hosts = engine === "mysql";
  const profiles = (overview.data?.capabilities ?? ["profiles"]).includes("profiles");

  const reveal = useMutation({
    mutationFn: (entry: AccessEntry) => request("post", "/api/databases/users/{engine}/{username}/password/reveal", { params: { engine, username: entry.username } }),
    onSuccess: (result) =>
      setSecret({
        title: t("databases.users.revealTitle", { user: result.username }),
        description: t("databases.users.revealDescription"),
        fields: [
          { label: t("databases.fields.username"), value: result.username },
          { label: t("databases.fields.password"), value: result.password },
        ],
      }),
    onError: (error) => reportActionError(t("databases.users.revealFailed"), error),
  });

  const Add = ICONS.add;
  return (
    <div className="flex min-w-0 flex-col gap-8">
      <Section
        title={t("databases.users.title")}
        description={t("databases.users.description")}
        actions={
          <>
            {hidden > 0 ? <Switch checked={showSystem} onCheckedChange={setShowSystem} label={t("databases.users.showSystem", { count: hidden })} /> : null}
            <Button size="sm" icon={<Add aria-hidden="true" />} onClick={() => setCreating(true)}>
              {t("databases.users.new")}
            </Button>
          </>
        }
      >
        {access.isError && access.data === undefined ? (
          <ErrorBlock error={access.error} title={t("databases.users.loadFailed")} onRetry={() => void access.refetch()} retrying={access.isRefetching} />
        ) : (
          <DataTable<AccessEntry>
            caption={t("databases.users.caption", { name })}
            rows={shown}
            loading={access.isPending}
            skeletonRows={3}
            getRowId={(entry) => `${entry.username}@${entry.host}`}
            mobile="cards"
            empty={<EmptyState variant="inline" title={t("databases.users.empty")} />}
            columns={[
              {
                id: "username",
                header: t("databases.users.account"),
                card: "title",
                sortValue: (entry) => entry.username,
                cell: (entry) => (
                  <span className="flex items-center gap-2">
                    <Mono>{entry.username}</Mono>
                    {entry.internal ? <Badge>{t("databases.users.system")}</Badge> : null}
                  </span>
                ),
              },
              { id: "profile", header: t("databases.users.profile"), width: "w-40", sortValue: (entry) => entry.profile, cell: (entry) => t(profileLabel(entry.profile)) },
              ...(hosts ? [{ id: "host", header: t("databases.fields.host"), width: "w-32", cell: (entry: AccessEntry) => <Mono tone="muted">{entry.host}</Mono> }] : []),
              {
                id: "apps",
                header: t("databases.users.usedBy"),
                hideBelow: "md" as const,
                cell: (entry: AccessEntry) =>
                  (entry.apps ?? []).length === 0 ? (
                    <EmptyCell reason={t("databases.users.noApp")} />
                  ) : (
                    <span className="flex flex-wrap gap-x-2">
                      {(entry.apps ?? []).map((domain) => (
                        <Link key={domain} to="/apps/$domain/database" params={{ domain }} translate="no" className={TEXT_LINK}>
                          {domain}
                        </Link>
                      ))}
                    </span>
                  ),
              },
              {
                id: "password",
                header: t("databases.users.password"),
                width: "w-44",
                hideBelow: "sm" as const,
                cell: (entry: AccessEntry) =>
                  entry.password_changed_at ? (
                    <RelativeTime value={entry.password_changed_at} />
                  ) : entry.managed ? (
                    <span className="text-fg-muted">{t("databases.users.kept")}</span>
                  ) : (
                    <span className="text-fg-faint">{t("databases.users.notKept")}</span>
                  ),
              },
            ]}
            rowActions={(entry) =>
              entry.internal ? null : (
                <Menu align="end" trigger={<IconButton size="sm" label={t("databases.users.actionsFor", { user: entry.username })} icon={<ICONS.more />} tooltip={false} />}>
                  {profiles ? (
                    <MenuItem icon={<UserCog />} onClick={() => setChanging(entry)}>
                      {t("databases.users.changeAccess")}
                    </MenuItem>
                  ) : null}
                  <MenuItem icon={<KeyRound />} disabled={job.busy} onClick={() => setRotating(entry)}>
                    {t("databases.users.rotate")}
                  </MenuItem>
                  {entry.managed ? (
                    <MenuItem icon={<Eye />} onClick={() => reveal.mutate(entry)}>
                      {t("databases.users.reveal")}
                    </MenuItem>
                  ) : null}
                  <MenuSeparator />
                  <MenuItem icon={<Trash2 />} destructive disabled={(entry.apps ?? []).length > 0} onClick={() => setDeleting(entry)}>
                    {(entry.apps ?? []).length > 0 ? t("databases.users.deleteLinked") : t("databases.users.delete")}
                  </MenuItem>
                </Menu>
              )
            }
          />
        )}
      </Section>

      <CommandHint command={`noust db access ${name} -e ${engine}`} label={t("databases.common.fromTerminal")} />

      <NewUserDialog
        engine={engine}
        name={name}
        hosts={hosts}
        open={creating}
        onOpenChange={setCreating}
        onCreated={(username, password) =>
          setSecret({
            title: t("databases.users.createdTitle", { user: username }),
            description: t("databases.users.createdDescription"),
            fields: [
              { label: t("databases.fields.username"), value: username },
              { label: t("databases.fields.password"), value: password },
            ],
          })
        }
      />
      <ProfileDialog key={changing?.username ?? "none"} engine={engine} name={name} entry={changing} onClose={() => setChanging(null)} />
      {rotating !== null ? (
        <ConfirmDialog
          friction="simple"
          destructive={false}
          open
          onOpenChange={(open) => (open ? undefined : setRotating(null))}
          title={t("databases.users.rotateTitle", { user: rotating.username })}
          description={rotateDescription(t, rotating)}
          actionLabel={t("databases.users.rotateAction")}
          server={node}
          onConfirm={async () => {
            const accepted = await request("post", "/api/databases/users/{engine}/{username}/password", {
              params: { engine, username: rotating.username },
              body: { propagate: true, host: rotating.host },
            });
            job.track(accepted, "rotate", rotating.username);
            void queryClient.invalidateQueries({ queryKey: jobKeys.active });
          }}
        />
      ) : null}
      {deleting !== null ? (
        <ConfirmDialog
          friction="simple"
          open
          onOpenChange={(open) => (open ? undefined : setDeleting(null))}
          title={t("databases.users.deleteTitle", { user: deleting.username })}
          description={t("databases.users.deleteDescription", { user: deleting.username })}
          actionLabel={t("databases.users.deleteAction")}
          server={node}
          onConfirm={async () => {
            await request("delete", "/api/databases/users/{engine}/{username}", { params: { engine, username: deleting.username }, query: { host: deleting.host } });
            await queryClient.invalidateQueries({ queryKey: databaseKeys.access(engine, name) });
          }}
        />
      ) : null}
      <SecretShown secret={secret} onClose={() => setSecret(null)} />
    </div>
  );
}
