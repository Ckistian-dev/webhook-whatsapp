import os
import json
import requests
import redis
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
REDIS_URL = os.getenv("REDIS_URL")
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY")
EVOLUTION_INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME")
TARGET_JID = os.getenv("TARGET_JID")

# --- Verificação de Configuração Essencial ---
config_vars = [GEMINI_API_KEY, GEMINI_MODEL_NAME, SYSTEM_PROMPT, REDIS_URL, EVOLUTION_API_URL, EVOLUTION_API_KEY, EVOLUTION_INSTANCE_NAME, TARGET_JID]
if not all(config_vars):
    print("🚨 ERRO CRÍTICO: Verifique se todas as variáveis de ambiente estão definidas no seu arquivo .env!")
    exit()

# --- Configuração dos Clientes ---
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

try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    print("✅ Conectado ao Redis com sucesso!")
except Exception as e:
    print(f"🚨 ERRO CRÍTICO ao conectar com o Redis: {e}")
    redis_client = None

# --- Funções Auxiliares ---

async def enviar_presenca(remetente_jid: str, tipo_presenca: str):
    """Envia uma notificação de presença (digitando ou pausado)."""
    url = f"{EVOLUTION_API_URL}/chat/setPresence/{EVOLUTION_INSTANCE_NAME}"
    headers = {"Content-Type": "application/json", "apikey": EVOLUTION_API_KEY}
    payload = {"number": remetente_jid, "presence": tipo_presenca}
    
    try:
        # Usamos uma sessão para enviar de forma assíncrona
        async with requests.AsyncClient() as client:
            await client.post(url, headers=headers, json=payload, timeout=10)
        print(f"   -> Presença '{tipo_presenca}' enviada para {remetente_jid}.")
    except requests.exceptions.RequestException as e:
        print(f"   🚨 Erro ao enviar presença: {e}")

async def enviar_resposta_whatsapp(remetente_jid: str, texto_resposta: str):
    """Envia a resposta gerada de volta para o usuário."""
    url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE_NAME}"
    headers = {"Content-Type": "application/json", "apikey": EVOLUTION_API_KEY}
    
    payload = {
        "number": remetente_jid,
        "textMessage": {
            "text": texto_resposta
        }
    }
    
    print(f"   -> Enviando resposta para {remetente_jid}...")
    try:
        # Usamos uma sessão para enviar de forma assíncrona
        async with requests.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
        print("   -> Resposta enviada com sucesso!")
    except requests.exceptions.RequestException as e:
        print(f"   🚨 Erro ao enviar resposta via Evolution API: {e}")
        if e.response:
            print(f"   -> Status Code: {e.response.status_code}")
            try:
                print(f"   -> Resposta do Erro: {e.response.json()}")
            except json.JSONDecodeError:
                print(f"   -> Resposta do Erro (não-JSON): {e.response.text}")


# --- Aplicação FastAPI ---
app = FastAPI(title="Chatbot WhatsApp com Gemini e Redis")

# Rota para verificação de saúde
@app.get("/health")
def health_check():
    return {"status": "ok"}

# Rota para eventos de conexão
@app.post("/connection-update")
async def webhook_connection_update(request: Request):
    data = await request.json()
    instance = data.get("instance")
    state = data.get("data", {}).get("state")
    print(f"✅ Evento de conexão recebido da instância '{instance}': {state}")
    return {"status": "connection_update_received"}

# Rota para receber novas mensagens
@app.post("/messages-upsert")
async def webhook_receiver(request: Request):
    data = await request.json()
    
    if data.get("event") != "messages.upsert":
        return {"status": "evento_ignorado", "reason": "nao_e_messages_upsert"}

    mensagem_data = data.get("data")
    if not mensagem_data:
        return {"status": "evento_ignorado", "reason": "sem_payload_de_dados"}

    if mensagem_data.get("key", {}).get("fromMe", False):
        return {"status": "ignorado", "reason": "mensagem_propria"}
        
    remetente_jid = mensagem_data.get("key", {}).get("remoteJid")
    if not remetente_jid:
        raise HTTPException(status_code=400, detail="Remetente desconhecido")
    
    if remetente_jid != TARGET_JID:
        print(f"   -> Mensagem de {remetente_jid} ignorada (não é o contato alvo).")
        return {"status": "ignorado", "reason": "nao_e_contato_alvo"}

    message_obj = mensagem_data.get("message", {})
    if "ephemeralMessage" in message_obj:
        message_obj = message_obj.get("ephemeralMessage", {}).get("message", {})

    nova_mensagem_texto = (
        message_obj.get("extendedTextMessage", {}).get("text") or
        message_obj.get("conversation", "")
    ).strip()

    if not nova_mensagem_texto:
        print(f"   -> Mensagem de {remetente_jid} ignorada (sem conteúdo de texto).")
        return {"status": "ignorado", "reason": "sem_texto"}

    print(f"\n--- Mensagem Recebida de {remetente_jid} ---")
    print(f"Mensagem: {nova_mensagem_texto}")

    if not redis_client:
        print("   🚨 Atenção: Cliente Redis não está disponível. A conversa não terá memória.")
        raise HTTPException(status_code=503, detail="Redis indisponível")
        
    try:
        history_key = f"history:{remetente_jid}"
        conversa_json = redis_client.get(history_key)
        historico_conversa = json.loads(conversa_json) if conversa_json else []
        print(f"   -> Histórico recuperado: {len(historico_conversa)} turnos.")
        
        historico_conversa.append({'role': 'user', 'parts': [{'text': nova_mensagem_texto}]})
        
        print("   -> Enviando para o Gemini...")
        chat = model.start_chat(history=historico_conversa)
        resposta_gemini = chat.send_message(nova_mensagem_texto)
        texto_resposta = resposta_gemini.text
        print(f"   -> Resposta do Gemini: {texto_resposta}")

        historico_conversa.append({'role': 'model', 'parts': [{'text': texto_resposta}]})
        
        redis_client.set(history_key, json.dumps(historico_conversa))
        print("   -> Histórico atualizado no Redis.")

        # --- LÓGICA DE DIGITAÇÃO E ESPERA ---
        # 1. Calcula um delay baseado no tamanho da resposta para parecer mais natural
        # Média de 60ms por caractere, com um mínimo de 2s e máximo de 8s.
        tempo_de_espera = min(max(len(texto_resposta) * 0.06, 2), 8)
        
        # 2. Envia o status "digitando..."
        await enviar_presenca(remetente_jid, "composing")
        
        # 3. Espera o tempo calculado
        await asyncio.sleep(tempo_de_espera)
        
        # 4. Envia o status "pausado" (opcional, mas bom para encerrar o "digitando")
        await enviar_presenca(remetente_jid, "paused")
        
        # 5. Envia a mensagem de fato
        await enviar_resposta_whatsapp(remetente_jid, texto_resposta)
        # ------------------------------------

    except Exception as e:
        print(f"   🚨 Erro no ciclo do chatbot: {e}")
        raise HTTPException(status_code=500, detail="Erro interno no processamento do chatbot")
    
    return {"status": "recebido_e_processado"}

