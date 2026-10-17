import { Section } from "../../components/page/Section";
import { CommandHint } from "../../components/page/CommandHint";
import { Card } from "../../components/ui/Card";
import { CopyValue } from "../../components/ui/CopyValue";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";

/** The path a scraper asks (noust.web.api.metrics.export_prometheus). */
export const EXPORT_PATH = "/api/metrics/prometheus";

/**
 * A `scrape_configs` entry for this server: the address from where the console is open, the
 * path, and a credentials file for the token. Neither a token nor a secret is ever written here.
 */
export function scrapeConfig(host: string, scheme: string): string {
  return [
    "scrape_configs:",
    "  - job_name: noust",
    `    scheme: ${scheme}`,
    `    metrics_path: ${EXPORT_PATH}`,
    "    authorization:",
    "      credentials_file: /etc/prometheus/noust.token",
    "    static_configs:",
    `      - targets: ["${host}"]`,
  ].join("\n");
}

/**
 * The server's metrics for a scraper (Prometheus, Grafana Agent, an OpenTelemetry Collector): the
 * address to ask, a configuration to paste, and what credential to give it. The credential is an
 * API token that can only read; there is no export secret of its own to leak.
 */
export function MetricsExportSection() {
  const t = useT();
  const { host, protocol } = window.location;
  const scheme = protocol === "http:" ? "http" : "https";
  return (
    <Section title={t("metricsExport.title")} description={t("metricsExport.description")}>
      <Card>
        <div className="flex min-w-0 flex-col gap-4">
          <div className="flex min-w-0 flex-col gap-1.5">
            <p className="text-13 font-medium text-fg">{t("metricsExport.addressLabel")}</p>
            <CopyValue value={`${scheme}://${host}${EXPORT_PATH}`} label={t("metricsExport.copyAddress")} name={t("metricsExport.addressLabel")} />
          </div>
          <div className="flex min-w-0 flex-col gap-1.5">
            <p className="text-13 font-medium text-fg">{t("metricsExport.configLabel")}</p>
            <CopyValue value={scrapeConfig(host, scheme)} label={t("metricsExport.copyConfig")} name={t("metricsExport.configLabel")} className="whitespace-pre-wrap" />
          </div>
          <p className="max-w-measure text-13 text-pretty text-fg-muted">
            {t.rich("metricsExport.credential", {
              tokens: (
                <TextLink key="tokens" to="/settings/tokens">
                  {t("metricsExport.tokensLink")}
                </TextLink>
              ),
            })}
          </p>
          <CommandHint command="noust token create prometheus --scope read" label={t("metricsExport.createToken")} />
          <CommandHint command="noust monitor export --format openmetrics" label={t("metricsExport.fromTerminal")} />
        </div>
      </Card>
    </Section>
  );
}
