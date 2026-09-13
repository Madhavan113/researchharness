# Same task base; adds the pinned Terminus-2 runtime before network isolation.
FROM python@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
WORKDIR /app
ENV LITELLM_LOCAL_MODEL_COST_MAP=True TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache TERM=xterm-256color
RUN apt-get update \
    && apt-get install -y --no-install-recommends tmux=3.3a-3 asciinema=2.2.0-1 \
    && dpkg-query -W \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /opt/terminus-requirements.txt
RUN python -m pip install --no-cache-dir -r /opt/terminus-requirements.txt \
    && python -m pip freeze \
    && python -c "import tiktoken; tiktoken.get_encoding('o200k_base'); tiktoken.get_encoding('cl100k_base')" \
    && touch /app/run.py
