# AuditorIA 📊🤖

**AuditorIA** é uma plataforma SaaS de auditoria financeira automatizada. O sistema foi projetado para analisar centenas ou milhares de documentos fiscais (notas fiscais, recibos, contratos) em lote, utilizando Inteligência Artificial para extrair dados estruturados e um motor de regras de negócio para detectar anomalias, possíveis fraudes e divergências operacionais.

Ao final do processamento, o utilizador tem acesso a um dashboard interativo no frontend e a ficheiros CSV prontos para download (compatíveis com ferramentas de análise como Power BI e Excel).

---

## 🚀 Visão Geral do Produto

1. **Upload em Lote:** O utilizador faz o upload de um ficheiro `.zip` contendo os documentos a serem auditados.
2. **Processamento Assíncrono e Seguro:** O backend recebe o ficheiro, valida-o com proteções de segurança avançadas (como prevenção contra Zip Bombs) e sanitiza o texto.
3. **Extração de Dados com IA:** Utilizando o modelo `gpt-4o-mini` da OpenAI, o sistema extrai informações cruciais estruturadas (fornecedor, valores, datas, status e CNPJ) de cada documento individual.
4. **Motor de Auditoria (Regras de Negócio):** Os dados extraídos passam por uma verificação automatizada com base em regras estritas, identificando:
   - Notas duplicadas
   - Divergências de datas (ex: pagamento anterior à emissão)
   - Status inconsistentes
   - Valores atípicos
   - Validação matemática de CNPJ
5. **Dashboard & Relatórios:** O frontend exibe o status da análise de forma resiliente, compondo os dados em um relatório rico e disponibilizando as bases de auditoria completas em CSV.

---

## 🏗️ Destaques da Arquitetura

O **AuditorIA** foi construído com resiliência de alto nível para garantir o funcionamento contínuo mesmo diante de grandes volumes e infraestruturas em nuvem limitadas (como _free-tiers_).

*   **Resiliência de Rede:** O frontend realiza **Polling Assíncrono** com status HTTP 202. Desenvolvido para ignorar quedas temporárias de conectividade do servidor (ex: erros `502 Bad Gateway` ou `404`), ele assegura que o utilizador final nunca seja deparado com ecrãs de erro falsos enquanto o Job ainda está em processamento.
*   **Prevenção de OOM (Out of Memory):** Para respeitar o limite severo de RAM das instâncias em nuvem gratuitas (ex: Render com 512MB), o Backend implementa **Chunking Inteligente**. O processamento dos ficheiros e a requisição à IA ocorrem em lotes (ex: 100 documentos por ciclo), geridos por semáforos assíncronos (`asyncio.Semaphore`), controlando o pico de memória e evitando banimentos por _Rate Limits_ na API da OpenAI.
*   **Recuperação de Falhas (State Persistence):** O estado de cada Job de auditoria tem persistência contínua num disco virtual (*Persistent Disk*) em formato JSON. Se o provedor de nuvem forçar a reinicialização do servidor (`Worker Restart`), o processo não é perdido. Assim que o servidor regressa à atividade, a sua memória é restaurada pelo disco, poupando dados e tempo.
*   **Tradução e Fusão de Contratos no Frontend:** O sistema recebe respostas JSON complexas e segmentadas do backend (que separa a extração pura da IA `extracao_ia` dos laudos de fraude `auditoria`). O Frontend, através do seu middleware (`auditor-api.ts`), interceta estas chaves, junta os dados pelo nome de cada ficheiro e converte tudo num objeto limpo, aplicando máscaras formatadas de moeda para o UI e traduzindo estados técnicos em alertas visuais claros.

---

## 💻 Tecnologias Utilizadas

**Backend:**
*   **Python 3.10+** com **FastAPI** para endpoints assíncronos de alta performance.
*   **OpenAI SDK** (`gpt-4o-mini`) com suporte total ao **Structured Outputs** para prever rigidamente o esquema do JSON retornado.
*   **Pydantic V2** atuando tanto como contrato para a IA quanto validação de request/response interna.
*   **Tenacity** para resiliência de chamadas em rede (*Exponential Backoff*).

**Frontend:**
*   **React + Vite** (com scaffolding ágil em conjunto com o Lovable).
*   **TypeScript** (tipagem de ponta a ponta reforçando os contratos complexos da API).
*   **Tailwind CSS** para interfaces modernas e customização fluída.

---

## ⚙️ Variáveis de Ambiente Necessárias (`.env`)

Para o sistema funcionar, é necessário configurar as chaves de acesso.

### Backend (`/backend/.env`)
Crie um ficheiro `.env` na raiz da pasta `backend`:

```env
# Configurações do Servidor
ENVIRONMENT=development
LOG_LEVEL=info

# Origens CORS Permitidas (Frontend)
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173,http://localhost:8080

# Chave de acesso do modelo IA
OPENAI_API_KEY=sk-sua-chave-secreta-da-openai-aqui
```

### Frontend (`/frontend/src/lib/auditor-api.ts`)
*(Nota: No código fornecido, a URL do Backend está hardcoded. Para desenvolvimento local, pode ser necessário alterar a constante na primeira linha de `auditor-api.ts` de `https://nlconsulting-ai-auditor.onrender.com` para `http://localhost:8000`)*

---

## 🚀 Como rodar localmente

Siga estes passos num ambiente com Node.js e Python ativados.

### 1. Iniciar o Backend
Aceda ao diretório do backend, crie o ambiente virtual e execute:

```bash
cd backend

# Criar e ativar o virtual env
python -m venv venv
source venv/bin/activate  # No Windows use: venv\Scripts\activate

# Instalar as dependências
pip install -r requirements.txt

# Iniciar o servidor
uvicorn main:app --reload --port 8000
```
O Backend estará disponível em `http://localhost:8000`. A documentação Swagger gerada automaticamente pode ser acedida em `http://localhost:8000/docs`.

### 2. Iniciar o Frontend
Noutra janela do terminal, vá para a diretoria do frontend:

```bash
cd frontend

# Instalar todos os pacotes
npm install

# Levantar a plataforma em modo Dev
npm run dev
```
A sua interface interativa subirá por padrão em `http://localhost:8080` (verifique o terminal caso a porta mude).
