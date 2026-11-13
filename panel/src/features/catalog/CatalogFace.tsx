import { useNavigate, useSearch } from "@tanstack/react-router";
import { useMemo } from "react";

import { inPlace } from "../../app/searchNavigation";
import { CatalogPage } from "./CatalogPage";
import { validateCatalogSearch } from "./model";

/**
 * The catalog seen from "All servers": the same page, because the recipes are the same on every server and nobody answers for them
 * (item 196). Its filters are kept in the address like the page's own, and using a recipe asks which server first, in the wizard it opens.
 */
export function CatalogFace() {
  const raw: Record<string, unknown> = useSearch({ strict: false });
  const navigate = useNavigate();
  const search = useMemo(() => validateCatalogSearch(raw), [raw]);
  return <CatalogPage search={search} onSearchChange={(next, options) => void navigate({ to: ".", search: next, ...inPlace(options) } as never)} />;
}
