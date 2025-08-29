import os
import json
import requests
import redis
import google.generativeai as genai
from fastapi import FastAPI, Request
from dotenv import load_dotenv

# --- Carregando as Configurações do .env ---
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME") # <-- Carrega o nome do modelo
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
# Cliente do Gemini, agora com o modelo e a persona carregados do .env
try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL_NAME, # <-- Usa a variável aqui
        system_instruction=SYSTEM_PROMPT
    )
    print(f"✅ Modelo Gemini '{GEMINI_MODEL_NAME}' configurado com a persona.")
except Exception as e:
    print(f"🚨 ERRO CRÍTICO ao configurar o modelo Gemini: {e}")
    exit()

# Cliente do Redis
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    print("✅ Conectado ao Redis com sucesso!")
except Exception as e:
    print(f"🚨 ERRO CRÍTICO ao conectar com o Redis: {e}")
    redis_client = None

# --- Função para Enviar Respostas via Evolution API ---
def enviar_resposta_whatsapp(remetente_jid: str, texto_resposta: str):
    """Envia a resposta gerada de volta para o usuário."""
    url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE_NAME}"
    headers = {"Content-Type": "application/json", "apikey": EVOLUTION_API_KEY}
    payload = {"number": remetente_jid, "text": texto_resposta}
    
    print(f"   -> Enviando resposta para {remetente_jid}...")
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
        response.raise_for_status()
        print("   -> Resposta enviada com sucesso!")
    except requests.exceptions.RequestException as e:
        print(f"   🚨 Erro ao enviar resposta via Evolution API: {e}")

# --- Aplicação FastAPI ---
app = FastAPI(title="Chatbot WhatsApp com Gemini e Redis")

@app.post("/")
async def webhook_receiver(request: Request):
    data = await request.json()
    
    if data.get("event") == "MESSAGES_UPSERT" and "data" in data:
        mensagem_data = data["data"]
        
        if not mensagem_data.get("key", {}).get("fromMe", False):
            remetente_jid = mensagem_data.get("key", {}).get("remoteJid")
            
            if remetente_jid != TARGET_JID:
                print(f"   -> Mensagem de {remetente_jid} ignorada (não é o contato alvo).")
                return {"status": "ignorado"}

            nova_mensagem_texto = (
                mensagem_data.get("message", {}).get("extendedTextMessage", {}).get("text") or
                mensagem_data.get("message", {}).get("conversation", "")
            )

            print(f"\n--- Mensagem Recebida de {remetente_jid} ---")
            print(f"Mensagem: {nova_mensagem_texto}")

            if redis_client:
                history_key = f"history:{remetente_jid}"
                
                try:
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

                    enviar_resposta_whatsapp(remetente_jid, texto_resposta)

                except Exception as e:
                    print(f"   🚨 Erro no ciclo do chatbot: {e}")
    
    return {"status": "recebido"}

@app.get("/health")
def health_check():
    return {"status": "ok"}

