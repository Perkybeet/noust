import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { databaseKeys } from "../../../api/queries/databases";
import { publicDetailQuery, publicKeys } from "../../../api/queries/publicAccess";
import type { PublicConnection, PublicExposure, PublicOpened } from "../../../api/queries/publicAccess";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { FeatureState } from "../../../components/ui/FeatureState";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { SecretShown } from "../users/SecretShown";
import type { SecretShownProps } from "../users/SecretShown";
import { OpenPublicDialog } from "./OpenPublicDialog";
import { namedSources } from "./publicAccess";
import type { AccessProfile } from "./publicAccess";

/** What the firewall record says Noust did, as plain data: it is stored, so it is read defensively. */
function firewallOf(exposure: PublicExposure): { backend: string; active: boolean } | null {
  const record = exposure.firewall;
  if (record === null || record === undefined) return null;
  const backend = typeof record["backend"] === "string" ? record["backend"] : "";
  return backend === "" ? null : { backend, active: record["active"] === true };
}

function firewallWords(t: T, exposure: PublicExposure): string {
  const firewall = firewallOf(exposure);
  if (firewall === null) return t("publicAccess.firewall.none");
  const sources = namedSources(exposure.sources);
  if (sources.length === 0) return t("publicAccess.firewall.allowedAnyone", { backend: firewall.backend });
  return t("publicAccess.firewall.allowed", { backend: firewall.backend, sources: sources.join(", ") });
}

/** The connection, as the dialog that shows it once lists it. */
export function connectionSecret(t: T, name: string, connection: PublicConnection): NonNullable<SecretShownProps["secret"]> {
  return {
    title: t("publicAccess.secret.title", { name }),
    description: t("publicAccess.secret.description"),
    fields: [
      { label: t("publicAccess.secret.host"), value: connection.host },
      { label: t("publicAccess.secret.port"), value: String(connection.port) },
      { label: t("publicAccess.secret.database"), value: connection.database },
      { label: t("publicAccess.secret.username"), value: connection.username },
      ...(connection.password !== null && connection.password !== undefined ? [{ label: t("publicAccess.secret.password"), value: connection.password }] : []),
      { label: t("publicAccess.secret.url"), value: connection.url },
    ],
  };
}

/**
 * "Public access" on a database's Connect tab: private by default, and opened to a client on the
 * operator's own computer on purpose, on a port they choose, with the account, the TLS rule and
 * the firewall rule said before anything is made. Making it private again removes exactly what
 * opening made.
 */
export function PublicAccessSection({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const detail = useQuery(publicDetailQuery(engine, name));
  const [opening, setOpening] = useState(false);
  const [closing, setClosing] = useState(false);
  const [secret, setSecret] = useState<SecretShownProps["secret"]>(null);

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: publicKeys.all });
    void queryClient.invalidateQueries({ queryKey: databaseKeys.database(engine, name) });
  };

  const reveal = useMutation({
    mutationFn: () => request("post", "/api/databases/public/{engine}/{database}/reveal", { params: { engine, database: name } }),
    onSuccess: (connection) => setSecret(connectionSecret(t, name, connection)),
    onError: (error) => reportActionError(t("publicAccess.revealFailed"), error),
  });

  const data = detail.data;
  const exposure = data?.public === true ? (data.exposure ?? null) : null;
  const unreachable = exposure !== null && exposure.state === "exposed" && !exposure.listening;
  const stuck = exposure !== null && exposure.state !== "exposed";
  const open = (
    <Button size="sm" variant="secondary" onClick={() => setOpening(true)}>
      {t("publicAccess.actions.open")}
    </Button>
  );

  return (
    <Section title={t("publicAccess.title")} description={t("publicAccess.description")} loading={data === undefined && !detail.isError}>
      {detail.isError && data === undefined ? (
        <ErrorBlock compact error={detail.error} title={t("publicAccess.loadFailed")} onRetry={() => void detail.refetch()} retrying={detail.isRefetching} />
      ) : data === undefined ? (
        <Skeleton className="h-24 w-full rounded-card" />
      ) : exposure === null ? (
        <FeatureState state="off" title={t("publicAccess.state.private")} action={open}>
          {t("publicAccess.state.privateBody")}
        </FeatureState>
      ) : (
        <div className="flex min-w-0 flex-col gap-4">
          <FeatureState
            state={stuck || unreachable ? "problem" : "on"}
            title={stuck ? t("publicAccess.state.closing") : unreachable ? t("publicAccess.state.notAnswering") : t("publicAccess.state.public", { port: exposure.port })}
            action={
              <div className="flex flex-wrap items-center gap-2">
                {!stuck ? (
                  <Button size="sm" loading={reveal.isPending} onClick={() => reveal.mutate()}>
                    {t("publicAccess.actions.reveal")}
                  </Button>
                ) : null}
                <Button size="sm" variant="secondary" onClick={() => setClosing(true)}>
                  {t("publicAccess.actions.close")}
                </Button>
              </div>
            }
          >
            {stuck ? t("publicAccess.state.closingBody") : unreachable ? t("publicAccess.state.notAnsweringBody") : t("publicAccess.state.publicBody")}
          </FeatureState>
          <KeyValueList
            items={[
              { label: t("publicAccess.facts.address"), value: data.connection !== null && data.connection !== undefined ? `${data.connection.host}:${String(data.connection.port)}` : `${exposure.bind_address}:${String(exposure.port)}` },
              { label: t("publicAccess.facts.tls"), value: exposure.tls === "required" ? t("publicAccess.tls.required") : t("publicAccess.tls.optional"), mono: false, copy: false },
              { label: t("publicAccess.facts.from"), value: namedSources(exposure.sources).length === 0 ? t("publicAccess.anyone") : namedSources(exposure.sources).join(", "), mono: namedSources(exposure.sources).length > 0, copy: false },
              { label: t("publicAccess.facts.account"), value: exposure.remote_user },
              { label: t("publicAccess.facts.access"), value: t(`publicAccess.profile.${exposure.profile as AccessProfile}`), mono: false, copy: false },
              { label: t("publicAccess.facts.firewall"), value: firewallWords(t, exposure), mono: false, copy: false },
              { label: t("publicAccess.facts.proxy"), value: exposure.unit },
              { label: t("publicAccess.facts.since"), value: <RelativeTime value={exposure.created_at} />, mono: false, copy: false },
            ]}
          />
        </div>
      )}
      <OpenPublicDialog
        key={`${engine}/${name}`}
        engine={engine}
        database={name}
        open={opening}
        onOpenChange={setOpening}
        onOpened={(opened: PublicOpened) => {
          refresh();
          setSecret(connectionSecret(t, name, opened.connection));
        }}
      />
      <ConfirmDialog
        friction="simple"
        open={closing}
        onOpenChange={setClosing}
        title={t("publicAccess.close.title", { name })}
        description={t("publicAccess.close.description")}
        actionLabel={t("publicAccess.close.action")}
        server={node}
        onConfirm={async () => {
          await request("delete", "/api/databases/public/{engine}/{database}", { params: { engine, database: name } });
          refresh();
        }}
      />
      <SecretShown secret={secret} onClose={() => setSecret(null)} />
    </Section>
  );
}
