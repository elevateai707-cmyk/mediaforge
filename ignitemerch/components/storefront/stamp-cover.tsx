import { cn } from "@/lib/utils";

interface StampCoverProps {
  lotNumber: string;
  name: string;
  tone: string;
  className?: string;
}

export function StampCover({ lotNumber, name, tone, className }: StampCoverProps) {
  const toneClass = coverToneClass(tone);
  return (
    <div
      className={cn(
        "relative isolate overflow-hidden",
        toneClass.bg,
        className,
      )}
    >
      <div className="press-grain absolute inset-0 opacity-30 mix-blend-multiply" />
      <div className={cn("absolute inset-x-0 top-0 h-px", toneClass.rule)} />
      <div className={cn("absolute inset-y-0 left-0 w-px", toneClass.rule)} />
      <p
        className={cn(
          "absolute left-4 top-4 font-mono text-[10px] uppercase tracking-[0.22em]",
          toneClass.meta,
        )}
      >
        Lot {lotNumber}
      </p>
      <p
        className={cn(
          "absolute right-4 top-4 font-mono text-[10px] uppercase tracking-[0.22em]",
          toneClass.meta,
        )}
      >
        Press
      </p>
      <h3
        className={cn(
          "absolute inset-x-4 bottom-4 font-display text-[clamp(1.8rem,4vw,3.4rem)] font-extrabold uppercase leading-[0.86] tracking-tight",
          toneClass.title,
        )}
      >
        {name}
      </h3>
    </div>
  );
}

type CoverTone = "ember" | "rust" | "bone" | "soot";

function isCoverTone(value: string): value is CoverTone {
  return value === "ember" || value === "rust" || value === "bone" || value === "soot";
}

function coverToneClass(tone: string) {
  const resolved: CoverTone = isCoverTone(tone) ? tone : "ember";
  switch (resolved) {
    case "rust":
      return {
        bg: "bg-[oklch(0.42_0.12_42)]",
        title: "text-bone",
        meta: "text-bone/70",
        rule: "bg-bone/25",
      };
    case "bone":
      return {
        bg: "bg-[oklch(0.84_0.04_80)]",
        title: "text-soot",
        meta: "text-soot/65",
        rule: "bg-soot/20",
      };
    case "soot":
      return {
        bg: "bg-[oklch(0.22_0.03_46)]",
        title: "text-bone",
        meta: "text-bone/70",
        rule: "bg-bone/20",
      };
    case "ember":
      return {
        bg: "bg-[oklch(0.52_0.16_48)]",
        title: "text-bone",
        meta: "text-bone/75",
        rule: "bg-bone/25",
      };
    default: {
      const _never: never = resolved;
      return _never;
    }
  }
}
