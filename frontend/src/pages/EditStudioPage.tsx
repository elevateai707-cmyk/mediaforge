import { useEffect, useState, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { PageHeader } from "@/components/PageHeader";
import { Switch } from "@/components/ui/switch";
import { ProviderStudio } from "@/components/ProviderStudio";
import { proxyUrl, thumbUrl } from "@/lib/api";
import {
  editorRequest,
  copyText,
  duration,
  mediaUrl,
  type Project,
  type ProjectEnvelope,
  type EditorJob,
  type TextStyle,
  type Cue,
} from "@/lib/editor";
import type { Asset, EditPlan } from "@/lib/types";

function StyleEditor({
  style,
  onChange,
}: {
  style: TextStyle;
  onChange: (s: TextStyle) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-2">
      <label className="mf-field">
        Preset
        <select
          className="mf-input"
          value={style.preset}
          onChange={(e) =>
            onChange({
              ...style,
              preset: e.target.value as TextStyle["preset"],
            })
          }
        >
          <option value="clean">Clean subtitles</option>
          <option value="bold">Bold social captions</option>
          <option value="active">Active-word highlighting</option>
        </select>
      </label>
      <label className="mf-field">
        Font
        <select
          className="mf-input"
          value={style.font}
          onChange={(e) =>
            onChange({ ...style, font: e.target.value as TextStyle["font"] })
          }
        >
          <option>DejaVu Sans</option>
          <option>Liberation Sans</option>
        </select>
      </label>
      {(["size", "outline", "max_lines", "margin"] as const).map((k) => (
        <label className="mf-field" key={k}>
          {
            {
              size: "Size (at 1920px high)",
              outline: "Outline",
              max_lines: "Maximum lines",
              margin: "Safe margin (fraction)",
            }[k]
          }
          <input
            className="mf-input"
            type="number"
            step={k === "margin" ? 0.01 : 1}
            value={style[k]}
            onChange={(e) =>
              onChange({ ...style, [k]: Number(e.target.value) })
            }
          />
        </label>
      ))}
      <label className="mf-field">
        Colour
        <input
          type="color"
          className="mf-input"
          value={style.color}
          onChange={(e) => onChange({ ...style, color: e.target.value })}
        />
      </label>
      <label className="mf-field">
        Position
        <select
          className="mf-input"
          value={style.position}
          onChange={(e) =>
            onChange({
              ...style,
              position: e.target.value as TextStyle["position"],
            })
          }
        >
          <option value="bottom">Bottom</option>
          <option value="center">Centre</option>
          <option value="top">Top</option>
        </select>
      </label>
      <label className="mf-field">
        <input
          type="checkbox"
          checked={style.background}
          onChange={(e) => onChange({ ...style, background: e.target.checked })}
        />{" "}
        Background
      </label>
    </div>
  );
}
export function EditStudioPage() {
  const [params] = useSearchParams();
  const [planId, setPlanId] = useState(
    params.get("plan_id") ?? localStorage.getItem("mf-project") ?? "",
  );
  const [plans, setPlans] = useState<EditPlan[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [intent, setIntent] = useState("30-second highlight reel, upbeat");
  const [p, setP] = useState<Project>();
  const [approved, setApproved] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [clipIndex, setClipIndex] = useState(0);
  const [jobId, setJobId] = useState(
    localStorage.getItem("mf-editor-job") ?? "",
  );
  const [job, setJob] = useState<EditorJob>();
  const [preview, setPreview] = useState("");
  const [outputs, setOutputs] = useState<Record<string, string>>({});
  const [profile, setProfile] = useState("balanced");
  const [language, setLanguage] = useState("");
  const [city, setCity] = useState("");
  const [codecs, setCodecs] = useState(["libx264"]);
  const [scanPath, setScanPath] = useState("");
  const active = p?.clips[clipIndex];
  const captionKey =
    p?.caption_source === "narration" ? "narration" : active?.uid;
  const cues =
    p?.caption_source === "narration"
      ? p.narration_captions
      : (p?.captions[active?.uid ?? ""] ?? []);
  const currentPlan = useRef(planId);
  currentPlan.current = planId;
  const latest = useRef<{ project?: Project; dirty: boolean }>({
    dirty: false,
  });
  function changed(next: Project) {
    latest.current = { project: next, dirty: true };
    setP(next);
    setDirty(true);
    setApproved(null);
    setPreview("");
  }
  function loaded(e: ProjectEnvelope) {
    latest.current = { project: e.project, dirty: false };
    setP(e.project);
    setApproved(e.approved_revision);
    setDirty(false);
  }
  async function refreshAssets() {
    const data = await editorRequest<{ items: Asset[] }>(
      `/api/assets?limit=200${city ? "&city=" + encodeURIComponent(city) : ""}`,
    );
    setAssets(data.items);
  }
  useEffect(() => {
    editorRequest<EditPlan[]>("/api/edits/plans")
      .then(setPlans)
      .catch((e) => setMessage(e.message));
    editorRequest<{ items: Asset[] }>("/api/assets?limit=200")
      .then((data) => setAssets(data.items))
      .catch((e) => setMessage(e.message));
    editorRequest<{ codecs: string[] }>("/api/editor/capabilities")
      .then((x) => setCodecs(x.codecs))
      .catch((e) => setMessage(e.message));
  }, []);
  useEffect(() => {
    if (!planId) return;
    localStorage.setItem("mf-project", planId);
    editorRequest<ProjectEnvelope>(`/api/editor/${planId}`)
      .then(loaded)
      .catch((e) => setMessage(e.message));
    setClipIndex(0);
    setPreview("");
  }, [planId]);
  useEffect(() => {
    if (!jobId) return;
    localStorage.setItem("mf-editor-job", jobId);
    let stopped = false;
    const poll = async () => {
      try {
        const j = await editorRequest<EditorJob>(`/api/jobs/${jobId}`);
        if (stopped) return;
        setJob(j);
        if (
          j.status === "done" &&
          j.meta?.result &&
          j.meta.plan_id === currentPlan.current
        ) {
          const result = j.meta.result;
          if (result.video) {
            if (result.video.split("/").pop()?.startsWith("preview_")) {
              if (
                !latest.current.dirty &&
                result.revision === latest.current.project?.revision
              )
                setPreview(mediaUrl(result.video));
            } else
              setOutputs(
                Object.fromEntries(
                  Object.entries(result).filter(
                    ([k, v]) =>
                      ["video", "clean_master", "srt", "vtt", "ass"].includes(
                        k,
                      ) && typeof v === "string",
                  ),
                ) as Record<string, string>,
              );
          }
          setMessage("Job completed.");
        }
        if (["done", "error", "cancelled", "interrupted"].includes(j.status)) {
          clearInterval(timer);
        }
      } catch (e) {
        setMessage(String(e));
        clearInterval(timer);
      }
    };
    const timer = setInterval(poll, 700);
    void poll();
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [jobId]);
  async function save(next = p) {
    if (!next) return;
    setBusy(true);
    try {
      const e = await editorRequest<ProjectEnvelope>(
        `/api/editor/${planId}`,
        "PUT",
        next,
      );
      if (latest.current.project === next || !latest.current.project) loaded(e);
      else {
        const nextDraft = {
          ...latest.current.project,
          revision: e.project.revision,
        };
        latest.current = { project: nextDraft, dirty: true };
        setP(nextDraft);
        setDirty(true);
      }
      setMessage(
        "Saved. Review this revision and approve before final export.",
      );
      return e.project;
    } catch (e) {
      setMessage(String(e));
      throw e;
    } finally {
      setBusy(false);
    }
  }
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function beginRender(isPreview: boolean) {
    if (!p) return;
    let current = p;
    if (dirty) {
      const saved = await save();
      if (saved) current = saved;
    }
    if (!isPreview) {
      loaded(
        await editorRequest(`/api/editor/${planId}/approve`, "POST", {
          revision: current.revision,
        }),
      );
    }
    const r = await editorRequest<{ job_id: string }>(
      `/api/editor/${planId}/render?preview=${isPreview}`,
      "POST",
    );
    setJob(undefined);
    setJobId(r.job_id);
  }
  function setCues(next: Cue[]) {
    if (!p || !captionKey) return;
    changed(
      p.caption_source === "narration"
        ? { ...p, narration_captions: next }
        : { ...p, captions: { ...p.captions, [captionKey]: next } },
    );
  }
  const running = job && ["queued", "running"].includes(job.status);
  return (
    <div className="space-y-5 pb-16">
      <PageHeader
        title="Edit Studio"
        description="Select footage → describe → review → adjust → preview and export"
      />
      <section className="glass rounded-xl p-5 space-y-3">
        <h2 className="font-semibold">1 · Select footage</h2>
        <div className="flex gap-2 flex-wrap">
          <label className="grow mf-field">
            Import folder or file
            <input
              className="mf-input"
              value={scanPath}
              onChange={(e) => setScanPath(e.target.value)}
              placeholder="/home/you/Videos/trip"
            />
          </label>
          <button
            className="mf-button"
            disabled={busy || !scanPath}
            onClick={() =>
              void action(async () => {
                const j = await editorRequest<{ job_id: string }>(
                  "/api/scan",
                  "POST",
                  { paths: [scanPath] },
                );
                setJobId(j.job_id);
                setMessage(
                  "Import started. Refresh footage when the scan finishes.",
                );
              })
            }
          >
            Import
          </button>
        </div>
        <label className="mf-field">
          Filter city
          <input
            className="mf-input"
            value={city}
            onChange={(e) => setCity(e.target.value)}
          />
        </label>
        <button
          className="mf-button"
          onClick={() => void action(refreshAssets)}
        >
          Refresh footage
        </button>
        <details>
          <summary>
            {selected.length
              ? `${selected.length} selected`
              : "Use matching library footage, or select specific sources"}
          </summary>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 max-h-72 overflow-auto mt-3">
            {assets.map((a) => (
              <label key={a.id} className="border rounded-lg p-2 text-xs">
                <img
                  src={thumbUrl(a.id)}
                  alt=""
                  className="h-20 w-full object-cover"
                />
                <input
                  type="checkbox"
                  checked={selected.includes(a.id)}
                  onChange={(e) =>
                    setSelected(
                      e.target.checked
                        ? [...selected, a.id]
                        : selected.filter((id) => id !== a.id),
                    )
                  }
                />
                <span className="break-all">{a.path.split("/").pop()}</span>
                {a.city && <span className="block">{a.city}</span>}
              </label>
            ))}
          </div>
        </details>
        <h2 className="font-semibold">2 · Describe your edit</h2>
        <textarea
          aria-label="Describe the edit"
          className="mf-input"
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
        />
        <button
          className="mf-button"
          disabled={busy || !intent.trim()}
          onClick={() =>
            void action(async () => {
              setMessage(
                "AI is planning your edit on the local model. This can take up to a minute the first time.",
              );
              const plan = await editorRequest<EditPlan>(
                "/api/edits/plan",
                "POST",
                {
                  intent,
                  asset_ids: selected.length ? selected : undefined,
                  trip_id: params.get("trip_id")
                    ? Number(params.get("trip_id"))
                    : undefined,
                },
              );
              setPlans([plan, ...plans]);
              setPlanId(plan.plan_id);
              setMessage(plan.summary ?? "Review the sequence");
            })
          }
        >
          Generate local plan
        </button>
        <label className="mf-field">
          Open saved project
          <select
            className="mf-input"
            value={planId}
            onChange={(e) => setPlanId(e.target.value)}
          >
            <option value="">Choose project</option>
            {plans.map((plan) => (
              <option key={plan.plan_id} value={plan.plan_id}>
                {plan.summary ?? plan.plan_id} · {plan.plan_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
      </section>
      <p
        role="status"
        className="border rounded-lg p-3 text-sm whitespace-pre-wrap"
      >
        {message ||
          "No cloud generation occurs unless you explicitly request it."}
      </p>
      {p && (
        <>
          <div className="flex gap-3 items-center flex-wrap">
            <span>
              Revision {p.revision} ·{" "}
              {dirty
                ? "Unsaved changes"
                : approved === p.revision
                  ? "Approved"
                  : "Needs approval"}{" "}
              · {duration(p).toFixed(2)} seconds
            </span>
            <button
              className="mf-button"
              disabled={busy || !dirty}
              onClick={() =>
                void action(async () => {
                  await save();
                })
              }
            >
              Save project
            </button>
            <button
              className="mf-button"
              disabled={busy}
              onClick={() =>
                void action(async () =>
                  loaded(await editorRequest(`/api/editor/${planId}`)),
                )
              }
            >
              Reload saved project
            </button>
          </div>
          <div className="grid items-start lg:grid-cols-[1.2fr_1fr] gap-5">
            <section className="glass rounded-xl p-5">
              <h2 className="font-semibold">3 · Review proposed sequence</h2>
              <div
                className="max-h-[65vh] overflow-auto pr-2"
                tabIndex={0}
                aria-label="Proposed clip sequence"
              >
                {p.clips.map((c, i) => (
                  <article
                    className={`border rounded-lg p-3 my-3 ${i === clipIndex ? "border-teal-400" : ""}`}
                    key={c.uid}
                  >
                    <button
                      className="mf-button"
                      onClick={() => setClipIndex(i)}
                    >
                      Clip {i + 1} · asset {c.asset_id}
                    </button>
                    <div className="grid grid-cols-3 gap-2">
                      {(["start", "end", "speed"] as const).map((k) => (
                        <label className="mf-field" key={k}>
                          {k}
                          <input
                            type="number"
                            className="mf-input"
                            step=".05"
                            value={c[k]}
                            onChange={(e) =>
                              changed({
                                ...p,
                                clips: p.clips.map((x, n) =>
                                  n === i
                                    ? { ...x, [k]: Number(e.target.value) }
                                    : x,
                                ),
                              })
                            }
                          />
                        </label>
                      ))}
                    </div>
                    <label className="mf-field">
                      Transition to next
                      <select
                        className="mf-input"
                        value={c.transition}
                        onChange={(e) =>
                          changed({
                            ...p,
                            clips: p.clips.map((x, n) =>
                              n === i
                                ? {
                                    ...x,
                                    transition: e.target.value as
                                      "cut" | "crossfade",
                                  }
                                : x,
                            ),
                          })
                        }
                      >
                        <option value="cut">Cut</option>
                        <option value="crossfade">
                          Crossfade (up to 0.3s)
                        </option>
                      </select>
                    </label>
                    {[-1, 1].map((direction) => (
                      <button
                        className="mf-button"
                        key={direction}
                        disabled={
                          i + direction < 0 || i + direction >= p.clips.length
                        }
                        onClick={() => {
                          const clips = [...p.clips];
                          [clips[i], clips[i + direction]] = [
                            clips[i + direction],
                            clips[i],
                          ];
                          changed({ ...p, clips });
                          setClipIndex(i + direction);
                        }}
                      >
                        {direction === -1 ? "Move up" : "Move down"}
                      </button>
                    ))}
                    <button
                      className="mf-button"
                      onClick={() => {
                        changed({
                          ...p,
                          clips: p.clips.filter((_, n) => n !== i),
                        });
                        setClipIndex(0);
                      }}
                    >
                      Remove from edit
                    </button>
                  </article>
                ))}
              </div>
            </section>
            <section className="glass rounded-xl p-5 space-y-3">
              <h2 className="font-semibold">Preview & text visibility</h2>
              <div className="border rounded-lg p-3 space-y-3">
                {(["speech_captions", "on_video_text"] as const).map((k) => (
                  <label
                    key={k}
                    className="flex justify-between gap-3 items-center"
                    htmlFor={k}
                  >
                    <span className="font-semibold">
                      {k === "speech_captions"
                        ? "Speech captions"
                        : "On-video text"}
                    </span>
                    <Switch
                      id={k}
                      checked={p[k]}
                      disabled={busy}
                      onCheckedChange={(v) => {
                        const next = { ...p, [k]: v };
                        changed(next);
                        void save(next).catch(() => {});
                      }}
                    />
                  </label>
                ))}
              </div>
              <p className="text-xs">
                Switches save immediately and never retranscribe or call a paid
                API. Changing settings requires reapproval.
              </p>
              {preview ? (
                <video
                  aria-label="Rendered project preview"
                  controls
                  src={preview}
                  className="max-h-96 w-full rounded bg-black"
                />
              ) : active ? (
                <>
                  <video
                    aria-label="Source footage reference"
                    key={active.uid}
                    controls
                    src={`${proxyUrl(active.asset_id)}#t=${active.start},${active.end}`}
                    poster={thumbUrl(active.asset_id)}
                    className="max-h-72 w-full rounded bg-black"
                  />
                  <p className="text-xs">
                    Source reference only. Generate a preview to see the exact
                    captions, overlays, transitions and audio mix.
                  </p>
                </>
              ) : null}
              <button
                className="mf-button"
                disabled={busy || !!running || !p.clips.length}
                onClick={() => void action(() => beginRender(true))}
              >
                Generate local preview
              </button>
              <h2 className="font-semibold">4 · Adjust captions and text</h2>
              <details>
                <summary>Speech captions · editable timing</summary>
                <label className="mf-field">
                  Caption source
                  <select
                    className="mf-input"
                    value={p.caption_source}
                    onChange={(e) =>
                      changed({
                        ...p,
                        caption_source: e.target.value as
                          "original" | "narration",
                      })
                    }
                  >
                    <option value="original">Original speech</option>
                    <option value="narration">Generated narration</option>
                  </select>
                </label>
                <label className="mf-field">
                  Local transcription profile
                  <select
                    className="mf-input"
                    value={profile}
                    onChange={(e) => setProfile(e.target.value)}
                  >
                    <option value="fast">Fast · multilingual base</option>
                    <option value="balanced">
                      Balanced · multilingual small
                    </option>
                    <option value="quality">
                      Higher accuracy · large-v3 (slower CPU fallback)
                    </option>
                  </select>
                </label>
                <label className="mf-field">
                  Language (blank detects automatically)
                  <input
                    className="mf-input"
                    placeholder="en, fr, es…"
                    value={language}
                    onChange={(e) => setLanguage(e.target.value)}
                  />
                </label>
                <button
                  className="mf-button"
                  disabled={busy || !!running || !active}
                  onClick={() =>
                    void action(async () => {
                      const saved = dirty ? await save() : p;
                      if (!saved || !active) return;
                      const j = await editorRequest<{ job_id: string }>(
                        `/api/editor/${planId}/transcribe`,
                        "POST",
                        {
                          clip_uid: active.uid,
                          revision: saved.revision,
                          profile,
                          language: language || null,
                        },
                      );
                      setJobId(j.job_id);
                      setJob(undefined);
                    })
                  }
                >
                  Transcribe selected clip locally
                </button>
                {job?.meta?.plan_id === planId && job?.meta?.result?.cues && (
                  <button
                    className="mf-button"
                    onClick={() => {
                      const r = job.meta!.result!;
                      if (r.revision !== p.revision) {
                        setMessage(
                          "Project changed since transcription started. Review timing before applying.",
                        );
                      }
                      if (
                        r.clip_uid &&
                        p.clips.some((c) => c.uid === r.clip_uid)
                      )
                        changed({
                          ...p,
                          captions: { ...p.captions, [r.clip_uid]: r.cues! },
                        });
                    }}
                  >
                    Apply transcription result
                  </button>
                )}
                <p className="text-xs">
                  Original speech timings are source seconds; narration uses
                  edited-timeline seconds. Trim, order, speed and transition
                  changes are mapped on export. Manual text correction clears
                  stale word alignment for that cue.
                </p>
                {cues.map((cue, i) => (
                  <div className="border rounded p-2 my-2" key={i}>
                    <textarea
                      aria-label={`Caption ${i + 1}`}
                      className="mf-input"
                      value={cue.text}
                      onChange={(e) =>
                        setCues(
                          cues.map((c, n) =>
                            n === i
                              ? { ...c, text: e.target.value, words: [] }
                              : c,
                          ),
                        )
                      }
                    />
                    <div className="grid grid-cols-2 gap-2">
                      {(["start", "end"] as const).map((k) => (
                        <label className="mf-field" key={k}>
                          {k}
                          <input
                            className="mf-input"
                            type="number"
                            step=".01"
                            value={cue[k]}
                            onChange={(e) =>
                              setCues(
                                cues.map((c, n) =>
                                  n === i
                                    ? {
                                        ...c,
                                        [k]: Number(e.target.value),
                                        words: [],
                                      }
                                    : c,
                                ),
                              )
                            }
                          />
                        </label>
                      ))}
                    </div>
                    <details>
                      <summary>Refine word timing (optional)</summary>
                      <p className="text-xs">
                        Edit individual word boundaries for active-word
                        captions. Retranscribe at higher quality for a fresh
                        local alignment.
                      </p>
                      {cue.words.map((word, wi) => (
                        <div key={wi} className="grid grid-cols-3 gap-1">
                          <label className="mf-field">
                            Word
                            <input
                              className="mf-input"
                              value={word.text}
                              onChange={(e) =>
                                setCues(
                                  cues.map((c, n) =>
                                    n === i
                                      ? {
                                          ...c,
                                          words: c.words.map((w, j) =>
                                            j === wi
                                              ? { ...w, text: e.target.value }
                                              : w,
                                          ),
                                        }
                                      : c,
                                  ),
                                )
                              }
                            />
                          </label>
                          {(["start", "end"] as const).map((k) => (
                            <label key={k} className="mf-field">
                              {k}
                              <input
                                type="number"
                                step=".01"
                                className="mf-input"
                                value={word[k]}
                                onChange={(e) =>
                                  setCues(
                                    cues.map((c, n) =>
                                      n === i
                                        ? {
                                            ...c,
                                            words: c.words.map((w, j) =>
                                              j === wi
                                                ? {
                                                    ...w,
                                                    [k]: Number(e.target.value),
                                                  }
                                                : w,
                                            ),
                                          }
                                        : c,
                                    ),
                                  )
                                }
                              />
                            </label>
                          ))}
                        </div>
                      ))}
                    </details>
                    <button
                      className="mf-button"
                      onClick={() => setCues(cues.filter((_, n) => n !== i))}
                    >
                      Remove cue
                    </button>
                  </div>
                ))}
                <button
                  className="mf-button"
                  disabled={!active}
                  onClick={() =>
                    setCues([
                      ...cues,
                      {
                        start:
                          p.caption_source === "narration" ? 0 : active!.start,
                        end: p.caption_source === "narration" ? 2 : active!.end,
                        text: "",
                        words: [],
                      },
                    ])
                  }
                >
                  Add speech cue
                </button>
                <StyleEditor
                  style={p.caption_style}
                  onChange={(s) => changed({ ...p, caption_style: s })}
                />
              </details>
              {active && (
                <details>
                  <summary>On-video text · titles and labels</summary>
                  {(p.overlays[active.uid] ?? []).map((cue, i) => (
                    <div className="border rounded p-2 my-2" key={i}>
                      <textarea
                        aria-label={`Overlay ${i + 1}`}
                        className="mf-input"
                        value={cue.text}
                        onChange={(e) =>
                          changed({
                            ...p,
                            overlays: {
                              ...p.overlays,
                              [active.uid]: p.overlays[active.uid].map(
                                (c, n) =>
                                  n === i ? { ...c, text: e.target.value } : c,
                              ),
                            },
                          })
                        }
                      />
                      <div className="grid grid-cols-2 gap-2">
                        {(["start", "end"] as const).map((k) => (
                          <label className="mf-field" key={k}>
                            Source {k}
                            <input
                              className="mf-input"
                              type="number"
                              step=".01"
                              value={cue[k]}
                              onChange={(e) =>
                                changed({
                                  ...p,
                                  overlays: {
                                    ...p.overlays,
                                    [active.uid]: p.overlays[active.uid].map(
                                      (c, n) =>
                                        n === i
                                          ? {
                                              ...c,
                                              [k]: Number(e.target.value),
                                            }
                                          : c,
                                    ),
                                  },
                                })
                              }
                            />
                          </label>
                        ))}
                      </div>
                      <StyleEditor
                        style={cue.style ?? p.caption_style}
                        onChange={(s) =>
                          changed({
                            ...p,
                            overlays: {
                              ...p.overlays,
                              [active.uid]: p.overlays[active.uid].map(
                                (c, n) => (n === i ? { ...c, style: s } : c),
                              ),
                            },
                          })
                        }
                      />
                      <button
                        className="mf-button"
                        onClick={() =>
                          changed({
                            ...p,
                            overlays: {
                              ...p.overlays,
                              [active.uid]: p.overlays[active.uid].filter(
                                (_, n) => n !== i,
                              ),
                            },
                          })
                        }
                      >
                        Remove overlay
                      </button>
                    </div>
                  ))}
                  <button
                    className="mf-button"
                    onClick={() =>
                      changed({
                        ...p,
                        overlays: {
                          ...p.overlays,
                          [active.uid]: [
                            ...(p.overlays[active.uid] ?? []),
                            {
                              start: active.start,
                              end: active.end,
                              text: "New title",
                              words: [],
                              style: {
                                ...p.caption_style,
                                preset: "bold",
                                position: "top",
                              },
                            },
                          ],
                        },
                      })
                    }
                  >
                    Add overlay
                  </button>
                </details>
              )}
            </section>
          </div>
          <details className="glass rounded-xl p-5">
            <summary className="font-semibold">
              Post description · never burned into video
            </summary>
            {(["post_title", "post_description", "hashtags"] as const).map(
              (k) => (
                <div key={k} className="my-3">
                  <label className="mf-field">
                    {
                      {
                        post_title: "Post title",
                        post_description: "Description",
                        hashtags: "Hashtags",
                      }[k]
                    }
                    <textarea
                      className="mf-input"
                      aria-label={
                        {
                          post_title: "Post title",
                          post_description: "Description",
                          hashtags: "Hashtags",
                        }[k]
                      }
                      value={p[k]}
                      onChange={(e) => changed({ ...p, [k]: e.target.value })}
                    />
                  </label>
                  <button
                    className="mf-button"
                    onClick={() =>
                      void action(async () => {
                        await copyText(p[k]);
                        setMessage("Copied");
                      })
                    }
                  >
                    Copy{" "}
                    {k === "post_title"
                      ? "title"
                      : k === "post_description"
                        ? "description"
                        : "hashtags"}
                  </button>
                  {active && (
                    <button
                      className="mf-button"
                      onClick={() =>
                        changed({
                          ...p,
                          overlays: {
                            ...p.overlays,
                            [active.uid]: [
                              ...(p.overlays[active.uid] ?? []),
                              {
                                start: active.start,
                                end: active.end,
                                text: p[k],
                                words: [],
                                style: p.caption_style,
                              },
                            ],
                          },
                        })
                      }
                    >
                      Explicitly convert to overlay
                    </button>
                  )}
                </div>
              ),
            )}
            <button
              className="mf-button"
              onClick={() =>
                void action(async () => {
                  await copyText(
                    [p.post_title, p.post_description, p.hashtags]
                      .filter(Boolean)
                      .join("\n\n"),
                  );
                  setMessage("Copied all post text");
                })
              }
            >
              Copy all
            </button>
          </details>
          <details className="glass rounded-xl p-5">
            <summary className="font-semibold">Audio mix</summary>
            <div className="grid md:grid-cols-3 gap-4">
              {(["original_audio", "voice_over", "music"] as const).map((k) => (
                <div key={k}>
                  <h3>
                    {
                      {
                        original_audio: "Original audio",
                        voice_over: "Voice-over",
                        music: "Background music",
                      }[k]
                    }
                  </h3>
                  <label>
                    <input
                      type="checkbox"
                      checked={p[k].enabled}
                      onChange={(e) =>
                        changed({
                          ...p,
                          [k]: { ...p[k], enabled: e.target.checked },
                        })
                      }
                    />{" "}
                    Enabled
                  </label>
                  {k !== "original_audio" && (
                    <label className="mf-field">
                      Audio file path
                      <input
                        className="mf-input"
                        value={p[k].path ?? ""}
                        onChange={(e) =>
                          changed({
                            ...p,
                            [k]: { ...p[k], path: e.target.value || null },
                          })
                        }
                      />
                    </label>
                  )}
                  {(
                    [
                      "volume",
                      "fade_in",
                      "fade_out",
                      ...(k !== "original_audio" ? (["start"] as const) : []),
                    ] as const
                  ).map((field) => (
                    <label className="mf-field" key={field}>
                      {field.replace("_", " ")}
                      <input
                        className="mf-input"
                        type="number"
                        min="0"
                        step=".1"
                        value={p[k][field]}
                        onChange={(e) =>
                          changed({
                            ...p,
                            [k]: { ...p[k], [field]: Number(e.target.value) },
                          })
                        }
                      />
                    </label>
                  ))}
                </div>
              ))}
            </div>
            <label>
              <input
                type="checkbox"
                checked={p.ducking}
                onChange={(e) => changed({ ...p, ducking: e.target.checked })}
              />{" "}
              Duck background music under speech
            </label>
            <label className="mf-field">
              Ducking ratio
              <input
                className="mf-input"
                type="number"
                min="1"
                max="20"
                value={p.ducking_ratio}
                onChange={(e) =>
                  changed({ ...p, ducking_ratio: Number(e.target.value) })
                }
              />
            </label>
          </details>
          <ProviderStudio project={p} assets={assets} onChange={changed} />
          <section className="glass rounded-xl p-5 space-y-3">
            <h2 className="font-semibold">5 · Preview & export</h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <label className="mf-field">
                Format
                <select
                  className="mf-input"
                  value={p.export.ratio}
                  onChange={(e) =>
                    changed({
                      ...p,
                      export: {
                        ...p.export,
                        ratio: e.target.value as Project["export"]["ratio"],
                      },
                    })
                  }
                >
                  <option>9:16</option>
                  <option>1:1</option>
                  <option>16:9</option>
                </select>
              </label>
              <label className="mf-field">
                Width
                <input
                  className="mf-input"
                  type="number"
                  step="2"
                  value={p.export.width}
                  onChange={(e) =>
                    changed({
                      ...p,
                      export: { ...p.export, width: Number(e.target.value) },
                    })
                  }
                />
              </label>
              <label className="mf-field">
                Frame rate (0 = first source)
                <input
                  className="mf-input"
                  type="number"
                  value={p.export.fps}
                  onChange={(e) =>
                    changed({
                      ...p,
                      export: { ...p.export, fps: Number(e.target.value) },
                    })
                  }
                />
              </label>
              <label className="mf-field">
                Codec
                <select
                  className="mf-input"
                  value={p.export.codec}
                  onChange={(e) =>
                    changed({
                      ...p,
                      export: {
                        ...p.export,
                        codec: e.target.value as Project["export"]["codec"],
                      },
                    })
                  }
                >
                  {codecs.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </label>
            </div>
            <p className="text-xs">
              High-quality H.264/AAC, preserved aspect ratio with padding.
              Sources are not overwritten. HDR is converted to SDR when FFmpeg
              supports it; otherwise rendering reports an error.
            </p>
            <label className="block">
              <input
                type="checkbox"
                checked={p.export.clean_master}
                onChange={(e) =>
                  changed({
                    ...p,
                    export: { ...p.export, clean_master: e.target.checked },
                  })
                }
              />{" "}
              Also export a clean master without captions or overlays
            </label>
            <p className="text-sm">
              Burned-in captions cannot be turned off in an exported video.
              Export a clean master for reuse.
            </p>
            <div>
              Explicit subtitle sidecars{" "}
              {(["srt", "vtt", "ass"] as const).map((kind) => (
                <label className="mx-2" key={kind}>
                  <input
                    type="checkbox"
                    checked={p.export.sidecars.includes(kind)}
                    onChange={(e) =>
                      changed({
                        ...p,
                        export: {
                          ...p.export,
                          sidecars: e.target.checked
                            ? [...p.export.sidecars, kind]
                            : p.export.sidecars.filter((x) => x !== kind),
                        },
                      })
                    }
                  />{" "}
                  {kind.toUpperCase()}
                </label>
              ))}
            </div>
            <button
              className="mf-button"
              disabled={busy || !!running || !p.clips.length}
              onClick={() => void action(() => beginRender(false))}
            >
              Approve this revision & render
            </button>
            {job && (
              <div role="status">
                <p>
                  {job.status} · {job.message}
                  {job.status === "interrupted" && (
                    <button
                      className="mf-button"
                      onClick={() =>
                        void action(async () => {
                          const next = await editorRequest<{ job_id: string }>(
                            `/api/editor/jobs/${job.id}/resume`,
                            "POST",
                          );
                          setJobId(next.job_id);
                        })
                      }
                    >
                      Resume saved render snapshot
                    </button>
                  )}
                </p>
                <progress className="w-full" max="1" value={job.progress} />
                {running && (
                  <button
                    className="mf-button"
                    onClick={() =>
                      void action(async () => {
                        await editorRequest("/api/scan/cancel", "POST", {
                          job_id: job.id,
                        });
                      })
                    }
                  >
                    Cancel job
                  </button>
                )}
              </div>
            )}
            {outputs.video && (
              <video
                controls
                src={mediaUrl(outputs.video)}
                className="max-h-96 w-full bg-black"
              />
            )}
            <div>
              {Object.entries(outputs).map(([kind, path]) => (
                <a
                  className="mf-button"
                  href={mediaUrl(path)}
                  download
                  key={kind}
                >
                  Download {kind.replace("_", " ")}
                </a>
              ))}
            </div>
            <details>
              <summary>NLE exports · source sequence</summary>
              <p className="text-xs my-2">
                These existing exporters transfer the source sequence. Edited
                speech captions, styled overlays, narration/music mixing and
                speed changes do not round-trip. Use the rendered master and
                explicit subtitle sidecars for those features.
              </p>
              {["fcpxml", "edl", "capcut", "resolve"].map((kind) => (
                <button
                  className="mf-button"
                  key={kind}
                  disabled={busy || dirty || approved !== p.revision}
                  onClick={() =>
                    void action(async () => {
                      const r = await editorRequest<{ path?: string }>(
                        `/api/export/${kind}`,
                        "POST",
                        { plan_id: planId },
                      );
                      if (r.path) setOutputs({ ...outputs, [kind]: r.path });
                      else setMessage("Imported into Resolve");
                    })
                  }
                >
                  {kind}
                </button>
              ))}
            </details>
          </section>
        </>
      )}
    </div>
  );
}
