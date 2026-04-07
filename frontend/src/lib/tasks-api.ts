// API calls for the Tasks History page.

import { TaskSummaryListResponse, TaskDetail } from './types';

const BASE = '/api/v1';

export async function fetchTaskSummaries(
  params: Record<string, unknown>,
): Promise<TaskSummaryListResponse> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value));
    }
  }
  const res = await fetch(`${BASE}/tasks?${query.toString()}`);
  if (!res.ok) {
    throw new Error(`Failed to fetch tasks: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<TaskSummaryListResponse>;
}

export async function fetchTaskDetail(taskId: string): Promise<TaskDetail> {
  const res = await fetch(`${BASE}/tasks/${taskId}`);
  if (!res.ok) {
    throw new Error(`Failed to fetch task ${taskId}: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<TaskDetail>;
}

export async function deleteTask(taskId: string): Promise<void> {
  const res = await fetch(`${BASE}/tasks/${taskId}`, { method: 'DELETE' });
  if (!res.ok) {
    throw new Error(`Failed to delete task ${taskId}: ${res.status} ${res.statusText}`);
  }
}
