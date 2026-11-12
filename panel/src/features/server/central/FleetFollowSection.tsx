/**
 * What a server does with the settings the central carries (item 166): it follows them, which is the normal way, or it is
 * independent and is sent nothing. It is decided here, on the server's own page, and nowhere else, off by default; a value of
 * one setting for this server alone is made from the setting. Before it is decided the page says what will change, from what the
 * central already knows of the server.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { sessionQuery } from "../../../api/queries/auth";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { Can } from "../../../components/page/permissions";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { ApplyDialog } from "../../settings/fleet/ApplyDialog";
import { fleetSettingsKeys, fleetStatusQuery, setServerMode } from "../../settings/fleet/data";
import { faceOf, returnPlan, serverSummary } from "../../settings/fleet/faces";
import { KINDS_WITH_NAMES } from "../../settings/fleet/onlyHere";
import { useOnlyHere } from "../../settings/fleet/useOnlyHere";
import type { OnlyHereRow } from "../../settings/fleet/useOnlyHere";

type Way = "managed" | "independent";

/** The names of the settings of the fleet a server is in a given face for, as a row of names. */
function Names({ names }: { names: readonly string[] }) {
  return (
    <span className="flex flex-wrap gap-x-3">
      {names.map((name) => (
        <Mono key={name} tone="default">
          {name}
        </Mono>
      ))}
    </span>
  );
}

/** What only the server has, each with the kind it is of: the fleet does not know it, and a setting of the fleet never touches it. */
function OnlyNames({ rows }: { rows: readonly OnlyHereRow[] }) {
  const t = useT();
  return (
    <span className="flex flex-wrap gap-x-3 gap-y-1">
      {rows.map((row) => (
        <span key={`${row.kind}/${row.name}`} className="inline-flex items-baseline gap-1.5">
          <span className="text-12 text-fg-muted">{t(`fleetSettings.kind.${row.kind}`)}</span>
          <Mono tone="default">{row.name}</Mono>
        </span>
      ))}
    </span>
  );
}

export function FleetFollowSection({ node }: { node: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const status = useQuery(fleetStatusQuery());
  const { data: session } = useQuery(sessionQuery());
  const canManage = session?.permissions?.includes("fleet.manage") ?? false;
  const server = status.data?.servers.find((entry) => entry.node === node);
  const saved: Way = server?.mode === "independent" ? "independent" : "managed";
  // What the person has chosen and not confirmed; until they choose, the card says what the server does.
  const [chosen, setChosen] = useState<Way | null>(null);
  const way = chosen ?? saved;
  const [adopt, setAdopt] = useState(false);
  const [applying, setApplying] = useState(false);
  const change = useMutation({
    mutationFn: () => setServerMode(node, way, adopt),
    onSuccess: () => {
      setChosen(null);
      setAdopt(false);
      void queryClient.invalidateQueries({ queryKey: fleetSettingsKeys.all });
    },
    onError: (error) => {
      reportActionError(t("fleetSettings.servers.switchFailed", { name: node }), error);
      setChosen(null);
    },
  });

  // What the server keeps for itself: read from the server, so it is there when the fleet's own rows say nothing of it.
  const { rows: onlyHere } = useOnlyHere(KINDS_WITH_NAMES, node);

  if (status.data === undefined || server === undefined) {
    return status.isPending ? <Skeleton className="h-40 w-full rounded-card" /> : null;
  }
  const rows = status.data.settings;
  const summary = serverSummary(node, rows);
  const back = returnPlan(node, rows);
  const changed = way !== saved;
  const names = (face: "exception" | "changedHere" | "waiting"): string[] =>
    rows
      .filter((row) => {
        const cell = row.servers.find((candidate) => candidate.node === node);
        if (cell === undefined) return false;
        return face === "waiting" ? serverSummary(node, [row]).waiting > 0 : faceOf(cell) === face;
      })
      .map((row) => row.name);
  const exceptions = saved === "independent" ? [] : names("exception");
  const changedHere = saved === "independent" ? [] : names("changedHere");
  const waiting = saved === "independent" ? [] : names("waiting");
  const up = server.outcome === "ok";
  const detail = (items: string[]) => <ul className="flex list-disc flex-col gap-1 pl-4 text-13 text-fg-muted">{items.map((item) => <li key={item}>{item}</li>)}</ul>;

  // The one condition that stands in the way of this server, said once: a ceiling that does not allow changes, an older Noust, or silence.
  const condition = saved === "independent" ? null : server.outcome === "forbidden" ? "refused" : server.outcome === "unsupported" ? "unsupported" : !up ? "off" : null;
  const banner =
    condition === null ? null : (
      <Notice
        variant="banner"
        tone={condition === "unsupported" ? "info" : "warning"}
        title={t(condition === "refused" ? "fleetSettings.ficha.refusedTitle" : condition === "unsupported" ? "fleetSettings.ficha.unsupportedTitle" : "fleetSettings.ficha.offTitle", { server: node })}
      >
        <div className="flex flex-col gap-2">
          <p className="max-w-measure text-pretty">
            {condition === "refused"
              ? t("fleetSettings.ficha.refusedBody")
              : condition === "unsupported"
                ? t("fleetSettings.ficha.unsupportedBody")
                : waiting.length > 0
                  ? t("fleetSettings.ficha.offWaiting", { count: waiting.length, names: waiting.join(", ") })
                  : t("fleetSettings.ficha.offNothing")}
          </p>
          {condition === "refused" ? <CommandHint label={t("fleetSettings.ficha.onServer", { server: node })} command="noust fleet authorize --access admin" reason="otro-servidor" /> : null}
        </div>
      </Notice>
    );

  return (
    <>
      {banner}
      <SurfaceSection
        title={t("fleetSettings.ficha.title")}
        icon={OBJECT_ICONS.central}
        description={t("fleetSettings.ficha.description", { server: node })}
        status={
          saved === "independent" ? (
            <StatusPill state="stopped" label={t("fleetSettings.face.independent")} size="sm" />
          ) : (
            <StatusPill state="running" label={t("fleetSettings.ficha.follows")} size="sm" />
          )
        }
      >
        <div className="flex flex-col gap-5">
          <ChoiceCards<Way>
            legend={t("fleetSettings.ficha.legend")}
            value={way}
            onValueChange={(next) => {
              if (canManage) setChosen(next);
            }}
            options={[
              {
                value: "managed",
                label: t("fleetSettings.ficha.followLabel"),
                description: t("fleetSettings.ficha.followHelp"),
                detail: detail([t("fleetSettings.ficha.followA"), t("fleetSettings.ficha.followB"), t("fleetSettings.ficha.followC")]),
              },
              {
                value: "independent",
                label: t("fleetSettings.face.independent"),
                description: t("fleetSettings.ficha.independentHelp"),
                detail: detail([t("fleetSettings.ficha.independentA"), t("fleetSettings.ficha.independentB"), t("fleetSettings.ficha.independentC")]),
              },
            ]}
          />
          {changed ? (
            <Notice
              title={way === "independent" ? t("fleetSettings.ficha.leaving", { server: node }) : t("fleetSettings.ficha.returning", { server: node })}
              action={
                <span className="flex gap-2">
                  <Button size="sm" variant="secondary" disabled={change.isPending} onClick={() => setChosen(null)}>
                    {t("common.words.cancel")}
                  </Button>
                  <Can permission="fleet.manage">
                    <Button size="sm" variant="primary" loading={change.isPending} onClick={() => change.mutate()}>
                      {way === "independent" ? t("fleetSettings.ficha.makeIndependent") : t("fleetSettings.ficha.followAgain")}
                    </Button>
                  </Can>
                </span>
              }
            >
              <div className="flex flex-col gap-3">
                <p className="max-w-measure text-pretty">
                  {way === "independent" ? t("fleetSettings.ficha.leavingPlan", { count: summary.total }) : t("fleetSettings.ficha.returningPlan", { count: back.receives, same: back.same })}
                </p>
                {way === "managed" ? <Checkbox label={t("fleetSettings.ficha.adopt")} description={t("fleetSettings.ficha.adoptHelp")} checked={adopt} onCheckedChange={setAdopt} /> : null}
              </div>
            </Notice>
          ) : null}
        </div>
      </SurfaceSection>
      {saved === "managed" ? (
        <SurfaceSection
          title={t("fleetSettings.ficha.holds")}
          icon={OBJECT_ICONS.server}
          description={t("fleetSettings.ficha.holdsHelp")}
          actions={
            canManage && (waiting.length > 0 || changedHere.length > 0) ? (
              <Button size="sm" variant="secondary" disabled={!up} onClick={() => setApplying(true)}>
                {t("fleetSettings.panel.applyNow")}
              </Button>
            ) : undefined
          }
        >
          <KeyValueList
            items={[
              { label: t("fleetSettings.ficha.followsOf"), value: t("fleetSettings.ficha.followsCount", { follows: summary.follows, total: summary.total }), mono: false, copy: false },
              ...(exceptions.length > 0 ? [{ label: t("fleetSettings.ficha.exceptions"), value: <Names names={exceptions} />, mono: false, copy: false as const }] : []),
              ...(changedHere.length > 0 ? [{ label: t("fleetSettings.face.changedHere"), value: <Names names={changedHere} />, mono: false, copy: false as const }] : []),
              ...(waiting.length > 0 ? [{ label: t("fleetSettings.ficha.waiting"), value: <Names names={waiting} />, mono: false, copy: false as const }] : []),
              ...(onlyHere.length > 0 ? [{ label: t("fleetSettings.ficha.onlyHere"), value: <OnlyNames rows={onlyHere} />, mono: false, copy: false as const }] : []),
            ]}
          />
        </SurfaceSection>
      ) : null}
      {applying ? <ApplyDialog nodes={[node]} onClose={() => setApplying(false)} /> : null}
    </>
  );
}
