/**
 * What a page of the Docker zone draws when Docker cannot be used on the machine: not a list with nothing in it and not an
 * error, but what Docker is, why it is not usable here and what to do. The engine's own sentences are shown as the server says
 * them (a command in mono, the fix after the sentence), so what the doctor found is never paraphrased.
 */

import { Link } from "@tanstack/react-router";

import type { DockerEngine } from "../../api/queries/docker";
import { Can } from "../../components/page/permissions";
import { buttonClassName } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { ServerText } from "../../components/ui/ServerText";
import { useT } from "../../i18n";

type Sentence = DockerEngine["facts"][number];

/** The sentences the doctor said, one line each, with the fix after the sentence. */
export function EngineSentences({ sentences, label }: { sentences: readonly Sentence[]; label: string }) {
  if (sentences.length === 0) return null;
  return (
    <ul aria-label={label} className="flex min-w-0 flex-col gap-1.5 text-13 text-fg">
      {sentences.map((sentence) => {
        const coded = { code: sentence.code, ...(sentence.params !== undefined ? { params: sentence.params } : {}) };
        return (
          <li key={sentence.code} className="text-pretty">
            <ServerText coded={coded} text={sentence.text} />
            {sentence.hint !== null && sentence.hint !== undefined && sentence.hint !== "" ? (
              <>
                {" "}
                <span className="text-fg-muted">
                  <ServerText coded={coded} text={sentence.hint} part="hint" />
                </span>
              </>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export function Unavailable({ engine }: { engine: DockerEngine | null | undefined }) {
  const t = useT();
  const state = engine?.state ?? "missing";
  const key = state === "unreachable" || state === "too_old" ? state : "missing";
  const sentences = [...(engine?.facts ?? []), ...(engine?.limits ?? [])];
  return (
    <div className="flex min-w-0 flex-col items-center">
      <EmptyState
        variant="firstUse"
        icon={<OBJECT_ICONS.engine aria-hidden="true" />}
        title={t(`docker.unavailable.${key}.title`)}
        description={t(`docker.unavailable.${key}.description`)}
        {...(key === "missing"
          ? {
              action: (
                <Can permission="server.read">
                  <Link to="/server/software" className={buttonClassName("primary", "md")}>
                    {t("docker.unavailable.missing.action")}
                  </Link>
                </Can>
              ),
            }
          : {})}
      />
      {/* What the doctor said is a list, so it is not inside the empty state's paragraph. */}
      {key !== "missing" ? (
        <div className="max-w-measure px-4 pb-8">
          <EngineSentences sentences={sentences} label={t("docker.unavailable.said")} />
        </div>
      ) : null}
    </div>
  );
}
