import { useQuery } from "@tanstack/react-query";

import { localSignInQuery, ssoConfigQuery, ssoIdentitiesQuery } from "../../../api/queries/ssoAdmin";
import { useDocumentTitle } from "../../../app/documentTitle";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Sections } from "../../../components/page/Section";
import { SkeletonCard, SkeletonSection } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { ConnectionsSection } from "./ConnectionsSection";
import { IdentitiesSection } from "./IdentitiesSection";
import { LocalSignInSection } from "./LocalSignInSection";
import { ScimSection } from "./ScimSection";

/**
 * Settings > Single sign-on: the identity providers people sign in with, the people linked to
 * them, and whether passwords and passkeys still sign accounts in beside them. The providers
 * come first because everything else depends on there being one; turning the password sign-in
 * off follows, with its way back spelled out.
 *
 * The first three sections are read together and drawn together: each one's answer changes how
 * tall it is, and one that arrived after the others would push the rest down the page. The
 * provisioning sources come last and load on their own: a person who may read the sign-in settings
 * may not read them, and that must not hold the page back.
 */
export function SsoSettings() {
  const t = useT();
  useDocumentTitle(t("ssoAdmin.documentTitle"), 1);
  const providers = useQuery(ssoConfigQuery());
  const local = useQuery(localSignInQuery());
  const identities = useQuery(ssoIdentitiesQuery());
  const waiting = providers.isPending || local.isPending || identities.isPending;

  if (waiting) {
    return (
      <LoadingRegion label={t("ssoAdmin.loading")} className="flex flex-col gap-8">
        <SkeletonSection rows={3} />
        <SkeletonCard lines={2} />
        <SkeletonSection rows={2} />
      </LoadingRegion>
    );
  }
  return (
    <Sections>
      <ConnectionsSection />
      <LocalSignInSection />
      <ScimSection />
      <IdentitiesSection />
    </Sections>
  );
}
