/**
 * The console's translations. Components call `useT()`; code outside React calls
 * `translate(getLocale(), key, params)`. See README.md for how to add strings.
 */

export { useT, bindT } from "./useT";
export type { T } from "./useT";
export { translate, translateRich } from "./translate";
export type { RichParams } from "./translate";
export { catalogFor, isCatalogLoaded, loadCatalog } from "./catalogs";
export { LOCALES } from "./types";
export type { Catalog, Locale, MessageKey, Messages, Params, PlainKey, PluralForms, TranslateArgs } from "./types";
