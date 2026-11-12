import { RefreshCwIcon } from "lucide-react";
import { Button } from "@/components/ui/button";

/** A refresh failed after the view had loaded: say the numbers on screen may be old. */
export function StaleNotice({ stale, onRetry }: { stale: boolean; onRetry: () => void }) {
  if (!stale) return null;
  return (
    <div
      role="status"
      className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/5 py-1.5 pr-1.5 pl-3 text-sm"
    >
      <p>No se pudo actualizar</p>
      <Button variant="ghost" size="sm" onClick={onRetry}>
        <RefreshCwIcon data-icon="inline-start" />
        Reintentar
      </Button>
    </div>
  );
}
