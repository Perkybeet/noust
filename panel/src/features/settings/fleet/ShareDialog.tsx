import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { registryLoginsQuery } from "../../../api/queries/registries";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { PlainKey } from "../../../i18n";
import { focusFirstInvalid, splitErrors } from "../formErrors";
import { kindOptions, needsServer } from "../registries/registries";
import { fleetSettingsKeys, saveSetting } from "./data";
import type { FleetSetting, FleetSettingKind } from "./data";

const FIELDS = ["name", "value", "scope", "kind", "server", "username", "password", "secret"] as const;

/** What a secret shows as, and what is sent back to keep the one stored. */
const KEPT = "***";

type Value = Record<string, unknown>;

const KIND_WORDS: Readonly<Record<string, PlainKey>> = {
  channel: "fleetSettings.kind.channel",
  alert_rule: "fleetSettings.kind.alert_rule",
  backup_destination: "fleetSettings.kind.backup_destination",
  registry: "fleetSettings.kind.registry",
  variable: "fleetSettings.kind.variable",
  console_link: "fleetSettings.kind.console_link",
};

export function kindWord(t: (key: PlainKey) => string, key: string, fallback: string): string {
  const found = KIND_WORDS[key];
  return found === undefined ? fallback : t(found);
}

export interface ShareDialogProps {
  kinds: readonly FleetSettingKind[];
  /** A setting being changed: the fleet's value, or one server's exception. Null shares a new one. */
  setting: FleetSetting | null;
  /** The servers an exception can be for. */
  servers: readonly string[];
  /** Start on a server's exception rather than on the fleet's value. */
  node?: string | null;
  onClose: () => void;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Sharing a setting with the fleet, or changing the value of one (item 90). The value is the body of the server's
 * own route for it, so a variable and a registry account have their fields and the rest (a channel, an alert rule,
 * a backup destination) their JSON, with the server judging the content and naming the field it refuses. A secret
 * shows as `***` and is kept as it was until another is typed. For one server it is an exception: that server
 * gets this value and the others keep following the fleet.
 */
export function ShareDialog({ kinds, setting, servers, node = null, onClose }: ShareDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const available = kinds.filter((kind) => kind.available);
  const editing = setting !== null;
  const registries = useQuery(registryLoginsQuery());
  const [kind, setKind] = useState(setting?.resource ?? available[0]?.key ?? "variable");
  const [name, setName] = useState(setting?.name ?? "");
  const [scope, setScope] = useState(node !== null ? `node:${node}` : (setting?.scope ?? "fleet"));
  const initial: Value = setting?.value ?? {};
  const [variableValue, setVariableValue] = useState(text(initial["value"]));
  const [secret, setSecret] = useState(initial["secret"] === true);
  const [registryKind, setRegistryKind] = useState(text(initial["kind"]) || "dockerhub");
  const [server, setServer] = useState(text(initial["server"]));
  const [username, setUsername] = useState(text(initial["username"]));
  const [password, setPassword] = useState(text(initial["password"]));
  const [json, setJson] = useState(setting === null ? "{}" : JSON.stringify(initial, null, 2));
  const [apply, setApply] = useState(true);
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [tried, setTried] = useState(false);

  const body = (): Value | null => {
    if (kind === "variable") return { value: variableValue, secret };
    if (kind === "registry") return { kind: registryKind, username: username.trim(), password, ...(server.trim() === "" ? {} : { server: server.trim() }) };
    try {
      const parsed: unknown = JSON.parse(json);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error("not an object");
      return parsed as Value;
    } catch {
      setJsonError(t("fleetSettings.share.valueNotObject"));
      return null;
    }
  };

  const save = useMutation({
    mutationFn: (value: Value) => saveSetting(kind, name.trim(), { value, scope, apply }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: fleetSettingsKeys.all });
      onClose();
    },
  });
  const errors = splitErrors(save.error, FIELDS);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (save.isPending) return;
    setJsonError(null);
    // The button is never off for a missing value: it says what is missing, where, and goes there.
    if (name.trim() === "") {
      setTried(true);
      focusFirstInvalid(formId);
      return;
    }
    const value = body();
    if (value !== null) save.mutate(value);
  };

  const exception = scope !== "fleet";
  const title = setting === null ? t("fleetSettings.share.title") : exception ? t("fleetSettings.share.exceptionTitle", { name: setting.name, server: scope.replace(/^node:/, "") }) : t("fleetSettings.share.editTitle", { name: setting.name });

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="md"
      title={title}
      description={exception ? t("fleetSettings.share.exceptionDescription", { server: scope.replace(/^node:/, "") }) : t("fleetSettings.share.description")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {apply ? t("fleetSettings.share.saveAndApply") : t("common.words.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("fleetSettings.share.failed")} /> : null}
        {!editing ? (
          <Field label={t("fleetSettings.share.kind")} nativeLabel={false}>
            <Select options={available.map((entry) => ({ value: entry.key, label: kindWord(t, entry.key, entry.title) }))} value={kind} onValueChange={setKind} />
          </Field>
        ) : null}
        {!editing ? (
          <Field label={t("common.words.name")} description={t("fleetSettings.share.nameHelp")} error={errors.fields.name ?? (tried && name.trim() === "" ? t("settings.shared.required") : undefined)}>
            <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={128} value={name} onValueChange={setName} />
          </Field>
        ) : null}
        {editing && servers.length > 0 ? (
          <Field label={t("fleetSettings.share.for")} nativeLabel={false}>
            <Select
              options={[{ value: "fleet", label: t("fleetSettings.share.everyServer") }, ...servers.map((entry) => ({ value: `node:${entry}`, label: entry }))]}
              value={scope}
              onValueChange={setScope}
            />
          </Field>
        ) : null}
        {kind === "variable" ? (
          <>
            <Field label={t("fleetSettings.share.variable.value")} error={errors.fields.value}>
              <Textarea rows={2} spellCheck={false} value={variableValue} onChange={(event) => setVariableValue(event.target.value)} />
            </Field>
            <Checkbox label={t("fleetSettings.share.variable.secret")} description={t("fleetSettings.share.variable.secretHelp")} checked={secret} onCheckedChange={setSecret} />
          </>
        ) : kind === "registry" ? (
          <>
            <Field label={t("fleetSettings.share.registry.kind")} nativeLabel={false} error={errors.fields.kind}>
              <Select options={kindOptions(t, registries.data?.kinds ?? ["dockerhub", "ghcr", "gitlab", "ecr", "acr", "gar", "custom"])} value={registryKind} onValueChange={setRegistryKind} />
            </Field>
            <Field label={t("fleetSettings.share.registry.server")} optional={!needsServer(registryKind)} error={errors.fields.server}>
              <Input mono autoComplete="off" spellCheck={false} maxLength={300} value={server} onValueChange={setServer} />
            </Field>
            <Field label={t("fleetSettings.share.registry.username")} error={errors.fields.username}>
              <Input mono autoComplete="off" spellCheck={false} maxLength={256} value={username} onValueChange={setUsername} />
            </Field>
            <Field label={t("fleetSettings.share.registry.password")} description={password === KEPT ? t("fleetSettings.share.secretKept") : undefined} error={errors.fields.password}>
              <Input mono type="password" autoComplete="new-password" spellCheck={false} maxLength={8192} value={password} onValueChange={setPassword} />
            </Field>
          </>
        ) : (
          <Field label={t("fleetSettings.share.valueLabel")} description={`${t("fleetSettings.share.valueHelp")} ${t("fleetSettings.share.secretKept")}`} error={jsonError ?? errors.fields.value}>
            <Textarea mono rows={9} spellCheck={false} value={json} onChange={(event) => setJson(event.target.value)} />
          </Field>
        )}
        <Checkbox label={t("fleetSettings.share.apply")} checked={apply} onCheckedChange={setApply} />
      </form>
    </Dialog>
  );
}
