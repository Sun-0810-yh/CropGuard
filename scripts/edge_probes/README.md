# EdgeJev 校准探针（复现用）

这些脚本是 2026-10-16 给 `app/edge_engine.py` 定阈值、定问法时的原始实验，
结论已汇总到 [edge_models/README.md](../../edge_models/README.md)。想改动
`COMMAND_QUESTIONS` 问法或 `RESCUE_*` 闸门时，**先跑这里再改**，别拍脑袋。

前置：`edge_models/jev-int8` 已构建（`python scripts/build_edge_model.py`）。

```bash
python scripts/edge_probes/01_question_phrasing.py   # 中/英问法 × 13 条指令：intent/group/unsafe 对照
python scripts/edge_probes/02_combined_pipeline.py   # 规则优先 + 兜底闸门 的最终口径逐条表
python scripts/edge_probes/03_model_variants.py      # int8/fp32/mixed/kev 四变体横向对比
python scripts/edge_probes/04_stop_thresholds.py     # 停止类概率分布（校准 RESCUE_STOP_MIN）
```

说明：
- 探针直接 `from edgejev import Agent`（用仓库自带的包），在 `.venv` 或任何装有
  onnxruntime 的 Python 下运行；
- `03` 里 kev 变体不存在时自动跳过（该变体未部署，仅作过对照）；
- 脚本里的用例集是"记录在案"的校准集，改动前后跑一遍对比即可看出回归。
