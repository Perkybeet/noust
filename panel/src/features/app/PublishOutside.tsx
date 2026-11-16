import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { appAddressQuery } from "../../api/queries/apps";
import type { AppAddress } from "../../api/queries/apps";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { buttonClassName } from "../../components/ui/Button";
import { CopyButton } from "../../components/ui/CopyButton";
import { CopyValue } from "../../components/ui/CopyValue";
import { Disclosure } from "../../components/ui/Disclosure";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { useScopeOffers } from "../../nodes/useNode";
import { nginxRule, targetParts } from "./proxyRule";
import type { ProxyHint } from "./proxyRule";

/** The values the machine's proxy asks for, one by one, each with its copy button: panels ask for them field by field. */
function ProxyValues({ hint }: { hint: ProxyHint }) {
  const t = useT();
  const parts = targetParts(hint);
  const rule = nginxRule(hint);
  return (
    <div className="flex flex-col gap-4">
      <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("containerHost.publish.proxy.body")}</p>
      <KeyValueList
        items={[
          { label: t("containerHost.publish.proxy.protocol"), value: parts.protocol, pasted: true },
          { label: t("containerHost.publish.proxy.host"), value: parts.host, pasted: true },
          { label: t("containerHost.publish.proxy.port"), value: parts.port, pasted: true },
          { label: t("containerHost.publish.proxy.websocket"), value: t("containerHost.publish.proxy.websocketOn"), mono: false, copy: false },
          { label: t("containerHost.publish.proxy.timeout"), value: t("containerHost.publish.proxy.timeoutValue", { seconds: Math.max(hint.read_timeout_seconds, 600) }), mono: false, copy: false },
        ]}
      />
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <p className="text-14 font-medium text-fg">{t("containerHost.publish.proxy.rule")}</p>
          <CopyButton value={rule} label={t("containerHost.publish.proxy.copyRule")} />
        </div>
        <pre role="group" aria-label={t("containerHost.publish.proxy.ruleLabel")} translate="no" className="m-0 max-w-full overflow-x-auto rounded-control border border-border px-3 py-2 text-12 leading-5 text-fg">
          {rule}
        </pre>
      </div>
      <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("containerHost.publish.proxy.traps")}</p>
    </div>
  );
}

/** The three ways, in the order a person tries them: as it is, a tunnel that dials out, and the machine's own proxy. */
function Ways({ address }: { address: AppAddress }) {
  const t = useT();
  const offers = useScopeOffers();
  const url = address.url ?? "";
  const hint = address.proxy_hint ?? null;
  return (
    <div className="flex flex-col gap-3">
      <Disclosure variant="card" defaultOpen label={t("containerHost.publish.local.title")} summary={t("containerHost.publish.local.summary")}>
        <div className="flex flex-col gap-3">
          <p className="max-w-measure text-13 text-pretty text-fg-muted">{t.rich("containerHost.publish.local.body", { address: <Mono key="address">{url}</Mono> })}</p>
          <CopyValue value={url} label={t("containerHost.publish.local.copy")} name={t("containerHost.publish.local.title")} />
        </div>
      </Disclosure>
      {offers("tunnels") ? (
        <Disclosure variant="card" label={t("containerHost.publish.tunnel.title")} summary={t("containerHost.publish.tunnel.summary")}>
          <div className="flex flex-col items-start gap-3">
            <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("containerHost.publish.tunnel.body")}</p>
            <Link to="/network/tunnels" className={buttonClassName("secondary", "sm")}>
              {t("containerHost.publish.tunnel.open")}
            </Link>
          </div>
        </Disclosure>
      ) : null}
      {hint !== null ? (
        <Disclosure variant="card" label={t("containerHost.publish.proxy.title")} summary={t("containerHost.publish.proxy.summary")}>
          <ProxyValues hint={hint} />
        </Disclosure>
      ) : null}
    </div>
  );
}

/**
 * How an application of a central that deploys on its machine's Docker is reached from outside, in the three ways there are: as it
 * is on the local network, through a tunnel that dials out, and with a rule in the machine's own reverse proxy, with the values to
 * paste. There is no domain and no certificate of Noust's here (ports 80 and 443 are the machine's), and Noust never touches the
 * machine's proxy. It says what the server says (`GET /api/apps/{domain}/address`) and draws nothing before it answers.
 */
export function PublishOutside({ domain }: { domain: string }) {
  const t = useT();
  const address = useQuery(appAddressQuery(domain));
  const data = address.data;
  if (data === undefined && !address.isError) return <Skeleton className="h-24 w-full" />;
  return (
    <SurfaceSection title={t("containerHost.publish.title")} icon={OBJECT_ICONS.routes} description={t("containerHost.publish.description")}>
      {data === undefined ? (
        <ErrorBlock compact error={address.error} title={t("containerHost.publish.loadFailed")} onRetry={() => void address.refetch()} retrying={address.isRefetching} />
      ) : data.kind === "port" && data.url !== null && data.url !== undefined ? (
        <Ways address={data} />
      ) : (
        <p className="text-13 text-fg-muted">{t("containerHost.publish.noWeb")}</p>
      )}
    </SurfaceSection>
  );
}
