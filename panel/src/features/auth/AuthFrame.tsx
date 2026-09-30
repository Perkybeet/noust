import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import type { ReactNode } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { focusPageTitle } from "../../app/focus";
import { AuthLayout } from "../../components/page/AuthLayout";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";

export interface AuthFrameProps {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}

/**
 * T7 for every screen before the console: sign in, the checks after it, the first account,
 * an invitation, a sealed central. The machine comes first. Before sign-in the server names
 * itself only by the label its operator chose (`auth.login_label`), never its hostname or
 * version (ENS op.acc.6.7): the page must still tell servers apart without telling a stranger
 * what it runs. The title takes focus on arrival.
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
      footer={footer}
    >
      {children}
    </AuthLayout>
  );
}
