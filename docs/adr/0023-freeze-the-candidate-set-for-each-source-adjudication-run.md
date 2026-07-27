# Freeze the candidate set for each source adjudication run

每个普通 Source Adjudication Run 在首次提交时固定完整候选批次指纹，并将同一批次指纹写入每项 Decision Receipt；后续技术重试只有在来源身份、来源版本、处理身份与整批候选均相同时才能继续或返回既有结果，不能通过更换 `candidate_key` 新增记忆。候选仍逐项原子提交，因此中途失败后可以继续尚未落盘的原批次；模型升级或重新采样需要宿主创建带独立 `reprocessing_id` 的显式 Historical Reprocessing，并以追加方式留下新裁决。
