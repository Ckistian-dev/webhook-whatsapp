import os
import json
import httpx
import google.generativeai as genai
import asyncio
import random
from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv

# --- Carregando as Configurações do .env ---
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME")
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
        # Extrai o texto da mensagem, independentemente do formato
        message_obj = msg.get("message", {})
        if "ephemeralMessage" in message_obj:
            message_obj = message_obj.get("ephemeralMessage", {}).get("message", {})
        
        texto = (
            message_obj.get("extendedTextMessage", {}).get("text") or
            message_obj.get("conversation", "")
        ).strip()

        if not texto:
            continue

        # Define o 'role' com base em quem enviou a mensagem
        role = "model" if msg.get("key", {}).get("fromMe") else "user"
        
        historico_formatado.append({
            'role': role,
            'parts': [{'text': texto}]
        })
    return historico_formatado

async def obter_historico_conversa(remetente_jid: str):
    """Busca o histórico de mensagens da API da Evolution e formata para o Gemini."""
    url = f"{EVOLUTION_API_URL}/chat/findMessages/{EVOLUTION_INSTANCE_NAME}"
    headers = {"Content-Type": "application/json", "apikey": EVOLUTION_API_KEY}
    payload = {
        "where": {
            "key": {
                "remoteJid": remetente_jid
            }
        }
    }
    
    print(f"   -> Buscando histórico completo de '{remetente_jid}' na API via POST...")
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            mensagens_da_api = response.json()
            
            # A API retorna as mais recentes primeiro, então invertemos para ordem cronológica
            mensagens_da_api.reverse() 
            
            print(f"   -> {len(mensagens_da_api)} mensagens recuperadas e formatadas.")
            return formatar_historico_para_gemini(mensagens_da_api)
    except httpx.RequestError as e:
        print(f"   🚨 Erro ao buscar histórico da API: {e}")
        return [] # Retorna um histórico vazio em caso de falha

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
app = FastAPI(title="Chatbot WhatsApp com Gemini (sem Redis)")

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

    nova_mensagem_texto = (
        message_obj.get("extendedTextMessage", {}).get("text") or
        message_obj.get("conversation", "")
    ).strip()

    if not nova_mensagem_texto:
        return {"status": "ignorado"}

    print(f"\n--- Mensagem Recebida de {remetente_jid} ---")
    print(f"Mensagem: {nova_mensagem_texto}")
        
    try:
        # Busca o histórico da API
        historico_conversa = await obter_historico_conversa(remetente_jid)
        
        # Adiciona a mensagem atual ao histórico para o Gemini ter o contexto completo
        historico_conversa.append({'role': 'user', 'parts': [{'text': nova_mensagem_texto}]})
        
        print("   -> Enviando para o Gemini...")
        chat = model.start_chat(history=historico_conversa)
        resposta_gemini = chat.send_message(nova_mensagem_texto)
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
        raise HTTPException(status_code=500, detail="Erro interno no processamento do chatbot")
    
    return {"status": "recebido_e_processado"}

