# AuditorIA

## Visão Executiva

O AuditorIA é uma plataforma SaaS de classe corporativa (Enterprise) concebida para a auditoria financeira automatizada e assíncrona. O sistema foi arquitetado para processar lotes massivos de documentos fiscais e contratuais, extraindo dados não-estruturados através de modelos de linguagem de grande escala (LLMs) via integração estruturada com a OpenAI. Adicionalmente, a plataforma aplica motores de regras de negócio determinísticos para a deteção de fraudes, divergências operacionais e anomalias financeiras. O resultado é um pipeline de processamento robusto que assegura alta fiabilidade, escalabilidade horizontal e rastreabilidade total de dados para equipas de auditoria e compliance.

## Desafios de Engenharia e Soluções Arquiteturais

A conceção do AuditorIA exigiu a resolução de complexidades inerentes ao processamento de dados em larga escala e à integração com inteligência artificial generativa em ambientes de produção. 

### Envenenamento de Dados e Anomalias de Encoding

**Desafio:** O processamento de dados submetidos pelo utilizador introduz o risco de envenenamento de dados e submissão de ficheiros maliciosos ou legados (por exemplo, ficheiros codificados em ANSI disfarçados de UTF-8). Estas anomalias podem corromper o pipeline, causar alucinações nos modelos de linguagem e gerar custos operacionais desnecessários nas chamadas à API.

**Solução:** Implementação de uma camada de sanitização forense a nível de byte (strict decoding) no início do fluxo de execução. O sistema aplica o princípio de Fail-Fast: ficheiros com estruturas corrompidas, truncados ou com caracteres ilegíveis são intercetados imediatamente. Estes artefactos são tipificados no domínio de erro e impedidos de avançar para a inferência no LLM, salvaguardando a integridade da extração e otimizando os custos computacionais.

### Limites de Taxa de API (Rate Limiting) e Concorrência

**Desafio:** O processamento concorrente de milhares de documentos de forma a maximizar o throughput inevitavelmente esgota os limites de requisições por minuto (RPM) e tokens por minuto (TPM) impostos pelas APIs de inteligência artificial, resultando em erros HTTP 429 (Too Many Requests).

**Solução:** O pipeline foi dotado de um mecanismo de concorrência controlada através de semáforos assíncronos restritivos. Esta arquitetura encontra-se acoplada a um mecanismo de Exponential Backoff implementado via bibliotecas de resiliência (Tenacity). O sistema possui a capacidade de absorver o bloqueio da API, reter o estado da requisição e efetuar novas tentativas de forma progressiva e coordenada, garantindo que o lote seja processado na sua totalidade sem perda de dados ou falhas catastróficas.

### Restrições de Memória em Nuvem (OOM)

**Desafio:** O processamento em memória de grandes volumes de dados (gigabytes de ficheiros agregados num único lote) em instâncias de nuvem com restrições rigorosas de recursos frequentemente induz picos de memória, resultando no encerramento abrupto dos workers via erros de Out Of Memory (OOM).

**Solução:** A arquitetura de processamento foi refatorada para operar através de chunks (blocos) inteligentes. O estado do processamento é persistido incrementalmente em disco virtual persistente através de operações transacionais. Em caso de terminação prematura ou falha do worker, o design tolerante a falhas assegura que o job de auditoria seja retomado com precisão a partir do ponto exato da interrupção, eliminando redundâncias no reprocessamento.

### Rastreabilidade Absoluta (Data Lineage)

**Desafio:** Em sistemas corporativos de auditoria, qualquer falha silenciosa ou descarte de ficheiros compromete a integridade do ciclo de auditoria e a confiança no Data Warehouse. A omissão de documentos impossibilita a conciliação exata entre o input fornecido e o output reportado.

**Solução:** Implementação de uma arquitetura de pipeline fechado e imutável. Documentos vetados e rejeitados na camada inicial de sanitização não são descartados silenciosamente. Em contrapartida, são preservados em memória e injetados de forma controlada na etapa final do ciclo de exportação. Esta solução garante que a consolidação dos dados no Data Warehouse (por exemplo, exportações nativas para Power BI) reflita 100% dos ficheiros originais submetidos, mantendo o log de auditoria cronológico estritamente imaculado e transparente.

## Stack Tecnológico

A plataforma baseia-se numa arquitetura moderna, orientada a microsserviços e estritamente tipada:

* **Backend:** Python 3.10+, FastAPI
* **Integração IA:** OpenAI SDK, implementando Pydantic V2 para validação rigorosa via Structured Outputs.
* **Frontend:** React, TypeScript

## Infraestrutura e Hospedagem em Produção

A arquitetura de produção foi provisionada na plataforma cloud Render, garantindo alta disponibilidade, isolamento de processos e integração contínua (CI/CD). A segregação de responsabilidades foi mantida também na infraestrutura:

* **Backend (Web Service):** O motor FastAPI está hospedado num ambiente de execução isolado. Para mitigar a volatilidade inerente aos *workers* de instâncias cloud de baixo custo (que sofrem reinicializações periódicas), a infraestrutura faz uso de *Persistent Disks* (Discos Persistentes). Esta escolha arquitetural garante a retenção do estado das auditorias em processamento assíncrono; se o servidor for reiniciado pelo provedor, a recuperação do *job* ocorre de forma transparente.
* **Frontend (Static Site / SPA):** A interface em React/Vite está provisionada como um serviço estático de alta performance. O roteamento no lado do cliente (Client-Side Routing) foi devidamente configurado para evitar erros 404 em navegações diretas, garantindo a entrega otimizada dos *assets*.
* **Continuous Deployment (CD):** O pipeline de entrega está diretamente acoplado ao repositório GitHub. Qualquer alteração aprovada na *branch* principal aciona processos independentes de *build* e *deploy* para o cliente e para o servidor, assegurando um fluxo de entrega ágil e livre de intervenção manual.

## Configuração de Variáveis de Ambiente

O ambiente requer a definição exata das variáveis de configuração para aceder aos serviços externos e controlar o ambiente de execução. Crie um ficheiro `.env` na raiz do diretório `backend/` com a seguinte estrutura:

```env
# Chave de API obrigatória para inferência do modelo LLM
OPENAI_API_KEY=sk-...

# Limite máximo de bytes descomprimidos permitidos na mitigação de Zip Bombs
ZIP_MAX_UNCOMPRESSED_BYTES=524288000

# Limite máximo de ficheiros permitidos num único lote
ZIP_MAX_FILE_COUNT=2000
```

## Instruções de Execução Local

### Requisitos Prévios
* Node.js (v18+)
* Python (v3.10+)

### Execução do Backend

1. Navegue para o diretório do servidor:
   ```bash
   cd backend
   ```
2. Crie e ative um ambiente virtual:
   ```bash
   python -m venv venv
   # No Windows:
   .\venv\Scripts\activate
   # Em sistemas baseados em Unix:
   source venv/bin/activate
   ```
3. Instale as dependências rigorosas do projeto:
   ```bash
   pip install -r requirements.txt
   ```
4. Inicie o servidor Uvicorn em modo de desenvolvimento:
   ```bash
   uvicorn main:app --reload
   ```

### Execução do Frontend

1. Navegue para o diretório do cliente:
   ```bash
   cd frontend
   ```
2. Instale as dependências do ecossistema Node:
   ```bash
   npm install
   ```
3. Inicie o servidor de desenvolvimento Vite:
   ```bash
   npm run dev
   ```

A aplicação cliente estará imediatamente acessível, estabelecendo comunicação através de chamadas HTTP assíncronas com o backend local.
