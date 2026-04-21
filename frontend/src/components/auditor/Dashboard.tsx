import { useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowDownToLine,
  CheckCircle2,
  FileText,
  Filter,
  RotateCcw,
  Search,
  ShieldAlert,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  REPORT_DOWNLOAD_URL,
  type AuditRow,
  type AuditStatus,
  type AuditoriaResult,
} from "@/lib/auditor-api";

interface DashboardProps {
  onReset: () => void;
  result: AuditoriaResult;
}

type Status = AuditStatus;

function formatNumber(n: number) {
  return new Intl.NumberFormat("pt-PT").format(n);
}

function StatusBadge({ status }: { status: Status }) {
  if (status === "APROVADO") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-success/20 bg-success/10 px-2 py-0.5 text-xs font-medium text-success">
        <span className="h-1.5 w-1.5 rounded-full bg-success" />
        APROVADO
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-destructive/20 bg-destructive/10 px-2 py-0.5 text-xs font-medium text-destructive">
      <span className="h-1.5 w-1.5 rounded-full bg-destructive" />
      REPROVADO
    </span>
  );
}

export function Dashboard({ onReset, result }: DashboardProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"ALL" | Status>("ALL");

  const allRows: AuditRow[] = result.rows;
  const { total, approved, fraud } = result.summary;
  const fraudPct = total > 0 ? ((fraud / total) * 100).toFixed(1).replace(".", ",") : "0,0";
  const approvedPct = total > 0 ? ((approved / total) * 100).toFixed(1).replace(".", ",") : "0,0";

  const SUMMARY = [
    {
      label: "Total Processado",
      value: formatNumber(total),
      sub: "documentos analisados",
      icon: FileText,
      tone: "neutral" as const,
    },
    {
      label: "Aprovados",
      value: formatNumber(approved),
      sub: `${approvedPct}% do lote`,
      icon: CheckCircle2,
      tone: "success" as const,
    },
    {
      label: "Fraudes Detetadas",
      value: formatNumber(fraud),
      sub: `${fraudPct}% requerem revisão`,
      icon: ShieldAlert,
      tone: "danger" as const,
    },
  ];

  const rows = useMemo(() => {
    return allRows.filter((r) => {
      if (filter !== "ALL" && r.status !== filter) return false;
      if (!query) return true;
      const q = query.toLowerCase();
      return (
        r.file.toLowerCase().includes(q) ||
        r.vendor.toLowerCase().includes(q) ||
        r.anomaly.toLowerCase().includes(q)
      );
    });
  }, [query, filter, allRows]);

  return (
    <div className="animate-fade-in-up space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="mb-1 flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <span>Auditoria</span>
            <span>/</span>
            <span className="text-foreground">Lote #A-2026-0418</span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Resultados da Auditoria
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Concluído há instantes · {formatNumber(total)} documentos · motor v3.2
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={onReset}>
            <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
            Nova auditoria
          </Button>
          <Button size="sm" asChild>
            <a href={REPORT_DOWNLOAD_URL} target="_blank" rel="noopener noreferrer" download>
              <ArrowDownToLine className="mr-1.5 h-3.5 w-3.5" />
              Baixar Relatório
            </a>
          </Button>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {SUMMARY.map((card) => {
          const Icon = card.icon;
          const isDanger = card.tone === "danger";
          const isSuccess = card.tone === "success";
          return (
            <div
              key={card.label}
              className={cn(
                "relative overflow-hidden rounded-xl border bg-card p-5 shadow-card transition-all hover:shadow-elevated",
                isDanger && "border-destructive/30 bg-destructive/[0.03]"
              )}
            >
              <div className="flex items-start justify-between">
                <div>
                  <p
                    className={cn(
                      "text-xs font-medium uppercase tracking-wider",
                      isDanger ? "text-destructive" : "text-muted-foreground"
                    )}
                  >
                    {card.label}
                  </p>
                  <p
                    className={cn(
                      "mt-2 text-3xl font-semibold tracking-tight tabular-nums",
                      isDanger && "text-destructive",
                      isSuccess && "text-success",
                      !isDanger && !isSuccess && "text-foreground"
                    )}
                  >
                    {card.value}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">{card.sub}</p>
                </div>
                <div
                  className={cn(
                    "flex h-9 w-9 items-center justify-center rounded-lg",
                    isDanger && "bg-destructive/10 text-destructive",
                    isSuccess && "bg-success/10 text-success",
                    !isDanger && !isSuccess && "bg-muted text-muted-foreground"
                  )}
                >
                  <Icon className="h-4.5 w-4.5" strokeWidth={2} />
                </div>
              </div>

              {isDanger && (
                <div className="mt-4 flex items-center gap-1.5 rounded-md border border-destructive/20 bg-destructive/5 px-2.5 py-1.5 text-xs text-destructive">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  Acção urgente recomendada
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Table */}
      <div className="overflow-hidden rounded-xl border border-border bg-card shadow-card">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-4">
          <div>
            <h2 className="text-sm font-semibold text-foreground">
              Registo de Auditoria
            </h2>
            <p className="text-xs text-muted-foreground">
              {rows.length} de {allRows.length} ocorrências apresentadas
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Pesquisar ficheiro, fornecedor…"
                className="h-9 w-64 pl-8 text-sm"
              />
            </div>
            <div className="flex items-center overflow-hidden rounded-md border border-border">
              {(["ALL", "REPROVADO", "APROVADO"] as const).map((f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={cn(
                    "px-3 py-1.5 text-xs font-medium transition-colors",
                    filter === f
                      ? "bg-foreground text-background"
                      : "bg-card text-muted-foreground hover:bg-muted"
                  )}
                >
                  {f === "ALL" ? "Todos" : f === "APROVADO" ? "Aprovados" : "Fraudes"}
                </button>
              ))}
            </div>
            <Button variant="outline" size="sm" className="h-9">
              <Filter className="mr-1.5 h-3.5 w-3.5" />
              Filtros
            </Button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-surface text-left text-xs font-medium uppercase tracking-wider text-muted-foreground">
                <th className="px-4 py-2.5 font-medium">Ficheiro</th>
                <th className="px-4 py-2.5 font-medium">Fornecedor</th>
                <th className="px-4 py-2.5 font-medium">Anomalia</th>
                <th className="px-4 py-2.5 text-right font-medium">Valor</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr
                  key={row.file}
                  className={cn(
                    "border-b border-border/60 transition-colors last:border-0 hover:bg-surface",
                    i % 2 === 1 && "bg-surface/40"
                  )}
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                      <span className="font-mono text-xs text-foreground">
                        {row.file}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-foreground">{row.vendor}</td>
                  <td className="px-4 py-3">
                    {row.anomaly === "—" ? (
                      <span className="text-muted-foreground">—</span>
                    ) : (
                      <span className="text-foreground">{row.anomaly}</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-foreground">
                    {row.amount}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={row.status} />
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-12 text-center text-sm text-muted-foreground">
                    Nenhum registo corresponde aos filtros aplicados.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between border-t border-border bg-surface/60 px-4 py-2.5 text-xs text-muted-foreground">
          <span>A mostrar {rows.length} de {formatNumber(allRows.length)} registos</span>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm" className="h-7 text-xs" disabled>
              Anterior
            </Button>
            <Button variant="ghost" size="sm" className="h-7 text-xs" disabled={rows.length >= allRows.length}>
              Seguinte
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
