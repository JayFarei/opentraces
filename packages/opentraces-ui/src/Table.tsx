import { type HTMLAttributes, type ThHTMLAttributes, type TdHTMLAttributes, forwardRef } from "react";
import { cn } from "./lib/cn";

/* ── Wrapper ── */
const TableWrap = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("border border-border", className)} {...props} />
  ),
);
TableWrap.displayName = "TableWrap";

/* ── Head bar ── */
type TableHeadBarProps = HTMLAttributes<HTMLDivElement> & { title?: string };

const TableHeadBar = forwardRef<HTMLDivElement, TableHeadBarProps>(
  ({ className, title, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("flex justify-between items-center px-4 py-2.5 border-b border-border bg-surface", className)}
      {...props}
    >
      {title && <span className="font-mono text-xs font-medium">{title}</span>}
      {children}
    </div>
  ),
);
TableHeadBar.displayName = "TableHeadBar";

/* ── Table ── */
const Table = forwardRef<HTMLTableElement, HTMLAttributes<HTMLTableElement>>(
  ({ className, ...props }, ref) => (
    <table
      ref={ref}
      className={cn("w-full border-collapse font-mono text-xs", className)}
      {...props}
    />
  ),
);
Table.displayName = "Table";

/* ── Th ── */
const Th = forwardRef<HTMLTableCellElement, ThHTMLAttributes<HTMLTableCellElement>>(
  ({ className, ...props }, ref) => (
    <th
      ref={ref}
      className={cn(
        "text-[10px] tracking-wider uppercase text-text-dim text-left px-4 py-2 border-b border-border bg-surface font-normal",
        className,
      )}
      {...props}
    />
  ),
);
Th.displayName = "Th";

/* ── Td ── */
const Td = forwardRef<HTMLTableCellElement, TdHTMLAttributes<HTMLTableCellElement>>(
  ({ className, ...props }, ref) => (
    <td
      ref={ref}
      className={cn("px-4 py-2 border-b border-border text-text-secondary", className)}
      {...props}
    />
  ),
);
Td.displayName = "Td";

export { TableWrap, TableHeadBar, Table, Th, Td };
