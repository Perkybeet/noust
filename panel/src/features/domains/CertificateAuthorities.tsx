import { useQuery } from "@tanstack/react-query";

import { acmeQuery, internalCaQuery } from "../../api/queries/network";
import { useCan } from "../../components/page/permissions";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { Button } from "../../components/ui/Button";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { ResourceCard } from "../../components/ui/ResourceCard";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";

export interface CertificateAuthoritiesProps {
  /** Opens where the authority certificates are ordered from is chosen. */
  onChangePublic: () => void;
  /** Opens the server's own authority: made, or to be made. */
  onOpenOwn: () => void;
}

/**
 * Who signs the certificates of this server, as two cards (the public authority they are ordered from, and the server's
 * own for names that only exist inside a private network) instead of two entries of a menu, which is where a person did
 * not look for them. "Authority" is also a word of the firewall and of the sign-in (the atlas found three); here it is
 * the one that signs certificates, and the section says so.
 */
export function CertificateAuthorities({ onChangePublic, onOpenOwn }: CertificateAuthoritiesProps) {
  const t = useT();
  const mayChange = useCan("settings.manage");
  const acme = useQuery(acmeQuery());
  // A server whose Noust has no private authority answers 404 and the card says it is not made.
  const own = useQuery({ ...internalCaQuery(), retry: false });
  const made = own.data?.authority ?? null;
  return (
    <SurfaceSection title={t("domains.authorities.title")} icon={OBJECT_ICONS.certificate} description={t("domains.authorities.line")} loading={acme.isPending && own.isPending}>
      <div className="grid gap-3 md:grid-cols-2">
        {acme.data !== undefined ? (
          <ResourceCard
            name={acme.data.label}
            untranslated
            level={3}
            icon={OBJECT_ICONS.certificate}
            status={
              acme.data.trusted ? (
                <StatusPill state="running" label={t("domains.authorities.inUse")} size="sm" />
              ) : (
                <StatusPill state="warning" label={t("domains.authorities.untrusted")} size="sm" />
              )
            }
            facts={[
              { label: t("domains.authorities.forLabel"), value: t("domains.authorities.publicNames") },
              { label: t("domains.authorities.accountLabel"), value: acme.data.email === "" ? t("domains.authorities.noAccount") : acme.data.email },
            ]}
            actions={
              mayChange ? (
                <Button size="sm" onClick={onChangePublic}>
                  {t("domains.authorities.change")}
                </Button>
              ) : undefined
            }
          />
        ) : null}
        {own.data !== undefined || own.isError ? (
          <ResourceCard
            name={t("domains.authorities.ownName")}
            level={3}
            icon={OBJECT_ICONS.key}
            status={made !== null ? <StatusPill state="running" label={t("domains.authorities.inUse")} size="sm" /> : <StatusPill state="stopped" label={t("domains.authorities.notMade")} size="sm" />}
            facts={[
              { label: t("domains.authorities.forLabel"), value: t("domains.authorities.ownFor") },
              ...(made !== null ? [{ label: t("domains.authorities.validLabel"), value: t("domains.certificates.day", { count: made.days_left }) }] : []),
            ]}
            actions={
              made !== null ? (
                <Button size="sm" onClick={onOpenOwn}>
                  {mayChange ? t("domains.authorities.manage") : t("domains.authorities.see")}
                </Button>
              ) : mayChange ? (
                <Button size="sm" onClick={onOpenOwn}>
                  {t("domains.authorities.make")}
                </Button>
              ) : undefined
            }
          />
        ) : null}
      </div>
    </SurfaceSection>
  );
}
