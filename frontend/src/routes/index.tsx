import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import { TopBar } from "@/components/auditor/TopBar";
import { UploadZone } from "@/components/auditor/UploadZone";
import { ProcessingState } from "@/components/auditor/ProcessingState";
import { Dashboard } from "@/components/auditor/Dashboard";
import { processDocuments, type AuditoriaResult } from "@/lib/auditor-api";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "AuditorIA — NLConsulting · Auditoria financeira com IA" },
      {
        name: "description",
        content:
          "Plataforma de auditoria financeira automatizada com IA. Detete fraudes, anomalias e não-conformidades em lotes de documentos em segundos.",
      },
      { property: "og:title", content: "AuditorIA — NLConsulting" },
      {
        property: "og:description",
        content:
          "Auditoria financeira automatizada com IA. Carregue um .zip e obtenha um relatório de fraudes em segundos.",
      },
    ],
  }),
  component: Index,
});

type Stage = "upload" | "processing" | "results";

function Index() {
  const [stage, setStage] = useState<Stage>("upload");
  const [result, setResult] = useState<AuditoriaResult | null>(null);

  const handleProcess = async (file: File) => {
    setStage("processing");
    try {
      const data = await processDocuments(file);
      setResult(data);
      setStage("results");
      toast.success("Auditoria concluída com sucesso", {
        description: `${data.summary.total} documentos analisados`,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Erro desconhecido";
      toast.error("Falha ao processar a auditoria", {
        description: message,
      });
      setStage("upload");
    }
  };

  const handleReset = () => {
    setResult(null);
    setStage("upload");
  };

  return (
    <div className="min-h-screen bg-background">
      <TopBar />
      <main className="mx-auto max-w-7xl px-6 py-10 sm:py-14">
        {stage === "upload" && <UploadZone onProcess={handleProcess} />}
        {stage === "processing" && <ProcessingState />}
        {stage === "results" && result && <Dashboard onReset={handleReset} result={result} />}
      </main>
      <footer className="border-t border-border/80 bg-surface/40">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4 text-xs text-muted-foreground">
          <span>© 2026 NLConsulting · AuditorIA v3.2</span>
          <div className="flex items-center gap-4">
            <a href="#" className="hover:text-foreground">Termos</a>
            <a href="#" className="hover:text-foreground">Privacidade</a>
            <a href="#" className="hover:text-foreground">Estado do sistema</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
