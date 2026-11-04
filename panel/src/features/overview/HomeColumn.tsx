import type { JSX } from "react";

import { ColumnHead } from "../../app/frame/ColumnHead";
import { useT } from "../../i18n";

/**
 * The column of Home: where the panels are listed, and, while Home is being customised, the gallery of
 * widgets. This is the frame's stand-in with the contract's signature (lane INICIO replaces it with the
 * real one): the frame draws it for every page of Home, in the column's own shell, and decides nothing
 * about what is in it.
 */
export const HomeColumn: (props: { customising: boolean }) => JSX.Element = () => {
  const t = useT();
  return <ColumnHead title={t("nav.overview.label")} />;
};
