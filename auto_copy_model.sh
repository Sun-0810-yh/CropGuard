#!/bin/bash
SOURCE="D:/Dev Projects/Pycharm/yolov8/runs/train/pest102_run1/weights/best.pt"
TARGET="D:/Dev Projects/Pycharm/yolov8/weights/yolov8s/weights/best.pt"
BACKUP="D:/Dev Projects/Pycharm/yolov8/weights/yolov8s/weights/best_backup.pt"
RESULTS="D:/Dev Projects/Pycharm/yolov8/runs/train/pest102_run1/results.csv"

echo "[$(date '+%H:%M:%S')] 🔍 开始监控训练..."

while true; do
    if [ -f "$RESULTS" ]; then
        LAST_EPOCH=$(tail -1 "$RESULTS" 2>/dev/null | cut -d',' -f1 | tr -d ' ')
        SOURCE_TIME=$(stat -c %Y "$SOURCE" 2>/dev/null)
        CURRENT_TIME=$(date +%s)
        AGE=$(( CURRENT_TIME - SOURCE_TIME ))

        echo "[$(date '+%H:%M:%S')] 当前: epoch $LAST_EPOCH | best.pt ${AGE}秒前更新"

        # 训练完成条件: 到了100轮 且 best.pt 超过5分钟没更新
        if [ "$LAST_EPOCH" -ge 100 ] && [ "$AGE" -gt 300 ]; then
            echo "[$(date '+%H:%M:%S')] 🏁 训练已完成! epoch=$LAST_EPOCH"
            if [ -f "$SOURCE" ]; then
                [ -f "$TARGET" ] && cp "$TARGET" "$BACKUP" && echo "[$(date '+%H:%M:%S')] 📦 已备份旧模型"
                cp "$SOURCE" "$TARGET"
                echo "[$(date '+%H:%M:%S')] 🎉 新模型已更新→ $TARGET"
                ls -lh "$TARGET"
            fi
            break
        fi
    fi
    sleep 120
done
