import { Subsection } from "../../components/page/Subsection";
import { Card } from "../../components/ui/Card";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import type { ChoiceCard } from "../../components/ui/ChoiceCards";
import { Notice } from "../../components/ui/Notice";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { ServerText } from "../../components/ui/ServerText";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { chosenForm, withForm } from "./howItRuns";
import type { Way, WayForm, Ways } from "./howItRuns";
import type { ReviewForm } from "./wizard";

function Facts({ items }: { items: readonly string[] }) {
  return (
    <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-13 text-fg-muted">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

/** The two cards: what each way means in a line, and behind "Details" what it asks for and what it gives. */
function cards(t: T, ways: Ways, only: WayForm | null): ChoiceCard<WayForm>[] {
  const proposed = (form: WayForm) => (ways.proposed === form ? { badge: t("newApp.how.proposes") } : {});
  const server: ChoiceCard<WayForm> = {
    value: "server",
    label: t("newApp.how.server"),
    description: t("newApp.how.serverLine"),
    icon: OBJECT_ICONS.service,
    ...proposed("server"),
    detail: <Facts items={[t("newApp.how.serverRollback"), t("newApp.how.serverBuild"), t("newApp.how.serverRuntime")]} />,
  };
  const container: ChoiceCard<WayForm> = {
    value: "container",
    label: t("newApp.how.container"),
    description: t("newApp.how.containerLine"),
    icon: OBJECT_ICONS.container,
    ...proposed("container"),
    detail: (
      <Facts
        items={[
          ...(ways.container.via === "railpack" ? [t("newApp.how.containerRailpack")] : ways.container.via === "dockerfile" ? [t("newApp.how.containerDockerfile")] : []),
          t("newApp.how.containerImages"),
          t("newApp.how.containerVolumes"),
        ]}
      />
    ),
  };
  return only === "server" ? [server] : only === "container" ? [container] : [server, container];
}

/** A way that cannot be (yet): what is in the way, in the server's words, with the fix under it. */
function NotYet({ way, title }: { way: Way; title: string }) {
  const reason = way.reason ?? null;
  if (reason === null) return null;
  return (
    <Notice tone="info" title={title}>
      <span className="block">
        <ServerText coded={reason} text={reason.text} />
      </span>
      {reason.hint !== null && reason.hint !== undefined ? (
        <span className="mt-1 block text-fg-muted">
          <ServerText coded={reason} text={reason.hint} part="hint" />
        </span>
      ) : null}
    </Notice>
  );
}

/**
 * How it runs: directly on the server (a service of the system) or in a container, in the words of the server
 * (`ways` of the inspection). It says which Noust proposes and why, shows both as cards when both can be, and when
 * one cannot says why and what would make it possible; where there is nothing to choose it says so in one sentence
 * and draws no choice. A server that does not say how it runs (a node of 4.2) never gets here.
 */
export function HowItRuns({ ways, form, onChange }: { ways: Ways; form: ReviewForm; onChange: (form: ReviewForm) => void }) {
  const t = useT();
  const chosen = chosenForm(ways, form.appType);
  const stack = ways.container.via === "compose";
  const serverOnly = ways.server.status === "yes" && ways.container.status !== "yes";
  const containerOnly = ways.container.status === "yes" && ways.server.status !== "yes";
  return (
    <Subsection
      title={t("newApp.how.heading")}
      description={<ServerText coded={ways.why} text={ways.why.text} />}
    >
      {ways.choice || serverOnly ? (
        <ChoiceCards<WayForm>
          legend={t("newApp.how.choose")}
          options={cards(t, ways, ways.choice ? null : "server")}
          value={ways.choice ? chosen : "server"}
          onValueChange={(to) => {
            onChange(withForm(form, ways, to));
          }}
          columns={ways.choice ? 2 : 1}
        />
      ) : containerOnly ? (
        <Card padding="sm">
          <div className="flex items-start gap-3">
            <OBJECT_ICONS.container aria-hidden="true" className="mt-0.5 size-icon-lg shrink-0 text-fg-muted" />
            <div className="min-w-0">
              <p className="text-14 font-medium text-fg">{stack ? t("newApp.how.onlyStack") : t("newApp.how.container")}</p>
              <p className="mt-0.5 text-13 text-pretty text-fg-muted">{t("newApp.how.onlyWay")}</p>
            </div>
          </div>
        </Card>
      ) : null}

      {ways.container.status === "no" ? <NotYet way={ways.container} title={t("newApp.how.containerNotYet")} /> : null}
      {ways.server.status === "no" ? <NotYet way={ways.server} title={t("newApp.how.serverNotHere")} /> : null}
      {ways.choice ? <p className="text-12 text-fg-muted">{t("newApp.how.changeLater")}</p> : null}
    </Subsection>
  );
}
