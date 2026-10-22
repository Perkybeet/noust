import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { moveApp } from "../../api/queries/teams";
import { announce } from "../../app/Announcer";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import { DestinationFields } from "./DestinationFields";
import { refreshPlacement } from "./refresh";
import { useDestination } from "./useDestination";
import { useOrganization } from "./useOrganization";

/** What the move needs to know about an application: its domain and where it lives now. */
export interface MovableApp {
  domain: string;
  team?: string | null | undefined;
  project?: string | null | undefined;
  environment?: string | null | undefined;
}

function MoveForm({ app, onClose }: { app: MovableApp; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const org = useOrganization();
  const formId = useId();
  const here = {
    ...(app.team != null ? { team: app.team } : {}),
    ...(app.project != null ? { project: app.project } : {}),
    ...(app.environment != null ? { environment: app.environment } : {}),
  };
  const destination = useDestination(here);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const place = destination.value;
  const unchanged = place === null || (place.team === app.team && place.project === app.project && place.environment === app.environment);
  const where = place === null ? "" : `${org.labels.team(place.team)} / ${org.labels.project(place.team, place.project)} / ${org.labels.environment(place.team, place.project, place.environment)}`;

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (pending || place === null || unchanged) return;
    setPending(true);
    setError(null);
    moveApp(app.domain, { team: place.team, project: place.project, environment: place.environment }).then(
      async () => {
        await refreshPlacement(queryClient, app.domain);
        setPending(false);
        announce(t("teams.move.moved", { domain: app.domain, where }));
        onClose();
      },
      (failure: unknown) => {
        setPending(false);
        setError(failure);
      },
    );
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !pending) onClose();
      }}
      size="md"
      title={t("teams.move.title", { domain: app.domain })}
      description={t("teams.move.description")}
      footer={
        <>
          <Button disabled={pending} onClick={onClose}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={pending} disabled={unchanged}>
            {t("teams.move.submit")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {error !== null ? <ErrorBlock live compact error={error} title={t("teams.move.failed", { domain: app.domain })} /> : null}
        <p className="text-13 text-fg-muted">
          {t.rich("teams.move.now", {
            where: <Mono tone="default">{[app.team, app.project, app.environment].filter((part) => part != null).map(String).join(" / ")}</Mono>,
          })}
        </p>
        {/* Moving may cross teams whichever one the console is on; a new application only goes to the one it works in. */}
        <DestinationFields destination={{ ...destination, needed: true, showTeam: destination.teams.length > 1 }} help={t("teams.move.help")} />
      </form>
    </Dialog>
  );
}

export interface MoveApp {
  /** Whether there is somewhere to move it to that the operator may manage. */
  available: boolean;
  open: () => void;
  dialog: ReactNode;
}

/**
 * "Move to another project or environment" for one application. It is offered only where the
 * move means something: the server has more than one project or environment and the operator
 * may manage applications in the team it lives in. The move takes the application's sites, its
 * databases and its backup schedule with it, in one step, and restarts nothing.
 */
export function useMoveApp(app: MovableApp | undefined): MoveApp {
  const org = useOrganization();
  const [opened, setOpened] = useState(false);
  const available = app !== undefined && org.available && (org.projects.length > 1 || org.environments.length > 1) && org.can("apps.manage", app.team ?? null);
  return {
    available,
    open: () => setOpened(true),
    dialog: opened && app !== undefined ? <MoveForm app={app} onClose={() => setOpened(false)} /> : null,
  };
}
