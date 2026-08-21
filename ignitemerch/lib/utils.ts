import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatPrice(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: value % 1 === 0 ? 0 : 2,
  }).format(value);
}

export function formatNumber(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

export function calculateTrendingScore(input: {
  purchases: number;
  views: number;
  createdAt: Date;
}) {
  const ageHours = Math.max(
    1,
    (Date.now() - input.createdAt.getTime()) / 3_600_000,
  );
  const conversion = input.views === 0 ? 0 : input.purchases / input.views;
  return (input.purchases * 8 + conversion * 40) / Math.sqrt(ageHours);
}

export function bytesLabel(sizeBytes: number) {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${Math.round(sizeBytes / 1024)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}
