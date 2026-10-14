import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold", {
  variants: {
    variant: {
      default: "border-transparent bg-secondary text-secondary-foreground",
      PASS: "border-transparent bg-pass text-white",
      FIX: "border-transparent bg-fix text-black",
      REJECT: "border-transparent bg-reject text-white",
      outline: "text-foreground",
    },
  },
  defaultVariants: { variant: "default" },
});

export function Badge({
  className,
  variant,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export function Verdict({ v }: { v?: string | null }) {
  const variant = v === "PASS" || v === "FIX" || v === "REJECT" ? v : "default";
  return <Badge variant={variant}>{v ?? "?"}</Badge>;
}
