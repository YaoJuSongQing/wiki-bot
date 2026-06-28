# LLM Wiki Q&A Bot

对 https://llm.datasette.io/ 文档的问答机器人，提供 REST API 接口。

## 项目结构

```
~/wiki-bot/
├── scrape_wiki.py     # 抓取 wiki（需要时运行）
├── knowledge_base.py  # 知识库：加载、分块、TF-IDF 索引
├── server.py          # FastAPI 服务（RAG + LLM 回答）
├── data/
│   ├── llm_wiki_data.json  # 抓取的原始数据（27 页）
│   └── index/              # 序列化的 TF-IDF 索引
└── requirements.txt
```

## API 接口

### GET /health
服务健康检查
```bash
curl http://localhost:8080/health
```

### GET /pages
列出所有已索引的页面
```bash
curl http://localhost:8080/pages
```

### POST /search
纯检索，不调用 LLM（不需要 API key）
```bash
curl -X POST http://localhost:8080/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "how to install plugins"}'
```

### POST /ask
检索 + LLM 生成答案
```bash
curl -X POST http://localhost:8080/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "How do I set up API keys?"}'
```

## 启动

```bash
cd ~/wiki-bot

# 用 DeepSeek（自动检测 ~/.hermes/.env 中的 DEEPSEEK_API_KEY）
python3 server.py

# 或者显式指定
DEEPSEEK_API_KEY=sk-xxx python3 server.py

# 用 OpenAI
OPENAI_API_KEY=sk-xxx python3 server.py

# 用自定义 OpenAI 兼容 API
OPENAI_BASE_URL=https://your-api.com/v1 \
OPENAI_API_KEY=sk-xxx \
LLM_MODEL=your-model \
python3 server.py
```

服务监听 `0.0.0.0:8080`。

## 更新文档

当 llm.datasette.io 文档更新时，重新抓取：
```bash
cd ~/wiki-bot
python3 scrape_wiki.py
# 索引会自动重建
```
