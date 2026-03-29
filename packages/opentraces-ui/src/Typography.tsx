import { type HTMLAttributes, forwardRef } from "react";
import { cn } from "./lib/cn";

/* ── Section rule (section separator label) ── */
type SectionRuleProps = HTMLAttributes<HTMLDivElement> & {
  label?: string;
  rule?: string;
};

const SectionRule = forwardRef<HTMLDivElement, SectionRuleProps>(
  ({ className, label, rule, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("font-mono text-[11px] text-text-dim mb-8 tracking-wide", className)}
      {...props}
    >
      {label && (
        <span className="font-label text-text-muted uppercase tracking-widest text-[10px]">
          {label}
        </span>
      )}
      {rule && <span>{rule}</span>}
      {children}
    </div>
  ),
);
SectionRule.displayName = "SectionRule";

/* ── Section title ── */
const SectionTitle = forwardRef<HTMLHeadingElement, HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...props }, ref) => (
    <h2
      ref={ref}
      className={cn("font-display font-normal text-[28px] tracking-tight mb-2", className)}
      {...props}
    />
  ),
);
SectionTitle.displayName = "SectionTitle";

/* ── Section subtitle ── */
const SectionSub = forwardRef<HTMLParagraphElement, HTMLAttributes<HTMLParagraphElement>>(
  ({ className, ...props }, ref) => (
    <p
      ref={ref}
      className={cn("text-[13px] text-text-muted max-w-[480px] mb-10", className)}
      {...props}
    />
  ),
);
SectionSub.displayName = "SectionSub";

/* ── Inline code ── */
const InlineCode = forwardRef<HTMLElement, HTMLAttributes<HTMLElement>>(
  ({ className, ...props }, ref) => (
    <code
      ref={ref}
      className={cn("font-mono text-xs bg-surface border border-border px-[5px] py-px text-text-secondary", className)}
      {...props}
    />
  ),
);
InlineCode.displayName = "InlineCode";

export { SectionRule, SectionTitle, SectionSub, InlineCode };
