import { useQuery } from "@tanstack/react-query";

import { credentialsQuery } from "../../../api/queries/credentials";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { RowList } from "../../../components/ui/RowList";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";

/**
 * The credentials of the built-in assistant, one for each person it works for (item 185), next to the tokens and opening the same
 * activity page as a token does. It is drawn only when the assistant has acted for somebody this person may read: with the
 * assistant off, and for a server that never used it, this section does not exist.
 */
export function AssistantCredentials() {
  const t = useT();
  const query = useQuery(credentialsQuery());
  const assistants = (query.data?.credentials ?? []).filter((credential) => credential.kind === "assistant");
  if (assistants.length === 0) return null;
  return (
    <Section title={t("callerPolicy.activity.kind.assistant")} icon={OBJECT_ICONS.assistant} description={t("callerPolicy.activity.assistantNote")}>
      <RowList aria-label={t("callerPolicy.activity.kind.assistant")}>
        {assistants.map((credential) => (
          <RowList.Item key={credential.id} leading={<OBJECT_ICONS.assistant aria-hidden="true" className="size-icon-md text-fg-muted" />}>
            <span className="flex min-w-0 flex-col py-1">
              <TextLink to="/settings/tokens/$id" params={{ id: String(credential.id) }} variant="row" size="ui">
                {t("callerPolicy.activity.assistantTitle", { owner: credential.owner ?? "" })}
              </TextLink>
              {credential.last_used_at !== null && credential.last_used_at !== undefined ? (
                <span className="text-12 text-fg-muted">
                  <RelativeTime value={credential.last_used_at} />
                </span>
              ) : null}
            </span>
          </RowList.Item>
        ))}
      </RowList>
    </Section>
  );
}
