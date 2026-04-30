#!/bin/bash
# ============================================================
# XeEdio Video Production Platform — Ubuntu 部署脚本
# 域名: xneoovideo.com (通过 Cloudflare Tunnel)
#
# 用法: ssh root@113.250.190.85 'bash -s' < deploy.sh
# 或者: scp deploy.sh root@113.250.190.85:~ && ssh root@113.250.190.85 'bash ~/deploy.sh'
# ============================================================

set -e

echo "=========================================="
echo "XeEdio 部署开始 (xneoovideo.com)"
echo "=========================================="

# --- 1. 系统依赖 ---
echo "[1/8] 安装系统依赖..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    ffmpeg \
    git \
    nginx \
    curl wget \
    build-essential \
    libsndfile1 \
    > /dev/null 2>&1

echo "  ✓ 系统依赖安装完成"

# --- 2. 创建应用用户和目录 ---
echo "[2/8] 创建应用目录..."
APP_DIR="/opt/xeedio"
mkdir -p $APP_DIR
mkdir -p $APP_DIR/storage/assets
mkdir -p $APP_DIR/storage/tasks
mkdir -p $APP_DIR/logs

echo "  ✓ 目录创建完成: $APP_DIR"

# --- 3. Python 虚拟环境 ---
echo "[3/8] 创建 Python 虚拟环境..."
python3 -m venv $APP_DIR/venv
source $APP_DIR/venv/bin/activate
pip install --upgrade pip -q

echo "  ✓ 虚拟环境创建完成"

# --- 4. 安装 Python 依赖 ---
echo "[4/8] 安装 Python 依赖..."
# 如果有 requirements.txt 就用它，否则手动安装核心依赖
if [ -f "$APP_DIR/app/requirements.txt" ]; then
    pip install -r $APP_DIR/app/requirements.txt -q
else
    pip install -q \
        fastapi \
        uvicorn[standard] \
        sqlalchemy \
        httpx \
        pydantic \
        python-multipart \
        jinja2 \
        pyyaml \
        edge-tts \
        faster-whisper \
        aiofiles
fi

echo "  ✓ Python 依赖安装完成"

# --- 5. 生产环境配置 ---
echo "[5/8] 生成生产环境配置..."
cat > $APP_DIR/config.prod.yaml << 'YAML'
app:
  env: prod
  debug: false
  log_level: WARNING

llm:
  providers:
    deepseek:
      name: "DeepSeek"
      api_url: "https://api.deepseek.com/v1/chat/completions"
      api_key: ""
      model: "deepseek-chat"
      key_hint: "从 platform.deepseek.com 获取"

    doubao:
      name: "doubao-pro"
      api_url: "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
      api_key: ""
      model: "doubao-pro-32k"
      key_hint: "从火山引擎控制台获取"

    gpt-4o-mini:
      name: "gpt-4o-mini"
      api_url: "https://api.luxee.ai/v1/chat/completions"
      api_key: "sk-8cee9cb9147b553008798bd2ace636dc8e50363d8f676a742a0535034bdcaece"
      model: "gpt-5.4"
      key_hint: "从 platform.openai.com 获取"

  default_provider: "gpt-4o-mini"

vlm:
  api_url: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
  api_key: "sk-92ae3f703c814454a5537ab42c860f3d"
  model: "qwen3.6-plus"
  frame_interval: 2
  max_frames: 30

ai_tts:
  provider: "dashscope"
  api_key: "sk-92ae3f703c814454a5537ab42c860f3d"
  model: "cosyvoice-v2"
  voice: "longyingtian"
  fallback_to_edge_tts: true

tts:
  voices: "zh-CN-XiaoxiaoNeural,zh-CN-YunxiNeural"
  speed: "+0%"
  volume: "+0%"

video:
  resolution: "1080x1920"
  bitrate: "8M"
  format: "mp4"

upload:
  max_size_mb: 500
  allowed_formats: "mp4,mov,avi,jpg,png,webp,mp3,wav,aac"

batch:
  max_concurrency: 3

embedding:
  api_url: ""
  api_key: ""
  model: "text-embedding-3-small"

pexels:
  api_key: "P6KSrRWsl3RgerXTOpfkJE3LuwTyQBJt13xvXvWShyWJRjn8H3MOfoWB"
YAML

echo "  ✓ 配置文件生成完成"

# --- 6. Systemd 服务 ---
echo "[6/8] 配置 systemd 服务..."
cat > /etc/systemd/system/xeedio.service << 'SERVICE'
[Unit]
Description=XeEdio Video Production Platform
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/webapp/XeEdio/video-production-platform
Environment=APP_ENV=prod
ExecStart=/webapp/XeEdio/video-production-platform/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
echo "  ✓ systemd 服务配置完成"

# --- 7. Nginx 反向代理 ---
echo "[7/8] 配置 Nginx..."
cat > /etc/nginx/sites-available/xeedio << 'NGINX'
server {
    listen 80;
    server_name xneoovideo.com www.xneoovideo.com;

    client_max_body_size 500M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 支持
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }

    location /static/ {
        alias /opt/xeedio/app/static/;
        expires 7d;
    }

    location /storage/ {
        alias /opt/xeedio/storage/;
        expires 1d;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/xeedio /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "  ✓ Nginx 配置完成"

# --- 8. 安装 Cloudflare Tunnel (cloudflared) ---
echo "[8/8] 安装 cloudflared..."
if ! command -v cloudflared &> /dev/null; then
    curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb
    dpkg -i /tmp/cloudflared.deb
    rm -f /tmp/cloudflared.deb
    echo "  ✓ cloudflared 安装完成"
else
    echo "  ✓ cloudflared 已存在，跳过安装"
fi

echo ""
echo "=========================================="
echo "部署完成！后续步骤："
echo "=========================================="
echo ""
echo "1. 上传代码到服务器:"
echo "   rsync -avz --exclude='storage' --exclude='__pycache__' \\"
echo "     ./video-production-platform/ root@113.250.190.85:/opt/xeedio/"
echo ""
echo "2. 安装 Python 依赖:"
echo "   ssh root@113.250.190.85 'source /opt/xeedio/venv/bin/activate && pip install -r /opt/xeedio/requirements.txt'"
echo ""
echo "3. 启动应用服务:"
echo "   systemctl daemon-reload"
echo "   systemctl start xeedio"
echo "   systemctl enable xeedio"
echo ""
echo "4. 连接 Cloudflare Tunnel（用你在 CF 面板拿到的 token）:"
echo "   cloudflared service install <YOUR_TUNNEL_TOKEN>"
echo "   systemctl start cloudflared"
echo ""
echo "5. 在 Cloudflare Tunnel 路由配置:"
echo "   - Domain: xneoovideo.com"
echo "   - Service URL: http://localhost:8000"
echo "   - Path: 留空"
echo ""
echo "6. 查看日志:"
echo "   journalctl -u xeedio -f        # 应用日志"
echo "   journalctl -u cloudflared -f   # 隧道日志"
echo ""
echo "7. 访问: https://xneoovideo.com"
echo "=========================================="
