# Append-only relationship history with rebuildable projections

E.R.I.I. 将已接受的 Relationship Event 作为关系事实来源，并从事件顺序重建 Current Belief 与 Relationship State，而不是原地改写一行“当前关系”。这会增加读取时的投影成本，但保留了证据、纠正过程和可迁移历史，也使删除、规则升级和状态算法变化能够通过重建得到可审计结果；投影缓存未来可以增加，但不得成为唯一事实来源。
