/**
 * SSH as it runs: who may enter (the keys that open root and every account that can become root) and how they enter
 * (whether a password opens a session, whether root may sign in), with sshd's effective settings (`sshd -T -C`, not
 * the files, which Include and Match make unreliable to read) one press away and the rest of the fixes that would
 * tighten it. Who is connected, and who was, is the page of access next to this one.
 *
 * Every change here goes through the confirm-or-revert protocol: it undoes itself unless it is confirmed from a new SSH
 * login (see PendingChanges). A switch that removes a way in asks for the host's name to be typed, and one that has
 * no way back from here (passwords once off) says where it is turned on again.
 */

import { Wrench } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { Can, GuardedButton, ReadOnlyLine, useCan } from "../../../components/page/permissions";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Disclosure } from "../../../components/ui/Disclosure";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { ICONS } from "../../../components/ui/icons";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Select } from "../../../components/ui/Select";
import { Setting } from "../../../components/ui/Setting";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { Textarea } from "../../../components/ui/Textarea";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { ServerErrorBlock, useServerConfirm, ServerQueryState } from "../errors";
import { typedHost } from "../friction";
import { HeaderActions } from "../headerSlot";
import { identityQuery, sshKeysQuery, sshQuery } from "../queries";
import type { AccountKeys, FixPlan, SshKey, SshStatus } from "../queries";
import { useServerJob } from "../serverJob";
import { DirectiveChanges, FixBlocked } from "./FixDialogs";
import { ServerKeyCard } from "./ServerKeyCard";

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

/** The two fixes the switches of "How people sign in" apply: the others stay in the list of changes below them. */
const SWITCHED: ReadonlySet<string> = new Set(["disable-passwords", "root-no"]);

type Applying = { fix: string; plan: FixPlan } | null;

/** The question before an sshd fix: what changes, what proves another way in works, and how long it waits to be confirmed. */
function ApplyFix({ applying, hostname, minutes, onClose }: { applying: NonNullable<Applying>; hostname: string; minutes: number; onClose: () => void }) {
  const t = useT();
  const confirmation = useServerConfirm();
  const jobs = useServerJob();
  return (
    <ConfirmDialog
      {...confirmation}
      {...typedHost(hostname, TYPED_FIXES.has(applying.fix))}
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      destructive={false}
      size="md"
      title={fixLabel(t, applying.fix, applying.plan)}
      description={t("server.ssh.applyDescription", { count: minutes })}
      actionLabel={t("server.ssh.applyAction")}
      onConfirm={async () => {
        // design-exception: write-without-permission opened from a switch or a button drawn only for server.host_access
        const accepted = await request("post", "/api/server/security/ssh/fixes/{fix}", { params: { fix: applying.fix } });
        jobs.track(accepted.job_id, "security");
      }}
    >
      <DirectiveChanges plan={applying.plan} />
      {/* The evidence that another way in works, as the guard found it. */}
      {applying.plan.proof ? <p className="text-13 text-fg-muted">{applying.plan.proof}</p> : null}
    </ConfirmDialog>
  );
}

/**
 * How people sign in, as two switches: with a password, and as root. Each is what it says while it is on; pressing
 * one that is on asks the question of its fix (the host's name typed, what changes, how long it waits to be confirmed).
 * One that is off is read: a change that loosens sshd is made on the server, and the row says so.
 */
function HowPeopleSignIn({ ssh, hostname }: { ssh: SshStatus; hostname: string }) {
  const t = useT();
  const [applying, setApplying] = useState<Applying>(null);
  const minutes = Math.max(1, Math.round(ssh.confirm_window / 60));
  const root = effectiveValue(ssh.effective, "permitrootlogin");
  const passwordsOn = ssh.passwords_accepted !== false;
  const rootOn = root !== "no";
  const passwords = ssh.fixes["disable-passwords"];
  const rootFix = ssh.fixes["root-no"];
  const others = Object.entries(ssh.fixes).filter(([fix, plan]) => plan.needed && !SWITCHED.has(fix));
  const items: KeyValueItem[] = [
    { label: t("server.ssh.settings.ports"), value: ssh.ports.length > 0 ? ssh.ports.join(", ") : null },
    ...SETTINGS.map((setting) => ({ label: settingLabel(t, setting.label), value: effectiveValue(ssh.effective, setting.directive) })),
    { label: t("server.ssh.settings.dropin"), value: ssh.dropin ?? null },
  ];
  const offReason = t("server.ssh.turnedOffHere");
  const canChange = useCan("server.host_access");
  const needsPermission = t.rich("server.ssh.needsPermission", { permission: <Mono key="permission">server.host_access</Mono> });
  return (
    <SurfaceSection
      title={t("server.ssh.howTitle")}
      icon={OBJECT_ICONS.signIn}
      description={t.rich("server.ssh.howDescription", {
        port: <Mono key="port">{ssh.ports.length > 0 ? ssh.ports.join(", ") : "22"}</Mono>,
        link: (
          <TextLink key="blocks" to="/security/blocks" size="ui">
            {t("server.ssh.blocksLink")}
          </TextLink>
        ),
      })}
    >
      <div className="flex min-w-0 flex-col">
        {/* What a role may not change is still read: the switch stands where it is, off limits, with the reason said. */}
        <Setting
          label={t("server.ssh.allowPasswords")}
          help={t("server.ssh.allowPasswordsHelp")}
          checked={passwordsOn}
          onCheckedChange={(checked) => {
            if (!checked && passwords !== undefined) setApplying({ fix: "disable-passwords", plan: passwords });
          }}
          {...(!canChange
            ? { disabled: true as const, reason: needsPermission }
            : !passwordsOn
              ? { disabled: true as const, reason: offReason }
              : passwords !== undefined && !passwords.allowed
                ? { disabled: true as const, reason: t("server.ssh.blockedHere") }
                : {})}
        />
        <Setting
          label={t("server.ssh.allowRoot")}
          help={root === "prohibit-password" || root === "without-password" ? t("server.ssh.allowRootKeysOnly") : t("server.ssh.allowRootHelp")}
          checked={rootOn}
          onCheckedChange={(checked) => {
            if (!checked && rootFix !== undefined) setApplying({ fix: "root-no", plan: rootFix });
          }}
          {...(!canChange
            ? { disabled: true as const, reason: needsPermission }
            : !rootOn
              ? { disabled: true as const, reason: offReason }
              : rootFix !== undefined && !rootFix.allowed
                ? { disabled: true as const, reason: t("server.ssh.blockedHere") }
                : {})}
        />
        {passwords !== undefined && !passwords.allowed && passwordsOn ? (
          <div className="px-5 py-3">
            <FixBlocked plan={passwords} />
          </div>
        ) : null}
        {others.length > 0 ? (
          <div className="flex flex-col gap-2 border-t border-border px-5 py-3">
            <p className="text-13 font-medium text-fg">{t("server.ssh.moreChanges")}</p>
            <ul aria-label={t("server.ssh.fixesLabel")} className="flex flex-col divide-y divide-border">
              {others.map(([fix, plan]) => (
                <li key={fix} className="flex min-w-0 flex-col gap-2 py-2.5 first:pt-0 last:pb-0">
                  <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                    <span className="text-13 text-fg">{fixLabel(t, fix, plan)}</span>
                    {plan.allowed ? (
                      <Can permission="server.host_access">
                        <Button icon={<Wrench aria-hidden="true" />} size="sm" onClick={() => setApplying({ fix, plan })}>
                          {t("server.ssh.apply")}
                        </Button>
                      </Can>
                    ) : null}
                  </div>
                  <DirectiveChanges plan={plan} />
                  {!plan.allowed ? <FixBlocked plan={plan} /> : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <div className="border-t border-border px-5 py-3">
          <Disclosure label={t("server.ssh.settingsTitle")}>
            {ssh.error ? (
              <ServerErrorBlock compact error={{ detail: ssh.error, output: ssh.error_output ?? null }} title={t("server.ssh.readFailed")} />
            ) : (
              <KeyValueList items={items} />
            )}
            {!ssh.include_present ? <InlineMessage tone="warning" className="mt-3">{t("server.ssh.noInclude")}</InlineMessage> : null}
            {ssh.dropin_error ? <ServerErrorBlock compact error={{ detail: ssh.dropin_error }} title={t("server.ssh.dropinFailed")} className="mt-3" /> : null}
          </Disclosure>
        </div>
      </div>
      {applying !== null ? <ApplyFix applying={applying} hostname={hostname} minutes={minutes} onClose={() => setApplying(null)} /> : null}
    </SurfaceSection>
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

/** Who may enter: every key that opens root or an account that can become root, who it enters as and when it was last seen. */
function Keys({ hostname }: { hostname: string }) {
  const t = useT();
  const confirmation = useServerConfirm();
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
          <span className="flex min-w-0 flex-wrap items-center gap-1.5">
            <span className="min-w-0 truncate text-13 font-medium text-fg">{row.key.comment !== "" ? row.key.comment : row.key.type}</span>
            {kindLabel(t, row.key.kind) !== null ? <Badge>{kindLabel(t, row.key.kind)}</Badge> : null}
            {row.key.weak !== "" ? <StatusPill state="warning" label={t("server.ssh.keys.weak")} appearance="inline" size="sm" /> : null}
          </span>
          <Mono tone="muted" truncate>
            {row.key.fingerprint}
          </Mono>
        </span>
      ),
    },
    { id: "user", header: t("server.ssh.keys.account"), hideBelow: "sm", cell: (row) => <Mono>{row.user}</Mono>, sortValue: (row) => row.user },
    { id: "used", header: t("server.ssh.keys.lastUsed"), hideBelow: "md", cell: (row) => lastUsed(t, row.key) },
  ];
  return (
    <SurfaceSection
      title={t("server.ssh.keys.title")}
      icon={OBJECT_ICONS.key}
      description={t("server.ssh.keys.description")}
      flush
    >
      <HeaderActions>
        <GuardedButton permission="server.host_access" variant="primary" icon={<ICONS.add aria-hidden="true" />} onClick={() => setAdding(true)}>
          {t("server.ssh.keys.add")}
        </GuardedButton>
      </HeaderActions>
      {keys.isError ? (
        <div className="px-5 py-4">
          <ServerErrorBlock compact error={keys.error} title={t("server.ssh.keys.loadFailed")} onRetry={() => void keys.refetch()} />
        </div>
      ) : (
        <DataTable
          embedded
          columns={columns}
          rows={rows}
          getRowId={(row) => `${row.user}:${row.key.fingerprint}`}
          caption={t("server.ssh.keys.caption")}
          loading={keys.isPending}
          skeletonRows={2}
          empty={<EmptyState variant="inline" title={t("server.ssh.keys.none")} />}
          rowActions={(row) => (
            <Can permission="server.host_access">
              <Button size="sm" variant="ghost" aria-label={t("common.words.removeNamed", { name: row.key.comment !== "" ? row.key.comment : row.key.fingerprint })} onClick={() => setRemoving(row)}>
                {t("common.words.remove")}
              </Button>
            </Can>
          )}
        />
      )}
      {problems.length > 0 ? (
        <div className="border-t border-border px-5 py-3">
          <Notice tone="warning" title={t("server.ssh.keys.problemsTitle")}>
            <SystemOutput label={t("server.ssh.keys.problemsLabel")}>{problems.join("\n")}</SystemOutput>
          </Notice>
        </div>
      ) : null}
      {adding ? (
        <ConfirmDialog
          {...confirmation}
          open
          onOpenChange={(open) => {
            if (!open) setAdding(false);
          }}
          friction="simple"
          destructive={false}
          size="md"
          title={t("server.ssh.keys.addTitle")}
          description={t("server.ssh.keys.addDescription")}
          actionLabel={t("server.ssh.keys.addAction")}
          onConfirm={async () => {
            // design-exception: write-without-permission opened from the page's primary action, a GuardedButton for server.host_access
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
        </ConfirmDialog>
      ) : null}
      {removing !== null ? (
        <ConfirmDialog
          {...confirmation}
          {...typedHost(hostname)}
          open
          onOpenChange={(open) => {
            if (!open) setRemoving(null);
          }}
          size="md"
          title={t("server.ssh.keys.removeTitle")}
          description={t("server.ssh.keys.removeDescription", { user: removing.user })}
          actionLabel={t("server.ssh.keys.removeAction")}
          onConfirm={async () => {
            // design-exception: write-without-permission opened from the row's button, drawn only for server.host_access
            const accepted = await request("post", "/api/server/security/ssh/keys/remove", { body: { user: removing.user, fingerprint: removing.key.fingerprint, force: false } });
            jobs.track(accepted.job_id, "security");
          }}
        >
          <p className="text-13">
            <Mono>{removing.key.fingerprint}</Mono>
          </p>
          {removing.key.in_use ? <InlineMessage tone="warning">{t("server.ssh.keys.inUse")}</InlineMessage> : null}
        </ConfirmDialog>
      ) : null}
    </SurfaceSection>
  );
}

/** The SSH section: who may enter, how, and the server's own key for code hosts. */
export function SshView() {
  const t = useT();
  const ssh = useQuery(sshQuery());
  const identity = useQuery(identityQuery());
  const hostname = identity.data?.hostname.hostname ?? "";
  return (
    <ServerQueryState
      query={ssh}
      label={t("server.ssh.label")}
      skeleton={
        <div className="flex flex-col gap-6">
          <SkeletonCard lines={4} />
          <SkeletonCard lines={4} />
        </div>
      }
    >
      {(data) => (
        <div className="flex min-w-0 flex-col gap-6">
          <ReadOnlyLine permission="server.host_access" />
          <Keys hostname={hostname} />
          <HowPeopleSignIn ssh={data} hostname={hostname} />
          <ServerKeyCard />
        </div>
      )}
    </ServerQueryState>
  );
}
