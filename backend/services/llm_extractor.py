"""
backend/services/llm_extractor.py
==================================
Camada de integração com a API nativa da OpenAI para extração estruturada de
dados financeiros a partir de notas fiscais sanitizadas.

Provedor: OpenAI  —  endpoint: https://api.openai.com/v1 (padrão do SDK)
Modelo  : gpt-4o-mini

Responsabilidades:
  - Definir o schema de saída (``NotaFiscalData``) via Pydantic V2, que serve
    simultaneamente como contrato com a IA (Structured Outputs) e modelo de
    resposta da API.
  - Expor a função pública ``extrair_lote_documentos`` para processar um lote
    inteiro de forma concorrente e resiliente.
  - Controlar a concorrência com ``asyncio.Semaphore`` para nunca exceder o
    limite de requisições paralelas à OpenAI e evitar Rate Limit (HTTP 429).
  - Aplicar o padrão Retry com Exponential Backoff via ``tenacity`` nos erros
    recuperáveis (429 Rate Limit, 502 Bad Gateway).
  - Isolar falhas por documento: um erro em um item não interrompe o lote.

Padrões aplicados:
  - Structured Outputs: ``client.beta.chat.completions.parse()`` com ``response_format``
    apontando para o modelo Pydantic, garantindo JSON estruturado e validado.
  - Async/Await nativo: todo o I/O de rede é não-bloqueante (``AsyncOpenAI``).
  - Log de auditoria: cada resultado carrega metadados de rastreabilidade
    (arquivo de origem, tempo de processamento, modelo utilizado, tokens consumidos).
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import sys
import time
from typing import TYPE_CHECKING, Optional

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# ---------------------------------------------------------------------------
# Resolução do caminho do backend para imports relativos
# ---------------------------------------------------------------------------

# Garante que o diretório backend/ esteja no sys.path independentemente de
# como o módulo é invocado (uvicorn, pytest, script direto).
_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Guard de TYPE_CHECKING: o import de DocumentoValido é resolvido apenas pelo
# type checker estático (mypy/pyright), nunca em runtime, quebrando o ciclo
# de import circular entre main.py ↔ services/llm_extractor.py.
# Em runtime, o tipo é resolvido de forma lazy dentro das funções via
# `from main import DocumentoValido` (executado somente quando chamadas).
if TYPE_CHECKING:
    from main import DocumentoValido  # pragma: no cover

# ---------------------------------------------------------------------------
# Configuração de logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("auditor.llm_extractor")

# ---------------------------------------------------------------------------
# Constantes operacionais
# ---------------------------------------------------------------------------

#: Modelo OpenAI utilizado para extração. Facilita a substituição centralizada.
MODELO_OPENAI: str = "gpt-4o-mini"

#: Número máximo de tentativas em caso de erro recuperável.
RETRY_MAX_TENTATIVAS: int = 4

#: Tempo mínimo de espera (segundos) entre retentativas.
RETRY_ESPERA_MIN_S: float = 2.0

#: Tempo máximo de espera (segundos) entre retentativas (teto do backoff).
RETRY_ESPERA_MAX_S: float = 30.0

#: Tipos de exceção OpenAI que devem disparar retentativas automáticas.
_ERROS_RECUPERAVEIS: tuple[type[Exception], ...] = (
    openai.RateLimitError,       # HTTP 429 — Rate Limit excedido
    openai.APIStatusError,       # Cobre HTTP 502 Bad Gateway e similares
    openai.APIConnectionError,   # Falhas transitórias de rede
    openai.APITimeoutError,      # Timeout na chamada HTTP
)

# ---------------------------------------------------------------------------
# Schema de Saída — NotaFiscalData (Pydantic V2 + Structured Outputs)
# ---------------------------------------------------------------------------


class NotaFiscalData(BaseModel):
    """Schema de extração de dados financeiros de uma nota fiscal.

    Todos os campos são ``Optional`` porque a IA pode não encontrar determinado
    campo no documento — o que é preferível a uma alucinação.

    Este modelo é usado diretamente no ``response_format`` da chamada à OpenAI,
    que garante que a resposta esteja sempre em conformidade com o schema
    (Structured Outputs / JSON Schema enforcement).

    Attributes:
        tipo_documento: Tipo do documento fiscal (ex: NOTA_FISCAL, RECIBO).
        numero_documento: Número identificador do documento (ex: NF-87397).
        data_emissao: Data de emissão no formato encontrado no documento.
        fornecedor: Razão social ou nome do fornecedor/prestador.
        cnpj_fornecedor: CNPJ do fornecedor com formatação original.
        descricao_servico: Descrição do serviço ou produto faturado.
        valor_bruto: Valor bruto em reais (float). Ex: 8500.00.
        data_pagamento: Data em que o pagamento foi ou será efetuado.
        data_emissao_nf: Data de emissão específica da nota fiscal.
        aprovado_por: Nome do responsável pela aprovação interna.
        banco_destino: Dados bancários do destinatário do pagamento.
        status: Status atual do documento (ex: PAGO, PENDENTE, CANCELADO).
        hash_verificacao: Hash ou código de verificação/integridade do documento.
    """

    tipo_documento: Optional[str] = Field(
        default=None,
        description="Tipo do documento fiscal (ex: NOTA_FISCAL, RECIBO, FATURA).",
    )
    numero_documento: Optional[str] = Field(
        default=None,
        description="Número único de identificação do documento.",
    )
    data_emissao: Optional[str] = Field(
        default=None,
        description="Data de emissão do documento no formato original (ex: DD/MM/AAAA).",
    )
    fornecedor: Optional[str] = Field(
        default=None,
        description="Nome ou razão social do fornecedor ou prestador de serviço.",
    )
    cnpj_fornecedor: Optional[str] = Field(
        default=None,
        description="CNPJ do fornecedor com a formatação encontrada no documento.",
    )
    descricao_servico: Optional[str] = Field(
        default=None,
        description="Descrição do serviço prestado ou produto fornecido.",
    )
    valor_bruto: Optional[float] = Field(
        default=None,
        description=(
            "Valor bruto em reais como número decimal (ex: 8500.00). "
            "Remova símbolos como 'R$' e substitua vírgula por ponto."
        ),
    )
    data_pagamento: Optional[str] = Field(
        default=None,
        description="Data em que o pagamento foi realizado ou está previsto.",
    )
    data_emissao_nf: Optional[str] = Field(
        default=None,
        description="Data de emissão específica da nota fiscal (pode diferir da data de emissão do documento).",
    )
    aprovado_por: Optional[str] = Field(
        default=None,
        description="Nome completo do responsável interno que aprovou o pagamento.",
    )
    banco_destino: Optional[str] = Field(
        default=None,
        description="Informações bancárias do destinatário (banco, agência, conta corrente).",
    )
    status: Optional[str] = Field(
        default=None,
        description="Status atual do documento (ex: PAGO, PENDENTE, CANCELADO, APROVADO).",
    )
    hash_verificacao: Optional[str] = Field(
        default=None,
        description="Código de hash ou chave de verificação de integridade do documento.",
    )


# ---------------------------------------------------------------------------
# Modelos de Resultado e Auditoria
# ---------------------------------------------------------------------------


class ResultadoExtracao(BaseModel):
    """Encapsula o resultado da extração para um único documento.

    Contém tanto os dados extraídos quanto os metadados de auditoria e
    rastreabilidade necessários para o log operacional.

    Attributes:
        nome_arquivo: Nome do arquivo de origem (rastreabilidade).
        sucesso: ``True`` se a extração foi bem-sucedida; ``False`` se falhou.
        dados_extraidos: Instância de ``NotaFiscalData`` com os campos extraídos,
            ou ``None`` em caso de falha.
        motivo_falha: Descrição do erro em caso de falha, ou ``None`` em sucesso.
        modelo_utilizado: Identificador do modelo OpenAI utilizado.
        tempo_processamento_s: Tempo total de processamento em segundos (wall time),
            incluindo retentativas.
        tokens_prompt: Tokens consumidos no prompt (input), se disponível.
        tokens_completion: Tokens consumidos na resposta (output), se disponível.
        tokens_total: Total de tokens consumidos na chamada, se disponível.
    """

    nome_arquivo: str = Field(..., description="Nome do arquivo de origem.")
    sucesso: bool = Field(..., description="Indica se a extração foi concluída com êxito.")
    dados_extraidos: Optional[NotaFiscalData] = Field(
        default=None,
        description="Dados estruturados extraídos pela IA.",
    )
    motivo_falha: Optional[str] = Field(
        default=None,
        description="Descrição do erro ocorrido (apenas em caso de falha).",
    )
    modelo_utilizado: str = Field(
        default=MODELO_OPENAI,
        description="Identificador do modelo OpenAI efetivamente utilizado.",
    )
    tempo_processamento_s: float = Field(
        ...,
        ge=0.0,
        description="Tempo total de processamento em segundos.",
    )
    tokens_prompt: Optional[int] = Field(
        default=None,
        description="Tokens de entrada consumidos na chamada à API.",
    )
    tokens_completion: Optional[int] = Field(
        default=None,
        description="Tokens de saída gerados pela IA.",
    )
    tokens_total: Optional[int] = Field(
        default=None,
        description="Total de tokens consumidos (prompt + completion).",
    )


# ---------------------------------------------------------------------------
# Prompt de Sistema
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT: str = """Você é um sistema especializado em extração de dados financeiros de notas fiscais brasileiras.

Sua tarefa é ler o texto de uma nota fiscal e extrair os campos solicitados com precisão absoluta.

Regras obrigatórias:
1. Extraia apenas informações EXPLICITAMENTE presentes no texto. Nunca invente ou infira valores.
2. Para o campo `valor_bruto`, converta o valor para float em reais (ex: "R$ 8.500,00" → 8500.0).
3. Preserve as datas no formato encontrado no documento (ex: DD/MM/AAAA).
4. Se um campo não estiver presente no documento, retorne null para ele.
5. Não adicione comentários, explicações ou texto fora do JSON estruturado.
"""

_USER_PROMPT_TEMPLATE: str = """Extraia os dados financeiros da nota fiscal abaixo:

--- INÍCIO DO DOCUMENTO ---
{conteudo}
--- FIM DO DOCUMENTO ---
"""

# ---------------------------------------------------------------------------
# Função de extração individual (com Retry)
# ---------------------------------------------------------------------------


async def _extrair_documento(
    client: AsyncOpenAI,
    documento: DocumentoValido,
    semaforo: asyncio.Semaphore,
) -> ResultadoExtracao:
    """Extrai dados de um único documento com controle de concorrência e retry.

    O Semaphore garante que no máximo ``max_concorrencia`` chamadas simultâneas
    ocorram no event loop. O padrão Retry com Exponential Backoff é aplicado
    usando ``tenacity.AsyncRetrying`` para cobrir erros recuperáveis da API.

    Args:
        client: Instância compartilhada de ``AsyncOpenAI``.
        documento: Documento sanitizado a ser processado.
        semaforo: Semáforo de controle de concorrência compartilhado pelo lote.

    Returns:
        ``ResultadoExtracao`` com dados extraídos ou motivo de falha, nunca
        lança exceção — garantindo isolamento de falhas no lote.
    """
    inicio = time.monotonic()

    async with semaforo:
        try:
            # --- Retry com Exponential Backoff via tenacity ---
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(_ERROS_RECUPERAVEIS),
                stop=stop_after_attempt(RETRY_MAX_TENTATIVAS),
                wait=wait_exponential(
                    multiplier=1,
                    min=RETRY_ESPERA_MIN_S,
                    max=RETRY_ESPERA_MAX_S,
                ),
                reraise=True,  # Re-lança após esgotar as tentativas
            ):
                with attempt:
                    numero_tentativa = attempt.retry_state.attempt_number
                    if numero_tentativa > 1:
                        logger.warning(
                            "Retentativa %d/%d para '%s'.",
                            numero_tentativa,
                            RETRY_MAX_TENTATIVAS,
                            documento.nome_arquivo,
                        )

                    # --- Chamada à API com Structured Outputs ---
                    # client.beta.chat.completions.parse() valida a resposta
                    # contra o schema Pydantic antes de retornar, eliminando
                    # a necessidade de parsing manual do JSON.
                    resposta = await client.beta.chat.completions.parse(
                        model=MODELO_OPENAI,
                        messages=[
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": _USER_PROMPT_TEMPLATE.format(
                                    conteudo=documento.conteudo
                                ),
                            },
                        ],
                        response_format=NotaFiscalData,
                        temperature=0.0,  # Determinismo máximo para extração
                    )

            # --- Extrai dados da resposta ---
            choice = resposta.choices[0]
            dados: NotaFiscalData = choice.message.parsed  # type: ignore[assignment]

            usage = resposta.usage
            tokens_prompt = usage.prompt_tokens if usage else None
            tokens_completion = usage.completion_tokens if usage else None
            tokens_total = usage.total_tokens if usage else None

            modelo_real = resposta.model or MODELO_OPENAI

            tempo_total = time.monotonic() - inicio
            logger.info(
                "Extração OK '%s' | modelo=%s | tokens=%s | %.2fs",
                documento.nome_arquivo,
                modelo_real,
                tokens_total,
                tempo_total,
            )

            # Freio deliberado de 500 ms após cada extração bem-sucedida.
            # Garante respiro entre liberações do semáforo e a próxima chamada,
            # prevenindo rajadas de 429 Too Many Requests enquanto os limites
            # do Tier 1 ainda estão em propagação.
            await asyncio.sleep(0.5)

            return ResultadoExtracao(
                nome_arquivo=documento.nome_arquivo,
                sucesso=True,
                dados_extraidos=dados,
                motivo_falha=None,
                modelo_utilizado=modelo_real,
                tempo_processamento_s=round(tempo_total, 4),
                tokens_prompt=tokens_prompt,
                tokens_completion=tokens_completion,
                tokens_total=tokens_total,
            )

        except RetryError as exc:
            # Tenacity esgotou todas as tentativas.
            causa = str(exc.last_attempt.exception()) if exc.last_attempt else str(exc)
            tempo_total = time.monotonic() - inicio
            logger.error(
                "FALHA após %d tentativas para '%s': %s",
                RETRY_MAX_TENTATIVAS,
                documento.nome_arquivo,
                causa,
            )
            return ResultadoExtracao(
                nome_arquivo=documento.nome_arquivo,
                sucesso=False,
                motivo_falha=f"Falha após {RETRY_MAX_TENTATIVAS} tentativas: {causa}",
                modelo_utilizado=MODELO_OPENAI,
                tempo_processamento_s=round(tempo_total, 4),
            )

        except openai.APIStatusError as exc:
            # Erro de status HTTP não recuperável (ex: 401 Unauthorized, 400 Bad Request).
            tempo_total = time.monotonic() - inicio
            logger.error(
                "Erro de API (HTTP %d) para '%s': %s",
                exc.status_code,
                documento.nome_arquivo,
                exc.message,
            )
            return ResultadoExtracao(
                nome_arquivo=documento.nome_arquivo,
                sucesso=False,
                motivo_falha=f"Erro de API OpenAI (HTTP {exc.status_code}): {exc.message}",
                modelo_utilizado=MODELO_OPENAI,
                tempo_processamento_s=round(tempo_total, 4),
            )

        except Exception as exc:  # noqa: BLE001
            # Captura qualquer falha inesperada sem quebrar o lote.
            tempo_total = time.monotonic() - inicio
            logger.exception(
                "Erro inesperado ao processar '%s': %s",
                documento.nome_arquivo,
                exc,
            )
            return ResultadoExtracao(
                nome_arquivo=documento.nome_arquivo,
                sucesso=False,
                motivo_falha=f"Erro inesperado: {type(exc).__name__}: {exc}",
                modelo_utilizado=MODELO_OPENAI,
                tempo_processamento_s=round(tempo_total, 4),
            )


# ---------------------------------------------------------------------------
# Função Pública — Ponto de Entrada do Módulo
# ---------------------------------------------------------------------------


async def extrair_lote_documentos(
    documentos: list[DocumentoValido],
    max_concorrencia: int = 10,
    api_key: Optional[str] = None,
) -> list[ResultadoExtracao]:
    """Extrai dados financeiros de um lote de documentos sanitizados em paralelo.

    Orquestra a extração concorrente com:
      - ``asyncio.Semaphore`` para limitar o paralelismo real à OpenAI.
      - ``asyncio.gather`` para disparar todas as corrotinas e aguardar
        os resultados sem cancelar o lote em caso de falha individual.
      - Isolamento de falhas: cada documento falha de forma independente;
        o resultado de todos os outros documentos é preservado.

    .. note::
        O import de ``DocumentoValido`` é resolvido de forma lazy em runtime
        para evitar import circular entre ``main.py`` e este módulo.

    Args:
        documentos: Lista de ``DocumentoValido`` produzida pelo
            ``DocumentProcessorService`` em ``main.py``.
        max_concorrencia: Número máximo de chamadas simultâneas à API da
            OpenAI. Padrão 10; para contas Tier 1 (500 RPM) recomenda-se 30.
        api_key: Chave de API da OpenAI. Se ``None``, lida da variável de
            ambiente ``OPENAI_API_KEY`` (comportamento padrão do SDK).

    Returns:
        Lista de ``ResultadoExtracao``, um por documento de entrada, na mesma
        ordem da lista de entrada. Documentos com falha têm ``sucesso=False``
        e o campo ``motivo_falha`` preenchido.

    Raises:
        ValueError: Se ``max_concorrencia`` for menor ou igual a zero.
        EnvironmentError: Se ``OPENAI_API_KEY`` não estiver definida —
            falha de configuração do servidor, capturada pelo handler global.
    """
    if max_concorrencia <= 0:
        raise ValueError(
            f"'max_concorrencia' deve ser um inteiro positivo, recebido: {max_concorrencia}."
        )

    if not documentos:
        logger.info("Lote vazio recebido. Nenhuma chamada à API será realizada.")
        return []

    # --- Inicializa o cliente assíncrono ---
    # O cliente é criado uma única vez e compartilhado entre todas as
    # corrotinas do lote, reutilizando o pool de conexões HTTP (httpx).
    # O SDK lê OPENAI_API_KEY do ambiente automaticamente; não passamos
    # api_key nem base_url aqui para usar o endpoint oficial da OpenAI.
    resolved_api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not resolved_api_key:
        # Levanta EnvironmentError (falha de configuração do servidor), não
        # openai.AuthenticationError, pois o construtor do SDK exige um objeto
        # `response` válido e explode com AttributeError quando response=None.
        # O handler global em main.py captura EnvironmentError e retorna HTTP 500.
        raise EnvironmentError(
            "A variável de ambiente OPENAI_API_KEY não está definida. "
            "Adicione OPENAI_API_KEY=sk-... ao arquivo backend/.env "
            "e reinicie o servidor."
        )

    # Instancia o cliente nativo da OpenAI. O SDK lê OPENAI_API_KEY
    # automaticamente de os.environ; não é necessário passar api_key
    # nem base_url explicitamente.
    client = AsyncOpenAI()

    # --- Semáforo de controle de concorrência ---
    semaforo = asyncio.Semaphore(max_concorrencia)

    logger.info(
        "Iniciando extração de lote: %d documento(s) | concorrência máx=%d | provedor=OpenAI | modelo=%s",
        len(documentos),
        max_concorrencia,
        MODELO_OPENAI,
    )

    # --- Disparo paralelo em chunks para limitar pico de RAM ---
    # Criar 1000 corrotinas de uma vez consome memória antes do semáforo
    # entrar em ação. Processar em batches de CHUNK_SIZE limita o pico a
    # CHUNK_SIZE objetos de corrotina simultâneos, liberando memória após
    # cada batch e permitindo ao GC recuperar os objetos de documentos já
    # processados. O semáforo continua governando o paralelismo real (I/O).
    CHUNK_SIZE = 100
    resultados: list[ResultadoExtracao] = []

    for inicio_chunk in range(0, len(documentos), CHUNK_SIZE):
        chunk = documentos[inicio_chunk: inicio_chunk + CHUNK_SIZE]
        logger.info(
            "  Chunk %d/%d (%d docs)...",
            inicio_chunk // CHUNK_SIZE + 1,
            (len(documentos) + CHUNK_SIZE - 1) // CHUNK_SIZE,
            len(chunk),
        )
        tarefas_chunk = [
            _extrair_documento(client=client, documento=doc, semaforo=semaforo)
            for doc in chunk
        ]
        chunk_resultados: list[ResultadoExtracao] = list(
            await asyncio.gather(*tarefas_chunk)
        )
        resultados.extend(chunk_resultados)
        # chunk e tarefas_chunk saem de escopo aqui → GC pode recuperar memória

    # --- Sumariza o lote no log ---
    total_ok = sum(1 for r in resultados if r.sucesso)
    total_falha = len(resultados) - total_ok
    tokens_consumidos = sum(r.tokens_total or 0 for r in resultados)

    logger.info(
        "Extração de lote concluída: %d OK / %d com falha | tokens totais=%d",
        total_ok,
        total_falha,
        tokens_consumidos,
    )

    await client.close()

    return list(resultados)
