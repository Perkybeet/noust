import { CopyValue } from "../../components/ui/CopyValue";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";
import { machineUrl } from "./machineAddress";

export interface MachineAddressProps {
  /** The name Noust calls the application by, and the port of the machine it opens on. */
  name: string;
  port: string;
  onChange: (patch: { domain?: string; port?: string }) => void;
  errors: { domain?: string | undefined; port?: string | undefined };
  /** Which ports the console knows are taken, for the line that says so. */
  taken: ReadonlyMap<number, string>;
  /** A Compose stack publishes what its own file says: the port is read, not asked. `null` is a stack that publishes none. */
  stack?: { port: string | null } | undefined;
  /** What the application will be called as one asks for the port only (a recipe: Noust picks the port). */
  nameOnly?: boolean;
}

/**
 * How a new application opens on a machine with no web server of Noust's: a name for Noust to call it by and a port of the machine,
 * with the address that port makes already written. Nothing here is a domain: ports 80 and 443 are the machine's.
 */
export function MachineAddress({ name, port, onChange, errors, taken, stack, nameOnly = false }: MachineAddressProps) {
  const t = useT();
  const host = window.location.hostname;
  const used = [...taken.keys()].sort((a, b) => a - b);
  return (
    <div className="flex flex-col gap-5">
      <Field label={t("common.words.name")} error={errors.domain} description={t("containerHost.wizard.nameDescription")} className="sm:max-w-96">
        <Input mono value={name} onValueChange={(next: string) => onChange({ domain: next })} placeholder="my-app" autoComplete="off" autoCapitalize="off" spellCheck={false} />
      </Field>
      {nameOnly ? null : stack !== undefined ? (
        <div className="flex flex-col gap-2">
          <p className="text-13 font-medium text-fg">{t("containerHost.wizard.stackPortLabel")}</p>
          <p className="max-w-measure text-13 text-pretty text-fg-muted">{stack.port === null ? t("containerHost.wizard.stackNoPort") : t("containerHost.wizard.stackPort")}</p>
          {stack.port !== null ? <CopyValue value={machineUrl(host, stack.port)} label={t("containerHost.publish.local.copy")} name={t("containerHost.wizard.willOpen")} /> : null}
        </div>
      ) : (
        <>
          <Field label={t("containerHost.wizard.portLabel")} error={errors.port} description={t("containerHost.wizard.portDescription")} className="sm:max-w-64">
            <Input mono inputMode="numeric" value={port} onValueChange={(next: string) => onChange({ port: next })} autoComplete="off" />
          </Field>
          <div className="flex flex-col gap-2">
            <p className="text-13 font-medium text-fg">{t("containerHost.wizard.willOpen")}</p>
            <CopyValue value={machineUrl(host, port)} label={t("containerHost.publish.local.copy")} name={t("containerHost.wizard.willOpen")} />
            {used.length > 0 ? <p className="text-12 text-fg-muted">{t("containerHost.wizard.taken", { ports: used.join(", ") })}</p> : null}
          </div>
        </>
      )}
    </div>
  );
}
