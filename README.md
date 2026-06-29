# 📚 Wiki Q&A Bot

中文知识库问答机器人 —— 爬取任意 Wiki，用 AI 回答你的问题。

**支持：** Fandom 百科、ReadTheDocs 文档、Next.js SPA 站点  
**当前已收录：** 以撒的结合、Noita、脑叶公司、Llm Cli（4 个 Wiki，900+ 页）

## ✨ 功能

- 🔍 **智能检索** — 中文问题也能精准命中英文 Wiki（自动翻译 + 双语搜索）
- 🧠 **用户记忆** — 聊天里输入 `记住：xxx` 或 `修正：xxx` 保存你的知识，优先于 Wiki 内容
- 🖥️ **Web 界面** — 全中文 UI，选 Wiki 直接问，点 `+ 添加Wiki` 爬新站点
- 🔄 **自动更新** — 放新数据文件到 `data/` 文件夹，5 秒内自动加载
- 💰 **几乎免费** — 每次提问约 ¥0.008（DeepSeek API）

## 🚀 快速开始

### 你需要
- Windows 电脑
- Python 3.10+
- DeepSeek API key（[platform.deepseek.com](https://platform.deepseek.com) 注册即送额度）

### 安装

```cmd
cd WikiBot
copy config.example.yaml config.yaml
install.bat
```

编辑 `config.yaml`，填入你的 API key，然后双击 `start.bat`。浏览器会自动打开。

### 更新

双击 `update.bat`，自动下载最新代码，保留你的配置和数据。

## 📂 项目结构

```
wiki-bot/
├── server.py            # FastAPI 服务 + Web 界面（端口 8080）
├── knowledge_base.py     # 知识库引擎：嵌入、检索、重排序
├── scrape_wiki.py        # Wiki 爬虫（Fandom / ReadTheDocs）
├── scrape_duckov.py      # 浏览器爬虫（Next.js SPA 站点）
├── config.example.yaml   # 配置模板（复制为 config.yaml 后填 API key）
├── requirements.txt      # Python 依赖
├── install.bat           # 一键安装（创建 venv + 装依赖）
├── start.bat             # 一键启动
├── update.bat            # 一键更新
└── data/                 # Wiki 数据（.json）+ 索引（自动生成）
```

## 🔧 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | Web 聊天界面 |
| GET | `/health` | 健康检查 |
| POST | `/ask` | 提问 `{"question":"...","wiki":"slug"}` |
| POST | `/search` | 纯检索（无需 API key） |
| POST | `/scrape` | 添加 Wiki `{"url":"...","type":"mediawiki","name":"..."}` |
| POST | `/memory` | 保存记忆 `{"wiki":"slug","text":"..."}` |

## 📄 License

MIT
