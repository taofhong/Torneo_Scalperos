# Imagen base con Python 3.11 
FROM python:3.11-slim 
 
# Evitar .pyc y forzar logs inmediatos 
ENV PYTHONDONTWRITEBYTECODE=1 \ 
    PYTHONUNBUFFERED=1 
 
# Directorio de trabajo 
WORKDIR /app 
 
# Copia requirements primero (para aprovechar cache de Docker) 
COPY requirements.txt . 
 
# Instala dependencias 
RUN pip install --no-cache-dir -r requirements.txt 
 
# Copia todo el c�digo de la app 
COPY . . 
 
# Puerto que expone la app (Cloud Run, Docker, etc.) 
ENV PORT=8080 
 
# Comando de inicio (FastAPI con uvicorn) 
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"] 
