import { type HTMLAttributes, forwardRef } from "react";
import { cn } from "./lib/cn";

type BoxProps = HTMLAttributes<HTMLDivElement> & {
  label?: string;
};

const Box = forwardRef<HTMLDivElement, BoxProps>(
  ({ className, label, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("border border-border bg-surface relative", className)}
      data-label={label}
      {...props}
    >
      {label && (
        <span className="absolute -top-px left-3 -translate-y-1/2 bg-bg px-2 font-mono text-[10px] tracking-wider uppercase text-text-muted">
          {label}
        </span>
      )}
      {children}
    </div>
  ),
);
Box.displayName = "Box";

export { Box, type BoxProps };
