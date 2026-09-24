#!/bin/bash
# ============================================================
# CropGuard 一键部署脚本 (Linux 服务器)
# 102类农业害虫检测系统
# 用法: bash deploy.sh [cpu|gpu]
# ============================================================
set -e

MODE="${1:-cpu}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================================"
echo "  CropGuard - 农作物害虫智能检测系统 部署脚本"
echo "  模式: $MODE"
echo "============================================================"

# ---- 检查系统依赖 ----
echo ""
echo "[1/4] 检查系统依赖..."
if ! command -v python3 &>/dev/null; then
    echo "[INFO] 安装 Python3..."
    sudo apt-get update && sudo apt-get install -y python3 python3-pip python3-venv
fi
echo "[OK] Python3: $(python3 --version)"

# ---- 创建虚拟环境 ----
echo ""
echo "[2/4] 创建虚拟环境..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip -q

# ---- 安装依赖 ----
echo ""
echo "[3/4] 安装 Python 依赖..."
if [ "$MODE" = "gpu" ]; then
    pip install -r requirements-server.txt -q
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118
    # 修改配置使用GPU
    sed -i "s/DEVICE: 'cpu'/DEVICE: '0'/g" config/configs.yaml
    echo "[OK] GPU模式 (CUDA 11.8)"
else
    pip install -r requirements-server.txt -q
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    echo "[OK] CPU模式"
fi

# ---- 检查权重文件 ----
echo ""
if [ ! -f "weights/yolov8s/weights/best.pt" ]; then
    echo "[WARN] 未找到训练权重 weights/yolov8s/weights/best.pt"
    echo "      将使用默认 yolov8s.pt"
fi

# ---- 启动服务 ----
echo ""
echo "[4/4] 启动服务..."
echo ""
echo "============================================================"
echo "  部署完成！启动命令:"
echo ""
echo "  # 前台运行:"
echo "  source .venv/bin/activate"
echo "  python api_server.py --host 0.0.0.0 --port 8080"
echo ""
echo "  # 后台运行:"
echo "  nohup python api_server.py --host 0.0.0.0 --port 8080 > server.log 2>&1 &"
echo ""
echo "  # 使用 systemd (生产环境):"
echo "  sudo cp cropguard.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now cropguard"
echo ""
echo "  Web演示: http://<服务器IP>:8080/"
echo "  API文档: http://<服务器IP>:8080/docs"
echo "============================================================"
