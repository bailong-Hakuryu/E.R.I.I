# Use a versioned, bounded relationship policy per Character Blueprint

E.R.I.I. 以全局安全映射和绝对限幅为基础，并允许 Character Blueprint 选择经过确认、版本化且有界的 Relationship Policy，使慢热、重承诺或善于修复等人设差异能够影响相同信号的状态结果。每段关系记录策略版本以保证事件重放一致；LLM 不能在运行时修改策略，策略升级必须显式重建并比较投影，而不能静默改变既有关系。
