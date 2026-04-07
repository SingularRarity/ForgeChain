# Frontend Developer Skills
## Applied to role: frontend_dev

---

## Stack

- React 18, TypeScript 5, Vite
- Tailwind CSS v3 + shadcn/ui component primitives
- React Query (TanStack Query v5) for server state
- Zustand for client-only state
- React Hook Form + Zod for forms

---

## Component conventions

### Functional components only — no class components

```tsx
// Always name the function (no anonymous arrow exports at top level)
export function TaskCard({ task }: { task: Task }) {
  return <div className="rounded-lg border p-4">{task.description}</div>
}
```

### Props types inline for simple components, named interface for reused shapes

```tsx
interface TaskCardProps {
  task: Task
  onApprove: (id: string) => void
  isLoading?: boolean
}

export function TaskCard({ task, onApprove, isLoading = false }: TaskCardProps) {
  ...
}
```

---

## State management rules

**Server state** (anything from the API) → React Query, never useState

```tsx
const { data: jobs, isLoading } = useQuery({
  queryKey: ["forgechain", "jobs"],
  queryFn: () => fetch("/forgechain/jobs").then(r => r.json()),
  refetchInterval: 5000,  // poll every 5s for live status
})
```

**Client-only UI state** (modals, selections) → useState or Zustand

```tsx
// WRONG: server data in local state
const [jobs, setJobs] = useState([])
useEffect(() => { fetchJobs().then(setJobs) }, [])

// CORRECT: React Query
const { data: jobs } = useQuery(...)
```

**Never mutate state directly**

```tsx
// WRONG
items.push(newItem)
setItems(items)

// CORRECT
setItems(prev => [...prev, newItem])
```

---

## Tailwind patterns used in this project

```tsx
// Status badge pattern
const STATE_CLASSES = {
  pending:  "bg-slate-100 text-slate-600",
  running:  "bg-blue-100 text-blue-700",
  review:   "bg-amber-100 text-amber-700",
  approved: "bg-green-100 text-green-700",
  done:     "bg-green-100 text-green-700",
  failed:   "bg-red-100 text-red-700",
} as const

// Card layout
<div className="rounded-xl border bg-card p-4 space-y-3">
```

---

## API calls — always typed, always handle errors

```tsx
async function approveJob(taskId: string, reviewer: string): Promise<JobResponse> {
  const res = await fetch(`/forgechain/jobs/${taskId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reviewer }),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail ?? `HTTP ${res.status}`)
  }
  return res.json()
}
```

---

## Accessibility checklist

- All interactive elements reachable by keyboard
- `aria-label` on icon-only buttons
- Color is never the sole indicator of state — always add text or icon
- Loading states communicated via `aria-busy` or visually hidden text

---

## What to avoid

- `any` type — use `unknown` and narrow it
- Inline styles — use Tailwind classes
- `document.getElementById` — use refs
- `useEffect` for derived state — compute directly or use `useMemo`
- Large component files (>200 lines) — extract sub-components
