import { useQuery } from "@tanstack/react-query";
import { LockKeyhole, LockOpen } from "lucide-react";

import { sessionQuery } from "../../api/queries/auth";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import { Sections } from "../../components/page/Section";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { useServerList } from "../../nodes/servers";
import { useCentral } from "./central";
import { UnlockForm } from "./UnlockForm";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

/**
 * Settings > Central and sealing: what this central is (its name, its role, how many servers it manages, which is a
 * link to them) and the state of the secrets that reach its servers: sealed or not, locked or open, with the passphrase
 * form when it is locked. Sealing and unsealing stay commands on the central (docs/CENTRAL.md): they are said here, not
 * offered, because a passphrase lost from a browser would cost every server, and so the one command the page shows is
 * the one that cannot be done from it.
 */
export function CentralSettings() {
  const t = useT();
  useDocumentTitle(t("servers.central.documentTitle"), 1);
  const central = useCentral();
  const { data: session } = useQuery(sessionQuery());
  const { nodes, loaded, failed } = useServerList();
  // The count of servers is the one fact of this card that waits for the network.
  const reading = !loaded && !failed;
  const name = session?.hostname ?? "";

  const seal = central.locked
    ? { state: "warning" as const, label: t("servers.central.seal.locked") }
    : central.sealed
      ? { state: "running" as const, label: t("servers.central.seal.unlocked") }
      : { state: "stopped" as const, label: t("servers.central.seal.notSealed") };

  return (
    <Sections surfaces>
      <SurfaceSection loading={reading} title={t("servers.central.identity.title")} icon={OBJECT_ICONS.central} description={t("servers.central.identity.description")}>
        <KeyValueList
          items={[
            { label: t("common.words.name"), value: <Mono>{name}</Mono>, copy: name === "" ? false : name, mono: true },
            {
              label: t("servers.central.identity.role"),
              value: central.role === "hub" ? t("servers.central.identity.roleHub") : t("servers.central.identity.roleServer"),
              copy: false,
              mono: false,
            },
            {
              label: t("servers.central.identity.servers"),
              // The servers are one click away: the number is the way there.
              value: reading ? (
                <Skeleton className="h-3.5 w-24" />
              ) : (
                <TextLink to="/fleet/servers" size="ui">
                  {t("servers.central.identity.serverCount", { count: nodes.length })}
                </TextLink>
              ),
              copy: false,
              mono: false,
            },
          ]}
        />
      </SurfaceSection>

      <SurfaceSection
        title={t("servers.central.seal.title")}
        icon={OBJECT_ICONS.secretManager}
        description={t("servers.central.seal.description")}
        status={<StatusPill state={seal.state} label={seal.label} appearance="inline" size="sm" />}
      >
        <div className="flex flex-col gap-4">
          {central.locked ? (
            <>
              <Notice tone="warning" title={t("servers.lock.bannerTitle")}>
                {t("servers.lock.bannerDescription")}
              </Notice>
              <UnlockForm />
              <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("servers.lock.lostPassphrase")}</p>
            </>
          ) : central.sealed ? (
            <p className="flex max-w-measure items-start gap-2 text-14 text-pretty text-fg-muted">
              <LockOpen aria-hidden="true" className="mt-0.5 size-icon-md shrink-0" />
              {t("servers.central.seal.unlockedBody")}
            </p>
          ) : (
            <p className="flex max-w-measure items-start gap-2 text-14 text-pretty text-fg-muted">
              <LockKeyhole aria-hidden="true" className="mt-0.5 size-icon-md shrink-0" />
              {t("servers.central.seal.notSealedBody")}
            </p>
          )}
          {/* Sealing is not done from a browser: the console cannot do it, the terminal of the central can. */}
          <CommandHint label={t("servers.central.fromTerminal")} command={central.sealed ? "noust central unseal" : "noust central seal"} reason="solo-terminal" />
        </div>
      </SurfaceSection>
    </Sections>
  );
}
