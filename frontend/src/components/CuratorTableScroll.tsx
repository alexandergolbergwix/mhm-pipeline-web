import type {HTMLAttributes, ReactNode} from "react";

/** 70vh cap — keeps the horizontal scrollbar pinned inside the table viewport. */
export const CURATOR_TABLE_SCROLL_CLASS =
  "curator-table-scroll max-h-[min(70vh,720px)] overflow-auto";

export interface CuratorTableScrollProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  bordered?: boolean;
}

export function CuratorTableScroll({
  children,
  className = "",
  bordered = true,
  ...rest
}: CuratorTableScrollProps) {
  const border = bordered ? "border border-white/5 rounded-lg" : "";
  return (
    <div className={`${CURATOR_TABLE_SCROLL_CLASS} ${border} ${className}`.trim()} {...rest}>
      {children}
    </div>
  );
}
