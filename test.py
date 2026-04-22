import os
import re

pasta = "arquivos_nf" # Certifique-se de que a pasta está no mesmo nível do script

print("🔍 Iniciando varredura forense detalhada...\n")

# Dicionário para guardar o placar final
contadores = {"ok": 0, "encoding": 0, "mojibake": 0, "truncado": 0, "ausente": 0}

for nome_arquivo in sorted(os.listdir(pasta)):
    if not nome_arquivo.endswith(".txt"): continue
    
    caminho = os.path.join(pasta, nome_arquivo)
    with open(caminho, "rb") as f:
        bytes_brutos = f.read()
        
    # 1. Teste de Encoding Estrito
    try:
        texto = bytes_brutos.decode("utf-8", errors="strict")
    except UnicodeDecodeError as e:
        print(f"🚨 [ERRO ENCODING] {nome_arquivo} - Falha exata no byte {e.start}: caractere não reconhecido.")
        contadores["encoding"] += 1
        continue

    teve_erro = False

    # 2. Teste de Caracteres de Substituição (Mojibake real)
    if "\ufffd" in texto or "\x1a" in texto:
        print(f"⚠️  [MOJIBAKE] {nome_arquivo} - O Python leu, mas encontrou o símbolo de corrupção ().")
        teve_erro = True
        contadores["mojibake"] += 1
    
    # 3. Teste de Truncamento (Arquivo cortado ao meio)
    if len(texto) < 100:
        conteudo_exibido = texto.replace('\n', ' ')[:50] # Mostra um pedacinho para vermos a falha
        print(f"✂️  [TRUNCADO] {nome_arquivo} - Apenas {len(texto)} caracteres. Termina em: '{conteudo_exibido}...'")
        teve_erro = True
        contadores["truncado"] += 1
        
    # 4. Teste de Campos Essenciais Ausentes
    campos_esperados = ["TIPO_DOCUMENTO:", "FORNECEDOR:", "VALOR_BRUTO:"]
    faltantes = [c for c in campos_esperados if c not in texto]
    
    if faltantes:
        print(f"🕳️  [CAMPOS AUSENTES] {nome_arquivo} - O arquivo está oco. Faltam: {faltantes}")
        teve_erro = True
        contadores["ausente"] += 1

    # 5. CASO DE SUCESSO (Feedback verboso)
    if not teve_erro:
        # Tenta extrair o nome do fornecedor rapidinho só para dar feedback
        match = re.search(r"FORNECEDOR:\s*(.+)", texto)
        fornecedor = match.group(1).strip() if match else "Desconhecido"
        print(f"✅ [OK] {nome_arquivo} - Lido com sucesso ({len(texto)} chars) | Fornecedor: {fornecedor[:20]}")
        contadores["ok"] += 1

# --- RESUMO FINAL ---
print("\n" + "="*45)
print("📊 RESUMO DA AUDITORIA FORENSE")
print("="*45)
print(f"✅ Arquivos Íntegros (OK):    {contadores['ok']}")
print(f"🚨 Erros de Encoding Puro:    {contadores['encoding']}")
print(f"⚠️  Caracteres Corrompidos:    {contadores['mojibake']}")
print(f"✂️  Arquivos Truncados:        {contadores['truncado']}")
print(f"🕳️  Campos Ausentes:           {contadores['ausente']}")
print("="*45)