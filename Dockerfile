# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=America/Sao_Paulo

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libgomp1 \
    tzdata

COPY requirements.txt /app/requirements.txt

ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.6.0
ARG TORCHVISION_VERSION=0.21.0

RUN --mount=type=cache,id=analitico-pip-cache,target=/root/.cache/pip,sharing=locked \
    pip install --upgrade pip && \
    pip install \
        torch==${TORCH_VERSION} \
        torchvision==${TORCHVISION_VERSION} \
        --index-url ${PYTORCH_INDEX_URL} && \
    pip install -r /app/requirements.txt && \
    python -c "import torch, torchvision; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'torchvision', torchvision.__version__)"

COPY . /app

EXPOSE 8000

CMD ["python", "main.py"]
