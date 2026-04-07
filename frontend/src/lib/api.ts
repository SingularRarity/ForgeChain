// Core API service — used by the Dashboard and its components.

const BASE = '/api/v1';

// ── Types ─────────────────────────────────────────────────────────────────── //

export interface QueueDepths {
  primary: number;
  retry: number;
  scheduled: number;
  dlq: number;
}

export interface QueueStatus {
  queues: QueueDepths;
  states: Record<string, number>;
  retry_ratio: number;
}

export interface SSEMessage {
  type: 'initial_status' | 'queue_update' | 'heartbeat' | 'error' | 'fatal_error';
  queue_depths?: QueueDepths;
  state_counts?: Record<string, number>;
  retry_ratio?: number;
  message?: string;
}

export interface OpenRouterStatus {
  status: string;
  message: string;
  circuit_breaker_open?: boolean;
  consecutive_failures?: number;
}

export interface CircuitBreaker {
  state: string;
  success_count: number;
  fail_count: number;
  note?: string;
}

export interface WorkerDetail {
  worker_id: string;
  worker_name?: string;
  status: string;
  last_heartbeat?: string | number;
  timestamp?: string | number;
  error?: string;
  circuit_breaker: CircuitBreaker;
}

export interface WorkerStatus {
  total_workers: number;
  healthy_workers: number;
  stale_workers: number;
  overall_status: string;
  worker_details: WorkerDetail[];
}

// ── API service ───────────────────────────────────────────────────────────── //

export const apiService = {
  async getOpenRouterStatus(): Promise<OpenRouterStatus> {
    const res = await fetch(`${BASE}/health/openrouter`);
    if (!res.ok) {
      throw new Error(`OpenRouter status check failed: ${res.status}`);
    }
    return res.json() as Promise<OpenRouterStatus>;
  },

  async getWorkerStatus(): Promise<WorkerStatus> {
    const res = await fetch(`${BASE}/workers`);
    if (!res.ok) {
      throw new Error(`Worker status check failed: ${res.status}`);
    }
    return res.json() as Promise<WorkerStatus>;
  },

  createSSEConnection(
    onMessage: (data: SSEMessage) => void,
    onError: (error: Event) => void,
  ): EventSource {
    const source = new EventSource(`${BASE}/queue-status/stream`);
    source.onmessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data as string) as SSEMessage;
        onMessage(data);
      } catch {
        // ignore malformed SSE frames
      }
    };
    source.onerror = onError;
    return source;
  },
};
