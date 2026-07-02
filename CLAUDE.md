# CLAUDE.md — Weibo Saver 开发规范

## 项目概述

Weibo Saver 是一个 Windows 桌面微博内容存档与监测工具，使用 Python 3.13 + CustomTkinter + httpx + SQLite 构建，通过 PyInstaller 打包为单文件免安装 exe。

## 架构原则

### 1. 双层执行模式
- **源码模式**（`python gui.py`）：GUI 通过 `subprocess.run` 调用 `run_full_crawl.py` 子进程执行抓取，完全进程隔离
- **exe 模式**（PyInstaller 打包）：GUI 在后台线程中通过 `runpy.run_path` 执行抓取逻辑，Semaphore(1) 串行化保护

### 2. GUI 与后端隔离
- GUI (`gui_app.py`) **不导入**后端重依赖（Engine/Database/Scheduler）
- 抓取参数通过**环境变量**传递：`WEIBO_COOKIE_STRING`, `WEIBO_TARGET_UID`, `WEIBO_MAX_CARDS`, `WEIBO_ARCHIVE_ROOT`, `WEIBO_CONTENT_TEXT/IMAGES/VIDEOS`, `WEIBO_BACKFILL*`
- GUI 独立管理自己的配置（`users.json`, `cookie.json`, `gui_settings.json`）

### 3. 数据流
```
GUI 用户操作 → 修改 self._users 字典 → _save_users() → users.json
              → run_crawl(uid) → _crawl_subprocess(uid)
              → 设置 env vars → 执行 run_full_crawl.py
              → 解析 stdout 输出 → 更新 UI 状态
```

## 关键文件

| 文件 | 职责 | 行数 |
|------|------|------|
| `gui.py` | 启动器，GUI/Crawl/PDF引导 | ~130 |
| `run_full_crawl.py` | 抓取引擎 + 卡片渲染集成 | ~1430 |
| `weibo_saver/ui/gui_app.py` | GUI 主文件（含 DetailLogCard/看门狗）| ~1130 |
| `weibo_saver/ui/gui_config.py` | GUI 设置持久化 | ~50 |
| `weibo_saver/models/post.py` | 博文数据模型（含 avatar_url）| ~430 |
| `weibo_saver/storage/database.py` | SQLite 数据库层（WAL + media 表）| ~350 |
| `weibo_saver/core/card_renderer.py` | 卡片快照渲染引擎 (Pillow, 3列网格, 矢量图标) | ~450 |
| `weibo_saver/core/visibility_detector.py` | 可见性检测 | ~330 |
| `weibo_saver/core/media_downloader.py` | 媒体下载（图片/视频/GIF）| ~275 |

## 新增架构特性

### 卡片快照
- 每条博文保存时自动生成 800px 宽的微博风格 JPG 卡片
- 覆盖原创/转发/转发源(resource/)/历史版本(versions/v{N}/)
- 3 列正方形配图网格（220×220px，居中裁切），视频封面+播放按钮
- 转发展示原博浅灰背景内嵌卡片区
- 自转发原博使用独立下载的头像
- 补采 + 长文展开后自动更新对应卡片

### 详细日志
- 侧边栏「详细日志」页面仅记录失败/异常事件
- 每条记录含：时间、UID、错误类别、详情、环境快照、Cookie状态、原始返回数据
- 可展开卡片 + 一键复制，支持清空

### UI 防闪烁
- `UserCard.update_display()` — 仅刷新动态文字不重建控件
- `WeiboSaverGUI._update_cards()` — 遍历所有卡片刷新显示
- 抓取开始/完成/超时/失败 全部用 `_update_cards` 替代 `_refresh`
- `_refresh()` 仅保留给增减用户/排序等结构性变化

### 自定义应用图标
- `weibo_saver/resources/logo.ico` — 多尺寸 ICO（16~256px）
- GUI 启动时 `self.iconbitmap()` 设置任务栏和标题栏图标

## 编码规范

### Python 风格
- 遵循项目现有风格：紧凑型，允许单行 `if`/`for`，类内部方法间无空行
- 缩进 4 空格
- 字符串优先双引号，f-string 优先
- 导入顺序：标准库 → 第三方 → 项目内部
- 类型注解可选，不强求

### 命名约定
- 类：PascalCase（`WeiboSaverGUI`, `CookiePage`, `UserCard`）
- 函数/方法：snake_case（`run_crawl`, `_save_new_post`）
- 私有方法：下划线前缀（`_build`, `_refresh`）
- 模块级常量：UPPER_CASE（`HOURLY_LIMIT`, `MIN_REQ_GAP`）
- UI 控件：下划线前缀缩写（`_eb` 展开按钮, `_sl` 状态标签, `_scr` 滚动框）

### 线程安全
- `self._users` 读写必须持有 `self._users_lock`
- `self._crawling_uids` 读写必须持有 `self._crawl_lock`
- exe 模式抓取用 `self._crawl_sem` 串行化
- UI 更新必须通过 `ui()` 入队 → `_poll()` 主线程分发

### 错误处理
- API 调用失败不崩溃，记录日志后继续
- 文件 I/O 失败输出 `[DISK_ERR]` 不静默
- Cookie 过期通过 `_safe_get` 检测 403/302
- DB 操作失败抛 `DatabaseError`

## 必需检查清单

修改代码后必须：
1. `python -c "import ast; ast.parse(open('file.py').read())"` 语法检查
2. 确认 `sys.path` 导入在 exe 和源码模式都正确
3. 新增模块必须在 `weibo_saver.spec` 的 `hiddenimports` 中注册
4. 新增 UI 控件必须确认信号→env var→后端 完整链路
5. 打包前执行清理：`__pycache__/`, `build/`, `*.pyc`

## 环境变量接口（`run_full_crawl.py` 读取）

| 变量 | 默认 | 说明 |
|------|------|------|
| `WEIBO_COOKIE_STRING` | 无默认值（必须提供） | Cookie 字符串 |
| `WEIBO_TARGET_UID` | 无默认值（必须提供） | 目标 UID |
| `WEIBO_MAX_CARDS` | `"5"` | 卡片上限，0=不限制 |
| `WEIBO_ARCHIVE_ROOT` | 用户文档目录 | 存档根路径 |
| `WEIBO_CONTENT_TEXT` | `"1"` | 保存文字 |
| `WEIBO_CONTENT_IMAGES` | `"1"` | 下载图片 |
| `WEIBO_CONTENT_VIDEOS` | `"1"` | 下载视频 |
| `WEIBO_BACKFILL` | `"0"` | 启用补采 |
| `WEIBO_BACKFILL_TYPES` | `""` | 补采类型 "images,videos" |
| `WEIBO_BACKFILL_MAX_POSTS` | `"50"` | 补采上限 |

## 打包

```powershell
# 清理
Get-ChildItem -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Remove-Item -Recurse -Force "build", "dist" -ErrorAction SilentlyContinue

# 打包
python -m PyInstaller --clean --noconfirm weibo_saver.spec

# 产物
dist/WeiboSaver.exe  (~72.7 MB, 单文件免安装)
```
