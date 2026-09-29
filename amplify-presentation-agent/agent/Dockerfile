# Amplify presentation agent - Amazon Bedrock AgentCore Runtime container.
# AgentCore Runtime requires linux/arm64 images.
FROM public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app

# LibreOffice + poppler render slides for visual review; fonts cover the
# bundled Office themes (metric-compatible substitutes) plus any template
# fonts dropped into ./fonts.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress \
        poppler-utils \
        fontconfig \
        fonts-liberation2 \
        fonts-crosextra-carlito \
        fonts-crosextra-caladea \
        fonts-dejavu-core \
        fonts-noto-core \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY fonts/ /usr/share/fonts/truetype/amplify/
RUN fc-cache -f

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY presentation_agent/ ./presentation_agent/
COPY app.py .

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping')"

CMD ["opentelemetry-instrument", "python", "app.py"]
