import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const money = (v: number | null | undefined) => (v == null ? "n/a" : `$${v.toFixed(2)}`);
export const pct = (v: number | null | undefined) => (v == null ? "n/a" : `${(v * 100).toFixed(2)}%`);
