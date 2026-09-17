import { API_BASE } from "./api";
export async function editorRequest<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const r = await fetch(API_BASE + path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  return data;
}
export interface Word {
  start: number;
  end: number;
  text: string;
}
export interface Cue extends Word {
  words: Word[];
  style?: TextStyle;
}
export interface TextStyle {
  preset: "clean" | "bold" | "active";
  font: "DejaVu Sans" | "Liberation Sans";
  size: number;
  color: string;
  outline: number;
  background: boolean;
  position: "bottom" | "center" | "top";
  max_lines: number;
  margin: number;
}
export interface ProjectClip {
  uid: string;
  asset_id: number;
  start: number;
  end: number;
  speed: number;
  transition: "cut" | "crossfade";
}
export interface AudioTrack {
  enabled: boolean;
  path: string | null;
  volume: number;
  start: number;
  fade_in: number;
  fade_out: number;
}
export interface Project {
  schema_version: 2;
  revision: number;
  clips: ProjectClip[];
  speech_captions: boolean;
  on_video_text: boolean;
  captions: Record<string, Cue[]>;
  narration_captions: Cue[];
  caption_source: "original" | "narration";
  overlays: Record<string, Cue[]>;
  caption_style: TextStyle;
  original_audio: AudioTrack;
  voice_over: AudioTrack;
  music: AudioTrack;
  ducking: boolean;
  ducking_ratio: number;
  post_title: string;
  post_description: string;
  hashtags: string;
  narration_script: string;
  generated_assets: string[];
  export: {
    ratio: "9:16" | "1:1" | "16:9";
    width: number;
    fps: number;
    codec: "libx264" | "h264_nvenc";
    clean_master: boolean;
    sidecars: ("srt" | "vtt" | "ass")[];
    hdr: "tonemap" | "reject";
  };
}
export interface ProjectEnvelope {
  project: Project;
  approved_revision: number | null;
}
export interface EditorJob {
  id: string;
  status: string;
  progress: number;
  message: string;
  meta?: {
    plan_id?: string;
    result?: {
      video?: string;
      clean_master?: string;
      srt?: string;
      vtt?: string;
      ass?: string;
      cues?: Cue[];
      revision?: number;
      clip_uid?: string;
      notice?: string;
    };
  };
}
export function mediaUrl(path: string) {
  const suffix = path.split("/exports/")[1];
  return suffix
    ? API_BASE +
        "/exports/" +
        suffix.split("/").map(encodeURIComponent).join("/")
    : "";
}
export function duration(p: Project) {
  let total = 0;
  let previous = 0;
  p.clips.forEach((c, i) => {
    const d = (c.end - c.start) / c.speed;
    total +=
      d -
      (i && p.clips[i - 1].transition === "crossfade"
        ? Math.min(0.3, previous / 2, d / 2)
        : 0);
    previous = d;
  });
  return total;
}
export async function copyText(value: string) {
  if (navigator.clipboard && window.isSecureContext)
    return navigator.clipboard.writeText(value);
  const el = document.createElement("textarea");
  el.value = value;
  el.style.position = "fixed";
  el.style.opacity = "0";
  document.body.appendChild(el);
  el.select();
  const ok = document.execCommand("copy");
  el.remove();
  if (!ok) throw new Error("Copy failed; select the text and press Ctrl+C.");
}
