import { Eye, EyeOff, Link2 } from "lucide-react";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { ErrorBlock } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { CopyButton } from "../../components/ui/CopyButton";
import { Field } from "../../components/ui/Field";
import { IconButton } from "../../components/ui/IconButton";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";
import { useDatabaseActions } from "./useDatabaseActions";

/**
 * Builds a connection string from credentials the operator already has - nothing here is read
 * from the server, it only formats what is typed the way this engine expects it. The result
 * embeds the password, so it stays masked until revealed, like every other secret in the console.
 */
export function ConnectionString({ engine, database }: { engine: string; database: string }) {
  const t = useT();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [host, setHost] = useState("localhost");
  const [revealed, setRevealed] = useState(false);
  const { buildConnectionString } = useDatabaseActions();

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (username.trim() === "" || password === "") return;
    setRevealed(false);
    buildConnectionString.mutate({ engine, database, username: username.trim(), password, host: host.trim() });
  };

  const value = buildConnectionString.data?.connection_string ?? null;

  return (
    <Section title={t("databases.connectionString.title")} description={t("databases.connectionString.description")}>
      <div className="flex flex-col gap-4 rounded-card border border-border bg-surface p-4 shadow-raised">
        <form onSubmit={submit} className="grid max-w-4xl gap-3 sm:grid-cols-3">
          <Field label={t("databases.fields.username")}>
            <Input mono value={username} onValueChange={setUsername} autoComplete="off" spellCheck={false} />
          </Field>
          <Field label={t("databases.fields.password")}>
            <Input mono type="password" value={password} onValueChange={setPassword} autoComplete="off" />
          </Field>
          <Field label={t("databases.fields.host")}>
            <Input mono value={host} onValueChange={setHost} autoComplete="off" spellCheck={false} />
          </Field>
          <div className="sm:col-span-3">
            <Button
              type="submit"
              icon={<Link2 aria-hidden="true" />}
              loading={buildConnectionString.isPending}
              disabled={username.trim() === "" || password === ""}
            >
              {t("databases.connectionString.build")}
            </Button>
          </div>
        </form>
        {value !== null ? (
          <Field label={t("databases.connectionString.title")}>
            <Input
              readOnly
              mono
              type={revealed ? "text" : "password"}
              value={value}
              suffix={
                <>
                  <IconButton
                    label={revealed ? t("databases.connectionString.hide") : t("databases.connectionString.reveal")}
                    icon={revealed ? <EyeOff /> : <Eye />}
                    size="sm"
                    onClick={() => {
                      setRevealed((current) => !current);
                    }}
                  />
                  <CopyButton value={value} label={t("databases.connectionString.copy")} />
                </>
              }
            />
          </Field>
        ) : null}
        {buildConnectionString.isError ? (
          <ErrorBlock compact error={buildConnectionString.error} title={t("databases.connectionString.buildFailed")} />
        ) : null}
      </div>
    </Section>
  );
}
