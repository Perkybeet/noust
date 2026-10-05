/**
 * SSH as it runs: sshd's effective settings (`sshd -T -C`, not the files, which Include and
 * Match make unreliable to read), the fixes that would tighten them with their guard, the keys
 * that open root and every account that can become root, and who is connected now.
 *
 * Every change here goes through the confirm-or-revert protocol: it undoes itself unless it is
 * confirmed from a new SSH login (see PendingChanges).
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Menu, MenuItem } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Skeleton } from "../../../components/ui/Skeleton";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatDate, parseTimestamp } from "../../../lib/format";
import { ActionDialog } from "../ActionDialog";
import { ServerErrorBlock } from "../errors";
import { identityQuery, sshKeysQuery, sshQuery } from "../queries";
import type { AccountKeys, FixPlan, SshKey, SshStatus } from "../queries";
import { useServerJob } from "../serverJob";
import { DirectiveChanges, FixBlocked } from "./FixDialogs";

/** The sshd settings worth reading, in the order a person asks about them. */
const SETTINGS: readonly { directive: string; label: "passwords" | "root" | "keys" | "keyboard" | "empty" | "tries" | "log" | "x11" }[] = [
  { directive: "passwordauthentication", label: "passwords" },
  { directive: "permitrootlogin", label: "root" },
  { directive: "pubkeyauthentication", label: "keys" },
  { directive: "kbdinteractiveauthentication", label: "keyboard" },
  { directive: "permitemptypasswords", label: "empty" },
  { directive: "maxauthtries", label: "tries" },
  { directive: "loglevel", label: "log" },
  { directive: "x11forwarding", label: "x11" },
];

function settingLabel(t: T, label: (typeof SETTINGS)[number]["label"]): string {
  switch (label) {
    case "passwords":
      return t("server.ssh.settings.passwords");
    case "root":
      return t("server.ssh.settings.root");
    case "keys":
      return t("server.ssh.settings.keys");
    case "keyboard":
      return t("server.ssh.settings.keyboard");
    case "empty":
      return t("server.ssh.settings.empty");
    case "tries":
      return t("server.ssh.settings.tries");
    case "log":
      return t("server.ssh.settings.log");
    case "x11":
      return t("server.ssh.settings.x11");
  }
}

/** sshd's keywords are case-insensitive, and the API keeps the case it read them in. */
export function effectiveValue(effective: Readonly<Record<string, string>>, directive: string): string | null {
  const found = Object.entries(effective).find(([key]) => key.toLowerCase() === directive);
  return found?.[1] ?? null;
}

function Settings({ ssh }: { ssh: SshStatus }) {
  const t = useT();
  const items: KeyValueItem[] = [
    { label: t("server.ssh.settings.ports"), value: ssh.ports.length > 0 ? ssh.ports.join(", ") : null },
    ...SETTINGS.map((setting) => ({ label: settingLabel(t, setting.label), value: effectiveValue(ssh.effective, setting.directive) })),
    { label: t("server.ssh.settings.dropin"), value: ssh.dropin ?? null },
  ];
  return (
    <Card level={2} title={t("server.ssh.settingsTitle")} description={t("server.ssh.settingsDescription")} padding="sm" footer={<CommandHint command="noust server security ssh status" label={t("server.fromTerminal")} />}>
      {ssh.error ? (
        <ServerErrorBlock compact error={{ detail: ssh.error, output: ssh.error_output ?? null }} title={t("server.ssh.readFailed")} />
      ) : (
        <KeyValueList items={items} />
      )}
      {!ssh.include_present ? <Notice tone="warning" className="mt-3">{t("server.ssh.noInclude")}</Notice> : null}
      {ssh.dropin_error ? <ServerErrorBlock compact error={{ detail: ssh.dropin_error }} title={t("server.ssh.dropinFailed")} className="mt-3" /> : null}
    </Card>
  );
}

function fixLabel(t: T, fix: string, plan: FixPlan): string {
  switch (fix) {
    case "disable-passwords":
      return t("server.ssh.fixes.disablePasswords");
    case "root-prohibit-password":
      return t("server.ssh.fixes.rootProhibitPassword");
    case "root-no":
      return t("server.ssh.fixes.rootNo");
    case "no-empty-passwords":
      return t("server.ssh.fixes.noEmptyPasswords");
    case "sensible-defaults":
      return t("server.ssh.fixes.sensibleDefaults");
    case "verbose-logging":
      return t("server.ssh.fixes.verboseLogging");
    default:
      return plan.title;
  }
}

/** Turning off passwords or root logins can lock people out: the host name is typed. */
const TYPED_FIXES: ReadonlySet<string> = new Set(["disable-passwords", "root-no"]);

function Fixes({ ssh, hostname }: { ssh: SshStatus; hostname: string }) {
  const t = useT();
  const jobs = useServerJob();
  const [applying, setApplying] = useState<{ fix: string; plan: FixPlan } | null>(null);
  const needed = Object.entries(ssh.fixes).filter(([, plan]) => plan.needed);
  // The window comes in seconds; people think of it in minutes.
  const minutes = Math.max(1, Math.round(ssh.confirm_window / 60));
  if (needed.length === 0) {
    return (
      <Card level={2} title={t("server.ssh.fixesTitle")} padding="sm">
        <EmptyState variant="inline" title={t("server.ssh.fixesNone")} />
      </Card>
    );
  }
  return (
    <Card level={2} title={t("server.ssh.fixesTitle")} description={t("server.ssh.fixesDescription", { count: minutes })} padding="none">
      <ul aria-label={t("server.ssh.fixesLabel")} className="flex flex-col divide-y divide-border">
        {needed.map(([fix, plan]) => (
          <li key={fix} className="flex min-w-0 flex-col gap-2 px-5 py-3">
            <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
              <span className="text-13 font-medium text-fg">{fixLabel(t, fix, plan)}</span>
              {plan.allowed ? (
                <Button size="sm" onClick={() => setApplying({ fix, plan })}>
                  {t("server.ssh.apply")}
                </Button>
              ) : null}
            </div>
            <DirectiveChanges plan={plan} />
            {!plan.allowed ? <FixBlocked plan={plan} /> : null}
          </li>
        ))}
      </ul>
      {applying !== null ? (
        <ActionDialog
          title={fixLabel(t, applying.fix, applying.plan)}
          description={t("server.ssh.applyDescription", { count: minutes })}
          actionLabel={t("server.ssh.applyAction")}
          confirmText={TYPED_FIXES.has(applying.fix) ? hostname : undefined}
          onClose={() => setApplying(null)}
          onConfirm={async () => {
            const accepted = await request("post", "/api/server/security/ssh/fixes/{fix}", { params: { fix: applying.fix } });
            jobs.track(accepted.job_id, "security");
          }}
        >
          <DirectiveChanges plan={applying.plan} />
          {/* The evidence that another way in works, as the guard found it. */}
          {applying.plan.proof ? <p className="text-13 text-fg-muted">{applying.plan.proof}</p> : null}
        </ActionDialog>
      ) : null}
    </Card>
  );
}

function kindLabel(t: T, kind: string): string | null {
  switch (kind) {
    case "central":
      return t("server.ssh.keys.kindCentral");
    case "restricted":
      return t("server.ssh.keys.kindRestricted");
    case "cloud_disabled":
      return t("server.ssh.keys.kindCloudDisabled");
    default:
      return null;
  }
}

interface KeyRow {
  user: string;
  path: string;
  key: SshKey;
}

function lastUsed(t: T, key: SshKey) {
  if (key.last_used == null) return <EmptyCell reason={t("server.ssh.keys.neverSeen")} />;
  return (
    <span className="flex min-w-0 flex-col">
      <RelativeTime value={key.last_used} />
      {key.last_used_from ? <Mono tone="muted">{key.last_used_from}</Mono> : null}
    </span>
  );
}

function Keys({ hostname }: { hostname: string }) {
  const t = useT();
  const jobs = useServerJob();
  const keys = useQuery(sshKeysQuery());
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<KeyRow | null>(null);
  const [user, setUser] = useState("root");
  const [publicKey, setPublicKey] = useState("");
  const accounts = keys.data ?? [];
  const rows: KeyRow[] = accounts.flatMap((account) => account.files.flatMap((file) => file.keys.map((key) => ({ user: account.user, path: file.path, key }))));
  const problems = accounts.flatMap((account: AccountKeys) => [
    ...(account.login_allowed ? [] : [`${account.user}: ${account.login_refusal}`]),
    ...account.files.flatMap((file) => [...file.problems.map((problem) => `${file.path}: ${problem}`), ...(file.error ? [`${file.path}: ${file.error}`] : [])]),
  ]);
  const columns: Column<KeyRow>[] = [
    {
      id: "fingerprint",
      header: t("server.ssh.keys.fingerprint"),
      card: "title",
      cell: (row) => (
        <span className="flex min-w-0 flex-col">
          <Mono truncate>{row.key.fingerprint}</Mono>
          {row.key.comment !== "" ? <Mono tone="muted" truncate>{row.key.comment}</Mono> : null}
        </span>
      ),
    },
    { id: "user", header: t("server.ssh.keys.account"), cell: (row) => <Mono>{row.user}</Mono>, sortValue: (row) => row.user },
    {
      id: "type",
      header: t("server.ssh.keys.type"),
      hideBelow: "md",
      cell: (row) => (
        <span className="flex flex-wrap items-center gap-1.5">
          <Mono>{row.key.bits ? `${row.key.type} ${String(row.key.bits)}` : row.key.type}</Mono>
          {kindLabel(t, row.key.kind) !== null ? <Badge>{kindLabel(t, row.key.kind)}</Badge> : null}
          {row.key.weak !== "" ? <Badge tone="warn">{t("server.ssh.keys.weak")}</Badge> : null}
        </span>
      ),
    },
    { id: "used", header: t("server.ssh.keys.lastUsed"), hideBelow: "sm", cell: (row) => lastUsed(t, row.key) },
  ];
  return (
    <Card level={2}
      title={t("server.ssh.keys.title")}
      description={t("server.ssh.keys.description")}
      padding="none"
      actions={
        <Button size="sm" icon={<ICONS.add aria-hidden="true" />} onClick={() => setAdding(true)}>
          {t("server.ssh.keys.add")}
        </Button>
      }
    >
      {keys.isError ? (
        <div className="px-5 pb-4">
          <ServerErrorBlock compact error={keys.error} title={t("server.ssh.keys.loadFailed")} onRetry={() => void keys.refetch()} />
        </div>
      ) : (
        <DataTable
          columns={columns}
          rows={rows}
          getRowId={(row) => `${row.user}:${row.key.fingerprint}`}
          caption={t("server.ssh.keys.caption")}
          loading={keys.isPending}
          skeletonRows={2}
          mobile="cards"
          empty={<EmptyState variant="inline" title={t("server.ssh.keys.none")} />}
          rowActions={(row) => (
            <Menu align="end" trigger={<IconButton label={t("server.ssh.keys.actionsFor", { fingerprint: row.key.fingerprint })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
              <MenuItem destructive icon={<ICONS.delete />} onClick={() => setRemoving(row)}>
                {t("server.ssh.keys.remove")}
              </MenuItem>
            </Menu>
          )}
        />
      )}
      {problems.length > 0 ? (
        <div className="px-5 py-3">
          <Notice tone="warning" title={t("server.ssh.keys.problemsTitle")}>
            <SystemOutput label={t("server.ssh.keys.problemsLabel")}>{problems.join("\n")}</SystemOutput>
          </Notice>
        </div>
      ) : null}
      {adding ? (
        <ActionDialog
          title={t("server.ssh.keys.addTitle")}
          description={t("server.ssh.keys.addDescription")}
          actionLabel={t("server.ssh.keys.addAction")}
          onClose={() => setAdding(false)}
          onConfirm={async () => {
            const accepted = await request("post", "/api/server/security/ssh/keys", { body: { user, public_key: publicKey.trim() } });
            jobs.track(accepted.job_id, "security");
          }}
        >
          <Field label={t("server.ssh.keys.accountLabel")} nativeLabel={false}>
            <Select value={user} onValueChange={setUser} mono options={(accounts.length > 0 ? accounts.map((account) => account.user) : ["root"]).map((name) => ({ value: name, label: name }))} />
          </Field>
          <Field label={t("server.ssh.keys.publicKeyLabel")} description={t("server.ssh.keys.publicKeyDescription")}>
            <Textarea mono rows={4} value={publicKey} onChange={(event) => setPublicKey(event.target.value)} spellCheck={false} />
          </Field>
        </ActionDialog>
      ) : null}
      {removing !== null ? (
        <ActionDialog
          title={t("server.ssh.keys.removeTitle")}
          description={t("server.ssh.keys.removeDescription", { user: removing.user })}
          actionLabel={t("server.ssh.keys.removeAction")}
          destructive
          confirmText={hostname !== "" ? hostname : undefined}
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            const accepted = await request("post", "/api/server/security/ssh/keys/remove", { body: { user: removing.user, fingerprint: removing.key.fingerprint, force: false } });
            jobs.track(accepted.job_id, "security");
          }}
        >
          <p className="text-13">
            <Mono>{removing.key.fingerprint}</Mono>
          </p>
          {removing.key.in_use ? <Notice tone="warning">{t("server.ssh.keys.inUse")}</Notice> : null}
        </ActionDialog>
      ) : null}
    </Card>
  );
}

function Sessions({ ssh }: { ssh: SshStatus }) {
  const t = useT();
  const recent = ssh.logins.recent.slice(0, 5);
  return (
    <Card level={2} title={t("server.ssh.sessionsTitle")} padding="sm">
      <div className="flex flex-col gap-3">
        {ssh.sessions.length === 0 ? (
          <p className="text-13 text-fg-muted">{t("server.ssh.noSessions")}</p>
        ) : (
          <ul aria-label={t("server.ssh.sessionsLabel")} className="flex flex-col gap-1">
            {ssh.sessions.map((session) => (
              <li key={`${session.peer}:${String(session.port)}`} className="text-13 text-fg">
                <Mono>{session.user ? `${session.user}@${session.peer}` : session.peer}</Mono>
              </li>
            ))}
          </ul>
        )}
        <p className="text-12 text-fg-muted">{t("server.ssh.loginsTitle")}</p>
        {ssh.logins.error ? (
          <p className="text-12 text-fg-muted">{ssh.logins.error}</p>
        ) : recent.length === 0 ? (
          <p className="text-13 text-fg-muted">{t("server.ssh.noLogins", { count: ssh.logins.days })}</p>
        ) : (
          <ul aria-label={t("server.ssh.loginsLabel")} className="flex flex-col divide-y divide-border">
            {recent.map((login) => {
              const at = parseTimestamp(login.at);
              return (
                <li key={`${String(login.at)}-${login.source}`} className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 py-1.5 text-13">
                  <Mono>{`${login.user}@${login.source}`}</Mono>
                  <span className="text-12 text-fg-muted">
                    {login.method} · {at ? formatDate(at, {}, t.locale) : String(login.at)}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
  );
}

/** The SSH view of the Security tab. */
export function SshView() {
  const t = useT();
  const ssh = useQuery(sshQuery());
  const identity = useQuery(identityQuery());
  const hostname = identity.data?.hostname.hostname ?? "";
  if (ssh.isError && ssh.data === undefined) {
    return <ServerErrorBlock error={ssh.error} title={t("server.ssh.loadFailed")} onRetry={() => void ssh.refetch()} retrying={ssh.isRefetching} />;
  }
  if (ssh.data === undefined) {
    return (
      <LoadingRegion label={t("server.ssh.loading")} className="grid gap-6 xl:grid-cols-2">
        <Skeleton className="h-80 w-full rounded-card" />
        <Skeleton className="h-80 w-full rounded-card" />
      </LoadingRegion>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-6">
      {ssh.data.passwords_accepted === true ? <Notice tone="warning" title={t("server.ssh.passwordsTitle")}>{t("server.ssh.passwordsDescription")}</Notice> : null}
      <div className="grid min-w-0 items-start gap-6 xl:grid-cols-2">
        <Settings ssh={ssh.data} />
        <div className="flex min-w-0 flex-col gap-6">
          <Fixes ssh={ssh.data} hostname={hostname} />
          <Sessions ssh={ssh.data} />
        </div>
      </div>
      <Keys hostname={hostname} />
    </div>
  );
}
