# 真实 API 测试报告（历史结果撤回）

此前文件包含凭证片段和不可审计的硬编码汇总，现已清除。旧运行没有保存足够的
逐轴 ground truth 对比，不能重建准确率，也不能支持生产推荐。

请用环境变量提供临时 Key，并运行：

```bash
cd experiments/deepseek-continuity-review
python -m evaluation.comprehensive_test --thinking both --output result.json
```

生成的 JSON 是后续评审的原始输入；它本身仍不是生产结论。凭证、原始 reasoning、
完整 prompt 和用户私密数据不应写入报告或提交到版本库。
