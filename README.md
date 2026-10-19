# 本地媒体去重

Windows 图片与视频去重工具，当前版本 **1.0**。使用 React、Tailwind CSS 和 Radix UI 构建深色界面，通过 pywebview / WebView2 连接 Python 比较引擎。

## 功能

- 支持文件夹内去重、单个文件对比文件夹、两个文件夹互相对比。
- 图片支持相似度匹配和可选的裁剪比较；视频通过多帧比较识别相似内容。
- 照片和视频各保存一个基准文件夹，三个模式共享路径。
- 展示重复分组、并排预览和建议保留的文件，支持手动勾选。
- 确认清理后将文件移入系统回收站。
- 使用 `settings.yml` 保存路径和选项，所有媒体均在本机处理。

## 运行源码

需要 Windows、Python 3.12、Node.js 20.19+（或 22.12+），以及 Microsoft Edge WebView2 Runtime。

在项目目录运行：

```powershell
python -m venv .venv312
.\.venv312\Scripts\python.exe -m pip install -r requirements-lock.txt -r requirements-build.txt
npm ci --prefix frontend
npm run build --prefix frontend
.\.venv312\Scripts\python.exe run.py
```

完成首次构建后，也可以双击 `start.bat` 启动。设置文件首次保存时自动生成，图片和视频的选项分别记录。

## 测试与打包

```powershell
.\.venv312\Scripts\python.exe -m unittest discover -s tests -v
.\build.ps1
```

构建脚本会编译前端、运行测试，再将单文件 EXE 输出到项目上一级的 `release` 目录；可以通过 `-Destination` 指定输出路径。发布版无需安装 Python 或 Node.js，需要系统具备 WebView2 Runtime，设置保存在 EXE 同目录。

## 目录

```text
frontend/src/                  React 界面
photo_deduplicator/engine.py    图片和视频比较引擎
photo_deduplicator/desktop.py   扫描、预览及桌面桥接
photo_deduplicator/settings.py  YAML 设置
photo_deduplicator/windowing.py Windows 窗口拖动
tests/                        自动化测试
```

`.gitignore` 基于 [Toptal gitignore](https://www.toptal.com/developers/gitignore/) 的 Python、Node、Windows 和 Visual Studio Code 模板生成，并补充了项目设置、虚拟环境与前端编译产物规则。
