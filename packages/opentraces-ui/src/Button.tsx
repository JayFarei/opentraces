import { type ButtonHTMLAttributes, forwardRef } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "./lib/cn";

const button = cva(
  "inline-flex items-center gap-1.5 font-mono text-xs border cursor-pointer transition-all no-underline leading-snug",
  {
    variants: {
      variant: {
        primary: "bg-text text-bg border-text hover:opacity-85",
        outline: "bg-transparent text-text-secondary border-border-strong hover:border-text-muted hover:text-text",
        accent: "bg-accent text-black border-accent font-medium hover:opacity-90",
        ghost: "bg-transparent text-text-muted border-transparent hover:text-text",
      },
      size: {
        default: "px-[18px] py-2.5 min-h-11",
        sm: "text-[11px] px-3 py-[5px]",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "default",
    },
  },
);

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof button>;

const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(button({ variant, size }), className)}
      {...props}
    />
  ),
);
Button.displayName = "Button";

export { Button, button, type ButtonProps };
