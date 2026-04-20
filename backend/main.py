"""
backend/main.py
===============
Ponto de entrada do serviço de backend para o SaaS de Auditoria de Notas Fiscais.

Responsabilidades deste módulo:
  - Configurar a aplicação FastAPI e políticas de CORS.
  - Registrar os handlers globais de exceção para respostas HTTP padronizadas.
  - Expor as rotas HTTP (interface de transporte), delegando toda a lógica de
    negócio ao `DocumentProcessorService` e ao `LlmExtractorService`.

Padrões aplicados:
  - Separação de Conceitos (SoC): rotas apenas como interface HTTP.
  - Isolamento de Event Loop: operações bloqueantes em `asyncio.to_thread`;
    chamadas à IA via `AsyncOpenAI` completamente não-bloqueantes.
  - Segurança Anti-Zip Bomb: limites de tamanho e quantidade de arquivos.
  - Sanitização Heurística: encoding, nulos, proporção imprimível, truncagem.
  - Tratamento Global de Exceções: sem vazamento de stack trace ao cliente.
"""

from __future__ import annotations

import asyncio
import collections
import csv
import datetime
import io
import logging
import pathlib
import re
import string
import zipfile
from typing import Annotated

import os

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Bootstrap: carrega .env ANTES de qualquer import de módulo local
# ---------------------------------------------------------------------------
# ORDEM CRÍTICA: load_dotenv() deve ser a primeira instrução executável.
# O import de services.llm_extractor ocorre a seguir, garantindo que
# GROQ_API_KEY (e demais segredos) já estejam em os.environ quando o
# módulo de extração for inicializado pelo interpretador.
#
# O path explícito evita dependência do CWD do processo uvicorn.
_DOTENV_PATH = pathlib.Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_DOTENV_PATH, override=True)

# Importa a camada de integração com a IA (Groq via SDK OpenAI-compatível).
# Este import é feito DEPOIS do load_dotenv() para que GROQ_API_KEY já
# esteja disponível em os.environ no momento da inicialização do módulo.
from services.llm_extractor import ResultadoExtracao, extrair_lote_documentos  # noqa: E402

# ---------------------------------------------------------------------------
# Configuração da Aplicação (pydantic-settings lê de os.environ / .env)
# ---------------------------------------------------------------------------


class AppSettings(BaseSettings):
    """Parâmetros de configuração lidos de variáveis de ambiente.

    Em desenvolvimento: definidos no arquivo .env (carregado pelo load_dotenv acima).
    Em produção (Render): configurados no dashboard Environment > Environment Variables.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "info"

    # CORS: lista de origens separadas por vírgula.
    # Exemplo de valor em prod: "https://auditor.vercel.app,https://www.nlconsulting.com.br"
    allowed_origins: str = "http://localhost:3000,http://localhost:5173"

    @property
    def cors_origins(self) -> list[str]:
        """Parse da string CSV para lista de origens válidas."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


settings = AppSettings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("auditor.backend")
logger.info(
    "AuditorIA iniciando | ambiente=%s | cors_origins=%s",
    settings.environment,
    settings.cors_origins,
)

# ---------------------------------------------------------------------------
# Constantes de Segurança (Anti-Zip Bomb)
# ---------------------------------------------------------------------------

#: Tamanho máximo descompactado permitido (50 MB).
ZIP_MAX_UNCOMPRESSED_BYTES: int = 50 * 1024 * 1024

#: Quantidade máxima de entradas (arquivos) aceitas dentro do ZIP.
ZIP_MAX_FILE_COUNT: int = 1_500

#: Tamanho máximo do payload HTTP recebido (60 MB — folga sobre o ZIP).
HTTP_MAX_UPLOAD_BYTES: int = 60 * 1024 * 1024

# ---------------------------------------------------------------------------
# Constantes de Sanitização Heurística
# ---------------------------------------------------------------------------

#: Proporção mínima de caracteres imprimíveis para o arquivo ser considerado texto.
MIN_PRINTABLE_RATIO: float = 0.85

#: Comprimento mínimo (em caracteres) para o arquivo não ser considerado truncado.
MIN_CHAR_LENGTH: int = 30

#: Quantidade mínima de quebras de linha para o arquivo não ser considerado truncado.
MIN_NEWLINE_COUNT: int = 3

# ---------------------------------------------------------------------------
# Modelos de Dados — Pydantic V2
# ---------------------------------------------------------------------------


class DocumentoValido(BaseModel):
    """Representa um documento de nota fiscal que passou em todas as verificações.

    Attributes:
        nome_arquivo: Nome original do arquivo dentro do ZIP.
        conteudo: Texto sanitizado e pronto para envio à IA.
        tamanho_bytes: Tamanho do conteúdo final em bytes (após sanitização).
    """

    nome_arquivo: str = Field(..., description="Nome original do arquivo no ZIP.")
    conteudo: str = Field(..., description="Conteúdo textual sanitizado.")
    tamanho_bytes: int = Field(..., ge=0, description="Tamanho em bytes do conteúdo sanitizado.")


class DocumentoComErro(BaseModel):
    """Representa um documento que foi rejeitado pela sanitização heurística.

    Attributes:
        nome_arquivo: Nome original do arquivo dentro do ZIP.
        motivo_rejeicao: Descrição legível da razão da rejeição.
    """

    nome_arquivo: str = Field(..., description="Nome original do arquivo no ZIP.")
    motivo_rejeicao: str = Field(..., description="Motivo pelo qual o documento foi rejeitado.")


class ResumoProcessamento(BaseModel):
    """Resumo agregado do resultado do processamento do lote de documentos.

    Attributes:
        total_arquivos_no_zip: Quantidade total de entradas encontradas no ZIP.
        total_txt_processados: Quantidade de arquivos .txt submetidos à sanitização.
        documentos_validos: Lista de documentos aprovados e prontos para análise.
        documentos_com_erro: Lista de documentos rejeitados com seus motivos.
        total_validos: Quantidade de documentos válidos (derivado).
        total_com_erro: Quantidade de documentos com erro (derivado).
    """

    total_arquivos_no_zip: int = Field(..., ge=0)
    total_txt_processados: int = Field(..., ge=0)
    documentos_validos: list[DocumentoValido] = Field(default_factory=list)
    documentos_com_erro: list[DocumentoComErro] = Field(default_factory=list)
    total_validos: int = Field(..., ge=0)
    total_com_erro: int = Field(..., ge=0)


class AnomaliaDetectada(BaseModel):
    """Registro auditável de uma anomalia detectada pelo motor de fraudes."""

    nome_arquivo: str = Field(..., description="Arquivo onde a anomalia foi detectada.")
    regra: str = Field(..., description="Identificador da regra disparada (ex: NF_DUPLICADA).")
    evidencia: str = Field(..., description="Descrição legível da evidência encontrada.")
    grau_confianca: float = Field(..., ge=0.0, le=1.0, description="Probabilidade 0-1 de ser fraude.")
    severidade: str = Field(..., description="ALTA | MEDIA | BAIXA.")


class ResultadoAuditoria(BaseModel):
    """Resultado da auditoria para um único documento."""

    nome_arquivo: str
    status_auditoria: str = Field(..., description="APROVADO | SUSPEITO | REPROVADO.")
    anomalias: list[AnomaliaDetectada] = Field(default_factory=list)


class RelatorioAuditoria(BaseModel):
    """Relatório agregado do motor de auditoria de fraudes."""

    total_documentos_auditados: int = Field(..., ge=0)
    total_aprovados: int = Field(..., ge=0)
    total_suspeitos: int = Field(..., ge=0)
    total_reprovados: int = Field(..., ge=0)
    total_anomalias: int = Field(..., ge=0)
    resultados: list[ResultadoAuditoria] = Field(default_factory=list)


class ExportacaoInfo(BaseModel):
    """Metadados dos arquivos CSV gerados para o Power BI."""

    base_auditoria_csv: str = Field(..., description="Caminho absoluto do base_auditoria.csv.")
    log_auditoria_csv: str = Field(..., description="Caminho absoluto do log_auditoria.csv.")
    total_linhas_base: int = Field(..., ge=0)
    total_linhas_log: int = Field(..., ge=0)


class AuditoriaFinalResponse(BaseModel):
    """Resposta final unificada — 4 etapas do pipeline de auditoria."""

    sanitizacao: ResumoProcessamento = Field(..., description="Etapa 1: sanitização.")
    extracao_ia: list[ResultadoExtracao] = Field(default_factory=list, description="Etapa 2: extração IA.")
    auditoria: RelatorioAuditoria = Field(..., description="Etapa 3: motor de fraudes.")
    exportacao: ExportacaoInfo = Field(..., description="Etapa 4: CSVs gerados.")


# ---------------------------------------------------------------------------
# Exceções de Domínio
# ---------------------------------------------------------------------------


class ZipBombDetectadaError(ValueError):
    """Levantada quando o ZIP viola os limites de segurança anti-bomb."""


class ArquivoInvalidoError(ValueError):
    """Levantada quando o arquivo enviado não é um ZIP válido."""


# ---------------------------------------------------------------------------
# Serviço de Processamento — DocumentProcessorService
# ---------------------------------------------------------------------------


class DocumentProcessorService:
    """Encapsula toda a lógica de negócio de extração e sanitização de documentos.

    Esta classe é projetada para ser usada de forma estática (sem estado de
    instância), garantindo que cada chamada seja completamente isolada e
    thread-safe quando executada via `asyncio.to_thread`.

    Métodos públicos:
        processar_zip: Ponto de entrada principal; orquestra extração e sanitização.

    Métodos privados:
        _validar_e_extrair_zip: Aplica as proteções anti-zip-bomb.
        _sanitizar_arquivo_txt: Aplica as verificações heurísticas em um único arquivo.
    """

    # ------------------------------------------------------------------
    # API Pública
    # ------------------------------------------------------------------

    @staticmethod
    def processar_zip(zip_bytes: bytes) -> ResumoProcessamento:
        """Orquestra a extração segura e a sanitização dos arquivos `.txt` do ZIP.

        Este método é síncrono e deve ser chamado via `asyncio.to_thread` para
        não bloquear o event loop do FastAPI.

        Args:
            zip_bytes: Conteúdo bruto do arquivo ZIP recebido via upload HTTP.

        Returns:
            Um `ResumoProcessamento` com a separação entre documentos válidos
            e documentos rejeitados.

        Raises:
            ArquivoInvalidoError: Se os bytes não formarem um ZIP válido.
            ZipBombDetectadaError: Se o ZIP violar os limites de tamanho ou
                quantidade de arquivos.
        """
        arquivos_extraidos = DocumentProcessorService._validar_e_extrair_zip(zip_bytes)

        total_no_zip = len(arquivos_extraidos)
        txt_processados = 0
        validos: list[DocumentoValido] = []
        com_erro: list[DocumentoComErro] = []

        for nome_arquivo, conteudo_bytes in arquivos_extraidos.items():
            if not nome_arquivo.lower().endswith(".txt"):
                # Ignora entradas que não sejam arquivos de texto plano.
                continue

            txt_processados += 1
            resultado = DocumentProcessorService._sanitizar_arquivo_txt(
                nome_arquivo=nome_arquivo,
                conteudo_bytes=conteudo_bytes,
            )

            if isinstance(resultado, DocumentoValido):
                validos.append(resultado)
            else:
                com_erro.append(resultado)

        return ResumoProcessamento(
            total_arquivos_no_zip=total_no_zip,
            total_txt_processados=txt_processados,
            documentos_validos=validos,
            documentos_com_erro=com_erro,
            total_validos=len(validos),
            total_com_erro=len(com_erro),
        )

    # ------------------------------------------------------------------
    # Métodos Privados
    # ------------------------------------------------------------------

    @staticmethod
    def _validar_e_extrair_zip(zip_bytes: bytes) -> dict[str, bytes]:
        """Valida o ZIP contra ataques de Zip Bomb e extrai os arquivos em memória.

        A proteção opera em duas camadas:
          1. Verificação da quantidade de entradas no diretório central do ZIP
             antes de descomprimir qualquer dado.
          2. Verificação incremental do tamanho descompactado durante a leitura,
             abortando imediatamente ao ultrapassar o limite.

        Args:
            zip_bytes: Conteúdo bruto do arquivo ZIP.

        Returns:
            Dicionário mapeando `nome_do_arquivo -> bytes_descompactados`.

        Raises:
            ArquivoInvalidoError: Se os bytes não formarem um ZIP válido.
            ZipBombDetectadaError: Se o ZIP violar os limites configurados.
        """
        buffer = io.BytesIO(zip_bytes)

        try:
            zf = zipfile.ZipFile(buffer, mode="r")
        except zipfile.BadZipFile as exc:
            raise ArquivoInvalidoError(
                "O arquivo enviado não é um ZIP válido ou está corrompido."
            ) from exc

        with zf:
            entradas = zf.infolist()

            # --- Proteção 1: Limite de quantidade de arquivos ---
            if len(entradas) > ZIP_MAX_FILE_COUNT:
                raise ZipBombDetectadaError(
                    f"O ZIP contém {len(entradas)} entradas, excedendo o limite de "
                    f"{ZIP_MAX_FILE_COUNT}. Operação abortada por segurança."
                )

            arquivos: dict[str, bytes] = {}
            bytes_acumulados = 0

            for info in entradas:
                # Ignora entradas de diretório (sem conteúdo real).
                if info.is_dir():
                    continue

                # --- Proteção 2: Limite de tamanho descompactado (soma acumulada) ---
                bytes_acumulados += info.file_size
                if bytes_acumulados > ZIP_MAX_UNCOMPRESSED_BYTES:
                    raise ZipBombDetectadaError(
                        f"O tamanho descompactado estimado ({bytes_acumulados / 1_048_576:.1f} MB) "
                        f"excede o limite de {ZIP_MAX_UNCOMPRESSED_BYTES // 1_048_576} MB. "
                        "Operação abortada por segurança."
                    )

                arquivos[info.filename] = zf.read(info.filename)

        return arquivos

    @staticmethod
    def _sanitizar_arquivo_txt(
        nome_arquivo: str,
        conteudo_bytes: bytes,
    ) -> DocumentoValido | DocumentoComErro:
        """Aplica a pipeline de sanitização heurística em um único arquivo `.txt`.

        Pipeline (em ordem):
          1. Decodificação UTF-8 — falha gera flag 'Erro de Encoding'.
          2. Remoção de caracteres nulos (\\x00).
          3. Verificação da proporção de caracteres imprimíveis — abaixo de
             `MIN_PRINTABLE_RATIO` gera flag 'Corrompido/Binário'.
          4. Verificação de conteúdo mínimo (comprimento e quebras de linha) —
             falha gera flag 'Arquivo Truncado'.

        Args:
            nome_arquivo: Nome original do arquivo no ZIP (usado para logging/rastreio).
            conteudo_bytes: Bytes brutos lidos do ZIP.

        Returns:
            `DocumentoValido` se o arquivo passar em todas as verificações, ou
            `DocumentoComErro` com o motivo da rejeição.
        """
        # --- Etapa 1: Decodificação UTF-8 ---
        try:
            texto = conteudo_bytes.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            logger.warning("Erro de encoding detectado em '%s'.", nome_arquivo)
            return DocumentoComErro(
                nome_arquivo=nome_arquivo,
                motivo_rejeicao="Erro de Encoding",
            )

        # --- Etapa 2: Remoção de caracteres nulos ---
        texto = texto.replace("\x00", "")

        # --- Etapa 3: Verificação da proporção de caracteres imprimíveis ---
        if len(texto) > 0:
            printable_set = set(string.printable)
            qtd_imprimiveis = sum(1 for c in texto if c in printable_set)
            proporcao = qtd_imprimiveis / len(texto)
        else:
            proporcao = 0.0

        if proporcao < MIN_PRINTABLE_RATIO:
            logger.warning(
                "Arquivo '%s' classificado como Corrompido/Binário (%.1f%% imprimível).",
                nome_arquivo,
                proporcao * 100,
            )
            return DocumentoComErro(
                nome_arquivo=nome_arquivo,
                motivo_rejeicao=(
                    f"Corrompido/Binário — apenas {proporcao * 100:.1f}% de caracteres imprimíveis "
                    f"(mínimo exigido: {MIN_PRINTABLE_RATIO * 100:.0f}%)."
                ),
            )

        # --- Etapa 4: Verificação de conteúdo mínimo (truncagem) ---
        qtd_newlines = texto.count("\n")
        if len(texto) < MIN_CHAR_LENGTH or qtd_newlines < MIN_NEWLINE_COUNT:
            logger.warning(
                "Arquivo '%s' classificado como Truncado (chars=%d, newlines=%d).",
                nome_arquivo,
                len(texto),
                qtd_newlines,
            )
            return DocumentoComErro(
                nome_arquivo=nome_arquivo,
                motivo_rejeicao=(
                    f"Arquivo Truncado — {len(texto)} caractere(s) e {qtd_newlines} "
                    f"quebra(s) de linha (mínimo: {MIN_CHAR_LENGTH} chars / "
                    f"{MIN_NEWLINE_COUNT} newlines)."
                ),
            )

        # --- Aprovado ---
        conteudo_final = texto.strip()
        return DocumentoValido(
            nome_arquivo=nome_arquivo,
            conteudo=conteudo_final,
            tamanho_bytes=len(conteudo_final.encode("utf-8")),
        )



# ---------------------------------------------------------------------------
# Motor de Auditoria de Fraudes — AuditorMotorService
# ---------------------------------------------------------------------------


class AuditorMotorService:
    """Aplica as 5 regras de negócio de auditoria fiscal sobre os resultados da IA.

    Algoritmo O(N): o índice de duplicatas é construído em uma única passagem
    antes de avaliar cada documento, evitando comparações N² pairwise.
    """

    #: Limiar para classificar valor como atípico (Seção 4 do briefing).
    VALOR_ATIPICO_THRESHOLD: float = 50_000.0

    #: Sinônimos de "PAGO" aceitos nos dados extraídos pela IA.
    _STATUS_PAGO: frozenset[str] = frozenset({"PAGO", "LIQUIDADO", "QUITADO", "PAGA"})

    #: Sinônimos de "PENDENTE" aceitos nos dados extraídos pela IA.
    _STATUS_PENDENTE: frozenset[str] = frozenset({"PENDENTE", "EM ABERTO", "A PAGAR", "AGUARDANDO"})

    #: Regex que aceita CNPJ formatado (XX.XXX.XXX/XXXX-XX) ou só dígitos (14d).
    _RE_CNPJ = re.compile(r"^\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}$")

    # ------------------------------------------------------------------
    # API Pública
    # ------------------------------------------------------------------

    @staticmethod
    def auditar_lote(resultados_ia: list[ResultadoExtracao]) -> RelatorioAuditoria:
        """Audita o lote completo e retorna o relatório com anomalias por documento."""
        # Passo 1 — O(N): constrói índice de duplicatas por numero_documento
        indice_dup: dict[str, list[str]] = collections.defaultdict(list)
        for r in resultados_ia:
            if r.sucesso and r.dados_extraidos and r.dados_extraidos.numero_documento:
                chave = r.dados_extraidos.numero_documento.strip().upper()
                indice_dup[chave].append(r.nome_arquivo)
        # Mantém apenas chaves com mais de 1 ocorrência
        duplicatas: dict[str, list[str]] = {k: v for k, v in indice_dup.items() if len(v) > 1}

        # Passo 2 — O(N): aplica regras em cada documento
        resultados_audit: list[ResultadoAuditoria] = []
        for r in resultados_ia:
            anomalias = AuditorMotorService._aplicar_regras(r, duplicatas)
            status = AuditorMotorService._calcular_status(anomalias)
            resultados_audit.append(ResultadoAuditoria(
                nome_arquivo=r.nome_arquivo,
                status_auditoria=status,
                anomalias=anomalias,
            ))

        total_ap = sum(1 for x in resultados_audit if x.status_auditoria == "APROVADO")
        total_su = sum(1 for x in resultados_audit if x.status_auditoria == "SUSPEITO")
        total_re = sum(1 for x in resultados_audit if x.status_auditoria == "REPROVADO")
        total_an = sum(len(x.anomalias) for x in resultados_audit)

        logger.info(
            "[Etapa 3/4] Auditoria: %d aprovados / %d suspeitos / %d reprovados | %d anomalias.",
            total_ap, total_su, total_re, total_an,
        )
        return RelatorioAuditoria(
            total_documentos_auditados=len(resultados_audit),
            total_aprovados=total_ap,
            total_suspeitos=total_su,
            total_reprovados=total_re,
            total_anomalias=total_an,
            resultados=resultados_audit,
        )

    # ------------------------------------------------------------------
    # Aplicação das Regras
    # ------------------------------------------------------------------

    @staticmethod
    def _aplicar_regras(
        resultado: ResultadoExtracao,
        duplicatas: dict[str, list[str]],
    ) -> list[AnomaliaDetectada]:
        """Aplica todas as 5 regras ao resultado de um único documento."""
        if not resultado.sucesso or not resultado.dados_extraidos:
            return []
        d = resultado.dados_extraidos
        nome = resultado.nome_arquivo
        anomalias: list[AnomaliaDetectada] = []
        for fn in (
            lambda: AuditorMotorService._regra_nf_duplicada(d, nome, duplicatas),
            lambda: AuditorMotorService._regra_divergencia_data(d, nome),
            lambda: AuditorMotorService._regra_status_inconsistente(d, nome),
            lambda: AuditorMotorService._regra_valor_atipico(d, nome),
            lambda: AuditorMotorService._regra_cnpj(d, nome),
        ):
            a = fn()
            if a:
                anomalias.append(a)
        return anomalias

    # ------------------------------------------------------------------
    # Regra 1 — NF Duplicada
    # ------------------------------------------------------------------

    @staticmethod
    def _regra_nf_duplicada(
        dados: object,
        nome: str,
        duplicatas: dict[str, list[str]],
    ) -> AnomaliaDetectada | None:
        from services.llm_extractor import NotaFiscalData
        assert isinstance(dados, NotaFiscalData)
        if not dados.numero_documento:
            return None
        chave = dados.numero_documento.strip().upper()
        if chave not in duplicatas:
            return None
        outros = [f for f in duplicatas[chave] if f != nome]
        return AnomaliaDetectada(
            nome_arquivo=nome,
            regra="NF_DUPLICADA",
            evidencia=f"Número '{dados.numero_documento}' também aparece em: {', '.join(outros)}.",
            grau_confianca=0.95,
            severidade="ALTA",
        )

    # ------------------------------------------------------------------
    # Regra 2 — Divergência de Data de Pagamento
    # ------------------------------------------------------------------

    @staticmethod
    def _regra_divergencia_data(dados: object, nome: str) -> AnomaliaDetectada | None:
        from services.llm_extractor import NotaFiscalData
        assert isinstance(dados, NotaFiscalData)
        dt_pg = AuditorMotorService._parse_data(dados.data_pagamento)
        dt_nf = AuditorMotorService._parse_data(dados.data_emissao_nf or dados.data_emissao)
        if dt_pg is None or dt_nf is None:
            return None
        if dt_pg < dt_nf:
            return AnomaliaDetectada(
                nome_arquivo=nome,
                regra="DIVERGENCIA_DATA_PAGAMENTO",
                evidencia=(
                    f"Pagamento ({dados.data_pagamento}) anterior à emissão da NF "
                    f"({dados.data_emissao_nf or dados.data_emissao})."
                ),
                grau_confianca=0.90,
                severidade="MEDIA",
            )
        return None

    # ------------------------------------------------------------------
    # Regra 3 — Status Inconsistente
    # ------------------------------------------------------------------

    @staticmethod
    def _regra_status_inconsistente(dados: object, nome: str) -> AnomaliaDetectada | None:
        from services.llm_extractor import NotaFiscalData
        assert isinstance(dados, NotaFiscalData)
        if not dados.status:
            return None
        st = dados.status.strip().upper()
        pago = st in AuditorMotorService._STATUS_PAGO
        pendente = st in AuditorMotorService._STATUS_PENDENTE
        tem_data = bool(dados.data_pagamento and dados.data_pagamento.strip())
        if pago and not tem_data:
            return AnomaliaDetectada(
                nome_arquivo=nome,
                regra="STATUS_INCONSISTENTE",
                evidencia=f"Status '{dados.status}' mas data_pagamento ausente.",
                grau_confianca=0.85,
                severidade="ALTA",
            )
        if pendente and tem_data:
            return AnomaliaDetectada(
                nome_arquivo=nome,
                regra="STATUS_INCONSISTENTE",
                evidencia=f"Status '{dados.status}' mas data_pagamento preenchida ({dados.data_pagamento}).",
                grau_confianca=0.80,
                severidade="ALTA",
            )
        return None

    # ------------------------------------------------------------------
    # Regra 4 — Valor Atípico
    # ------------------------------------------------------------------

    @staticmethod
    def _regra_valor_atipico(dados: object, nome: str) -> AnomaliaDetectada | None:
        from services.llm_extractor import NotaFiscalData
        assert isinstance(dados, NotaFiscalData)
        if dados.valor_bruto is None:
            return None
        if dados.valor_bruto > AuditorMotorService.VALOR_ATIPICO_THRESHOLD:
            return AnomaliaDetectada(
                nome_arquivo=nome,
                regra="VALOR_ATIPICO",
                evidencia=f"Valor bruto R$ {dados.valor_bruto:,.2f} excede limiar de R$ 50.000,00.",
                grau_confianca=0.70,
                severidade="BAIXA",
            )
        return None

    # ------------------------------------------------------------------
    # Regra 5 — CNPJ Ausente ou Inválido
    # ------------------------------------------------------------------

    @staticmethod
    def _regra_cnpj(dados: object, nome: str) -> AnomaliaDetectada | None:
        from services.llm_extractor import NotaFiscalData
        assert isinstance(dados, NotaFiscalData)
        cnpj = (dados.cnpj_fornecedor or "").strip()
        if not cnpj:
            return AnomaliaDetectada(
                nome_arquivo=nome,
                regra="CNPJ_AUSENTE_OU_INVALIDO",
                evidencia="Campo cnpj_fornecedor ausente ou vazio.",
                grau_confianca=0.95,
                severidade="ALTA",
            )
        if not AuditorMotorService._validar_cnpj(cnpj):
            return AnomaliaDetectada(
                nome_arquivo=nome,
                regra="CNPJ_AUSENTE_OU_INVALIDO",
                evidencia=f"CNPJ '{cnpj}' não passa na validação matemática da Receita Federal.",
                grau_confianca=0.95,
                severidade="ALTA",
            )
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validar_cnpj(cnpj: str) -> bool:
        """Valida CNPJ pelo algoritmo oficial da Receita Federal (módulo 11)."""
        digits = re.sub(r"\D", "", cnpj)
        if len(digits) != 14 or len(set(digits)) == 1:
            return False
        def _calc(d: str, pesos: list[int]) -> int:
            s = sum(int(c) * p for c, p in zip(d, pesos))
            r = s % 11
            return 0 if r < 2 else 11 - r
        p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        p2 = [6] + p1
        return (
            _calc(digits[:12], p1) == int(digits[12])
            and _calc(digits[:13], p2) == int(digits[13])
        )

    @staticmethod
    def _parse_data(s: str | None) -> datetime.date | None:
        """Converte string DD/MM/AAAA ou AAAA-MM-DD para date; retorna None se inválida."""
        if not s:
            return None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.datetime.strptime(s.strip(), fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _calcular_status(anomalias: list[AnomaliaDetectada]) -> str:
        if not anomalias:
            return "APROVADO"
        if any(a.severidade == "ALTA" for a in anomalias):
            return "REPROVADO"
        return "SUSPEITO"


# ---------------------------------------------------------------------------
# Pipeline de Exportação — ExportacaoService
# ---------------------------------------------------------------------------


class ExportacaoService:
    """Gera base_auditoria.csv e log_auditoria.csv compatíveis com Power BI / Excel.

    Encoding UTF-8 com BOM (utf-8-sig) para detecção automática no ecossistema
    Microsoft. Separador ';' (padrão pt-BR). Executa em thread via to_thread
    para não bloquear o event loop do FastAPI.
    """

    #: Diretório de saída — criado automaticamente se não existir.
    DIR_EXPORTS: pathlib.Path = pathlib.Path(__file__).resolve().parent / "exports"
    ENCODING: str = "utf-8-sig"
    SEP: str = ";"

    # ------------------------------------------------------------------
    # API Pública
    # ------------------------------------------------------------------

    @staticmethod
    def gerar_csvs(
        resultados_ia: list[ResultadoExtracao],
        relatorio: RelatorioAuditoria,
    ) -> ExportacaoInfo:
        """Gera ambos os CSVs e retorna metadados dos arquivos criados."""
        ExportacaoService.DIR_EXPORTS.mkdir(parents=True, exist_ok=True)
        path_base = ExportacaoService.DIR_EXPORTS / "base_auditoria.csv"
        path_log  = ExportacaoService.DIR_EXPORTS / "log_auditoria.csv"

        n_base = ExportacaoService._gerar_base_auditoria(path_base, resultados_ia, relatorio)
        n_log  = ExportacaoService._gerar_log_auditoria(path_log, resultados_ia, relatorio)

        logger.info(
            "[Etapa 4/4] CSVs gerados: base=%d linhas → %s | log=%d linhas → %s",
            n_base, path_base, n_log, path_log,
        )
        return ExportacaoInfo(
            base_auditoria_csv=str(path_base),
            log_auditoria_csv=str(path_log),
            total_linhas_base=n_base,
            total_linhas_log=n_log,
        )

    # ------------------------------------------------------------------
    # base_auditoria.csv — dados limpos + status de auditoria
    # ------------------------------------------------------------------

    @staticmethod
    def _gerar_base_auditoria(
        path: pathlib.Path,
        resultados_ia: list[ResultadoExtracao],
        relatorio: RelatorioAuditoria,
    ) -> int:
        # Índice de auditoria por nome_arquivo para O(1) lookup
        audit_idx: dict[str, ResultadoAuditoria] = {
            r.nome_arquivo: r for r in relatorio.resultados
        }
        cabecalho = [
            "nome_arquivo", "tipo_documento", "numero_documento", "data_emissao",
            "fornecedor", "cnpj_fornecedor", "descricao_servico", "valor_bruto",
            "data_pagamento", "data_emissao_nf", "aprovado_por", "banco_destino",
            "status", "hash_verificacao",
            "status_auditoria", "total_anomalias", "regras_disparadas",
        ]
        linhas = 0
        with path.open("w", newline="", encoding=ExportacaoService.ENCODING) as f:
            w = csv.writer(f, delimiter=ExportacaoService.SEP)
            w.writerow(cabecalho)
            for r in resultados_ia:
                d = r.dados_extraidos
                audit = audit_idx.get(r.nome_arquivo)
                regras = "|".join(a.regra for a in audit.anomalias) if audit else ""
                status_aud = audit.status_auditoria if audit else ("N/A" if not r.sucesso else "APROVADO")
                n_anomalias = len(audit.anomalias) if audit else 0
                w.writerow([
                    ExportacaoService._s(r.nome_arquivo),
                    ExportacaoService._s(d.tipo_documento if d else None),
                    ExportacaoService._s(d.numero_documento if d else None),
                    ExportacaoService._s(d.data_emissao if d else None),
                    ExportacaoService._s(d.fornecedor if d else None),
                    ExportacaoService._s(d.cnpj_fornecedor if d else None),
                    ExportacaoService._s(d.descricao_servico if d else None),
                    ExportacaoService._f(d.valor_bruto if d else None),
                    ExportacaoService._s(d.data_pagamento if d else None),
                    ExportacaoService._s(d.data_emissao_nf if d else None),
                    ExportacaoService._s(d.aprovado_por if d else None),
                    ExportacaoService._s(d.banco_destino if d else None),
                    ExportacaoService._s(d.status if d else None),
                    ExportacaoService._s(d.hash_verificacao if d else None),
                    status_aud,
                    n_anomalias,
                    regras,
                ])
                linhas += 1
        return linhas

    # ------------------------------------------------------------------
    # log_auditoria.csv — rastreabilidade total (1 linha por anomalia)
    # ------------------------------------------------------------------

    @staticmethod
    def _gerar_log_auditoria(
        path: pathlib.Path,
        resultados_ia: list[ResultadoExtracao],
        relatorio: RelatorioAuditoria,
    ) -> int:
        audit_idx: dict[str, ResultadoAuditoria] = {
            r.nome_arquivo: r for r in relatorio.resultados
        }
        cabecalho = [
            "nome_arquivo", "sucesso_extracao", "motivo_falha_extracao",
            "modelo_utilizado", "tempo_processamento_s",
            "tokens_prompt", "tokens_completion", "tokens_total",
            "status_auditoria", "regra", "evidencia", "grau_confianca", "severidade",
        ]
        linhas = 0
        with path.open("w", newline="", encoding=ExportacaoService.ENCODING) as f:
            w = csv.writer(f, delimiter=ExportacaoService.SEP)
            w.writerow(cabecalho)
            for r in resultados_ia:
                audit = audit_idx.get(r.nome_arquivo)
                status_aud = audit.status_auditoria if audit else "N/A"
                base_row = [
                    ExportacaoService._s(r.nome_arquivo),
                    "SIM" if r.sucesso else "NAO",
                    ExportacaoService._s(r.motivo_falha),
                    ExportacaoService._s(r.modelo_utilizado),
                    ExportacaoService._f(r.tempo_processamento_s),
                    ExportacaoService._s(r.tokens_prompt),
                    ExportacaoService._s(r.tokens_completion),
                    ExportacaoService._s(r.tokens_total),
                    status_aud,
                ]
                anomalias = audit.anomalias if audit else []
                if anomalias:
                    for a in anomalias:
                        w.writerow(base_row + [a.regra, a.evidencia, f"{a.grau_confianca:.2f}", a.severidade])
                        linhas += 1
                else:
                    w.writerow(base_row + ["", "", "", ""])
                    linhas += 1
        return linhas

    # ------------------------------------------------------------------
    # Helpers de formatação segura
    # ------------------------------------------------------------------

    @staticmethod
    def _s(v: object) -> str:
        """Converte qualquer valor para string, substituindo None por vazio."""
        return "" if v is None else str(v)

    @staticmethod
    def _f(v: object) -> str:
        """Formata float com 4 casas; None → vazio."""
        if v is None:
            return ""
        try:
            return f"{float(v):.4f}"
        except (TypeError, ValueError):
            return str(v)


# ---------------------------------------------------------------------------
# Inicialização da Aplicação FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NL Consulting — AuditorIA",
    description=(
        "API de auditoria inteligente de notas fiscais. "
        "Processa lotes ZIP, extrai dados via IA (gpt-4o-mini) e aplica "
        "5 regras de detecção de fraudes com exportação CSV para Power BI."
    ),
    version="1.1.0",
    # Em produção, desativa docs públicos para não expor o schema da API.
    # Altere para "/docs" se quiser manter acesso (adicione autenticação antes).
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

# ---------------------------------------------------------------------------
# Configuração de CORS — Dinâmica via variável de ambiente
# ---------------------------------------------------------------------------
# ALLOWED_ORIGINS é lida de os.environ (Render Dashboard em prod, .env em dev).
# Nunca use allow_origins=["*"] em produção com credenciais.

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept"],
    expose_headers=["Content-Disposition"],  # necessário para download de arquivos
)

# ---------------------------------------------------------------------------
# Handlers Globais de Exceção
# ---------------------------------------------------------------------------


@app.exception_handler(ArquivoInvalidoError)
async def handler_arquivo_invalido(
    _request: Request, exc: ArquivoInvalidoError
) -> JSONResponse:
    """Traduz `ArquivoInvalidoError` em HTTP 400 Bad Request.

    O detalhe da exceção é seguro para exibição ao cliente pois é gerado
    internamente, sem expor informações do sistema.
    """
    logger.warning("Arquivo inválido recebido: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"erro": "arquivo_invalido", "detalhe": str(exc)},
    )


@app.exception_handler(ZipBombDetectadaError)
async def handler_zip_bomb(
    _request: Request, exc: ZipBombDetectadaError
) -> JSONResponse:
    """Traduz `ZipBombDetectadaError` em HTTP 400 Bad Request.

    Registra o evento em nível WARNING para auditoria de segurança.
    """
    logger.warning("ALERTA DE SEGURANÇA — Zip Bomb detectada: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"erro": "zip_bomb_detectada", "detalhe": str(exc)},
    )


@app.exception_handler(EnvironmentError)
async def handler_configuracao_ausente(
    _request: Request, exc: EnvironmentError
) -> JSONResponse:
    """Captura falhas de configuração do servidor (variáveis de ambiente ausentes).

    Retorna HTTP 500 — pois é um problema de infraestrutura, não do cliente —
    sem expor o detalhe técnico interno na resposta pública.
    O detalhe completo é registrado no log do servidor para diagnóstico.
    """
    logger.critical("ERRO DE CONFIGURAÇÃO DO SERVIDOR: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "erro": "configuracao_servidor_incompleta",
            "detalhe": (
                "O servidor não está configurado corretamente. "
                "Verifique as variáveis de ambiente obrigatórias (ex: GROQ_API_KEY)."
            ),
        },
    )


@app.exception_handler(Exception)
async def handler_excecao_generica(
    _request: Request, exc: Exception
) -> JSONResponse:
    """Captura qualquer exceção não tratada e retorna HTTP 500 genérico.

    O stack trace NÃO é exposto ao cliente; apenas registrado no log do servidor.
    """
    logger.exception("Falha interna não esperada: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "erro": "erro_interno_servidor",
            "detalhe": "Ocorreu um erro inesperado. Por favor, tente novamente.",
        },
    )


# ---------------------------------------------------------------------------
# Rotas HTTP
# ---------------------------------------------------------------------------


@app.get(
    "/api/health",
    summary="Verificação de saúde",
    tags=["Sistema"],
    response_model=dict,
)
async def health_check() -> dict[str, str]:
    """Health check para Render, load balancers e Docker HEALTHCHECK.

    Returns:
        Status do serviço com versão e ambiente atual.
    """
    return {
        "status": "ok",
        "servico": "auditor-ia",
        "versao": "1.1.0",
        "ambiente": settings.environment,
    }


@app.get(
    "/api/downloads/{filename}",
    summary="Download de CSV gerado",
    tags=["Sistema"],
    response_class=FileResponse,
)
async def download_csv(filename: str) -> FileResponse:
    """Serve os arquivos CSV gerados pelo pipeline para download pelo frontend.

    Aceita apenas ``base_auditoria.csv`` e ``log_auditoria.csv`` —
    quaisquer outros nomes retornam 404 (proteção contra path traversal).

    Args:
        filename: Nome do arquivo (``base_auditoria.csv`` ou ``log_auditoria.csv``).

    Returns:
        FileResponse com o CSV e header Content-Disposition para download direto.

    Raises:
        HTTPException 404: Se o arquivo não existir ou o nome não for permitido.
    """
    # Whitelist explícita — previne path traversal (ex: "../../etc/passwd")
    ALLOWED_FILES = {"base_auditoria.csv", "log_auditoria.csv"}
    if filename not in ALLOWED_FILES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Arquivo '{filename}' não encontrado ou não permitido para download.",
        )

    path = ExportacaoService.DIR_EXPORTS / filename
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{filename}' ainda não foi gerado. Execute o processamento primeiro.",
        )

    return FileResponse(
        path=str(path),
        media_type="text/csv; charset=utf-8",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post(
    "/api/process-documents",
    summary="Processar e auditar lote de notas fiscais",
    description=(
        "Recebe um arquivo `.zip` contendo notas fiscais em formato `.txt`, "
        "aplica sanitização heurística (Anti-Zip Bomb + pipeline de qualidade) e, "
        "em seguida, extrai os dados financeiros de cada documento válido via IA (Groq). "
        "Retorna o resultado completo das duas etapas em um único objeto."
    ),
    response_model=AuditoriaFinalResponse,
    status_code=status.HTTP_200_OK,
    tags=["Documentos"],
    responses={
        400: {
            "description": "Arquivo inválido ou violação de limite de segurança.",
            "content": {
                "application/json": {
                    "examples": {
                        "arquivo_invalido": {
                            "summary": "Arquivo não é um ZIP válido",
                            "value": {
                                "erro": "arquivo_invalido",
                                "detalhe": "O arquivo enviado não é um ZIP válido ou está corrompido.",
                            },
                        },
                        "zip_bomb": {
                            "summary": "Zip Bomb detectada",
                            "value": {
                                "erro": "zip_bomb_detectada",
                                "detalhe": "O ZIP contém 2000 entradas, excedendo o limite de 1500.",
                            },
                        },
                    }
                }
            },
        },
        500: {
            "description": "Falha interna do servidor.",
            "content": {
                "application/json": {
                    "example": {
                        "erro": "erro_interno_servidor",
                        "detalhe": "Ocorreu um erro inesperado. Por favor, tente novamente.",
                    }
                }
            },
        },
    },
)
async def processar_documentos(
    arquivo: Annotated[
        UploadFile,
        File(description="Arquivo .zip contendo as notas fiscais em formato .txt."),
    ],
) -> AuditoriaFinalResponse:
    """Pipeline completo de auditoria: sanitização + extração de dados via IA.

    Executa as duas etapas em sequência:

    **Etapa 1 — Sanitização (CPU-bound, isolada em thread):**
      - Extração segura do ZIP com proteções Anti-Zip Bomb.
      - Pipeline heurística: encoding UTF-8, remoção de nulos, proporção
        imprimível, verificação de truncagem.

    **Etapa 2 — Extração IA (I/O-bound, assíncrona):**
      - Chamadas paralelas ao Groq (llama-3.3-70b-versatile) via AsyncOpenAI.
      - Concorrência controlada por ``asyncio.Semaphore(10)``.
      - Retry automático com Exponential Backoff para erros 429/502.
      - Falhas individuais são isoladas — nunca quebram o lote.

    Args:
        arquivo: Upload HTTP contendo o arquivo `.zip`.

    Returns:
        ``AuditoriaFinalResponse`` com os resultados completos das duas etapas.

    Raises:
        HTTPException (400): Se o tipo de arquivo for inválido (não `.zip`) ou
            exceder o tamanho máximo permitido.
        ArquivoInvalidoError: Propagado para o handler global se o ZIP estiver corrompido.
        ZipBombDetectadaError: Propagado para o handler global se os limites forem excedidos.
    """
    # --- Validação do tipo de arquivo ---
    content_type = arquivo.content_type or ""
    nome_arquivo = arquivo.filename or ""

    is_zip_content_type = content_type in (
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
        "multipart/x-zip",
    )
    is_zip_extension = nome_arquivo.lower().endswith(".zip")

    if not (is_zip_content_type or is_zip_extension):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Tipo de arquivo não suportado: '{content_type}'. "
                "Apenas arquivos .zip são aceitos."
            ),
        )

    # --- Leitura do payload com limite de tamanho ---
    zip_bytes = await arquivo.read(HTTP_MAX_UPLOAD_BYTES + 1)

    if len(zip_bytes) > HTTP_MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"O arquivo enviado excede o tamanho máximo permitido de "
                f"{HTTP_MAX_UPLOAD_BYTES // 1_048_576} MB."
            ),
        )

    logger.info(
        "Recebendo ZIP '%s' (%.2f MB) para processamento.",
        nome_arquivo,
        len(zip_bytes) / 1_048_576,
    )

    # --- Etapa 1: Sanitização (bloqueante → thread separada) ---
    resumo: ResumoProcessamento = await asyncio.to_thread(
        DocumentProcessorService.processar_zip,
        zip_bytes,
    )

    logger.info(
        "[Etapa 1/2] Sanitização concluída: %d válidos / %d com erro "
        "(de %d .txt em %d entradas no ZIP).",
        resumo.total_validos,
        resumo.total_com_erro,
        resumo.total_txt_processados,
        resumo.total_arquivos_no_zip,
    )

    # --- Etapa 2: Extração via IA (assíncrona, paralela, com semáforo) ---
    # Apenas os documentos aprovados pela sanitização são enviados à IA.
    # Se não houver documentos válidos, a lista de resultados será vazia.
    # Concorrência reduzida para 5 durante a propagação dos limites do Tier 1.
    # Aumentar para 30 quando a conta estiver estabilizada (500 RPM garantidos).
    resultados_ia: list[ResultadoExtracao] = await extrair_lote_documentos(
        documentos=resumo.documentos_validos,
        max_concorrencia=5,
    )

    total_ia_ok = sum(1 for r in resultados_ia if r.sucesso)
    logger.info(
        "[Etapa 2/4] Extração IA concluída: %d OK / %d com falha.",
        total_ia_ok,
        len(resultados_ia) - total_ia_ok,
    )

    # --- Etapa 3: Motor de Auditoria de Fraudes (CPU-bound → thread separada) ---
    # O(N) garantido: índice de duplicatas construído em passagem única antes
    # de varrer os documentos com as 5 regras de negócio.
    relatorio_auditoria: RelatorioAuditoria = await asyncio.to_thread(
        AuditorMotorService.auditar_lote,
        resultados_ia,
    )

    # --- Etapa 4: Exportação CSV (I/O de disco → thread separada) ---
    # Encoding utf-8-sig (UTF-8 com BOM) para compatibilidade com Excel/Power BI.
    # Separador ';' conforme padrão pt-BR. Nulos → string vazia.
    info_exportacao: ExportacaoInfo = await asyncio.to_thread(
        ExportacaoService.gerar_csvs,
        resultados_ia,
        relatorio_auditoria,
    )

    return AuditoriaFinalResponse(
        sanitizacao=resumo,
        extracao_ia=resultados_ia,
        auditoria=relatorio_auditoria,
        exportacao=info_exportacao,
    )
