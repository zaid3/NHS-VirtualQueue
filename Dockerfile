FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock pyproject.toml README.md LICENSE NOTICE COMMERCIAL.md MANIFEST.in ./
COPY LICENSES ./LICENSES
RUN pip install --no-cache-dir -r requirements.lock
COPY src ./src
RUN pip install --no-deps . && useradd --uid 10001 --create-home vq
COPY alembic.ini ./
COPY migrations ./migrations
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import os,urllib.request,urllib.parse; host=urllib.parse.urlparse(os.environ['PUBLIC_URL']).netloc; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/health/ready',headers={'Host':host}),timeout=3)"
CMD ["uvicorn", "virtualqueue.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
