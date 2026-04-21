import { useEffect, useState } from "react";
import { CheckCircle2, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

const STAGES = [
  "A extrair ficheiros do arquivo .zip…",
  "A analisar conteúdo com IA…",
  "A aplicar regras de fraude e compliance…",
  "A consolidar relatório de auditoria…",
];

export function ProcessingState() {
  const [activeStage, setActiveStage] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setActiveStage((s) => (s < STAGES.length - 1 ? s + 1 : s));
    }, 1250);
    return () => clearInterval(interval);
  }, []);

  return (
    <section className="mx-auto w-full max-w-3xl animate-fade-in-up">
      <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-card">
        <div className="relative h-1 w-full overflow-hidden bg-muted">
          <div className="animate-shimmer absolute inset-0 h-full w-full" />
          <div className="absolute inset-y-0 left-0 h-full w-1/3 bg-primary/80" />
        </div>

        <div className="p-8">
          <div className="mb-6 flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-foreground">
                A processar a sua auditoria
              </h2>
              <p className="text-xs text-muted-foreground">
                Isto pode demorar alguns segundos. Não feche esta página.
              </p>
            </div>
          </div>

          <ol className="space-y-3">
            {STAGES.map((stage, idx) => {
              const done = idx < activeStage;
              const active = idx === activeStage;
              return (
                <li
                  key={stage}
                  className={cn(
                    "flex items-center gap-3 rounded-lg border px-4 py-3 transition-all",
                    active && "border-primary/40 bg-accent",
                    done && "border-border bg-surface",
                    !done && !active && "border-border bg-card opacity-60"
                  )}
                >
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center">
                    {done ? (
                      <CheckCircle2 className="h-5 w-5 text-success" />
                    ) : active ? (
                      <Loader2 className="h-4 w-4 animate-spin text-primary" />
                    ) : (
                      <span className="h-2 w-2 rounded-full bg-muted-foreground/40" />
                    )}
                  </span>
                  <span
                    className={cn(
                      "text-sm",
                      active && "font-medium text-foreground",
                      done && "text-muted-foreground line-through decoration-muted-foreground/40",
                      !done && !active && "text-muted-foreground"
                    )}
                  >
                    {stage}
                  </span>
                </li>
              );
            })}
          </ol>

          {/* Skeleton preview */}
          <div className="mt-8 space-y-3">
            <div className="grid grid-cols-3 gap-3">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="h-20 animate-pulse rounded-lg border border-border bg-muted/60"
                />
              ))}
            </div>
            <div className="space-y-2 rounded-lg border border-border bg-card p-4">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="flex items-center gap-3">
                  <div className="h-3 w-1/4 animate-pulse rounded bg-muted" />
                  <div className="h-3 w-1/3 animate-pulse rounded bg-muted" />
                  <div className="h-3 flex-1 animate-pulse rounded bg-muted" />
                  <div className="h-5 w-16 animate-pulse rounded-full bg-muted" />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
