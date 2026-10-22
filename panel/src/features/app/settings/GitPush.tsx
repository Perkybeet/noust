import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { addPushKey, appPushQuery, disableAppPush, enableAppPush, prepareServerForPush, pushKeys, refreshAppPush, removePushKey } from "../../../api/queries/push";
import type { AppPush, PushKey } from "../../../api/queries/push";
import { announce } from "../../../app/Announcer";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Subsection } from "../../../components/page/Subsection";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { CopyValue } from "../../../components/ui/CopyValue";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { TextLink } from "../../../components/ui/TextLink";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { SETTINGS_ICONS } from "./objectIcons";

/** The application types that run as a stack or a container: built from their own files. */
const OWN_FILES = new Set(["docker-compose", "container"]);

/** Whether a push can deploy the application at all: it is on releases and builds from a source. */
export function canPush(app: { layout?: string | null; app_type?: string | null }): "yes" | "inPlace" | "type" {
  if (OWN_FILES.has(app.app_type ?? "")) return "type";
  return app.layout === "releases" ? "yes" : "inPlace";
}

function keyColumns(t: T): Column<PushKey>[] {
  return [
    { id: "name", header: t("common.words.name"), cell: (key) => key.label, card: "title" },
    { id: "type", header: t("gitPush.on.columnType"), cell: (key) => key.key_type, mono: true },
    { id: "fingerprint", header: t("gitPush.on.columnFingerprint"), cell: (key) => key.fingerprint, mono: true },
  ];
}

/** Turning it on: the branch, and optionally a first key. */
function TurnOn({ domain, info }: { domain: string; info: AppPush }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [branch, setBranch] = useState("");
  const [label, setLabel] = useState("");
  const [key, setKey] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const settled = (result: AppPush): void => {
    queryClient.setQueryData(pushKeys.forApp(domain), result);
    announce(t("gitPush.off.turnedOn", { domain }));
  };
  const prepare = useMutation({
    mutationFn: () => prepareServerForPush(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: pushKeys.forApp(domain) });
      announce(t("gitPush.off.prepared"));
    },
    onError: (error: unknown) => reportActionError(t("gitPush.off.prepareFailed"), error),
  });
  const enable = useMutation({
    mutationFn: () =>
      enableAppPush(domain, {
        ...(branch.trim() !== "" ? { branch: branch.trim() } : {}),
        ...(key.trim() !== "" ? { key: key.trim(), label: label.trim() } : {}),
      }),
    onSuccess: settled,
    onError: (error: unknown) => reportActionError(t("gitPush.off.turnOnFailed", { domain }), error),
  });
  const labelError = submitted && key.trim() !== "" && label.trim() === "" ? t("gitPush.off.labelRequired") : undefined;
  const keyError = submitted && label.trim() !== "" && key.trim() === "" ? t("gitPush.off.keyRequired") : undefined;

  return (
    <div className="flex flex-col gap-4">
      {!info.host_ready ? (
        <Notice
          tone="warning"
          title={t("gitPush.off.notReadyTitle")}
          action={
            <Button icon={<SETTINGS_ICONS.prepare aria-hidden="true" />} size="sm" loading={prepare.isPending} onClick={() => prepare.mutate()}>
              {t("gitPush.off.prepare")}
            </Button>
          }
        >
          <div className="flex flex-col gap-2">
            <p>{t("gitPush.off.notReadyBody")}</p>
            {info.problems.length > 0 ? <SystemOutput label={t("gitPush.readFailed")}>{info.problems.join("\n")}</SystemOutput> : null}
          </div>
        </Notice>
      ) : null}
      <Field label={t("gitPush.off.branch")} optional description={t("gitPush.off.branchDescription")} className="sm:max-w-64">
        <Input mono value={branch} placeholder="main" onValueChange={setBranch} autoComplete="off" autoCapitalize="off" spellCheck={false} />
      </Field>
      <Field label={t("gitPush.off.keyLabel")} optional description={t("gitPush.off.keyLabelDescription")} error={labelError} className="sm:max-w-64">
        <Input mono value={label} placeholder="laptop" onValueChange={setLabel} autoComplete="off" autoCapitalize="off" spellCheck={false} />
      </Field>
      <Field label={t("gitPush.off.key")} optional description={t("gitPush.off.keyDescription")} error={keyError}>
        <Textarea mono rows={3} value={key} placeholder="ssh-ed25519 AAAA..." onChange={(event) => setKey(event.target.value)} />
      </Field>
      <div>
        {/* design-exception: submit-disabled-invalid the server must be prepared first, which the notice above says and offers */}
        <Button
          loading={enable.isPending}
          disabled={!info.host_ready}
          onClick={() => {
            setSubmitted(true);
            if (key.trim() !== "" && label.trim() === "") return;
            if (label.trim() !== "" && key.trim() === "") return;
            enable.mutate();
          }}
        >
          {t("gitPush.off.turnOn")}
        </Button>
      </div>
    </div>
  );
}

/** Adding a key to an application that already deploys from push. */
function AddKey({ domain }: { domain: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [label, setLabel] = useState("");
  const [key, setKey] = useState("");
  const add = useMutation({
    mutationFn: () => addPushKey(domain, { label: label.trim(), key: key.trim() }),
    onSuccess: (keys) => {
      void queryClient.invalidateQueries({ queryKey: pushKeys.forApp(domain) });
      setKey("");
      announce(t("gitPush.on.keyAdded", { label: keys.at(-1)?.label ?? label.trim() }));
      setLabel("");
    },
    onError: (error: unknown) => reportActionError(t("gitPush.on.keyAddFailed"), error),
  });
  return (
    <Subsection title={t("gitPush.on.addKey")} description={t("gitPush.on.addKeyDescription")}>
      <Field label={t("gitPush.off.keyLabel")} className="sm:max-w-64">
        <Input mono value={label} placeholder="ci" onValueChange={setLabel} autoComplete="off" autoCapitalize="off" spellCheck={false} />
      </Field>
      <Field label={t("gitPush.off.key")}>
        <Textarea mono rows={3} value={key} placeholder="ssh-ed25519 AAAA..." onChange={(event) => setKey(event.target.value)} />
      </Field>
      <div>
        <Button icon={<SETTINGS_ICONS.add aria-hidden="true" />} size="sm" loading={add.isPending} disabled={label.trim() === "" || key.trim() === ""} onClick={() => add.mutate()}>
          {t("gitPush.on.addKeyAction")}
        </Button>
      </div>
    </Subsection>
  );
}

/** An application that deploys from push: the remote to add, who may push, and turning it off. */
function TurnedOn({ domain, info }: { domain: string; info: AppPush }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [removing, setRemoving] = useState<PushKey | null>(null);
  const [turningOff, setTurningOff] = useState(false);
  const [deleteRepository, setDeleteRepository] = useState(false);
  const refresh = useMutation({
    mutationFn: () => refreshAppPush(domain),
    onSuccess: (result) => {
      queryClient.setQueryData(pushKeys.forApp(domain), result);
      announce(t("gitPush.on.hookRepaired"));
    },
    onError: (error: unknown) => reportActionError(t("gitPush.on.hookRepairFailed"), error),
  });
  const branch = info.branch ?? "main";
  const remote = info.remote ?? "";

  return (
    <div className="flex flex-col gap-5">
      <KeyValueList
        items={[
          { label: t("gitPush.on.branch"), value: branch },
          { label: t("gitPush.on.repository"), value: info.repository ?? "-" },
          {
            label: t("gitPush.on.lastPush"),
            value: info.last_push_at ? <RelativeTime value={info.last_push_at} /> : t("gitPush.on.never"),
            mono: false,
            copy: false,
          },
        ]}
      />
      {!info.hook_ok || info.problems.length > 0 ? (
        <Notice
          tone="warning"
          title={t("gitPush.on.hookTitle")}
          action={
            <Button icon={<SETTINGS_ICONS.prepare aria-hidden="true" />} size="sm" loading={refresh.isPending} onClick={() => refresh.mutate()}>
              {t("gitPush.on.hookRepair")}
            </Button>
          }
        >
          <div className="flex flex-col gap-2">
            <p>{t("gitPush.on.hookBody")}</p>
            {info.problems.length > 0 ? <SystemOutput label={t("gitPush.readFailed")}>{info.problems.join("\n")}</SystemOutput> : null}
          </div>
        </Notice>
      ) : null}
      <Subsection title={t("gitPush.on.stepsTitle")} description={t("gitPush.on.stepsDescription")}>
        <div className="flex flex-col gap-2">
          <CopyValue value={`git remote add noust ${remote}`} label={t("gitPush.on.remote")} />
          <CopyValue value={`git push noust ${branch}`} label={t("gitPush.on.branch")} />
        </div>
      </Subsection>
      <Subsection title={t("gitPush.on.keysTitle")} description={t("gitPush.keyPerApplication")}>
        {info.keys.length === 0 ? (
          <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("gitPush.on.noKeys")}</p>
        ) : (
          <DataTable
            caption={t("gitPush.on.keysCaption")}
            columns={keyColumns(t)}
            rows={info.keys}
            getRowId={(key) => key.label}
            density="compact"
            rowActions={(key) => (
              <Button size="sm" variant="ghost" aria-label={t("gitPush.on.removeKey", { label: key.label })} onClick={() => setRemoving(key)}>
                {t("gitPush.on.removeAction")}
              </Button>
            )}
          />
        )}
      </Subsection>
      <AddKey domain={domain} />
      <div>
        <Button icon={<SETTINGS_ICONS.turnOff aria-hidden="true" />} variant="ghost" onClick={() => setTurningOff(true)}>
          {t("gitPush.on.turnOff")}
        </Button>
      </div>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        friction="simple"
        server={node}
        title={t("gitPush.on.removeTitle", { label: removing?.label ?? "" })}
        description={t("gitPush.on.removeDescription")}
        actionLabel={t("gitPush.on.removeAction")}
        onConfirm={async () => {
          if (removing === null) return;
          await removePushKey(domain, removing.label);
          void queryClient.invalidateQueries({ queryKey: pushKeys.forApp(domain) });
          announce(t("gitPush.on.keyRemoved", { label: removing.label }));
        }}
      />
      <ConfirmDialog
        open={turningOff}
        onOpenChange={setTurningOff}
        friction="simple"
        server={node}
        title={t("gitPush.on.turnOffTitle", { domain })}
        description={t("gitPush.on.turnOffDescription")}
        actionLabel={t("gitPush.on.turnOff")}
        onConfirm={async () => {
          const result = await disableAppPush(domain, deleteRepository);
          queryClient.setQueryData(pushKeys.forApp(domain), result);
          void queryClient.invalidateQueries({ queryKey: ["app", domain], exact: true });
          setDeleteRepository(false);
          announce(t("gitPush.on.turnedOff", { domain }));
        }}
      >
        <Checkbox label={t("gitPush.on.deleteRepository")} checked={deleteRepository} onCheckedChange={setDeleteRepository} />
      </ConfirmDialog>
    </div>
  );
}

/**
 * Push from your computer: deploying with `git push` to a repository on this server. An
 * application on releases can turn it on (with a branch and a first key); it then has a remote
 * to add, the keys that may push, and a hook the console checks. The same as `noust push`.
 */
export function GitPushCard({ app }: { app: { domain: string; layout?: string | null; app_type?: string | null } }) {
  const t = useT();
  const domain = app.domain;
  const fit = canPush(app);
  const view = useQuery({ ...appPushQuery(domain), enabled: fit === "yes" });
  const title = t("gitPush.title");
  const description = t("gitPush.description");

  if (fit !== "yes") {
    return (
      <Card title={title} icon={SETTINGS_ICONS.upload} description={description}>
        <p className="max-w-measure text-13 text-pretty text-fg-muted">
          {fit === "inPlace" ? t("gitPush.unsupportedInPlace") : t("gitPush.unsupportedType", { type: app.app_type ?? "" })}
        </p>
        {fit === "inPlace" ? (
          <TextLink to="/apps/$domain/settings/deploys" params={{ domain }} size="ui">
            {t("appSettings.nav.deploys")}
          </TextLink>
        ) : null}
      </Card>
    );
  }
  if (view.isPending) {
    return (
      <Card title={title} icon={SETTINGS_ICONS.upload} description={description} loading>
        <span className="sr-only">{t("gitPush.loading")}</span>
        <Skeleton className="h-24 w-full rounded-control" />
      </Card>
    );
  }
  if (view.isError) {
    return (
      <Card title={title} icon={SETTINGS_ICONS.upload} description={description}>
        <ErrorBlock compact error={view.error} title={t("gitPush.readFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      </Card>
    );
  }
  return (
    <Card title={title} icon={SETTINGS_ICONS.upload} description={description}>
      {view.data.enabled ? <TurnedOn domain={domain} info={view.data} /> : <TurnOn domain={domain} info={view.data} />}
    </Card>
  );
}
