// Shared domain types for the AsyncTaskFlow / ForgeChain frontend.

export enum TaskState {
  PENDING   = 'pending',
  ACTIVE    = 'active',
  COMPLETED = 'completed',
  FAILED    = 'failed',
  SCHEDULED = 'scheduled',
  DLQ       = 'dlq',
}

export interface StateHistoryEntry {
  state: TaskState;
  timestamp: string;
}

export interface TaskSummary {
  task_id: string;
  state: TaskState;
  task_type: string;
  created_at: string;
  completed_at: string | null;
}

export interface TaskSummaryListResponse {
  tasks: TaskSummary[];
  total_items: number;
  total_pages: number;
  page: number;
  page_size: number;
}

export interface TaskDetail extends TaskSummary {
  updated_at: string;
  retry_count: number;
  max_retries: number;
  retry_after: string | null;
  last_error: string | null;
  error_type: string | null;
  content: string | null;
  result: string | null;
  state_history: StateHistoryEntry[];
  error_history: unknown[];
}
