#!/bin/bash
# ============================================================
# XeEdio Video Production Platform — Ubuntu 部署脚本
# 用法: ssh root@113.250.190.85 'bash -s' < deploy.sh
# 或者: scp deploy.sh root@113.250.190.85:~ && ssh root@113.250.190.85 'bash ~/deploy.sh'
# ============================================================

set -e

echo "=========================================="
echo "XeEdio 部署开始"
echo "=========================================="

# --- 1. 系统依赖 ---
echo "[1/7] 安装系统依赖..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    ffmpeg \
    git \
    nginx \
    certbot python3-certbot-nginx \
    curl wget \
    build-essential \
    libsndfile1 \
    > /dev/null 2>&1

echo "  ✓ 系统依赖安装完成"

# --- 2. 创建应用用户和目录 ---
echo "[2/7] 创建应用目录..."
APP_DIR="/opt/xeedio"
mkdir -p $APP_DIR
mkdir -p $APP_DIR/storage/assets
mkdir -p $APP_DIR/storage/tasks
mkdir -p $APP_DIR/logs

echo "  ✓ 目录创建完成: $APP_DIR"

# --- 3. Python 虚拟环境 ---
echo "[3/7] 创建 Python 虚拟环境..."
python3 -m venv $APP_DIR/venv
source $APP_DIR/venv/bin/activate
pip install --upgrade pip -q

echo "  ✓ 虚拟环境创建完成"

# --- 4. 安装 Python 依赖 ---
echo "[4/7] 安装 Python 依赖..."
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
echo "[5/7] 生成生产环境配置..."
cat > $APP_DIR/config.prod.yaml << 'YAML'
app:
  env: prod
  debug: false
  log_level: WARNING

llm:
  providers:
    gpt-4o-mini:
      name: "gpt-4o-mini"
      api_url: "https://api.luxee.ai/v1/chat/completions"
      api_key: "${LLM_API_KEY}"
      model: "gpt-5.4"
  default_provider: "gpt-4o-mini"

vlm:
  api_url: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
  api_key: "${VLM_API_KEY}"
  model: "qwen3.6-plus"
  frame_interval: 2
  max_frames: 30

ai_tts:
  provider: "dashscope"
  api_key: "${AI_TTS_API_KEY}"
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
  api_key: "${PEXELS_API_KEY}"
YAML

echo "  ✓ 配置文件生成完成"

# --- 6. Systemd 服务 ---
echo "[6/7] 配置 systemd 服务..."
cat > /etc/systemd/system/xeedio.service << 'SERVICE'
[Unit]
Description=XeEdio Video Production Platform
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/xeedio
Environment=APP_ENV=prod
Environment=LLM_API_KEY=your-llm-api-key-here
Environment=VLM_API_KEY=your-vlm-api-key-here
Environment=AI_TTS_API_KEY=your-tts-api-key-here
Environment=PEXELS_API_KEY=your-pexels-api-key-here
ExecStart=/opt/xeedio/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
echo "  ✓ systemd 服务配置完成"

# --- 7. Nginx 反向代理（8080 端口直连） ---
echo "[7/7] 配置 Nginx..."
cat > /etc/nginx/sites-available/xeedio << 'NGINX'
server {
    listen 8080;
    server_name _;

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

echo ""
echo "=========================================="
echo "部署完成！后续步骤："
echo "=========================================="
echo ""
echo "1. 上传代码到服务器:"
echo "   rsync -avz --exclude='storage' --exclude='__pycache__' \\"
echo "     ./video-production-platform/ root@113.250.190.85:/opt/xeedio/"
echo ""
echo "2. 编辑 API Key（必须）:"
echo "   vim /etc/systemd/system/xeedio.service"
echo "   # 修改 Environment=LLM_API_KEY=... 等行"
echo ""
echo "3. 启动服务:"
echo "   systemctl start xeedio"
echo "   systemctl enable xeedio"
echo ""
echo "4. 查看日志:"
echo "   journalctl -u xeedio -f"
echo ""
echo "5. 访问: http://113.250.190.85:8080"
echo ""
echo "注意: 确保服务器防火墙/安全组已放行 8080 端口"
echo "=========================================="
