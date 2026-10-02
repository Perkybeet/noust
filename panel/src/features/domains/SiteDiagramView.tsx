import { useMutation, useQuery } from "@tanstack/react-query";
import { Route, X } from "lucide-react";
import { useMemo, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { siteStructureQuery, siteTopologyQuery } from "../../api/queries/sites";
import type { SiteBackend, SiteRoute, SiteStructure } from "../../api/queries/sites";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { FlowDiagram } from "../../components/ui/FlowDiagram";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { traceSentence } from "./routeTrace";
import { buildSiteDiagram, parseTriedUrl, routePath, serverLabel } from "./siteDiagram";
import type { DiagramWords, TriedUrl } from "./siteDiagram";
import { locationLabel, locationsById } from "./siteStructure";
import { NotParsed, OlderNoust, ViewLoading, isMissingRoute } from "./SiteViewStates";

export interface SiteDiagramViewProps {
  site: string;
  webserver: string;
  /** The draft: the text every view shares. */
  text: string;
  dirty: boolean;
  onGoToText: (line?: number) => void;
}

function diagramWords(t: T): DiagramWords {
  const list = new Intl.ListFormat(t.locale, { type: "conjunction" });
  return {
    layers: {
      ports: t("domains.siteDiagram.layers.ports"),
      names: t("domains.siteDiagram.layers.names"),
      locations: t("domains.siteDiagram.layers.locations"),
      destinations: t("domains.siteDiagram.layers.destinations"),
      backends: t("domains.siteDiagram.layers.backends"),
    },
    groups: {
      upstreams: t("domains.siteDiagram.groups.upstreams"),
      proxied: t("domains.siteDiagram.groups.proxied"),
      files: t("domains.siteDiagram.groups.files"),
      answers: t("domains.siteDiagram.groups.answers"),
    },
    families: (ipv4, ipv6) => (ipv4 && ipv6 ? t("domains.siteDiagram.bothFamilies") : ipv6 ? t("domains.siteDiagram.ipv6Only") : t("domains.siteDiagram.ipv4Only")),
    alsoNames: (names) => (names.length > 0 ? t("domains.siteDiagram.alsoNames", { names: list.format(names) }) : undefined),
    serverReturns: (code, destination) => t("domains.siteDiagram.serverReturns", { code: code ?? "", destination: destination ?? "" }),
    expiresIn: (days) => t("domains.siteStructure.expiresIn", { count: days }),
    expired: (days) => t("domains.siteStructure.expired", { count: days }),
    upstreamServers: (count) => t("domains.siteDiagram.upstreamServers", { count }),
    owner: (backend: SiteBackend) => {
      if (!backend.local) return t("domains.siteDiagram.owner.remote");
      const owner = backend.owner;
      if (!owner) return backend.listening === false ? t("domains.siteDiagram.owner.nobody") : undefined;
      switch (owner.kind) {
        case "app":
          return t("domains.siteDiagram.owner.app", { app: owner.app ?? "" });
        case "compose":
          return t("domains.siteDiagram.owner.compose", { service: owner.service ?? "", project: owner.project ?? "" });
        case "container":
          return t("domains.siteDiagram.owner.container", { container: owner.container ?? "" });
        case "unit":
          return t("domains.siteDiagram.owner.unit", { unit: owner.unit ?? "" });
        default:
          return owner.pid !== null && owner.pid !== undefined
            ? t("domains.siteDiagram.owner.process", { process: owner.process ?? "", pid: owner.pid })
            : t("domains.siteDiagram.owner.processNoPid", { process: owner.process ?? "" });
      }
    },
    denied: t("domains.siteDiagram.denied"),
    answersItself: t("domains.siteDiagram.answersItself"),
    fastcgi: t("domains.siteDiagram.fastcgi"),
  };
}

interface Tried {
  url: TriedUrl;
  /** The text it was traced through: a newer draft makes the answer stale. */
  text: string;
  route: SiteRoute;
}

/**
 * How requests travel through a site: ports, names, locations, destinations and the
 * backends behind them, with whether each backend answers now and who holds its port. "Try a
 * URL" replays the web server's own choice (`/route`) and lights only that path, explained
 * step by step. Read only: the draft is edited in the Text and Structure views.
 */
export function SiteDiagramView({ site, webserver, text, dirty, onGoToText }: SiteDiagramViewProps) {
  const t = useT();
  const words = useMemo(() => diagramWords(t), [t]);
  const topology = useQuery(siteTopologyQuery(site));
  // The saved file's model comes with the topology; a draft's is asked for on its own.
  const draft = useQuery({ ...siteStructureQuery(site, text), enabled: dirty });
  const [value, setValue] = useState("");
  const [invalid, setInvalid] = useState(false);
  const [tried, setTried] = useState<Tried | null>(null);

  const trace = useMutation({
    mutationFn: async ({ url, config }: { url: TriedUrl; config: string | null }) =>
      request("post", "/api/sites/{domain}/route", {
        params: { domain: site },
        body: { host: url.host, path: url.path, scheme: url.scheme, ...(url.explicitPort ? { port: url.port } : {}), ...(config !== null ? { config } : {}) },
      }),
    onSuccess: (route, { url }) => setTried({ url, text, route }),
  });

  const source = dirty ? draft : topology;
  const structure: SiteStructure | null = source.data?.structure ?? null;
  const facts = useMemo(() => (topology.data ? { backends: topology.data.backends, certificates: topology.data.certificates } : null), [topology.data]);
  const diagram = useMemo(() => (structure ? buildSiteDiagram(structure, facts, words) : null), [structure, facts, words]);

  const missing = isMissingRoute(topology.error) || isMissingRoute(draft.error);
  if (missing) return <OlderNoust onText={() => onGoToText()} />;
  if (source.isError) {
    return (
      <ErrorBlock
        error={source.error}
        title={dirty ? t("domains.siteViews.couldNotRead", { site }) : t("domains.siteDiagram.couldNotRead", { site })}
        onRetry={() => void source.refetch()}
        retrying={source.isRefetching}
      />
    );
  }
  if (source.data === undefined) return <ViewLoading />;
  if (source.data.error) return <NotParsed failure={source.data.error} webserver={webserver} onGoToText={onGoToText} />;
  if (structure === null || diagram === null) return <ViewLoading />;

  const current = tried !== null && tried.text === text ? tried : null;
  const highlight = current ? routePath(current.route, diagram, current.url.port) : undefined;
  const locations = locationsById(structure);
  const serverName = (id: string): string => {
    const server = structure.servers.find((candidate) => candidate.id === id);
    return server ? serverLabel(server) : id;
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const url = parseTriedUrl(value, site);
    setInvalid(url === null);
    if (url === null) return;
    trace.mutate({ url, config: dirty ? text : null });
  };

  const shown = current ? `${current.url.scheme}://${current.url.host}${current.url.explicitPort ? `:${String(current.url.port)}` : ""}${current.url.path}` : "";
  const answer = current
    ? current.route.server_id === null || current.route.server_id === undefined
      ? t.rich("domains.siteRoute.noServer", { url: <Mono>{shown}</Mono> })
      : current.route.location_id === null || current.route.location_id === undefined
        ? t.rich("domains.siteRoute.serverItself", { server: <Mono>{serverName(current.route.server_id)}</Mono>, url: <Mono>{shown}</Mono> })
        : t.rich("domains.siteRoute.answeredBy", {
            location: <Mono>{locationLabel(locations.get(current.route.location_id) ?? { modifier: "", path: current.route.location_id })}</Mono>,
            server: <Mono>{serverName(current.route.server_id)}</Mono>,
            url: <Mono>{shown}</Mono>,
          })
    : null;

  return (
    <div className="flex min-w-0 flex-col gap-5">
      {dirty ? <p className="text-13 text-fg-muted">{t("domains.siteDiagram.draftNote")}</p> : null}
      <form onSubmit={submit} noValidate className="flex min-w-0 flex-col gap-3">
        <Field
          label={t("domains.siteRoute.field")}
          description={t("domains.siteRoute.help", { example: `https://${site}/api/` })}
          error={invalid ? t("domains.siteRoute.invalid") : null}
          action={
            <span className="flex items-center gap-2">
              <Button type="submit" icon={<Route aria-hidden="true" />} loading={trace.isPending}>
                {t("domains.siteRoute.submit")}
              </Button>
              {current ? (
                <Button
                  variant="ghost"
                  icon={<X aria-hidden="true" />}
                  onClick={() => {
                    setTried(null);
                    setValue("");
                    trace.reset();
                  }}
                >
                  {t("domains.siteRoute.clear")}
                </Button>
              ) : null}
            </span>
          }
        >
          <Input
            type="text"
            inputMode="url"
            mono
            value={value}
            placeholder={`https://${site}/`}
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => setValue(event.currentTarget.value)}
          />
        </Field>
        {trace.isError ? <ErrorBlock live compact error={trace.error} title={t("domains.siteRoute.couldNotTrace")} /> : null}
        <div role="status" className="flex min-w-0 flex-col gap-2">
          {current ? (
            <>
              <p className="text-14 font-medium text-fg">{answer}</p>
              {current.route.redirect ? (
                <p className="text-13 text-fg">{t.rich("domains.siteRoute.redirect", { redirect: <Mono>{current.route.redirect}</Mono> })}</p>
              ) : null}
              <ol aria-label={t("domains.siteRoute.explanation", { webserver })} className="flex list-decimal flex-col gap-1 pl-5 text-13 text-fg-muted">
                {current.route.trace.map((step, index) => (
                  <li key={`${String(index)}:${step.code}`}>{traceSentence(t, step, serverName)}</li>
                ))}
              </ol>
            </>
          ) : null}
        </div>
      </form>
      <FlowDiagram layers={diagram.layers} edges={diagram.edges} label={t("domains.siteDiagram.label", { site })} {...(highlight && highlight.length > 0 ? { highlight } : {})} />
    </div>
  );
}
