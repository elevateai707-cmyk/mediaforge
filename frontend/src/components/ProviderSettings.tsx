import { useEffect, useState } from "react";
import { editorRequest } from "@/lib/editor";
export interface ProviderConfig {
  configured: Record<string, boolean>;
  workflows: Record<
    string,
    {
      workflow: Record<string, unknown>;
      mappings: Record<
        string,
        { node: string; input: string; type: "text" | "asset" }
      >;
    }
  >;
  max_daily_jobs: number;
  max_narration_chars: number;
}
export function ProviderSettings() {
  const [cfg, setCfg] = useState<ProviderConfig>();
  const [workflow, setWorkflow] = useState("{}");
  const [comfy, setComfy] = useState("");
  const [eleven, setEleven] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    editorRequest<ProviderConfig>("/api/providers/settings")
      .then((c) => {
        setCfg(c);
        setWorkflow(JSON.stringify(c.workflows ?? {}, null, 2));
      })
      .catch((e) => setMessage(e.message));
  }, []);
  async function save() {
    setBusy(true);
    try {
      const body = {
        workflows: JSON.parse(workflow),
        max_daily_jobs: cfg?.max_daily_jobs ?? 10,
        max_narration_chars: cfg?.max_narration_chars ?? 5000,
        ...(comfy ? { comfy_key: comfy } : {}),
        ...(eleven ? { elevenlabs_key: eleven } : {}),
      };
      setCfg(await editorRequest("/api/providers/settings", "PUT", body));
      setComfy("");
      setEleven("");
      setMessage(
        "Saved on this machine. Keys are never returned to the browser.",
      );
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="glass rounded-xl p-5 space-y-3">
      <h2 className="text-lg font-semibold">Optional cloud accounts</h2>
      <p>
        Local editing works without either account. This application accepts
        local connections only; use an SSH tunnel for remote access.
      </p>
      <label className="block">
        Comfy Cloud API key {cfg?.configured.comfy ? "· configured" : ""}
        <input
          className="mf-input"
          type="password"
          autoComplete="new-password"
          value={comfy}
          onChange={(e) => setComfy(e.target.value)}
        />
      </label>
      <label className="block">
        ElevenLabs API key {cfg?.configured.elevenlabs ? "· configured" : ""}
        <input
          className="mf-input"
          type="password"
          autoComplete="new-password"
          value={eleven}
          onChange={(e) => setEleven(e.target.value)}
        />
      </label>
      <label className="block">
        Maximum generation requests per 24 hours
        <input
          className="mf-input"
          type="number"
          min="0"
          value={cfg?.max_daily_jobs ?? 10}
          onChange={(e) =>
            cfg && setCfg({ ...cfg, max_daily_jobs: Number(e.target.value) })
          }
        />
      </label>
      <p className="text-xs">
        This limits submissions from MediaForge, not money spent or usage in
        other applications. Unknown submissions count toward the limit.
      </p>
      <details>
        <summary>Comfy workflows and input mappings</summary>
        <p className="my-2 text-sm">
          Export a tested workflow from Comfy Cloud using “Export Workflow
          (API)”. Add named configurations as shown in docs/EDITOR_V2.md. Only
          configured workflows appear in the editor; Cloud node compatibility is
          checked before upload.
        </p>
        <textarea
          aria-label="Workflow configurations JSON"
          className="mf-input font-mono h-64"
          value={workflow}
          onChange={(e) => setWorkflow(e.target.value)}
        />
      </details>
      <button className="mf-button" disabled={busy} onClick={() => void save()}>
        Save provider settings
      </button>
      {["comfy", "elevenlabs"].map((p) => (
        <button
          key={p}
          className="mf-button"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              setMessage(
                JSON.stringify(
                  await editorRequest(`/api/providers/${p}/test`, "POST"),
                ),
              );
            } catch (e) {
              setMessage(String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          Test {p} connection (no generation)
        </button>
      ))}
      <p role="status" className="text-sm whitespace-pre-wrap break-words">
        {message}
      </p>
    </section>
  );
}
