# Thinking ON/OFF 对比报告（已作废，等待重跑）

旧对比把“响应成功解析”当作“评估准确”，且 fixture、评分和汇总不可独立复核，
因此其中的数值结论和生产建议全部撤回。历史明文凭证也已从版本库内容移除。

新的 `comprehensive_test.py` 对每个 `expected_assessment` 轴保存
`expected / actual / matched`，并将解析率、期望轴匹配率、token 和延迟分别报告。
在新的盲测数据和真实 provider 运行完成前，本文件不展示质量对比数字。
