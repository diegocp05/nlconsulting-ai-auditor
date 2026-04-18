"""
backend/main.py
===============
Ponto de entrada do serviço de backend para o SaaS de Auditoria de Notas Fiscais.

Responsabilidades deste módulo:
  - Configurar a aplicação FastAPI e políticas de CORS.
  - Registrar os handlers globais de exceção para respostas HTTP padronizadas.
  - Expor as rotas HTTP (interface de transporte), delegando toda a lógica de
    negócio ao `DocumentProcessorService`.

Padrões aplicados:
  - Separação de Conceitos (SoC): rotas apenas como interface HTTP.
  - Isolamento de Event Loop: operações bloqueantes em `asyncio.to_thread`.
  - Segurança Anti-Zip Bomb: limites de tamanho e quantidade de arquivos.
  - Sanitização Heurística: encoding, nulos, proporção imprimível, truncagem.
  - Tratamento Global de Exceções: sem vazamento de stack trace ao cliente.
"""

from __future__ import annotations

import asyncio
import io
import logging
import string
import zipfile
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("auditor.backend")

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
# Inicialização da Aplicação FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NL Consulting — Auditor de Notas Fiscais",
    description=(
        "API de processamento e sanitização de lotes de notas fiscais em formato ZIP. "
        "Fornece documentos limpos e validados para ingestão por modelos de IA."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# Configuração de CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",   # CRA / Next.js dev server
        "http://localhost:5173",   # Vite dev server
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept"],
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
    """Endpoint de health check para monitoramento e load balancers.

    Returns:
        Dicionário com o status do serviço.
    """
    return {"status": "ok", "servico": "auditor-nf"}


@app.post(
    "/api/process-documents",
    summary="Processar lote de notas fiscais",
    description=(
        "Recebe um arquivo `.zip` contendo notas fiscais em formato `.txt`, "
        "aplica sanitização heurística e retorna os documentos válidos separados "
        "dos documentos com erro. Possui proteções contra Zip Bomb."
    ),
    response_model=ResumoProcessamento,
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
) -> ResumoProcessamento:
    """Recebe um ZIP de notas fiscais, extrai e sanitiza os documentos `.txt`.

    O processamento bloqueante (I/O de descompactação e análise de conteúdo)
    é isolado do event loop via `asyncio.to_thread`, garantindo que a
    concorrência do FastAPI não seja comprometida mesmo com ZIPs pesados.

    Args:
        arquivo: Upload HTTP contendo o arquivo `.zip`.

    Returns:
        `ResumoProcessamento` com documentos válidos e com erro separados.

    Raises:
        HTTPException (400): Se o tipo de arquivo for inválido (não `.zip`).
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

    # --- Delega processamento bloqueante para thread separada ---
    resumo: ResumoProcessamento = await asyncio.to_thread(
        DocumentProcessorService.processar_zip,
        zip_bytes,
    )

    logger.info(
        "Processamento concluído: %d válidos / %d com erro (de %d .txt em %d entradas no ZIP).",
        resumo.total_validos,
        resumo.total_com_erro,
        resumo.total_txt_processados,
        resumo.total_arquivos_no_zip,
    )

    return resumo
