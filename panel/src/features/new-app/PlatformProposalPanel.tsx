import { FileCog } from "lucide-react";
import type { ReactNode } from "react";

import { Card } from "../../components/ui/Card";
import { Checkbox } from "../../components/ui/Checkbox";
import { ICONS } from "../../components/ui/icons";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { joinList, platformName } from "./wizard";
import type { PlatformProposal } from "./wizard";

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 items-baseline gap-3 py-1">
      <dt className="w-24 shrink-0 text-12 text-fg-muted">{label}</dt>
      <dd className="min-w-0 flex-1 text-12 text-fg">{children}</dd>
    </div>
  );
}

function Command({ argv }: { argv: string }) {
  return (
    <Mono truncate title={argv}>
      {argv}
    </Mono>
  );
}

export interface PlatformProposalPanelProps {
  proposal: PlatformProposal;
  /** Whether what it proposes is filled in below. */
  used: boolean;
  onUse: (used: boolean) => void;
  /** What the server said of the health check it sent, verbatim, when it refused it. */
  healthErrors?: readonly string[];
}

/**
 * What another platform's configuration in the repository says, in Noust's terms: which files
 * were read, a toggle for the values filled in below (port, variables, persistent paths) and
 * for the health check the first deploy is gated on, the commands for reference, and every
 * warning the server wrote about what has no equivalent here, verbatim.
 */
export function PlatformProposalPanel({ proposal, used, onUse, healthErrors = [] }: PlatformProposalPanelProps) {
  const t = useT();
  const files = proposal.files ?? [];
  const warnings = proposal.warnings ?? [];
  const databases = proposal.databases ?? [];
  const domains = proposal.domains ?? [];
  const commands = [
    { label: t("newApp.proposal.install"), argv: proposal.install_command ?? null },
    { label: t("newApp.proposal.build"), argv: proposal.build_command ?? null },
    { label: t("newApp.proposal.start"), argv: proposal.start_command ?? null },
  ].filter((entry): entry is { label: string; argv: string } => entry.argv !== null && entry.argv.trim() !== "");
  const path = proposal.health_path ?? null;
  const timeout = proposal.health_timeout ?? null;
  const health =
    path !== null && timeout !== null
      ? t("newApp.proposal.healthTimeout", { path, seconds: String(timeout) })
      : path !== null
        ? t("newApp.proposal.healthPath", { path })
        : timeout !== null
          ? t("newApp.proposal.healthTimeoutOnly", { seconds: String(timeout) })
          : null;

  return (
    <Card
      padding="sm"
      title={
        <span className="flex min-w-0 items-start gap-2">
          <FileCog aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
          <span className="min-w-0 text-pretty">
            {t.rich("newApp.proposal.found", { platform: platformName(proposal.platform), files: <Mono>{joinList(files, t.locale)}</Mono> })}
          </span>
        </span>
      }
    >
      <div className="flex min-w-0 flex-col gap-3">
      <Checkbox label={t("newApp.proposal.use")} description={t("newApp.proposal.useDescription")} checked={used} onCheckedChange={onUse} />

      {commands.length > 0 ? <p className="text-12 text-pretty text-fg-muted">{t("newApp.proposal.commandsNote")}</p> : null}
      {commands.length > 0 || health !== null || databases.length > 0 || domains.length > 0 ? (
        <dl className="flex flex-col rounded-control border border-border bg-bg-sunken px-3 py-1.5">
          {commands.map((entry) => (
            <Fact key={entry.label} label={entry.label}>
              <Command argv={entry.argv} />
            </Fact>
          ))}
          {health !== null ? (
            <Fact label={t("newApp.proposal.health")}>
              <Mono>{health}</Mono>
            </Fact>
          ) : null}
          {databases.length > 0 ? (
            <Fact label={t("newApp.proposal.databases")}>
              <Mono>{joinList(databases, t.locale)}</Mono>
              <span className="block text-fg-muted">{t("newApp.proposal.databasesNote")}</span>
            </Fact>
          ) : null}
          {domains.length > 0 ? (
            <Fact label={t("newApp.proposal.domains")}>
              <Mono>{joinList(domains, t.locale)}</Mono>
              <span className="block text-fg-muted">{t("newApp.proposal.domainsNote")}</span>
            </Fact>
          ) : null}
        </dl>
      ) : null}
      {used && healthErrors.length > 0 ? (
        <Notice tone="error" live title={t("newApp.proposal.healthRefused")}>
          {/* The server's own words, verbatim. */}
          {healthErrors.map((message) => (
            <p key={message}>
              <Mono>{message}</Mono>
            </p>
          ))}
        </Notice>
      ) : null}

      {warnings.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          <p className="text-12 font-medium text-fg">{t("newApp.proposal.warnings")}</p>
          {/* The server's words, verbatim: they say what has no equivalent and what to do instead. */}
          <ul aria-label={t("newApp.proposal.warnings")} className="flex flex-col gap-1.5">
            {warnings.map((warning) => (
              <li key={warning} className="flex items-start gap-2 text-13 text-pretty text-fg">
                <ICONS.warning aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-warn" />
                <span className="min-w-0">{warning}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      </div>
    </Card>
  );
}
