import { CommandHint } from "../../components/page/CommandHint";
import { Subsection } from "../../components/page/Subsection";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Field } from "../../components/ui/Field";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { DEFAULT_PORTS, RESTART_LABELS, RESTART_POLICIES, argField, argNameField, rowId, secretField, secretIdField, volumeField } from "./model";
import type { ContainerDraft, ContainerErrors, Method, RestartPolicy } from "./model";
import { SecretField } from "./SecretField";

export interface ContainerFieldsProps {
  draft: ContainerDraft;
  errors: ContainerErrors;
  onChange: (draft: ContainerDraft) => void;
  /**
   * An application that already exists: its method can change between a Dockerfile and
   * Railpack and never to or from an image, a build secret it keeps is listed by its id, and a
   * registry password it keeps is left alone unless a new one is typed.
   */
  existing?: boolean;
  /** Show the image's own field: the settings page does, the wizard asked for it on the source step. */
  showImage?: boolean;
  /** What the process most likely listens on, read off the Dockerfile, for the port's hint. */
  suggestedPort?: number | null;
}

/**
 * What the server refused about a whole group (the build arguments, the secrets, the volumes)
 * rather than one of its rows: said above the group, in the server's words.
 */
function ErrorLine({ message }: { message: string | undefined }) {
  if (message === undefined) return null;
  return <Notice tone="error">{message}</Notice>;
}

function ArgsEditor({ draft, errors, onChange }: Pick<ContainerFieldsProps, "draft" | "errors" | "onChange">) {
  const t = useT();
  const railpack = draft.method === "railpack";
  const set = (buildArgs: ContainerDraft["buildArgs"]): void => onChange({ ...draft, buildArgs });
  return (
    <Subsection title={t(railpack ? "containers.args.titleRailpack" : "containers.args.title")} description={t(railpack ? "containers.args.descriptionRailpack" : "containers.args.description")}>
      <div className="flex flex-col gap-4">
        <ErrorLine message={errors["build_args"]} />
        {draft.buildArgs.map((row) => (
          <div key={row.id} className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start">
            <Field label={t("common.words.name")} error={errors[argNameField(row)]} className="sm:w-56 sm:shrink-0">
              <Input
                mono
                value={row.name}
                placeholder={railpack ? "RAILPACK_NODE_VERSION" : "NODE_ENV"}
                onValueChange={(name: string) => set(draft.buildArgs.map((other) => (other.id === row.id ? { ...other, name } : other)))}
                autoComplete="off"
                autoCapitalize="characters"
                spellCheck={false}
              />
            </Field>
            <Field
              label={t("containers.args.value")}
              error={errors[argField(row)]}
              className="min-w-0 flex-1"
              action={
                <IconButton
                  label={row.name.trim() === "" ? t("containers.args.removeEmpty") : t("common.words.removeNamed", { name: row.name.trim() })}
                  icon={<ICONS.delete />}
                  onClick={() => set(draft.buildArgs.filter((other) => other.id !== row.id))}
                />
              }
            >
              <Input
                mono
                value={row.value}
                onValueChange={(value: string) => set(draft.buildArgs.map((other) => (other.id === row.id ? { ...other, value } : other)))}
                autoComplete="off"
                spellCheck={false}
              />
            </Field>
          </div>
        ))}
        <div>
          <Button size="sm" icon={<ICONS.add aria-hidden="true" />} onClick={() => set([...draft.buildArgs, { id: rowId("arg"), name: "", value: "" }])}>
            {t("containers.args.add")}
          </Button>
        </div>
      </div>
    </Subsection>
  );
}

function SecretsEditor({ draft, errors, onChange }: Pick<ContainerFieldsProps, "draft" | "errors" | "onChange">) {
  const t = useT();
  const railpack = draft.method === "railpack";
  const set = (buildSecrets: ContainerDraft["buildSecrets"]): void => onChange({ ...draft, buildSecrets });
  return (
    <Subsection title={t("containers.secrets.title")} description={t(railpack ? "containers.secrets.descriptionRailpack" : "containers.secrets.description")}>
      <div className="flex flex-col gap-4">
        <ErrorLine message={errors["build_secrets"]} />
        {draft.buildSecrets.map((row) =>
          row.stored ? (
            <div key={row.id} className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-control border border-border px-3 py-2">
              <span className="flex min-w-0 flex-col gap-0.5">
                <Mono>{row.secretId}</Mono>
                <span className="text-12 text-fg-muted">{row.remove ? t("containers.secrets.willRemove") : t("containers.secrets.stored")}</span>
              </span>
              <Button
                size="sm"
                variant="ghost"
                aria-label={t(row.remove ? "containers.secrets.keep" : "containers.secrets.removeStored", { id: row.secretId })}
                onClick={() => set(draft.buildSecrets.map((other) => (other.id === row.id ? { ...other, remove: !other.remove } : other)))}
              >
                {t(row.remove ? "containers.secrets.undo" : "common.words.remove")}
              </Button>
            </div>
          ) : (
            <div key={row.id} className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start">
              <Field label={t("containers.secrets.id")} error={errors[secretIdField(row)]} className="sm:w-56 sm:shrink-0">
                <Input
                  mono
                  value={row.secretId}
                  placeholder={railpack ? "NPM_TOKEN" : "npmrc"}
                  onValueChange={(secretId: string) => set(draft.buildSecrets.map((other) => (other.id === row.id ? { ...other, secretId } : other)))}
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                />
              </Field>
              <Field
                label={t("containers.secrets.value")}
                error={errors[secretField(row)]}
                className="min-w-0 flex-1"
                action={
                  <IconButton
                    label={row.secretId.trim() === "" ? t("containers.secrets.removeEmpty") : t("containers.secrets.remove", { id: row.secretId.trim() })}
                    icon={<ICONS.delete />}
                    onClick={() => set(draft.buildSecrets.filter((other) => other.id !== row.id))}
                  />
                }
              >
                <SecretField
                  name={row.secretId.trim() === "" ? t("containers.secrets.valueName") : row.secretId.trim()}
                  value={row.value}
                  onValueChange={(value) => set(draft.buildSecrets.map((other) => (other.id === row.id ? { ...other, value } : other)))}
                />
              </Field>
            </div>
          ),
        )}
        <div>
          <Button
            size="sm"
            icon={<ICONS.add aria-hidden="true" />}
            onClick={() => set([...draft.buildSecrets, { id: rowId("secret"), secretId: "", value: "", stored: false, remove: false }])}
          >
            {t("containers.secrets.add")}
          </Button>
        </div>
      </div>
    </Subsection>
  );
}

function VolumesEditor({ draft, errors, onChange }: Pick<ContainerFieldsProps, "draft" | "errors" | "onChange">) {
  const t = useT();
  const set = (volumes: ContainerDraft["volumes"]): void => onChange({ ...draft, volumes });
  return (
    <Subsection title={t("containers.volumes.title")} description={t("containers.volumes.description")}>
      <div className="flex flex-col gap-3">
        <ErrorLine message={errors["volumes"]} />
        {draft.volumes.map((row) => (
          <Field
            key={row.id}
            label={t("containers.volumes.path")}
            error={errors[volumeField(row)]}
            action={
              <IconButton
                label={row.value.trim() === "" ? t("containers.volumes.removeEmpty") : t("containers.volumes.remove", { path: row.value.trim() })}
                icon={<ICONS.delete />}
                onClick={() => set(draft.volumes.filter((other) => other.id !== row.id))}
              />
            }
          >
            <Input
              mono
              value={row.value}
              placeholder="/data"
              onValueChange={(value: string) => set(draft.volumes.map((other) => (other.id === row.id ? { ...other, value } : other)))}
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
            />
          </Field>
        ))}
        <div>
          <Button size="sm" icon={<ICONS.add aria-hidden="true" />} onClick={() => set([...draft.volumes, { id: rowId("volume"), value: "" }])}>
            {t("containers.volumes.add")}
          </Button>
        </div>
      </div>
    </Subsection>
  );
}

function RegistryEditor({ draft, errors, onChange, existing }: Pick<ContainerFieldsProps, "draft" | "errors" | "onChange" | "existing">) {
  const t = useT();
  const kept = existing === true && draft.registryPasswordSet;
  return (
    <Subsection title={t("containers.registry.title")} description={t("containers.registry.description")}>
      <div className="flex flex-col gap-5">
        {draft.clearRegistry ? <InlineMessage>{t("containers.registry.willClear")}</InlineMessage> : null}
        <Field label={t("containers.registry.server")} optional error={errors["registry_server"]} description={t("containers.registry.serverDescription")} className="sm:max-w-96">
          <Input
            mono
            value={draft.registryServer}
            placeholder="ghcr.io"
            disabled={draft.clearRegistry}
            onValueChange={(registryServer: string) => onChange({ ...draft, registryServer })}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
          />
        </Field>
        <Field label={t("containers.registry.user")} optional error={errors["registry_username"]} className="sm:max-w-96">
          <Input
            mono
            value={draft.registryUsername}
            disabled={draft.clearRegistry}
            onValueChange={(registryUsername: string) => onChange({ ...draft, registryUsername })}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
          />
        </Field>
        <Field
          label={t("containers.registry.password")}
          optional
          error={errors["registry_password"]}
          description={kept ? t("containers.registry.passwordKept") : t("containers.registry.passwordDescription")}
          className="sm:max-w-96"
        >
          <SecretField
            name={t("containers.registry.passwordName")}
            value={draft.registryPassword}
            kept={kept}
            disabled={draft.clearRegistry}
            onValueChange={(registryPassword) => onChange({ ...draft, registryPassword })}
          />
        </Field>
        {existing === true && (draft.registryUsername.trim() !== "" || draft.registryPasswordSet) ? (
          <Checkbox checked={draft.clearRegistry} onCheckedChange={(clearRegistry) => onChange({ ...draft, clearRegistry })} label={t("containers.registry.clear")} />
        ) : null}
      </div>
    </Subsection>
  );
}

/**
 * How a container application is built and run, as fields: the way its image comes to exist
 * (a Dockerfile, Railpack, or a registry image), the Dockerfile, context and stage, build
 * arguments and build secrets, the port the process listens on, the volumes that keep its
 * data, how it restarts, and the account of a private registry. The same fields for the wizard
 * and for the settings of an application that exists, so the two cannot disagree about what a
 * container is.
 *
 * Nothing secret is ever drawn: the registry password and the values of build secrets are
 * typed into write-only fields and, once saved, only their existence is shown.
 */
export function ContainerFields({ draft, errors, onChange, existing = false, showImage = false, suggestedPort = null }: ContainerFieldsProps) {
  const t = useT();
  const set = (patch: Partial<ContainerDraft>): void => onChange({ ...draft, ...patch });
  const image = draft.method === "image";
  const builds = !image;
  const fallback = DEFAULT_PORTS[draft.method];

  return (
    <div className="flex flex-col gap-6">
      {builds ? (
        <div className="flex flex-col gap-3">
          <ChoiceCards<Exclude<Method, "image">>
            legend={t("containers.method.label")}
            options={[
              { value: "dockerfile", label: t("containers.method.dockerfile"), description: t("containers.method.dockerfileDescription") },
              { value: "railpack", label: t("containers.method.railpack"), description: t("containers.method.railpackDescription") },
            ]}
            value={draft.method === "railpack" ? "railpack" : "dockerfile"}
            onValueChange={(method) => set({ method })}
          />
          {draft.method === "railpack" ? (
            <div className="flex flex-col gap-2">
              <Notice title={t("containers.method.railpackNeeds")}>{t("containers.method.railpackNeedsBody")}</Notice>
              <CommandHint command="noust setup railpack" label={t("common.words.fromTerminal")} />
            </div>
          ) : null}
        </div>
      ) : null}

      {image && showImage ? (
        <Field label={t("containers.fields.image")} error={errors["image"]} description={t("containers.fields.imageDescription")}>
          <Input
            mono
            value={draft.image}
            placeholder="ghcr.io/owner/app:1.4"
            onValueChange={(value: string) => set({ image: value })}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
          />
        </Field>
      ) : null}

      {builds ? (
        <div className="flex flex-col gap-5">
          {draft.method === "dockerfile" ? (
            <div className="grid gap-5 sm:grid-cols-2">
              <Field label={t("containers.fields.dockerfile")} optional error={errors["dockerfile"]} description={t("containers.fields.dockerfileDescription")}>
                <Input mono value={draft.dockerfile} placeholder="Dockerfile" onValueChange={(value: string) => set({ dockerfile: value })} autoComplete="off" autoCapitalize="off" spellCheck={false} />
              </Field>
              <Field label={t("containers.fields.target")} optional error={errors["target"]} description={t("containers.fields.targetDescription")}>
                <Input mono value={draft.target} placeholder="runtime" onValueChange={(value: string) => set({ target: value })} autoComplete="off" autoCapitalize="off" spellCheck={false} />
              </Field>
            </div>
          ) : null}
          <Field label={t("containers.fields.context")} optional error={errors["context"]} description={t("containers.fields.contextDescription")} className="sm:max-w-96">
            <Input mono value={draft.context} placeholder="." onValueChange={(value: string) => set({ context: value })} autoComplete="off" autoCapitalize="off" spellCheck={false} />
          </Field>
        </div>
      ) : null}

      <div className="grid gap-5 sm:grid-cols-2">
        <Field
          label={t("containers.fields.port")}
          optional
          error={errors["container_port"]}
          description={
            suggestedPort !== null && draft.containerPort.trim() === ""
              ? t("containers.fields.portSuggested", { port: String(suggestedPort) })
              : t("containers.fields.portDescription", { port: String(fallback) })
          }
        >
          <Input mono inputMode="numeric" value={draft.containerPort} placeholder={String(suggestedPort ?? fallback)} onValueChange={(value: string) => set({ containerPort: value })} autoComplete="off" />
        </Field>
        <Field label={t("containers.fields.restart")} nativeLabel={false} error={errors["restart_policy"]} description={t("containers.fields.restartDescription")}>
          <Select<RestartPolicy>
            options={RESTART_POLICIES.map((policy) => ({ value: policy, label: t(RESTART_LABELS[policy]) }))}
            value={draft.restartPolicy}
            onValueChange={(restartPolicy) => set({ restartPolicy })}
            className="w-full"
          />
        </Field>
      </div>

      {builds ? <ArgsEditor draft={draft} errors={errors} onChange={onChange} /> : null}
      {builds ? <SecretsEditor draft={draft} errors={errors} onChange={onChange} /> : null}
      <VolumesEditor draft={draft} errors={errors} onChange={onChange} />
      {image ? <RegistryEditor draft={draft} errors={errors} onChange={onChange} existing={existing} /> : null}
    </div>
  );
}
