/**
 * PRApproval — approve or reject a ForgeChain job that is in REVIEW state.
 */

import { useState } from "react";
import { Button } from "../../../frontend/src/components/ui/button";
import { DiffViewer } from "./DiffViewer";

interface Job {
  task_id: string;
  state: string;
  role: string;
  pr_url?: string;
  patch?: string;
  description?: string;
  error?: string;
}

interface PRApprovalProps {
  job: Job;
  reviewer: string;
  apiBase?: string;
  onDecision?: (taskId: string, decision: "approved" | "rejected") => void;
}

export function PRApproval({
  job,
  reviewer,
  apiBase = "http://localhost:8000",
  onDecision,
}: PRApprovalProps) {
  const [comment, setComment] = useState("");
  const [loading, setLoading] = useState<"approve" | "reject" | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const isReview = job.state === "review";

  async function handleDecision(decision: "approve" | "reject") {
    setError(null);
    setLoading(decision);
    const endpoint =
      decision === "approve"
        ? `/forgechain/jobs/${job.task_id}/approve`
        : `/forgechain/jobs/${job.task_id}/reject`;

    try {
      const res = await fetch(`${apiBase}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewer, comment }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail ?? `HTTP ${res.status}`);
      }
      setResult(decision === "approve" ? "Approved!" : "Rejected — job re-queued.");
      onDecision?.(job.task_id, decision === "approve" ? "approved" : "rejected");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="space-y-4 p-4 border rounded-xl bg-card">
      <div className="flex items-center justify-between">
        <div>
          <span className="text-xs font-mono text-muted-foreground">{job.task_id}</span>
          <h3 className="font-semibold text-sm mt-0.5">
            [{job.role.toUpperCase()}] Review Required
          </h3>
        </div>
        <span
          className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            job.state === "review"
              ? "bg-amber-100 text-amber-700"
              : job.state === "done"
              ? "bg-green-100 text-green-700"
              : "bg-slate-100 text-slate-600"
          }`}
        >
          {job.state}
        </span>
      </div>

      {job.pr_url && (
        <a
          href={job.pr_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm text-blue-600 hover:underline"
        >
          View GitHub PR →
        </a>
      )}

      {job.patch && <DiffViewer patch={job.patch} />}

      {isReview && !result && (
        <>
          <div>
            <label className="block text-sm font-medium mb-1">Review Comment (optional)</label>
            <textarea
              className="w-full min-h-[60px] rounded-md border bg-background px-3 py-2 text-sm"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Feedback for the agent on retry..."
              maxLength={2000}
            />
          </div>

          {error && (
            <p className="text-sm text-destructive bg-destructive/10 rounded p-2">{error}</p>
          )}

          <div className="flex gap-2">
            <Button
              onClick={() => handleDecision("approve")}
              disabled={loading !== null}
              className="bg-green-600 hover:bg-green-700 text-white"
            >
              {loading === "approve" ? "Approving..." : "Approve & Merge"}
            </Button>
            <Button
              onClick={() => handleDecision("reject")}
              disabled={loading !== null}
              variant="destructive"
            >
              {loading === "reject" ? "Rejecting..." : "Reject & Retry"}
            </Button>
          </div>
        </>
      )}

      {result && (
        <p className="text-sm text-green-700 bg-green-50 rounded p-2 font-medium">{result}</p>
      )}
    </div>
  );
}
