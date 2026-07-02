<p align="center">
  <img src="weibo_saver/resources/logo.png" alt="Weibo Saver" width="96" height="96">
</p>

<h1 align="center">Weibo Saver</h1>

<p align="center">
  <strong>Windows 桌面微博内容存档与监测工具</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.13-blue.svg" alt="Python 3.13">
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey.svg" alt="Windows">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/badge/build-PyInstaller-orange.svg" alt="PyInstaller">
</p>

---

## 简介

Weibo Saver 是一款 Windows 桌面工具，能将微博内容**完整存档到本地**，并**持续监测博文变化**（编辑/删除/隐藏）。

核心能力：
- 📥 **全量抓取** — 一键下载目标用户全部可见微博（图文+视频）
- 🔍 **增量监测** — 定时轮询，自动发现新博文、编辑、删除
- 🎨 **卡片快照** — 自动生成 800px 微博风格 JPG 卡片（3列配图网格）
- 📝 **编辑追踪** — 检测博文编辑，保存每个版本的完整内容和 unified diff
- 🗃️ **多格式导出** — JSON / Markdown / TXT 三种格式同时输出

### 截图预览

<p align="center">
  <em>（运行截图 — 将图片放入 docs/screenshots/ 目录）</em>
</p>

| 用户管理 | 卡片快照示例 |
|:---:|:---:|
| ![用户管理](docs/screenshots/users.png) | ![卡片快照](docs/screenshots/card.png) |

---

## 系统架构

```
┌──────────────────────────────────────────────┐
│              表现层 (GUI)                      │
│  CustomTkinter 暗色主题 · 5个功能页面           │
│  用户卡片管理 · Cookie验证 · 详细日志            │
├──────────────────────────────────────────────┤
│              业务逻辑层                         │
│  抓取引擎 · 媒体下载 · 卡片渲染                  │
│  编辑检测 · 可见性检测 · 版本对比                │
├──────────────────────────────────────────────┤
│              数据层                            │
│  SQLite (WAL) · 文件系统归档                   │
│  JSON/MD/TXT 三格式导出                        │
└──────────────────────────────────────────────┘
```

### 存档目录结构

```
WeiboArchive/
└── users/
    └── {screen_name}_{uid}/
        └── YYYY/MM/{日期}_{类型}/
            ├── post.json          # 结构化数据
            ├── post.md            # Markdown 格式
            ├── post.txt           # 纯文本
            ├── post_card.jpg      # 微博风格卡片快照
            ├── images/            # 图片原文件
            ├── videos/            # 视频原文件
            ├── resource/          # 转发原博内容
            └── versions/          # 编辑历史版本
                └── v2/
                    ├── post.json
                    ├── post_card.jpg
                    └── diff_v2.patch
```

---

## 快速开始

### 环境要求

- Python 3.11+
- Windows 10/11

### 安装

```bash
# 克隆仓库
git clone https://github.com/yourname/weibo-saver.git
cd weibo-saver

# 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 运行

```bash
# 启动 GUI
python gui.py
```

程序启动后：
1. 在 **Cookie 页面** 输入微博 Cookie（或点击扫码登录）
2. 在 **用户管理** 添加目标 UID
3. 点击 **开始抓取**，等待完成
4. 在 `WeiboArchive/` 目录查看存档内容

### 打包为 exe

```powershell
python -m PyInstaller --clean --noconfirm weibo_saver.spec
# 产物: dist/WeiboSaver.exe (~72 MB, 单文件免安装)
```

---

## 技术栈

| 类别 | 技术 | 用途 |
|------|------|------|
| GUI 框架 | CustomTkinter | 暗色主题桌面界面 |
| HTTP 客户端 | httpx (async) | 微博 API 请求 |
| 数据库 | SQLite + aiosqlite | 本地数据存储 (WAL 模式) |
| 图像处理 | Pillow | 卡片快照渲染 |
| 打包工具 | PyInstaller | 单文件 exe 打包 |
| 异步 | asyncio | 并发下载 + API 请求 |

---

## 核心特性详解

### 反爬策略

- 请求间隔随机化 (1.2s – 2.5s)
- 移动端 User-Agent 伪装
- XSRF-TOKEN 自动刷新
- Cookie 过期检测与告警
- 单用户每小时上限 800 次请求（SLA: 1000次/小时）

### 编辑检测

博文每次被抓取时，对比内容 SHA-256 哈希值。若发现变化：
1. 通过长文 API 展开全文（排除截断导致的误判）
2. 生成 unified diff 补丁
3. 保存新版本到 `versions/v{N}/` 目录
4. 自动更新卡片快照

### 可见性检测

自动检测目标用户主页的可见时间范围，区分：
- **被博主删除** → 标记为 `is_deleted`
- **超出可见时间范围**（如"仅展示半年"）→ 标记为 `visibility_hidden`

避免因平台限制而误判为删除。

---

## 项目结构

```
weibo-saver/
├── gui.py                      # 启动器（源码/exe 双模式）
├── run_full_crawl.py           # 独立抓取脚本（子进程调用）
├── weibo_saver.spec            # PyInstaller 打包配置
├── requirements.txt            # Python 依赖
├── weibo_saver/
│   ├── core/                   # 核心引擎
│   │   ├── engine.py           #   抓取引擎调度
│   │   ├── api_fetcher.py      #   API 请求封装
│   │   ├── card_renderer.py    #   卡片快照渲染 (Pillow)
│   │   ├── media_downloader.py #   媒体异步下载
│   │   ├── visibility_detector.py # 可见性检测
│   │   ├── edit_detector.py    #   编辑检测
│   │   ├── block_detector.py   #   封禁检测
│   │   ├── qr_login.py         #   扫码登录
│   │   ├── session_manager.py  #   Cookie 会话管理
│   │   ├── proxy_pool.py       #   代理池
│   │   └── dedup.py            #   去重
│   ├── models/                 # 数据模型
│   │   ├── post.py             #   博文模型
│   │   ├── user.py             #   用户模型
│   │   └── media_item.py       #   媒体项模型
│   ├── storage/                # 数据持久化
│   │   ├── database.py         #   SQLite 数据库层
│   │   ├── layout.py           #   目录结构管理
│   │   └── file_writer.py      #   多格式文件写入
│   ├── versioning/             # 版本管理
│   │   ├── differ.py           #   unified diff 对比
│   │   └── tracker.py          #   版本追踪
│   ├── monitor/                # 监测调度
│   │   ├── scheduler.py        #   定时任务调度
│   │   └── state.py            #   爬取状态管理
│   ├── ui/                     # GUI 界面
│   │   ├── gui_app.py          #   主界面
│   │   ├── gui_config.py       #   设置持久化
│   │   ├── tray.py             #   系统托盘
│   │   └── icon.py             #   图标管理
│   └── utils/                  # 工具函数
│       ├── rate_limiter.py     #   速率限制
│       ├── retry.py            #   重试机制
│       └── text_sanitizer.py   #   文本清洗
├── docs/
│   ├── 项目管理文档.md           # 产品/项目管理文档
│   └── screenshots/            # 运行截图（待添加）
└── weibo_saver/resources/
    ├── logo.ico                # 应用图标 (多尺寸)
    └── logo.png                # Logo (48x48)
```

---

## 文档

- [开发规范](CLAUDE.md) — 架构、编码规范、线程安全策略
- [项目管理文档](docs/项目管理文档.md) — 产品设计、迭代计划、技术决策记录
- [使用指南](Weibo%20Saver使用指南.pdf) — 用户操作手册 (PDF)

---

## 许可

本项目采用 [MIT License](LICENSE) 开源。

**注意**：本项目仅供学习和技术研究使用。使用前请确认遵守微博平台的相关服务条款。
