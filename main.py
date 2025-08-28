from fastapi import FastAPI, Request, HTTPException
import json
import os
from datetime import datetime

# Cria a instância do FastAPI com documentação
app = FastAPI(
    title="Servidor de Webhook para WhatsApp",
    description="Recebe e exibe notificações da Evolution API em tempo real.",
    version="1.0.0"
)

def formatar_evento_webhook(evento: dict):
    """
    Formata o JSON do webhook para uma exibição mais amigável no log.
    Tenta extrair as informações mais relevantes.
    """
    try:
        # Pega o tipo de evento
        tipo_evento = evento.get("event")
        instancia = evento.get("instance")
        data_hora = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

        print(f"--- [ {data_hora} ] ---")
        print(f"Instância: {instancia} | Evento: {tipo_evento}")

        # Se for uma nova mensagem (MESSAGES_UPSERT)
        if tipo_evento == "MESSAGES_UPSERT" and "data" in evento:
            mensagem_data = evento["data"]
            remetente = mensagem_data.get("key", {}).get("remoteJid", "N/A")
            
            # Tenta encontrar o conteúdo da mensagem
            conteudo = (
                mensagem_data.get("message", {}).get("extendedTextMessage", {}).get("text") or
                mensagem_data.get("message", {}).get("conversation", "")
            )
            
            print(f"De: {remetente}")
            print(f"Mensagem: {conteudo}")
        
        # Se for uma atualização de status (MESSAGES_UPDATE)
        elif tipo_evento == "MESSAGES_UPDATE" and "data" in evento:
            status_data = evento["data"][0] # Geralmente vem em uma lista
            status = status_data.get("status")
            msg_id = status_data.get("key", {}).get("id")
            print(f"Status da Mensagem ID {msg_id} atualizado para: {status}")

        # Para outros eventos, apenas imprime o JSON completo
        else:
            print(json.dumps(evento, indent=2))
            
        print("-------------------------------------------\n")

    except Exception as e:
        print(f"🚨 Erro ao formatar o webhook: {e}")
        # Em caso de erro, imprime o dado bruto
        print(json.dumps(evento, indent=2))


@app.post(
    "/", 
    summary="Receptor de Webhooks",
    description="Recebe notificações POST da Evolution API e as exibe no log."
)
async def webhook_receiver(request: Request):
    """
    Recebe um webhook, formata e imprime seu conteúdo, e retorna uma confirmação.
    """
    try:
        data = await request.json()
        formatar_evento_webhook(data)
        return {"status": "sucesso", "message": "Webhook recebido corretamente."}
    except json.JSONDecodeError:
        print("🚨 Erro: Não foi possível decodificar o corpo da requisição como JSON.")
        raise HTTPException(status_code=400, detail="Corpo da requisição inválido.")

@app.get(
    "/health",
    summary="Verificação de Saúde",
    description="Endpoint para verificar se o servidor está online."
)
def health_check():
    return {"status": "ok"}

# O Railway usará o Procfile para iniciar, mas este bloco é útil para testes locais
if __name__ == '__main__':
    import uvicorn
    port = int(os.environ.get('PORT', 5000))
    uvicorn.run(app, host='0.0.0.0', port=port)
