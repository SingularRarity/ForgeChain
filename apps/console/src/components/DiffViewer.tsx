/**
 * DiffViewer — renders a unified diff patch with syntax highlighting.
 *
 * Uses simple line-by-line colouring (no external diff library dependency).
 * For a richer experience, drop in `react-diff-viewer-continued`.
 */

interface DiffViewerProps {
  patch: string;
  maxLines?: number;
}

type LineKind = "added" | "removed" | "header" | "context";

function classifyLine(line: string): LineKind {
  if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")) return "header";
  if (line.startsWith("+")) return "added";
  if (line.startsWith("-")) return "removed";
  return "context";
}

const LINE_STYLES: Record<LineKind, string> = {
  added: "bg-green-50 text-green-900 border-l-2 border-green-400",
  removed: "bg-red-50 text-red-900 border-l-2 border-red-400",
  header: "bg-slate-100 text-slate-600 font-semibold",
  context: "text-slate-700",
};

export function DiffViewer({ patch, maxLines = 500 }: DiffViewerProps) {
  if (!patch) {
    return (
      <div className="rounded border p-4 text-sm text-muted-foreground italic">
        No patch available yet.
      </div>
    );
  }

  const lines = patch.split("\n").slice(0, maxLines);
  const truncated = patch.split("\n").length > maxLines;

  return (
    <div className="rounded-lg border overflow-auto max-h-[600px] text-xs font-mono">
      <div className="bg-slate-800 text-slate-200 px-3 py-1.5 text-xs font-sans font-medium sticky top-0">
        Patch Preview
      </div>
      <table className="w-full border-collapse">
        <tbody>
          {lines.map((line, i) => {
            const kind = classifyLine(line);
            return (
              <tr key={i} className={LINE_STYLES[kind]}>
                <td className="select-none text-right text-slate-400 px-2 py-0 w-10 text-[10px]">
                  {i + 1}
                </td>
                <td className="px-2 py-0 whitespace-pre">{line || " "}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {truncated && (
        <div className="bg-amber-50 text-amber-700 text-xs px-3 py-1 border-t">
          Output truncated at {maxLines} lines.
        </div>
      )}
    </div>
  );
}
