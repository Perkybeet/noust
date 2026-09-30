import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { request } from "../../../api/client";
import { accessQuery, connectQuery } from "../../../api/queries/databases";
import type { ConnectParams } from "../../../api/queries/databases";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section, Sections } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { CopyButton } from "../../../components/ui/CopyButton";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { ICONS } from "../../../components/ui/icons";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { useServerList } from "../../../nodes/servers";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { useDatabaseJob } from "../jobs";
import { LinkDialog } from "../LinkDialog";
import { TEXT_LINK } from "../ui";
import { SecretShown } from "../users/SecretShown";
import type { SecretShownProps } from "../users/SecretShown";

/** The placeholder the API writes where it does not know this server's address. */
const UNKNOWN_SERVER = "<server>";

const CLIENT_NAMES: Readonly<Record<string, string>> = {
  psql: "psql",
  mysql: "mysql",
  "redis-cli": "redis-cli",
  mongosh: "mongosh",
  jdbc: "JDBC / DBeaver",
};

/** A line to copy: a command or a connection string, whole, with its copy button. */
function CopyLine({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex min-w-0 items-center gap-1 rounded-control border border-border bg-bg-sunken py-1 pr-1 pl-3">
      <Mono tone="default" className="min-w-0 flex-1 overflow-x-auto py-1 whitespace-nowrap scroll-thin">
        {value}
      </Mono>
      <CopyButton value={value} label={label} />
    </div>
  );
}

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

/**
 * How to reach a database. From an application: the variable Noust wrote and its connection
 * string (the password shown in sudo mode). From the operator's computer: an SSH tunnel to the
 * engine's port on loopback, never a port opened to the network, with the connection string and
 * each client's command through it. And where the engine listens, with a port open beyond this
 * machine said as the danger it is.
 */
export function ConnectTab({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const { node } = useNode();
  const job = useDatabaseJob();
  const servers = useServerList();
  const nodeRecord = node !== null ? servers.nodes.find((item) => item.name === node) : undefined;
  const access = useQuery(accessQuery(engine, name));
  const accounts = (access.data?.access ?? []).filter((entry) => !entry.internal);

  const [account, setAccount] = useState<string | null>(null);
  // What the operator typed, else the address this central reaches the node at.
  const [typedServer, setServer] = useState<string | null>(null);
  const server = typedServer ?? nodeRecord?.ssh_host ?? "";
  const [sshUser, setSshUser] = useState("");
  const [localPort, setLocalPort] = useState("");
  const [linking, setLinking] = useState(false);
  const [secret, setSecret] = useState<SecretShownProps["secret"]>(null);

  const debounced = useDebounced({ account, server: server.trim(), sshUser: sshUser.trim(), localPort: localPort.trim() }, 400);
  const port = Number(debounced.localPort);
  const params: ConnectParams = {
    ...(debounced.account !== null ? { username: debounced.account } : {}),
    ...(debounced.server !== "" ? { server: debounced.server } : {}),
    ...(debounced.sshUser !== "" ? { ssh_user: debounced.sshUser } : {}),
    ...(Number.isInteger(port) && port >= 1 && port <= 65535 ? { local_port: port } : {}),
  };
  const connect = useQuery({ ...connectQuery(engine, name, params), placeholderData: keepPreviousData });
  const data = connect.data;

  const revealUrl = useMutation({
    mutationFn: (domain: string) => request("post", "/api/apps/{domain}/databases/{engine}/{name}/url", { params: { domain, engine, name } }),
    onSuccess: (result) =>
      setSecret({
        title: t("databases.connect.urlTitle", { domain: result.domain }),
        description: t("databases.connect.urlDescription"),
        fields: [{ label: t("databases.connect.connectionString"), value: result.url }],
      }),
    onError: (error) => reportActionError(t("databases.connect.revealFailed"), error),
  });
  const revealPassword = useMutation({
    mutationFn: (username: string) => request("post", "/api/databases/users/{engine}/{username}/password/reveal", { params: { engine, username } }),
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

  if (connect.isError && data === undefined) {
    return <ErrorBlock error={connect.error} title={t("databases.connect.loadFailed")} onRetry={() => void connect.refetch()} retrying={connect.isRefetching} />;
  }

  const tunnel = data?.tunnel;
  const unknownServer = tunnel?.server === UNKNOWN_SERVER;
  const Warning = ICONS.warning;

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <Sections>
        <Section
          title={t("databases.connect.fromApp")}
          description={t("databases.connect.fromAppDescription")}
          actions={
            <Button size="sm" onClick={() => setLinking(true)}>
              {t("databases.overview.linkApp")}
            </Button>
          }
        >
          {data === undefined ? (
            <Skeleton className="h-16 w-full rounded-card" />
          ) : data.apps.length === 0 ? (
            <EmptyState variant="inline" title={t("databases.connect.noApps")} />
          ) : (
            <Card padding="none" as="div">
              <ul className="flex flex-col divide-y divide-border">
                {data.apps.map((link) => (
                  <li key={link.domain} className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3">
                    <div className="flex min-w-0 flex-col gap-1">
                      <Link to="/apps/$domain/database" params={{ domain: link.domain }} translate="no" className={`${TEXT_LINK} w-fit text-13`}>
                        {link.domain}
                      </Link>
                      <span className="flex min-w-0 flex-wrap items-center gap-x-2 text-12 text-fg-muted">
                        {link.env_var ? <Mono tone="default">{link.env_var}</Mono> : <span>{t("databases.connect.noVariable")}</span>}
                        {link.url ? (
                          <Mono tone="muted" truncate>
                            {link.url}
                          </Mono>
                        ) : null}
                      </span>
                    </div>
                    {link.url ? (
                      <Button size="sm" variant="ghost" loading={revealUrl.isPending && revealUrl.variables === link.domain} onClick={() => revealUrl.mutate(link.domain)}>
                        {t("databases.connect.showUrl")}
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </Section>

        <Section title={t("databases.connect.fromComputer")} description={t("databases.connect.fromComputerDescription")}>
          <div className="flex min-w-0 flex-col gap-5">
            <div className="flex min-w-0 flex-wrap gap-4">
              <Field label={t("databases.connect.account")} nativeLabel={false} className="w-56">
                <Select
                  value={account ?? data?.username ?? null}
                  placeholder={t("databases.connect.accountPlaceholder")}
                  onValueChange={(value) => setAccount(value)}
                  mono
                  options={accounts.map((entry) => ({ value: entry.username, label: entry.username }))}
                />
              </Field>
              <Field label={t("databases.connect.server")} description={node !== null ? t("databases.connect.serverNode") : t("databases.connect.serverHelp")} className="w-64">
                <Input mono value={server} onValueChange={(value: string) => setServer(value)} placeholder={t("databases.connect.serverPlaceholder")} autoComplete="off" spellCheck={false} />
              </Field>
              <Field label={t("databases.connect.sshUser")} optional className="w-40">
                <Input mono value={sshUser} onValueChange={(value: string) => setSshUser(value)} placeholder={tunnel?.ssh_user ?? "root"} autoComplete="off" spellCheck={false} />
              </Field>
              <Field label={t("databases.connect.localPort")} optional className="w-32">
                <Input mono inputMode="numeric" value={localPort} onValueChange={(value: string) => setLocalPort(value)} placeholder={tunnel ? String(tunnel.local_port) : ""} autoComplete="off" />
              </Field>
            </div>
            {tunnel === undefined ? (
              <Skeleton className="h-80 w-full rounded-card" />
            ) : (
              <ol className="flex min-w-0 flex-col gap-5">
                <li className="flex min-w-0 flex-col gap-2">
                  <p className="text-13 font-medium text-fg">{t("databases.connect.step1")}</p>
                  <CopyLine value={tunnel.command} label={t("databases.connect.copyTunnel")} />
                  <p className="max-w-measure text-12 text-fg-muted">
                    {unknownServer ? t("databases.connect.step1Unknown") : t("databases.connect.step1Help", { port: String(tunnel.remote_port), local: String(tunnel.local_port) })}
                  </p>
                </li>
                <li className="flex min-w-0 flex-col gap-2">
                  <p className="text-13 font-medium text-fg">{t("databases.connect.step2")}</p>
                  <CopyLine value={tunnel.url} label={t("databases.connect.copyUrl")} />
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-12 text-fg-muted">
                    <span>{t("databases.connect.passwordMasked")}</span>
                    {data?.password_known && data.username ? (
                      <Button size="sm" variant="ghost" loading={revealPassword.isPending} onClick={() => revealPassword.mutate(data.username ?? "")}>
                        {t("databases.connect.showPassword")}
                      </Button>
                    ) : (
                      <Link to="/databases/$engine/$name/users" params={{ engine, name }} className={TEXT_LINK}>
                        {t("databases.connect.rotateToSee")}
                      </Link>
                    )}
                  </div>
                </li>
                {Object.keys(tunnel.clients ?? {}).length > 0 ? (
                  <li className="flex min-w-0 flex-col gap-2">
                    <p className="text-13 font-medium text-fg">{t("databases.connect.clients")}</p>
                    <dl className="flex min-w-0 flex-col gap-2">
                      {Object.entries(tunnel.clients ?? {}).map(([client, command]) => (
                        <div key={client} className="flex min-w-0 flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
                          <dt className="w-32 shrink-0 text-12 text-fg-muted">{CLIENT_NAMES[client] ?? client}</dt>
                          <dd className="min-w-0 flex-1">
                            <CopyLine value={command} label={t("databases.connect.copyClient", { client: CLIENT_NAMES[client] ?? client })} />
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </li>
                ) : null}
              </ol>
            )}
          </div>
        </Section>

        <Section title={t("databases.connect.exposure")} description={t("databases.connect.exposureDescription")}>
          {data === undefined ? (
            <Skeleton className="h-12 w-full rounded-card" />
          ) : (
            <div className="flex min-w-0 flex-col gap-3">
              {data.exposed.length > 0 ? (
                <Notice tone="error" title={t("databases.connect.exposedTitle", { count: data.exposed.length })}>
                  <ul className="flex flex-col gap-2">
                    {data.exposed.map((port) => (
                      <li key={`${port.address}:${String(port.port)}`} className="flex flex-col gap-0.5">
                        <Mono tone="default">{`${port.address}:${String(port.port)}${port.process ? ` (${port.process})` : ""}${port.container ? ` · ${port.container}` : ""}`}</Mono>
                        {port.advice ? <span translate="no">{port.advice}</span> : null}
                      </li>
                    ))}
                  </ul>
                </Notice>
              ) : null}
              {data.listen !== null && data.listen !== undefined ? (
                <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-13">
                  {data.listen.loopback_only ? null : <Warning aria-hidden="true" className="size-icon-md text-warn" />}
                  <span className="text-fg">{data.listen.loopback_only ? t("databases.connect.loopbackOnly") : t("databases.connect.notLoopback")}</span>
                  <span className="text-fg-muted">
                    <Mono>{data.listen.setting}</Mono> = <Mono>{data.listen.addresses.join(", ")}</Mono>
                  </span>
                </div>
              ) : (
                <p className="text-13 text-fg-muted">{t("databases.connect.listenUnknown")}</p>
              )}
            </div>
          )}
        </Section>
      </Sections>

      <CommandHint command={`noust db connect-info ${name} -e ${engine}`} label={t("databases.common.fromTerminal")} />
      <LinkDialog open={linking} onOpenChange={setLinking} engine={engine} database={name} onQueued={(accepted, domain) => job.track(accepted, "link", domain)} />
      <SecretShown secret={secret} onClose={() => setSecret(null)} />
    </div>
  );
}
