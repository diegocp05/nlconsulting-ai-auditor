import { useCallback, useRef, useState } from "react";
import { FileArchive, UploadCloud, X, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface UploadZoneProps {
  onProcess: (file: File) => void;
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

export function UploadZone({ onProcess }: UploadZoneProps) {
  const [file, setFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFiles = useCallback((files: FileList | null) => {
    if (!files || files.length === 0) return;
    const f = files[0];
    setFile(f);
  }, []);

  return (
    <section className="mx-auto w-full max-w-3xl">
      <div className="mb-6 text-center">
        <div className="mb-3 inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-muted-foreground">
          <Sparkles className="h-3 w-3 text-primary" />
          Auditoria automatizada com IA
        </div>
        <h1 className="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
          Carregue o seu lote de documentos
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Arraste um ficheiro <code className="rounded bg-muted px-1 py-0.5 text-xs">.zip</code>{" "}
          contendo notas fiscais, contratos ou recibos para iniciar a análise.
        </p>
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          handleFiles(e.dataTransfer.files);
        }}
        className={cn(
          "relative overflow-hidden rounded-2xl border-2 border-dashed bg-surface p-10 text-center transition-all",
          dragOver
            ? "border-primary bg-accent shadow-glow"
            : "border-border hover:border-primary/50 hover:bg-accent/40"
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".zip"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />

        {!file ? (
          <div className="flex flex-col items-center gap-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-primary/10 text-primary">
              <UploadCloud className="h-7 w-7" strokeWidth={1.75} />
            </div>
            <div>
              <p className="text-base font-medium text-foreground">
                Arraste o ficheiro .zip para aqui
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                ou clique no botão abaixo para selecionar do seu computador
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => inputRef.current?.click()}
            >
              Selecionar ficheiro
            </Button>
            <p className="text-xs text-muted-foreground">
              Tamanho máximo: 500 MB · Apenas .zip
            </p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-4">
            <div className="flex w-full max-w-md items-center gap-3 rounded-xl border border-border bg-card p-3 text-left shadow-card">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <FileArchive className="h-5 w-5" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">
                  {file.name}
                </p>
                <p className="text-xs text-muted-foreground">
                  {formatBytes(file.size)} · pronto para análise
                </p>
              </div>
              <button
                onClick={() => setFile(null)}
                className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                aria-label="Remover ficheiro"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="mt-6 flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          Os seus ficheiros são processados em ambiente isolado e cifrado.
        </p>
        <Button
          size="lg"
          disabled={!file}
          onClick={() => file && onProcess(file)}
          className="font-medium"
        >
          Processar Auditoria
        </Button>
      </div>
    </section>
  );
}
