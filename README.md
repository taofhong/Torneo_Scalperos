🏆 Torneo Scalperos

Plataforma para organizar y monitorear en tiempo real un torneo de trading usando NinjaTrader 8 y un backend en FastAPI + WebSockets, con frontend responsive en HTML/CSS/JS.

✨ Características principales

Backend (FastAPI + SQLite + SQLAlchemy)

Endpoints /events/trade, /events/heartbeat para recibir trades y estado de cuentas.

WebSocket /ws/leaderboard para leaderboard en tiempo real.

Sistema de inscripción con credenciales (API_KEY, SECRET) y validación HMAC opcional.

Reset automático cada semana según reglas del torneo.

Reglas de eliminación:

StopLoss > -2000 USD → eliminado.

Más de 10 contratos micros → eliminado.

Frontend (HTML + CSS + JS)

Hero con logo animado.

Timer hasta inicio del torneo.

Inscripción con modal y envío automático al backend.

Leaderboard responsive tipo grid/barras.

Terminal de logs con frases de trading motivacionales.

AddOn NinjaTrader 8 (C#)

Ventana Torneo Reporter en el menú de Ninja.

Configuración de cuenta, URL del backend y credenciales.

Reporta automáticamente trades y heartbeats al backend.

Email

Al registrarse, cada participante recibe sus credenciales por correo.

El administrador recibe notificación de nuevas inscripciones.

⚙️ Instalación local
1. Clonar repositorio
git clone https://github.com/taofhong/Torneo_Scalperos.git
cd Torneo_Scalperos

2. Crear entorno virtual
python -m venv .venv
.venv\Scripts\activate

3. Instalar dependencias
pip install -r requirements.txt

4. Iniciar servidor local
uvicorn app.main:app --reload --port 8000


Abrir en navegador:
👉 http://127.0.0.1:8000/web/client_v2.html

🚀 Despliegue en Google Cloud Run

Instalar Google Cloud CLI.

Autenticar:

gcloud init


Construir y subir contenedor:

gcloud builds submit --tag gcr.io/torneo-scalperos/torneo-scalperos


Desplegar en Cloud Run:

gcloud run deploy torneo-scalperos \
  --image gcr.io/torneo-scalperos/torneo-scalperos \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080


URL de servicio:
https://torneo-scalperos-<PROJECT_ID>.us-central1.run.app

🖥️ AddOn NinjaTrader 8

Abre NinjaTrader 8 → New → NinjaScript Editor.

En el árbol, botón derecho sobre AddOns → New AddOn.

Pega el contenido de nt8/TorneoReporter.cs.

Haz clic en Compile (martillo).

En el menú de Ninja → Herramientas → Torneo Reporter.

Configura:

Cuenta (ej: Sim101).

Backend URL: https://torneo-scalperos-<PROJECT_ID>.us-central1.run.app/events/trade

API Key y Secret (recibidas por correo).

📊 Reglas del Torneo

Torneo cada viernes de 9:00 AM a 1:00 PM (hora Colombia).

Se reinician participantes y leaderboard 5 min antes de iniciar.

24h después del cierre (sábado 1:00 PM) se limpian credenciales.


Vista desde Run Google https://chatgpt.com/g/g-p-68bf44eb2f4c8191b7030e65d8dba108/c/68d66f82-fcec-832f-8be0-4e6d267f2d26

🛡️ Seguridad

Comunicación autenticada mediante HMAC-SHA256 (opcional).

Variables sensibles gestionadas vía .env (nunca público en GitHub).
