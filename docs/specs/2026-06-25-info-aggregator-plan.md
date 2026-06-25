# 原型实现计划

## 第1步：项目骨架 + 配置系统

- [ ] 初始化 Python 项目结构
- [ ] 配置系统：YAML 文件定义各源的限额、开关、base_url
- [ ] `cc-search` CLI 入口骨架（argparse，接收 query + --mode 参数）

验证：`cc-search "test" --mode budget` 能跑通，输出 "hello world"

## 第2步：插件化源接口 + SearXNG 适配器

- [ ] `SearchSource` 抽象基类
- [ ] `SourceRegistry` 注册机制
- [ ] SearXNG 适配器（连接本地 Docker，JSON API 搜索）
- [ ] CLI 输出 SearXNG 原始结果

验证：`cc-search "Rust"` 输出 SearXNG 搜索结果

## 第3步：Firecrawl 本地适配器

- [ ] Firecrawl 本地适配器（连接 Docker，调用 /v1/scrape）
- [ ] 对搜索结果中的前几条 URL 自动抓取全文
- [ ] 测试多源并行调用

验证：一次搜索同时返回 SearXNG + Firecrawl 结果

## 第4步：标准化 + 去重

- [ ] StandardResult 数据模型
- [ ] 各源的原始结果 → StandardResult 转换
- [ ] URL 去重（同一 URL 多个源返回 → 合并，保留最优摘要）

验证：去重后不再有重复 URL

## 第5步：模式控制 + 预算追踪

- [ ] 三档搜索模式（full / budget / manual）
- [ ] 基于配置的源启用/禁用
- [ ] 每次调用的 credit 消耗记录
- [ ] CLI `--mode` 参数生效

验证：budget 模式只跑免费源，full 模式所有源并行

## 第6步：分层聚合输出

- [ ] 按设计的三层结构输出结果
- [ ] Layer 3: 按源分组的原始结果
- [ ] Layer 2: 标注不同源对同一内容的覆盖差异
- [ ] Layer 1: （原型暂不做 AI 合成）占位，标注 "需要本地 LLM"
- [ ] 来源权威度基础分级（域名白名单）

验证：输出清晰的多层、多源对比视图

## 第7步：Tavily + Exa 适配器

- [ ] Tavily 适配器（API key 从环境变量读取）
- [ ] Exa 适配器
- [ ] 纳入预算追踪

验证：full 模式 5 个源全跑通

## 第8步：Firecrawl Cloud 适配器 + 收尾

- [ ] Firecrawl Cloud 适配器
- [ ] 使用量统计报告（`cc-search --stats` 查看各源消耗）
- [ ] 整体测试、调优输出格式

验证：端到端可用，输出可读
