import { ShieldCheck } from "lucide-react";

export function TopBar() {
  return (
    <header className="sticky top-0 z-30 w-full border-b border-border/80 bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-6">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <ShieldCheck className="h-4 w-4" strokeWidth={2.5} />
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-sm font-semibold tracking-tight text-foreground">
              AuditorIA
            </span>
            <span className="text-xs font-medium text-muted-foreground">
              NLConsulting
            </span>
          </div>
        </div>

        <nav className="hidden items-center gap-6 md:flex">
          <a className="text-sm text-muted-foreground transition-colors hover:text-foreground" href="#">
            Auditorias
          </a>
          <a className="text-sm text-muted-foreground transition-colors hover:text-foreground" href="#">
            Relatórios
          </a>
          <a className="text-sm text-muted-foreground transition-colors hover:text-foreground" href="#">
            Definições
          </a>
        </nav>

        <div className="flex items-center gap-3">
          <div className="hidden items-center gap-2 rounded-full border border-border bg-surface px-3 py-1 sm:flex">
            <span className="h-1.5 w-1.5 rounded-full bg-success" />
            <span className="text-xs font-medium text-muted-foreground">
              Motor IA online
            </span>
          </div>
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
            NC
          </div>
        </div>
      </div>
    </header>
  );
}
