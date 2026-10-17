import { useEffect, useState } from "react";
import {
  editorRequest,
  mediaUrl,
  duration,
  type Project,
  type Cue,
} from "@/lib/editor";
import type { ProviderConfig } from "./ProviderSettings";
import type { Asset } from "@/lib/types";
type ProviderJob = {
  id: string;
  provider: string;
  status: string;
  error?: string;
  remote_id?: string;
  request: {
    script?: string;
    voice?: string;
    model?: string;
    workflow_name?: string;
    inputs?: Record<string, unknown>;
    uploads?: { filename: string; bytes: number }[];
  };
  result?: {
    assets: string[];
    cues?: Cue[];
    duration?: number;
    notice?: string;
    asset_id?: number;
  };
};
export function ProviderStudio({
  project,
  assets,
  onChange,
}: {
  project: Project;
  assets: Asset[];
  onChange: (p: Project) => void;
}) {
  const [config, setConfig] = useState<ProviderConfig>();
  const [jobs, setJobs] = useState<ProviderJob[]>([]);
  const [workflow, setWorkflow] = useState("");
  const [inputs, setInputs] = useState<Record<string, string | number>>({});
  const [voice, setVoice] = useState("");
  const [model, setModel] = useState("");
  const [voices, setVoices] = useState<{ id: string; name: string }[]>([]);
  const [models, setModels] = useState<{ id: string; name: string }[]>([]);
  const [message, setMessage] = useState("");
  const [review, setReview] = useState<ProviderJob>();
  const [busy, setBusy] = useState(false);
  const [stability, setStability] = useState(0.5);
  const [similarity, setSimilarity] = useState(0.75);
  const [speed, setSpeed] = useState(1);
  const [sttAsset, setSttAsset] = useState(0);
  const [sttModel, setSttModel] = useState("scribe_v2");
  const [sttLanguage, setSttLanguage] = useState("");
  useEffect(() => {
    editorRequest<ProviderConfig>("/api/providers/settings")
      .then(setConfig)
      .catch((e) => setMessage(e.message));
    const poll = () =>
      editorRequest<ProviderJob[]>("/api/providers/jobs")
        .then(setJobs)
        .catch((e) => setMessage(e.message));
    void poll();
    const timer = setInterval(poll, 3000);
    return () => clearInterval(timer);
  }, []);
  async function makeReview(provider: string, preview = false) {
    setBusy(true);
    try {
      setReview(
        await editorRequest(
          "/api/providers/review",
          "POST",
          provider === "comfy"
            ? { provider, workflow, inputs }
            : {
                provider,
                voice,
                model,
                script: preview
                  ? project.narration_script.slice(0, 250)
                  : project.narration_script,
                preview,
                settings: { stability, similarity_boost: similarity, speed },
              },
        ),
      );
      setMessage(
        "Review the upload and billable action below. Nothing has been generated yet.",
      );
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="glass rounded-xl p-5">
      <summary className="font-semibold">
        Generated assets & optional voice-over
      </summary>
      <p className="text-sm my-3">
        Generation uses your subscription. No price estimate is available.
        Preview generation is also billable; replaying saved audio is free.
      </p>
      <div className="grid gap-5 md:grid-cols-2">
        <section>
          <h3>Comfy Cloud</h3>
          <label className="mf-field">
            Compatible workflow
            <select
              className="mf-input"
              value={workflow}
              onChange={(e) => {
                setWorkflow(e.target.value);
                setInputs({});
              }}
            >
              <option value="">Choose configured workflow</option>
              {Object.keys(config?.workflows ?? {}).map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </label>
          {Object.entries(config?.workflows?.[workflow]?.mappings ?? {}).map(
            ([name, m]) => (
              <label key={name} className="mf-field mt-2">
                {name}
                {m.type === "asset" ? (
                  <select
                    className="mf-input"
                    value={inputs[name] ?? ""}
                    onChange={(e) =>
                      setInputs({ ...inputs, [name]: Number(e.target.value) })
                    }
                  >
                    <option value="">Select only the asset to upload</option>
                    {assets.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.path.split("/").pop()}
                      </option>
                    ))}
                  </select>
                ) : (
                  <textarea
                    className="mf-input"
                    value={inputs[name] ?? ""}
                    onChange={(e) =>
                      setInputs({ ...inputs, [name]: e.target.value })
                    }
                  />
                )}
              </label>
            ),
          )}
          <button
            className="mf-button"
            disabled={busy || !config?.configured.comfy || !workflow}
            onClick={() => void makeReview("comfy")}
          >
            Review selected uploads
          </button>
        </section>
        <section>
          <h3>ElevenLabs narration</h3>
          <label className="mf-field">
            Narration script
            <textarea
              className="mf-input h-32"
              aria-label="Narration script"
              value={project.narration_script}
              onChange={(e) =>
                onChange({ ...project, narration_script: e.target.value })
              }
            />
          </label>
          <button
            className="mf-button"
            disabled={busy || !config?.configured.elevenlabs}
            onClick={async () => {
              setBusy(true);
              try {
                const d = await editorRequest<{
                  voices: { id: string; name: string }[];
                  models: { id: string; name: string }[];
                  usage: unknown;
                }>("/api/providers/elevenlabs/test", "POST");
                setVoices(d.voices);
                setModels(d.models);
                setMessage(
                  "Account usage: " + JSON.stringify(d.usage ?? "unavailable"),
                );
              } catch (e) {
                setMessage(String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Load available voices and models
          </button>
          <label className="mf-field">
            Voice
            <select
              className="mf-input"
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
            >
              <option value="">Select voice</option>
              {voices.map((v) => (
                <option value={v.id} key={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </label>
          <label className="mf-field">
            Model
            <select
              className="mf-input"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            >
              <option value="">Select model</option>
              {models.map((v) => (
                <option value={v.id} key={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </label>
          {[
            ["Stability", stability, setStability],
            ["Similarity", similarity, setSimilarity],
            ["Speed", speed, setSpeed],
          ].map(([label, value, set]) => (
            <label key={String(label)} className="mf-field">
              {String(label)}
              <input
                className="mf-input"
                type="number"
                step=".05"
                min={label === "Speed" ? 0.7 : 0}
                max={label === "Speed" ? 1.2 : 1}
                value={value as number}
                onChange={(e) =>
                  (set as (n: number) => void)(Number(e.target.value))
                }
              />
            </label>
          ))}
          <button
            className="mf-button"
            disabled={busy || !voice || !model}
            onClick={() => void makeReview("elevenlabs", true)}
          >
            Review short preview (first 250 characters)
          </button>
          <button
            className="mf-button"
            disabled={busy || !voice || !model}
            onClick={() => void makeReview("elevenlabs")}
          >
            Review full voice-over
          </button>
        </section>
      </div>
      <section className="border rounded-lg p-3 mt-4">
        <h3>Optional cloud transcription · ElevenLabs</h3>
        <p className="text-sm">
          This explicitly uploads one selected video. Local transcription
          remains available in the captions panel.
        </p>
        <label className="mf-field">
          Source video
          <select
            className="mf-input"
            value={sttAsset}
            onChange={(e) => setSttAsset(Number(e.target.value))}
          >
            <option value={0}>Select one source</option>
            {assets
              .filter((a) => a.kind === "video")
              .map((a) => (
                <option value={a.id} key={a.id}>
                  {a.path.split("/").pop()}
                </option>
              ))}
          </select>
        </label>
        <label className="mf-field">
          Transcription model
          <select
            className="mf-input"
            value={sttModel}
            onChange={(e) => setSttModel(e.target.value)}
          >
            <option>scribe_v2</option>
            <option>scribe_v1</option>
          </select>
        </label>
        <label className="mf-field">
          Language (blank for auto)
          <input
            className="mf-input"
            value={sttLanguage}
            onChange={(e) => setSttLanguage(e.target.value)}
          />
        </label>
        <p className="text-xs">
          Model availability depends on your account; no subscription access is
          assumed.
        </p>
        <button
          className="mf-button"
          disabled={busy || !sttAsset || !config?.configured.elevenlabs}
          onClick={async () => {
            setBusy(true);
            try {
              setReview(
                await editorRequest("/api/providers/review", "POST", {
                  provider: "elevenlabs",
                  operation: "transcription",
                  asset_id: sttAsset,
                  model: sttModel,
                  language: sttLanguage || null,
                }),
              );
            } catch (e) {
              setMessage(String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          Review cloud transcription upload
        </button>
      </section>
      {review && (
        <div className="border rounded-xl p-4 my-4">
          <h3>Review before generation</h3>
          <p>
            Provider: {review.provider}. Status: {review.status}. Cost
            unavailable; usage is billable.
          </p>
          <p>
            {review.request.workflow_name ||
              `${review.request.voice ?? ""} · ${review.request.model ?? ""}`}
          </p>
          {review.request.script && (
            <blockquote className="whitespace-pre-wrap border rounded p-3">
              {review.request.script}
            </blockquote>
          )}
          {review.request.inputs && (
            <pre className="whitespace-pre-wrap break-words text-xs">
              {JSON.stringify(review.request.inputs, null, 2)}
            </pre>
          )}
          {review.request.uploads?.map((u) => (
            <p key={u.filename}>
              {u.filename} · {(u.bytes / 1e6).toFixed(2)} MB
            </p>
          ))}
          <button
            className="mf-button"
            disabled={busy || !["review", "rejected"].includes(review.status)}
            onClick={async () => {
              setBusy(true);
              try {
                await editorRequest(
                  `/api/providers/jobs/${review.id}/generate`,
                  "POST",
                  { confirmed: true },
                );
                setReview(undefined);
                setMessage(
                  "Generation submitted. You can leave this page; the job is saved.",
                );
              } catch (e) {
                setMessage(String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Generate using my subscription
          </button>
          <button className="mf-button" onClick={() => setReview(undefined)}>
            Close review
          </button>
        </div>
      )}
      <p role="status" className="my-3 text-sm">
        {message}
      </p>
      <div className="space-y-3">
        {jobs.map((j) => (
          <article className="border rounded-lg p-3" key={j.id}>
            <p>
              {j.provider} · {j.status} · {j.id.slice(0, 8)}
            </p>
            {j.remote_id && (
              <p className="text-xs">Provider ID: {j.remote_id}</p>
            )}
            {j.error && <p role="alert">{j.error}</p>}
            {!["succeeded", "failed", "canceled", "expired"].includes(
              j.status,
            ) && (
              <button
                className="mf-button"
                onClick={async () => {
                  try {
                    await editorRequest(
                      `/api/providers/jobs/${j.id}/cancel`,
                      "POST",
                    );
                  } catch (e) {
                    setMessage(String(e));
                  }
                }}
              >
                Cancel where supported
              </button>
            )}
            {j.status === "rejected" && (
              <button className="mf-button" onClick={() => setReview(j)}>
                Review retry after fixing credentials or limits
              </button>
            )}
            {j.status === "unknown" && j.provider === "comfy" && (
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  try {
                    const data = new FormData(e.currentTarget);
                    await editorRequest(
                      `/api/providers/jobs/${j.id}/attach`,
                      "POST",
                      { remote_id: data.get("remote_id") },
                    );
                  } catch (e) {
                    setMessage(String(e));
                  }
                }}
              >
                <label>
                  Provider job ID from your Comfy history
                  <input name="remote_id" className="mf-input" required />
                </label>
                <button className="mf-button">Resume monitoring this ID</button>
              </form>
            )}
            {j.result?.asset_id && j.result?.cues && (
              <button
                className="mf-button"
                onClick={() => {
                  const captions = { ...project.captions };
                  for (const clip of project.clips) {
                    if (clip.asset_id === j.result?.asset_id)
                      captions[clip.uid] = j.result.cues!;
                  }
                  onChange({
                    ...project,
                    captions,
                    caption_source: "original",
                  });
                }}
              >
                Apply cloud transcript to matching source clips
              </button>
            )}
            {j.result?.assets.map((path, i) => (
              <div className="my-2" key={path}>
                {/\.(mp3|wav)$/.test(path) ? (
                  <audio controls src={mediaUrl(path)} />
                ) : /\.(mp4|webm|mov)$/.test(path) ? (
                  <video controls src={mediaUrl(path)} className="max-h-52" />
                ) : (
                  <img
                    src={mediaUrl(path)}
                    alt="Generated output"
                    className="max-h-52"
                  />
                )}
                <a href={mediaUrl(path)} download className="mf-button">
                  Download
                </a>
                {j.provider === "elevenlabs" ? (
                  <>
                    <p className="text-sm">
                      Narration {j.result?.duration?.toFixed(1)}s · edit{" "}
                      {duration(project).toFixed(1)}s. Longer audio is trimmed
                      at export; shorter audio leaves room for original
                      sound/music. Adjust clip trims or regenerate an edited
                      script.
                    </p>
                    <button
                      className="mf-button"
                      onClick={() =>
                        onChange({
                          ...project,
                          voice_over: {
                            ...project.voice_over,
                            enabled: true,
                            path,
                          },
                          narration_captions: j.result?.cues ?? [],
                          caption_source: "narration",
                          generated_assets: [
                            ...new Set([...project.generated_assets, j.id]),
                          ],
                        })
                      }
                    >
                      Use narration & timing
                    </button>
                  </>
                ) : (
                  <button
                    className="mf-button"
                    onClick={async () => {
                      try {
                        const a = await editorRequest<{
                          asset_id: number;
                          duration: number;
                        }>(`/api/providers/jobs/${j.id}/import/${i}`, "POST");
                        onChange({
                          ...project,
                          clips: [
                            ...project.clips,
                            {
                              uid: crypto.randomUUID(),
                              asset_id: a.asset_id,
                              start: 0,
                              end: a.duration,
                              speed: 1,
                              transition: "crossfade",
                            },
                          ],
                          generated_assets: [
                            ...new Set([...project.generated_assets, j.id]),
                          ],
                        });
                      } catch (e) {
                        setMessage(String(e));
                      }
                    }}
                  >
                    Add selected result to sequence
                  </button>
                )}
              </div>
            ))}
          </article>
        ))}
      </div>
    </details>
  );
}
