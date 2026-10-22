import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { focusPageTitle } from "../../app/focus";
import { AuthLayout } from "../../components/page/AuthLayout";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";

export interface AuthFrameProps {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}

/** Where the licence is read: the file the repository carries, as About links it. */
const LICENSE_URL = "https://github.com/Perkybeet/noust/blob/main/LICENSE";

/**
 * The notice the AGPL-3.0 asks an interactive interface to display (section 5, d): who holds the
 * copyright, and under which licence the program is, with the licence one link away. It is the
 * same on every screen before the console, which is the first one a person sees.
 */
function LegalLine() {
  const t = useT();
  return (
    <p data-slot="legal" className="text-12 text-pretty text-fg-faint">
      {t.rich("auth.legal.line", {
        license: (
          <ExternalLink key="license" inline href={LICENSE_URL}>
            {t("auth.legal.license")}
          </ExternalLink>
        ),
      })}
    </p>
  );
}

/**
 * T7 for every screen before the console: sign in, the checks after it, the first account,
 * an invitation, a sealed central. The machine comes first. Before sign-in the server names
 * itself only by the label its operator chose (`auth.login_label`), never its hostname or
 * version (ENS op.acc.6.7): the page must still tell servers apart without telling a stranger
 * what it runs. The title takes focus on arrival. The licence notice closes every one.
 */
export function AuthFrame({ title, description, children, footer }: AuthFrameProps) {
  const t = useT();
  const { data: session, isPending } = useQuery(sessionQuery());

  // Once per screen: the title is where a screen reader starts reading a new one.
  useEffect(() => {
    focusPageTitle();
  }, [title]);

  const name = session?.authenticated === true ? session.hostname : (session?.login_label ?? null);
  // Without a label of its operator's, the server says nothing of itself before sign-in.
  const host = isPending ? <Skeleton className="h-3.5 w-32" /> : name !== null && name !== "" ? name : undefined;
  const version = session?.authenticated === true && session.version !== "" ? session.version : null;
  return (
    <AuthLayout
      title={title}
      description={description}
      {...(host !== undefined ? { host } : {})}
      subtitle={version === null ? t("auth.frame.product") : t("auth.frame.productVersion", { version })}
      footer={
        <div className="flex flex-col gap-6">
          {footer}
          <LegalLine />
        </div>
      }
    >
      {children}
    </AuthLayout>
  );
}
