import { ChevronLeft, ChevronRight } from "lucide-react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatCount } from "../../lib/format";
import { IconButton } from "./IconButton";
import { PAGE_SIZES } from "./pageSize";
import type { PageSize } from "./pageSize";
import { Select } from "./Select";

export interface PageWindow {
  /** The first row shown, counted from 1; 0 when there are none. */
  from: number;
  /** The last row shown. */
  to: number;
  /** How many pages there are, at least one. */
  pages: number;
  /** The page shown, from 0, brought back into range. */
  page: number;
}

/**
 * Which rows a page holds. A page that is out of range (the list shrank under the operator, a
 * filter narrowed it) is the last one, never an empty screen; `all` and a size larger than the
 * list are one page.
 */
export function pageWindow(total: number, size: PageSize, page: number): PageWindow {
  if (total <= 0) return { from: 0, to: 0, pages: 1, page: 0 };
  const per = size === "all" ? total : size;
  const pages = Math.max(1, Math.ceil(total / per));
  const shown = Math.min(Math.max(0, page), pages - 1);
  return { from: shown * per + 1, to: Math.min(total, (shown + 1) * per), pages, page: shown };
}

export interface TablePaginationProps {
  /** Rows in the whole list, not on this page. */
  total: number;
  /** The page shown, from 0. */
  page: number;
  pageSize: PageSize;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: PageSize) => void;
  className?: string;
}

/**
 * Under a list: which rows are on screen out of how many, how many to a page (20, 50, 100 or
 * all), and the way to the next and the previous. The size is the operator's, kept per list
 * (`usePageSize`); `DataTable` draws this itself when it is given a `pagination` id.
 */
export function TablePagination({ total, page, pageSize, onPageChange, onPageSizeChange, className }: TablePaginationProps) {
  const t = useT();
  const shown = pageWindow(total, pageSize, page);
  const options = PAGE_SIZES.map((size) => ({ value: String(size), label: size === "all" ? t("common.pagination.all") : formatCount(size, t.locale) }));
  return (
    <div role="group" aria-label={t("common.pagination.label")} className={cx("flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-12 text-fg-muted", className)}>
      <div className="flex items-center gap-2">
        <span aria-hidden="true">{t("common.pagination.rowsPerPage")}</span>
        <Select
          compact
          size="sm"
          aria-label={t("common.pagination.rowsPerPage")}
          options={options}
          value={String(pageSize)}
          onValueChange={(next) => {
            const size = PAGE_SIZES.find((candidate) => String(candidate) === next);
            if (size !== undefined) onPageSizeChange(size);
          }}
        />
      </div>
      <div className="flex items-center gap-2">
        <span className="tabular-nums">
          {t("common.pagination.showing", { from: formatCount(shown.from, t.locale), to: formatCount(shown.to, t.locale), total: formatCount(total, t.locale) })}
        </span>
        <span role="status" className="sr-only">{t("common.pagination.page", { page: String(shown.page + 1), pages: String(shown.pages) })}</span>
        <IconButton
          variant="secondary"
          size="sm"
          label={t("common.pagination.previous")}
          icon={<ChevronLeft aria-hidden="true" />}
          disabled={shown.page === 0}
          onClick={() => onPageChange(shown.page - 1)}
        />
        <IconButton
          variant="secondary"
          size="sm"
          label={t("common.pagination.next")}
          icon={<ChevronRight aria-hidden="true" />}
          disabled={shown.page >= shown.pages - 1}
          onClick={() => onPageChange(shown.page + 1)}
        />
      </div>
    </div>
  );
}
