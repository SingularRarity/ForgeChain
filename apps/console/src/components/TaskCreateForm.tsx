import { useState } from "react";
import { Button } from "../../../frontend/src/components/ui/button";
import { Input } from "../../../frontend/src/components/ui/input";

const ROLES = [
  "auto",
  "frontend_dev",
  "backend_dev",
  "qa_backend",
  "db_eng",
  "ai_eng",
  "sre",
  "ba",
] as const;

type Role = (typeof ROLES)[number];

interface JobCreatePayload {
  description: string;
  role?: string;
  jira_ticket?: string;
  context?: string;
  pii_policy: "strict" | "permissive";
}

interface TaskCreateFormProps {
  apiBase?: string;
  onCreated?: (taskId: string) => void;
}

export function TaskCreateForm({
  apiBase = "http://localhost:8000",
  onCreated,
}: TaskCreateFormProps) {
  const [description, setDescription] = useState("");
  const [role, setRole] = useState<Role>("auto");
  const [jiraTicket, setJiraTicket] = useState("");
  const [context, setContext] = useState("");
  const [piiPolicy, setPiiPolicy] = useState<"strict" | "permissive">("strict");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setCreated(null);
    setLoading(true);

    const payload: JobCreatePayload = {
      description,
      pii_policy: piiPolicy,
    };
    if (role !== "auto") payload.role = role;
    if (jiraTicket.trim()) payload.jira_ticket = jiraTicket.trim();
    if (context.trim()) payload.context = context.trim();

    try {
      const res = await fetch(`${apiBase}/forgechain/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail ?? `HTTP ${res.status}`);
      }
      const data = await res.json();
      setCreated(data.task_id);
      onCreated?.(data.task_id);
      // Reset form
      setDescription("");
      setJiraTicket("");
      setContext("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4 p-4 border rounded-xl bg-card">
      <h2 className="text-lg font-semibold">New ForgeChain Job</h2>

      <div>
        <label className="block text-sm font-medium mb-1">Description *</label>
        <textarea
          className="w-full min-h-[100px] rounded-md border bg-background px-3 py-2 text-sm"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Describe what the agent should implement..."
          required
          minLength={10}
          maxLength={4000}
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium mb-1">Agent Role</label>
          <select
            className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            value={role}
            onChange={(e) => setRole(e.target.value as Role)}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r === "auto" ? "Auto-detect" : r}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">PII Policy</label>
          <select
            className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            value={piiPolicy}
            onChange={(e) => setPiiPolicy(e.target.value as "strict" | "permissive")}
          >
            <option value="strict">Strict (redact all PII)</option>
            <option value="permissive">Permissive</option>
          </select>
        </div>
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">JIRA Ticket (optional)</label>
        <textarea
          className="w-full min-h-[60px] rounded-md border bg-background px-3 py-2 text-sm"
          value={jiraTicket}
          onChange={(e) => setJiraTicket(e.target.value)}
          placeholder="Paste the full JIRA ticket body for BA role..."
          maxLength={8000}
        />
      </div>

      <div>
        <label className="block text-sm font-medium mb-1">Additional Context (optional)</label>
        <textarea
          className="w-full min-h-[60px] rounded-md border bg-background px-3 py-2 text-sm"
          value={context}
          onChange={(e) => setContext(e.target.value)}
          placeholder="Relevant files, existing patterns, constraints..."
          maxLength={8000}
        />
      </div>

      {error && (
        <p className="text-sm text-destructive bg-destructive/10 rounded p-2">{error}</p>
      )}
      {created && (
        <p className="text-sm text-green-600 bg-green-50 rounded p-2">
          Job created: <code className="font-mono">{created}</code>
        </p>
      )}

      <Button type="submit" disabled={loading || description.length < 10}>
        {loading ? "Submitting..." : "Submit Job"}
      </Button>
    </form>
  );
}
