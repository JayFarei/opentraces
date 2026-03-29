import { type HTMLAttributes, forwardRef } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "./lib/cn";

const badge = cva("inline-block font-mono text-[10px] px-2 py-0.5 border", {
  variants: {
    variant: {
      ok: "border-green-dim text-green bg-green-bg",
      error: "border-red text-red bg-red-bg",
      warning: "border-yellow text-yellow bg-yellow-bg",
      accent: "border-accent-dim text-accent bg-accent-bg",
      info: "border-blue text-blue bg-blue-bg",
    },
  },
  defaultVariants: {
    variant: "ok",
  },
});

type BadgeProps = HTMLAttributes<HTMLSpanElement> &
  VariantProps<typeof badge>;

const Badge = forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, variant, ...props }, ref) => (
    <span ref={ref} className={cn(badge({ variant }), className)} {...props} />
  ),
);
Badge.displayName = "Badge";

export { Badge, badge, type BadgeProps };
