import { readdirSync, readFileSync, statSync } from "fs";
import path from "path";
import { zipSync } from "fflate";

export const BUNDLE_SLUG = "ignite-press-bundle";

export const BUNDLE_KIT_SLUGS = [
  "heat-press-photo-desk",
  "ugc-floor",
  "n8n-lead-stripe",
  "cursor-agency-skill-drop",
] as const;

export interface KitFile {
  relativePath: string;
  publicUrl: string;
  name: string;
  fileType: string;
  sizeBytes: number;
  absolutePath: string;
}

export function kitsRoot() {
  return path.join(process.cwd(), "content", "kits");
}

export function kitDir(slug: string) {
  return path.join(kitsRoot(), slug);
}

export function listKitFiles(slug: string): KitFile[] {
  const root = kitDir(slug);
  const files: KitFile[] = [];

  function walk(current: string) {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      if (entry.name === ".DS_Store") continue;
      const absolutePath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        walk(absolutePath);
        continue;
      }
      const relativePath = path
        .relative(root, absolutePath)
        .split(path.sep)
        .join("/");
      files.push({
        relativePath,
        publicUrl: relativePath,
        name: relativePath,
        fileType: mimeFromPath(relativePath),
        sizeBytes: statSync(absolutePath).size,
        absolutePath,
      });
    }
  }

  walk(root);
  return files.sort((a, b) => a.relativePath.localeCompare(b.relativePath));
}

export function zipKit(slug: string) {
  const files: Record<string, Uint8Array> = {};

  if (slug === BUNDLE_SLUG) {
    for (const file of listKitFiles(BUNDLE_SLUG)) {
      files[`${BUNDLE_SLUG}/${file.relativePath}`] = readFileBytes(
        file.absolutePath,
      );
    }
    for (const nested of BUNDLE_KIT_SLUGS) {
      for (const file of listKitFiles(nested)) {
        files[`${nested}/${file.relativePath}`] = readFileBytes(file.absolutePath);
      }
    }
    return zipSync(files);
  }

  for (const file of listKitFiles(slug)) {
    files[`${slug}/${file.relativePath}`] = readFileBytes(file.absolutePath);
  }
  return zipSync(files);
}

function readFileBytes(absolutePath: string) {
  return new Uint8Array(readFileSync(absolutePath));
}

export function mimeFromPath(relativePath: string) {
  const ext = path.extname(relativePath).toLowerCase();
  switch (ext) {
    case ".md":
    case ".mdc":
      return "text/markdown";
    case ".json":
      return "application/json";
    case ".csv":
      return "text/csv";
    case ".sh":
      return "text/x-shellscript";
    case ".txt":
      return "text/plain";
    case ".example":
      return "text/plain";
    default:
      return "application/octet-stream";
  }
}
