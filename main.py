import os
import json
import httpx
import google.generativeai as genai
import asyncio
import random
import mimetypes # NOVO: Para identificar o tipo de arquivo de áudio
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

# --- Carregando as Configurações do .env ---
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME") # Recomendo usar 'gemini-1.5-pro-latest' ou 'gemini-1.5-flash-latest' para áudio
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT")
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY")
EVOLUTION_INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME")
TARGET_JID = os.getenv("TARGET_JID")

# --- Verificação de Configuração Essencial ---
config_vars = [GEMINI_API_KEY, GEMINI_MODEL_NAME, SYSTEM_PROMPT, EVOLUTION_API_URL, EVOLUTION_API_KEY, EVOLUTION_INSTANCE_NAME, TARGET_JID]
if not all(config_vars):
    print("🚨 ERRO CRÍTICO: Verifique se todas as variáveis de ambiente necessárias estão no seu arquivo .env!")
    exit()

# --- Configuração do Cliente Gemini ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL_NAME,
        system_instruction=SYSTEM_PROMPT
    )
    print(f"✅ Modelo Gemini '{GEMINI_MODEL_NAME}' configurado com a persona.")
except Exception as e:
    print(f"🚨 ERRO CRÍTICO ao configurar o modelo Gemini: {e}")
    exit()

# --- Funções Auxiliares da API ---

def formatar_historico_para_gemini(mensagens_api: list):
    """Converte o histórico da API da Evolution para o formato do Gemini."""
    historico_formatado = []
    for msg in mensagens_api:
        message_obj = msg.get("message", {})
        if "ephemeralMessage" in message_obj:
            message_obj = message_obj.get("ephemeralMessage", {}).get("message", {})
        
        texto = (
            message_obj.get("extendedTextMessage", {}).get("text") or
            message_obj.get("conversation", "")
        ).strip()

        if not texto:
            continue

        role = "model" if msg.get("key", {}).get("fromMe") else "user"
        
        historico_formatado.append({
            'role': role,
            'parts': [{'text': texto}]
        })
    return historico_formatado

# CORRIGIDO E MELHORADO: Função para obter histórico com paginação
async def obter_historico_conversa(remetente_jid: str):
    """Busca todo o histórico de mensagens da API da Evolution, lidando com paginação."""
    url = f"{EVOLUTION_API_URL}/chat/findMessages/{EVOLUTION_INSTANCE_NAME}"
    headers = {"Content-Type": "application/json", "apikey": EVOLUTION_API_KEY}
    
    historico_completo = []
    pagina_atual = 1
    total_paginas = 1  # Inicia com 1 para entrar no loop

    print(f"   -> Iniciando busca do histórico completo de '{remetente_jid}'...")
    
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            while pagina_atual <= total_paginas:
                print(f"     -> Buscando página {pagina_atual}/{total_paginas}...")
                payload = {
                    "page": pagina_atual,
                    "pageSize": 100, # ou 'offset': 100, dependendo da sua versão da API
                    "where": {
                        "key": {"remoteJid": remetente_jid}
                    }
                }
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()

                # A estrutura da resposta pode variar, ajuste se necessário
                messages_data = data.get("messages", data)

                if pagina_atual == 1:
                    total_paginas = messages_data.get("pages", 1)

                mensagens_da_pagina = messages_data.get("records", [])
                historico_completo.extend(mensagens_da_pagina)
                pagina_atual += 1

        # A API retorna as mais recentes primeiro em cada página, então ordenamos no final
        historico_ordenado = sorted(historico_completo, key=lambda msg: int(msg.get("messageTimestamp", 0)))
        
        print(f"   -> {len(historico_ordenado)} mensagens recuperadas e formatadas.")
        return formatar_historico_para_gemini(historico_ordenado)

    except httpx.RequestError as e:
        print(f"   🚨 Erro ao buscar histórico da API: {e}")
        return []

async def enviar_presenca(remetente_jid: str, tipo_presenca: str):
    """Envia uma notificação de presença (digitando ou pausado)."""
    url = f"{EVOLUTION_API_URL}/chat/setPresence/{EVOLUTION_INSTANCE_NAME}"
    headers = {"Content-Type": "application/json", "apikey": EVOLUTION_API_KEY}
    payload = {"number": remetente_jid, "presence": tipo_presenca}
    
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, headers=headers, json=payload, timeout=10)
        print(f"   -> Presença '{tipo_presenca}' enviada para {remetente_jid}.")
    except httpx.RequestError as e:
        print(f"   🚨 Erro ao enviar presença: {e}")

async def enviar_resposta_whatsapp(remetente_jid: str, texto_resposta: str):
    """Envia a resposta gerada de volta para o usuário."""
    url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE_NAME}"
    headers = {"Content-Type": "application/json", "apikey": EVOLUTION_API_KEY}
    payload = {
        "number": remetente_jid,
        "text": texto_resposta,
        "options": {
            "delay": 1200,
            "presence": "composing"
        }
    }
    
    print(f"   -> Enviando resposta para {remetente_jid}...")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
        print("   -> Resposta enviada com sucesso!")
    except httpx.RequestError as e:
        print(f"   🚨 Erro ao enviar resposta via Evolution API: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"   -> Status Code: {e.response.status_code}")
            try:
                print(f"   -> Resposta do Erro: {e.response.json()}")
            except json.JSONDecodeError:
                print(f"   -> Resposta do Erro (não-JSON): {e.response.text}")

# --- Aplicação FastAPI ---
app = FastAPI(title="Chatbot WhatsApp com Gemini (com Áudio)")

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/connection-update")
async def webhook_connection_update(request: Request):
    data = await request.json()
    instance = data.get("instance")
    state = data.get("data", {}).get("state")
    print(f"✅ Evento de conexão recebido da instância '{instance}': {state}")
    return {"status": "connection_update_received"}

@app.post("/messages-upsert")
async def webhook_receiver(request: Request):
    data = await request.json()
    
    if data.get("event") != "messages.upsert":
        return {"status": "evento_ignorado"}

    mensagem_data = data.get("data")
    if not mensagem_data or mensagem_data.get("key", {}).get("fromMe", False):
        return {"status": "ignorado"}
        
    remetente_jid = mensagem_data.get("key", {}).get("remoteJid")
    if not remetente_jid or remetente_jid != TARGET_JID:
        return {"status": "ignorado"}

    message_obj = mensagem_data.get("message", {})
    if "ephemeralMessage" in message_obj:
        message_obj = message_obj.get("ephemeralMessage", {}).get("message", {})

    nova_mensagem_texto = None
    arquivo_audio_gemini = None # Variável para o arquivo enviado ao Gemini

    if "extendedTextMessage" in message_obj or "conversation" in message_obj:
        nova_mensagem_texto = (
            message_obj.get("extendedTextMessage", {}).get("text") or
            message_obj.get("conversation", "")
        ).strip()
        print(f"\n--- Mensagem de Texto Recebida de {remetente_jid} ---")
        print(f"Mensagem: {nova_mensagem_texto}")

    elif "audioMessage" in message_obj:
        print(f"\n--- Mensagem de Áudio Recebida de {remetente_jid} ---")
        audio_info = message_obj["audioMessage"]
        audio_url = audio_info.get("url")
        if audio_url:
            try:
                # 1. Baixar o áudio
                async with httpx.AsyncClient() as client:
                    response = await client.get(audio_url)
                    response.raise_for_status()
                    audio_data = response.content
                
                caminho_arquivo_audio = "audio_recebido.ogg"
                
                # 2. Salvar o áudio em um arquivo local PRIMEIRO
                with open(caminho_arquivo_audio, "wb") as f:
                    f.write(audio_data)
                
                print(f"   -> Áudio baixado ({len(audio_data)} bytes) e salvo localmente.")

                # 3. AGORA, fazer o upload do arquivo que já existe
                arquivo_audio_gemini = genai.upload_file(
                    path=caminho_arquivo_audio,
                    display_name="MensagemDeVozWhatsApp"
                )
                
                print("   -> Arquivo de áudio enviado para a API do Gemini com sucesso.")

            except Exception as e:
                print(f"   🚨 Falha ao baixar ou preparar o áudio: {e}")
                await enviar_resposta_whatsapp(remetente_jid, "Desculpe, não consegui processar seu áudio. Tente novamente.")
                return {"status": "erro_audio"}

    if not nova_mensagem_texto and not arquivo_audio_gemini:
        return {"status": "ignorado_sem_conteudo"}

    try:
        historico_conversa = await obter_historico_conversa(remetente_jid)
        
        # O histórico já vem formatado, então podemos usá-lo diretamente
        # A nova API `generate_content` aceita o histórico neste formato diretamente
        conteudo_para_gemini = historico_conversa
        
        # Prepara a mensagem atual
        partes_mensagem_atual = []
        if nova_mensagem_texto:
            partes_mensagem_atual.append({'text': nova_mensagem_texto})
        
        if arquivo_audio_gemini:
            # Pede para o modelo transcrever e responder
            prompt_transcricao = "Transcreva o áudio a seguir e, em seguida, responda ao que foi dito de forma apropriada:"
            partes_mensagem_atual.append({'text': prompt_transcricao})
            partes_mensagem_atual.append(arquivo_audio_gemini)
        
        conteudo_para_gemini.append({'role': 'user', 'parts': partes_mensagem_atual})

        print("   -> Enviando para o Gemini...")
        
        # Usa generate_content que é mais flexível para multimodal
        resposta_gemini = model.generate_content(conteudo_para_gemini)
        
        texto_resposta = resposta_gemini.text
        print(f"   -> Resposta do Gemini: {texto_resposta}")

        # Simula digitação e envia a resposta
        tempo_de_espera = min(max(len(texto_resposta) * 0.06, 2), 8)
        
        await enviar_presenca(remetente_jid, "composing")
        await asyncio.sleep(tempo_de_espera)
        await enviar_presenca(remetente_jid, "paused")
        await enviar_resposta_whatsapp(remetente_jid, texto_resposta)

    except Exception as e:
        print(f"   🚨 Erro no ciclo do chatbot: {e}")
        # Tenta dar uma resposta de erro para o usuário
        try:
            await enviar_resposta_whatsapp(remetente_jid, "Ocorreu um erro interno e não pude processar sua mensagem. Por favor, tente novamente.")
        except:
            pass # Se até o envio de erro falhar, apenas loga no servidor
        
        raise HTTPException(status_code=500, detail=f"Erro interno no processamento do chatbot: {e}")
    
    return {"status": "recebido_e_processado"}