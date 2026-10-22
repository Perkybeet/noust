import { useQuery } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";

import { serverKeyQuery } from "../../api/queries/deployKeys";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { CopyValue } from "../../components/ui/CopyValue";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { useT } from "../../i18n";
import { deployKeysPage, sshRepository } from "./sshSource";

export interface SshKeyHelpProps {
  /** The SSH address that could not be cloned. */
  source: string;
  /** Reads the repository again, once the key is where it belongs. */
  onCheckAgain: () => void;
}

/**
 * What to do when a private repository over SSH cannot be cloned, without leaving the wizard: the
 * public key this server signs in with, the page of the repository where it is added, and a button
 * that reads the repository again. It sits under git's own words, which stay as they were. The
 * key is read-only here: making one is `noust setup ssh --generate`, which writes into the home
 * of the account Noust runs as and is not a button.
 */
export function SshKeyHelp({ source, onCheckAgain }: SshKeyHelpProps) {
  const t = useT();
  const repository = sshRepository(source);
  const key = useQuery({ ...serverKeyQuery(), retry: false });
  const page = repository === null ? null : deployKeysPage(repository);
  const publicKey = key.data?.public_key ?? null;
  // A principal that may not read the server's key (a team member) is told nothing of it: git's
  // words above and "Check again" still serve.
  const known = key.data !== undefined;
  return (
    <Card padding="sm">
      <div className="flex min-w-0 flex-col gap-3">
        <p className="text-13 font-medium text-fg">{t("newApp.ssh.heading")}</p>
        {!known ? null : publicKey !== null ? (
          <>
            <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("newApp.ssh.intro")}</p>
            <CopyValue value={publicKey} label={t("newApp.ssh.copy")} name={t("newApp.ssh.keyName")} />
          </>
        ) : (
          <>
            <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("newApp.ssh.noKey")}</p>
            <CopyValue value="noust setup ssh --generate" label={t("newApp.ssh.copyCommand")} name={t("newApp.ssh.commandName")} />
          </>
        )}
        <div className="flex flex-wrap items-center gap-3">
          {page !== null ? (
            <ExternalLink href={page.href} button="secondary" size="sm">
              {t("newApp.ssh.openDeployKeys", { host: page.host })}
            </ExternalLink>
          ) : null}
          <Button size="sm" variant={page === null ? "secondary" : "ghost"} icon={<RotateCw />} onClick={onCheckAgain}>
            {t("newApp.ssh.checkAgain")}
          </Button>
        </div>
      </div>
    </Card>
  );
}
