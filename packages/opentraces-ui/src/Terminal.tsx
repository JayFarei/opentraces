import { type HTMLAttributes, type ReactNode, forwardRef } from "react";
import { cn } from "./lib/cn";

/* ── Root ── */
type TerminalProps = HTMLAttributes<HTMLDivElement>;

const Terminal = forwardRef<HTMLDivElement, TerminalProps>(
  ({ className, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("border border-border bg-bg-alt font-mono text-xs", className)}
      {...props}
    >
      {children}
    </div>
  ),
);
Terminal.displayName = "Terminal";

/* ── Bar ── */
type TerminalBarProps = HTMLAttributes<HTMLDivElement> & {
  left?: ReactNode;
  right?: ReactNode;
};

const TerminalBar = forwardRef<HTMLDivElement, TerminalBarProps>(
  ({ className, left, right, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "flex items-center justify-between px-3 py-1.5 border-b border-border bg-surface text-[11px] text-text-dim",
        className,
      )}
      {...props}
    >
      {left && <div className="flex gap-3">{left}</div>}
      {children}
      {right && <div>{right}</div>}
    </div>
  ),
);
TerminalBar.displayName = "TerminalBar";

/* ── Body ── */
type TerminalBodyProps = HTMLAttributes<HTMLDivElement>;

const TerminalBody = forwardRef<HTMLDivElement, TerminalBodyProps>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("p-4 leading-[1.9] text-text-muted", className)}
      {...props}
    />
  ),
);
TerminalBody.displayName = "TerminalBody";

/* ── Tab ── */
type TerminalTabProps = HTMLAttributes<HTMLButtonElement> & {
  active?: boolean;
};

const TerminalTab = forwardRef<HTMLButtonElement, TerminalTabProps>(
  ({ className, active, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        "text-text-dim cursor-pointer py-0.5 border-b border-transparent transition-all",
        active && "text-text-secondary border-b-accent",
        !active && "hover:text-text-muted",
        className,
      )}
      {...props}
    />
  ),
);
TerminalTab.displayName = "TerminalTab";

export {
  Terminal,
  TerminalBar,
  TerminalBody,
  TerminalTab,
  type TerminalProps,
  type TerminalBarProps,
  type TerminalBodyProps,
  type TerminalTabProps,
};
