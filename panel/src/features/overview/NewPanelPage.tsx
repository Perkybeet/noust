import { Link, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { DashboardPage } from "../../components/page/DashboardPage";
import { Button, buttonClassName } from "../../components/ui/Button";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Field } from "../../components/ui/Field";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Input } from "../../components/ui/Input";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useCan } from "../../components/widgets/useCan";
import type { PanelScope } from "../../components/widgets/types";
import { useT } from "../../i18n";
import { useHasFleet } from "../../nodes/servers";
import { useNode } from "../../nodes/useNode";
import { usePanels } from "./usePanels";

/** The most characters a panel's name has (the server's own limit). */
const NAME_MAX = 60;

/**
 * Where a person makes a panel: its name, what it follows (the server chosen in the selector, or the whole fleet, when
 * there is a fleet to follow) and whether it begins as a copy of the one that comes with the console or empty. It ends
 * in the panel, customized, so the next thing a person does is choose its widgets.
 */
export function NewPanelPage({ scope: opened }: { scope: PanelScope }) {
  const t = useT();
  const navigate = useNavigate();
  const can = useCan();
  const hasFleet = useHasFleet();
  const panels = usePanels();
  const { node } = useNode();
  const { data: hostname } = useQuery({ ...sessionQuery(), select: (session) => session.hostname });
  const [name, setName] = useState(t("overview.panels.nameDefault"));
  const [scope, setScope] = useState<PanelScope>(opened);
  const [start, setStart] = useState<"copy" | "empty">("copy");
  const [problem, setProblem] = useState<{ field: "name" | "limit"; text: string } | null>(null);
  const nameField = useRef<HTMLInputElement>(null);
  const followsFleet = hasFleet && can("fleet.read");
  const chosen: PanelScope = followsFleet ? scope : "server";

  const submit = (event: SyntheticEvent): void => {
    event.preventDefault();
    const trimmed = name.trim();
    if (trimmed === "") {
      setProblem({ field: "name", text: t("overview.panels.nameRequired") });
      nameField.current?.focus();
      return;
    }
    const made = panels.create({ name: trimmed, scope: chosen, start });
    if (made === null) {
      setProblem({ field: "limit", text: t("overview.panels.limit", { count: panels.limits.dashboards }) });
      return;
    }
    void navigate({ to: chosen === "fleet" ? "/fleet" : "/", search: { panel: made.id, customise: true } as never });
  };

  return (
    <DashboardPage header={{ title: t("overview.panels.newTitle"), description: t(panels.persistence === "browser" ? "overview.panels.newDescriptionHere" : "overview.panels.newDescription") }}>
      <form onSubmit={submit} noValidate className="flex max-w-wizard flex-col gap-6">
        <Field label={t("common.words.name")} error={problem?.field === "name" ? problem.text : null}>
          <Input
            ref={nameField}
            value={name}
            maxLength={NAME_MAX}
            autoComplete="off"
            onValueChange={(value: string) => {
              setName(value);
              if (problem?.field === "name") setProblem(null);
            }}
          />
        </Field>
        {followsFleet ? (
          <ChoiceCards
            legend={t("overview.panels.scopeLegend")}
            value={scope}
            onValueChange={setScope}
            options={[
              { value: "server", label: t("overview.panels.scopeServer"), description: t("overview.panels.scopeServerDescription", { server: node ?? hostname ?? "" }), icon: OBJECT_ICONS.server },
              { value: "fleet", label: t("overview.panels.scopeFleet"), description: t("overview.panels.scopeFleetDescription"), icon: OBJECT_ICONS.fleetSettings },
            ]}
          />
        ) : null}
        <ChoiceCards
          legend={t("overview.panels.startLegend")}
          value={start}
          onValueChange={setStart}
          options={[
            { value: "copy", label: t("overview.panels.startCopy", { name: t(chosen === "fleet" ? "overview.panels.fleet" : "overview.panels.server") }), description: t("overview.panels.startCopyDescription") },
            { value: "empty", label: t("overview.panels.startEmpty"), description: t("overview.panels.startEmptyDescription") },
          ]}
        />
        {problem?.field === "limit" ? <InlineMessage tone="error">{problem.text}</InlineMessage> : null}
        <div className="flex gap-2">
          <Button type="submit" variant="primary">
            {t("overview.panels.create")}
          </Button>
          <Link to={opened === "fleet" ? "/fleet" : "/"} search={{} as never} className={buttonClassName("secondary")}>
            {t("common.words.cancel")}
          </Link>
        </div>
      </form>
    </DashboardPage>
  );
}
