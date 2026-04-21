export const API_BASE_URL = "https://nlconsulting-ai-auditor.onrender.com";

export type AuditStatus = "APROVADO" | "REPROVADO";

export interface AuditRow {
  file: string;
  vendor: string;
  anomaly: string;
  amount: string;
  status: AuditStatus;
}

export interface AuditoriaSummary {
  total: number;
  approved: number;
  fraud: number;
}

export interface AuditoriaResult {
  summary: AuditoriaSummary;
  rows: AuditRow[];
  raw: unknown;
}

function formatCurrency(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(String(value).replace(/[^\d.,-]/g, "").replace(",", "."));
  if (!Number.isFinite(n)) return String(value);
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
  }).format(n);
}

function normalizeStatus(value: unknown): AuditStatus {
  const s = String(value ?? "").toUpperCase().trim();
  if (["APROVADO", "APPROVED", "OK", "VALID", "VÁLIDO", "VALIDO", "PASS"].includes(s)) {
    return "APROVADO";
  }
  return "REPROVADO";
}

// 🔥 A MAGIA DA JUNÇÃO DE DADOS
export function normalizeAuditoriaResponse(data: any): AuditoriaResult {
  console.log("🔥 JSON RECEBIDO DO BACKEND:", data);

  // Pega nas duas "gavetas" separadas que o Python criou
  const extracoes = data?.extracao_ia || [];
  const resultadosAuditoria = data?.auditoria?.resultados || [];

  // Mescla os dados pelo nome do ficheiro
  const rows = extracoes.map((item: any, index: number) => {
    const dados = item.dados_extraidos || {};
    const file = item.nome_arquivo || `DOC_${String(index + 1).padStart(3, "0")}`;

    // Procura a anomalia deste documento específico
    const auditoriaItem = resultadosAuditoria.find((r: any) => r.nome_arquivo === file) || {};

    // Aqui estão os nomes exatos do seu JSON!
    const vendor = dados.fornecedor || "—";
    const amount = formatCurrency(dados.valor_bruto || 0);

    // Extrai o texto da 'evidencia' da fraude
    let anomalyStr = "—";
    if (Array.isArray(auditoriaItem.anomalias) && auditoriaItem.anomalias.length > 0) {
      anomalyStr = auditoriaItem.anomalias.map((a: any) => a.evidencia || a.regra).join(" | ");
    }

    const status = normalizeStatus(auditoriaItem.status_auditoria || "REPROVADO");

    return { file, vendor, anomaly: anomalyStr, amount, status };
  });

  const sBlock = data?.auditoria || {};
  const total = sBlock.total_documentos_auditados ?? rows.length;
  const approved = sBlock.total_aprovados ?? rows.filter((r) => r.status === "APROVADO").length;
  const fraud = sBlock.total_reprovados ?? rows.filter((r) => r.status === "REPROVADO").length;

  return {
    summary: { total: Number(total) || 0, approved: Number(approved) || 0, fraud: Number(fraud) || 0 },
    rows,
    raw: data,
  };
}

export type JobStatus = "processing" | "completed" | "error";

export interface JobResponse {
  status: JobStatus;
  result?: unknown;
  message?: string;
  error?: string;
  detail?: string;
}

async function parseError(res: Response): Promise<string> {
  let detail = `${res.status} ${res.statusText}`;
  try {
    const err = await res.json();
    if (err?.detail) detail = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
    else if (err?.message) detail = err.message;
    else if (err?.error) detail = err.error;
  } catch {
    // ignore
  }
  return detail;
}

export async function submitJob(file: File): Promise<string> {
  const formData = new FormData();
  formData.append("arquivo", file);

  const res = await fetch(`${API_BASE_URL}/api/process-documents`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) throw new Error(await parseError(res));

  const json = await res.json();
  const jobId = json?.job_id ?? json?.jobId ?? json?.id;

  if (!jobId || typeof jobId !== "string") {
    throw new Error("Resposta inválida da API: job_id em falta");
  }
  return jobId;
}

export async function fetchJobStatus(jobId: string): Promise<JobResponse> {
  const res = await fetch(`${API_BASE_URL}/api/jobs/${jobId}`);
  if (!res.ok) throw new Error(await parseError(res));
  return (await res.json()) as JobResponse;
}

export interface PollOptions {
  intervalMs?: number;
  signal?: AbortSignal;
}

export function pollJob(
  jobId: string,
  onUpdate: (job: JobResponse) => void,
  options: PollOptions = {},
): Promise<AuditoriaResult> {
  const { intervalMs = 5000, signal } = options;

  return new Promise<AuditoriaResult>((resolve, reject) => {
    let stopped = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const stop = () => {
      stopped = true;
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
      if (signal) signal.removeEventListener("abort", onAbort);
    };

    const onAbort = () => {
      stop();
      reject(new DOMException("Polling cancelado", "AbortError"));
    };

    if (signal) {
      if (signal.aborted) {
        onAbort();
        return;
      }
      signal.addEventListener("abort", onAbort);
    }

    const tick = async () => {
      if (stopped) return;
      try {
        const job = await fetchJobStatus(jobId);
        if (stopped) return;
        onUpdate(job);

        if (job.status === "completed") {
          stop();
          resolve(normalizeAuditoriaResponse(job.result ?? {}));
        } else if (job.status === "error") {
          stop();
          const msg = job.message ?? job.error ?? job.detail ?? "Erro interno no processamento.";
          reject(new Error(typeof msg === "string" ? msg : JSON.stringify(msg)));
        }
      } catch (err) {
        console.warn("Erro temporário de rede. A aguardar próximo ciclo...", err);
      }
    };

    void tick();
    timer = setInterval(tick, intervalMs);
  });
}

export async function processDocuments(
  file: File,
  options: PollOptions & { onUpdate?: (job: JobResponse) => void } = {},
): Promise<AuditoriaResult> {
  const jobId = await submitJob(file);
  return pollJob(jobId, options.onUpdate ?? (() => { }), options);
}

export const REPORT_DOWNLOAD_URL = `${API_BASE_URL}/api/downloads/base_auditoria.csv`;