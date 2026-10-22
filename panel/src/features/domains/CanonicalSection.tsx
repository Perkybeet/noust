import { useMutation } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { AppDomainChange } from "../../api/queries/domains";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";

type Spelling = "none" | "www" | "apex";

/** The two spellings of a name: its bare form and its `www.` one, whichever the application was deployed as. */
export function spellings(domain: string): { bare: string; www: string } {
  const bare = domain.startsWith("www.") ? domain.slice(4) : domain;
  return { bare, www: `www.${bare}` };
}

/**
 * Which spelling visitors see: `www.example.com`, `example.com`, or both as recorded. The one
 * that is not chosen redirects to it, over TLS too, because both names are recorded and the
 * certificate covers both. It applies at once; when the site serves TLS the certificate is
 * extended to the pair in a job the page follows.
 */
export function CanonicalSection({ domain, canonical, onChanged }: { domain: string; canonical: string; onChanged: (change: AppDomainChange) => void }) {
  const t = useT();
  const names = spellings(domain);
  const value: Spelling = canonical === "www" || canonical === "apex" ? canonical : "none";
  const choose = useMutation({
    mutationFn: (next: Spelling) => request("put", "/api/apps/{domain}/domains/canonical", { params: { domain }, body: { canonical: next } }),
    onSuccess: (change) => {
      onChanged(change);
    },
  });
  return (
    <Section icon={OBJECT_ICONS.domain} title={t("network.canonical.title")} description={t("network.canonical.description")}>
      <ChoiceCards<Spelling>
        legend={t("network.canonical.legend")}
        options={[
          { value: "none", label: t("network.canonical.none"), description: t("network.canonical.noneHelp") },
          { value: "www", label: names.www, untranslated: true, description: t("network.canonical.wwwHelp", { bare: names.bare }) },
          { value: "apex", label: names.bare, untranslated: true, description: t("network.canonical.apexHelp", { www: names.www }) },
        ]}
        value={choose.isPending ? choose.variables : value}
        onValueChange={(next) => {
          if (next !== value && !choose.isPending) choose.mutate(next);
        }}
      />
      {choose.isError ? <ErrorBlock live compact error={choose.error} title={t("network.canonical.failed", { domain })} /> : null}
    </Section>
  );
}
