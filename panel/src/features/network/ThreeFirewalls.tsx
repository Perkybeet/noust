import { Section } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";

/**
 * The three things people call "the firewall", named apart (owner item 119: the atlas found the same word for the
 * machine's, for the web application firewall and for the one the hosting provider keeps outside the machine). The
 * server's is this page; the web's is in Security, where it is installed and read; the provider's Noust does not see,
 * and the card says so, so a port that is open here and does not answer from outside has somewhere to be looked for.
 */
export function ThreeFirewalls() {
  const t = useT();
  return (
    <Section title={t("network.firewall.threeTitle")}>
      <ul className="grid gap-3 md:grid-cols-3">
        <li className="min-w-0">
          <Card level={3} padding="sm" as="div" className="h-full" title={t("network.firewall.ofServer")} icon={OBJECT_ICONS.firewall} status={<Badge>{t("network.firewall.thisPage")}</Badge>}>
            <p className="text-13 text-pretty text-fg-muted">{t("network.firewall.ofServerLine")}</p>
          </Card>
        </li>
        <li className="min-w-0">
          <Card level={3} padding="sm" as="div" className="h-full" title={t("network.firewall.ofWeb")} icon={OBJECT_ICONS.security}>
            <p className="text-13 text-pretty text-fg-muted">{t("network.firewall.ofWebLine")}</p>
            <p className="mt-2">
              <TextLink to="/security/waf" size="ui">
                {t("network.firewall.ofWebLink")}
              </TextLink>
            </p>
          </Card>
        </li>
        <li className="min-w-0">
          <Card level={3} padding="sm" as="div" className="h-full" title={t("network.firewall.ofProvider")} icon={OBJECT_ICONS.cloud}>
            <p className="text-13 text-pretty text-fg-muted">{t("network.firewall.ofProviderLine")}</p>
            <p className="mt-2 text-13 text-pretty text-fg-muted">{t("network.firewall.ofProviderAdvice")}</p>
          </Card>
        </li>
      </ul>
    </Section>
  );
}
