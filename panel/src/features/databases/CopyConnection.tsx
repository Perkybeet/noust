import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import { connectQuery } from "../../api/queries/databases";
import { IconButton } from "../../components/ui/IconButton";
import { useCopyState } from "../../components/ui/useCopyState";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";

/**
 * Copies the connection string an application uses for a database, in one press. The password is Noust's to
 * show in sudo mode (`POST .../url`), so the console asks to confirm it is you when that window is closed and goes
 * on by itself. A database whose password Noust does not keep, or that no application uses, opens its Connect tab
 * instead: that is where it says how to reach it. It is on the database's row and in its page's header: the one
 * place a person looks for it whichever they are on.
 */
export function CopyConnection({ engine, name, variant = "ghost", size = "sm" }: { engine: string; name: string; variant?: "ghost" | "secondary"; size?: "sm" | "md" }) {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { state, copy } = useCopyState();
  const [busy, setBusy] = useState(false);
  const press = async (): Promise<void> => {
    setBusy(true);
    try {
      const connect = await queryClient.query(connectQuery(engine, name, {}));
      const link = connect.apps[0];
      if (link === undefined || !connect.password_known) {
        void navigate({ to: "/databases/$engine/$name/connect", params: { engine, name } });
        return;
      }
      const shown = await request("post", "/api/apps/{domain}/databases/{engine}/{name}/url", { params: { domain: link.domain, engine, name } });
      await copy(shown.url);
    } catch (error) {
      reportActionError(t("databases.listRow.copyFailed", { name }), error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <IconButton
        label={t("databases.listRow.copyConnection", { name })}
        icon={state === "copied" ? <Check className="text-ok" /> : <Copy />}
        variant={variant}
        size={size}
        disabled={busy}
        onClick={() => void press()}
      />
      <span role="status" className="sr-only">
        {state === "copied" ? t("common.copyButton.copied") : state === "failed" ? t("common.copyButton.failed") : ""}
      </span>
    </>
  );
}
