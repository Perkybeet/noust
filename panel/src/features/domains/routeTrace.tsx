import type { ReactNode } from "react";

import type { SiteRouteStep } from "../../api/queries/sites";
import { Mono } from "../../components/ui/Mono";
import type { T } from "../../i18n";

/**
 * One step of `/route`'s explanation in the console's language: the backend sends each as a
 * code and its parameters (noust.managers.siteconf.route), and every value is a system value
 * set in mono. A server is named by its first name rather than its id (`s1`). A code this
 * console does not know yet is said in the backend's own English, never dropped.
 */
export function traceSentence(t: T, step: SiteRouteStep, serverName: (id: string) => string): ReactNode {
  const raw = (name: string): string => {
    const value: unknown = step.params[name];
    return typeof value === "string" || typeof value === "number" ? String(value) : "";
  };
  const v = (name: string): ReactNode => <Mono>{raw(name)}</Mono>;
  const server = (): ReactNode => <Mono>{serverName(raw("server"))}</Mono>;
  const servers = (): ReactNode => (
    <Mono>
      {raw("servers")
        .split(",")
        .map((id) => serverName(id.trim()))
        .join(", ")}
    </Mono>
  );
  const k = "domains.siteRoute.trace";
  switch (step.code) {
    case "no_server":
      return t.rich(`${k}.noServer`, { port: v("port") });
    case "port":
      return t.rich(`${k}.port`, { port: v("port"), servers: servers() });
    case "server_exact":
      return t.rich(`${k}.serverExact`, { host: v("host"), name: v("name"), server: server() });
    case "server_leading":
      return t.rich(`${k}.serverLeading`, { host: v("host"), name: v("name"), server: server() });
    case "server_trailing":
      return t.rich(`${k}.serverTrailing`, { host: v("host"), name: v("name"), server: server() });
    case "server_regex":
      return t.rich(`${k}.serverRegex`, { host: v("host"), name: v("name"), server: server() });
    case "server_default":
      return t.rich(`${k}.serverDefault`, { host: v("host"), server: server(), port: v("port") });
    case "server_first":
      return t.rich(`${k}.serverFirst`, { host: v("host"), server: server(), port: v("port") });
    case "regex_skipped":
      return t.rich(`${k}.regexSkipped`, { regex: v("regex") });
    case "no_tls":
      return t.rich(`${k}.noTls`, { server: server(), port: v("port") });
    case "uri":
      return t.rich(`${k}.uri`, { path: v("path") });
    case "location_exact":
      return t.rich(`${k}.locationExact`, { path: v("path"), location: v("location") });
    case "location_prefix":
      return t.rich(`${k}.locationPrefix`, { path: v("path"), location: v("location") });
    case "location_prefixes":
      return t.rich(`${k}.locationPrefixes`, { path: v("path"), prefixes: v("prefixes"), location: v("location") });
    case "location_nested":
      return t.rich(`${k}.locationNested`, { location: v("location") });
    case "location_stop":
      return t.rich(`${k}.locationStop`, { location: v("location") });
    case "location_regex":
      return t.rich(`${k}.locationRegex`, { path: v("path"), location: v("location") });
    case "location_no_regex":
      return t.rich(`${k}.locationNoRegex`, { path: v("path"), location: v("location") });
    case "location_none":
      return t.rich(`${k}.locationNone`, { path: v("path") });
    case "auto_redirect":
      return t.rich(`${k}.autoRedirect`, { path: v("path"), location: v("location"), redirect: v("redirect") });
    case "target_upstream":
      return t.rich(`${k}.targetUpstream`, { upstream: v("upstream") });
    case "target_proxy":
      return t.rich(`${k}.targetProxy`, { url: v("url") });
    case "target_static":
      return t.rich(`${k}.targetStatic`, { directory: v("directory") });
    case "target_return":
      return t.rich(`${k}.targetReturn`, { code: v("code"), destination: v("destination") });
    case "target_fastcgi":
      return t.rich(`${k}.targetFastcgi`, { address: v("address") });
    case "target_other":
      return t(`${k}.targetOther`);
    case "apache_name":
      return t.rich(`${k}.apacheName`, { host: v("host"), server: server(), name: v("name") });
    case "apache_first":
      return t.rich(`${k}.apacheFirst`, { host: v("host"), server: server(), port: v("port") });
    case "apache_location_proxy":
      return t.rich(`${k}.apacheLocationProxy`, { location: v("location"), url: v("url") });
    case "apache_proxypass":
      return t.rich(`${k}.apacheProxypass`, { location: v("location"), path: v("path") });
    case "apache_excluded":
      return t.rich(`${k}.apacheExcluded`, { location: v("location"), path: v("path") });
    case "apache_sections":
      return t.rich(`${k}.apacheSections`, { locations: v("locations") });
    case "apache_no_proxy":
      return t.rich(`${k}.apacheNoProxy`, { path: v("path") });
    default:
      return step.text;
  }
}
