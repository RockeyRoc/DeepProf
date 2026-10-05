# NoMIRACL 中文固定候选评测复现

本实验只使用已下载的固定数据快照和本地权重，不调用外部生成模型。覆盖开发1,475题、测试2,295题、31,826个不同文档、37,599个可用候选配对；保留95条缺失qrel清单（35条正例），不排除题目。

`scripts/run_nomiracl_zh_reranker.py` 必须显式提供数据清单、模型清单、独立推理环境、结果目录和断点目录。新运行不带 `--resume`；已有同配置运行续跑时带 `--resume`。配置不一致会拒绝复用。输入、模型及代码指纹冻结后不修改；完成格按批次跳过，损坏的内容缓存重算。

```powershell
python scripts/run_nomiracl_zh_reranker.py `
  --dataset-manifest $DatasetManifest `
  --model-manifest $ModelManifest `
  --output-dir $ResultDirectory `
  --checkpoint-dir $CheckpointDirectory `
  --inference-python $InferencePython `
  --device cuda --embedding-batch-size 2 --reranker-batch-size 4 --resume
```

推理为FP32、关闭TF32；文档和配对截断512 tokens，查询256 tokens。每256项原子保存，实际编码批大小2、重排4。CUDA设备错误发生时，保留诊断，并用相同权重的CPU FP32、16线程从头重做该阶段；不同设备缓存不混用。

四方法使用相同固定候选：BGE-M3 dense、BM25（k1=1.2，b=0.75）、两路RRF-60、BGE reranker v2-m3。并列分数按docid升序排列。nDCG@5仅对相关题求均值；证据门限只用开发集最大重排分数选择，满足非相关题FAR≤5%，最大化相关题接受率，并列选较低阈值，接受规则为分数≥阈值。测试标签不参与选择。

完成后运行 `scripts/validate_nomiracl_full.py --run $CheckpointDirectory --output $ValidationFile`，独立核验3,770条逐题记录和150,396条排名记录、全部分数、排序、nDCG、阈值、FAR／FRR分母及AUC。`scripts/verify_nomiracl_resume.py` 另验证完整文档阶段续跑不重新加载模型、不重复前向计算。

结果CSV和摘要在 `data/`；来源分数、门限冻结记录、配置、缓存、日志和进程清理证明由交付清单逐项追踪。R源码 `figures/make_nomiracl_figures.R` 从完成结果导出排序图和门限图。该评测不是全库Recall，也不评估生成答案正确率。
