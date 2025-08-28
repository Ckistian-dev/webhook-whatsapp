from fastapi import FastAPI, Request, HTTPException
import json
import os

# Cria a instância do FastAPI
app = FastAPI(
    title="Servidor de Webhook para WhatsApp",
    description="Recebe e exibe notificações da Evolution API em tempo real.",
    version="1.0.0"
)

# Este é o nosso endpoint que vai receber as notificações via POST
# Usamos 'summary' e 'description' para uma auto-documentação elegante
@app.post(
    "/", 
    summary="Receptor de Webhooks",
    description="Recebe notificações POST da Evolution API e as exibe no log."
)
async def webhook_receiver(request: Request):
    """
    Recebe um webhook, imprime seu conteúdo e retorna uma confirmação.
    """
    print("-------------------------------------------")
    print("🎉 Webhook Recebido! 🎉")
    
    try:
        # Pega os dados JSON que a Evolution API enviou
        data = await request.json()
        
        # Imprime os dados formatados para fácil leitura
        print(json.dumps(data, indent=2))
        
        # Responde para a Evolution API que recebemos com sucesso
        return {"status": "sucesso", "message": "Webhook recebido corretamente."}

    except json.JSONDecodeError:
        print("🚨 Erro: Não foi possível decodificar o corpo da requisição como JSON.")
        raise HTTPException(status_code=400, detail="Corpo da requisição inválido. Esperava-se um JSON.")

# Rota de health check para saber se o servidor está no ar
@app.get(
    "/health",
    summary="Verificação de Saúde",
    description="Um endpoint simples para verificar se o servidor está online."
)
def health_check():
    return {"status": "ok"}

# O código abaixo é útil para testes locais, mas o Railway usará o Procfile
if __name__ == '__main__':
    import uvicorn
    port = int(os.environ.get('PORT', 5000))
    uvicorn.run(app, host='0.0.0.0', port=port)