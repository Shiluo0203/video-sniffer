# 用 GitHub Actions 出 APK 的步骤

## 一次性准备

### 1. 在 GitHub 创建空仓库

登录 https://github.com/new
- Repository name: `video-sniffer`（或任意）
- 选 **Public**（必须公开，私有仓库 GitHub Actions 有分钟数限制，且 Chaquopy 免费授权要求开源）
- **不**勾选 "Add a README"
- **不**勾选 "Initialize this repository"
- 点 Create repository

### 2. 推送代码到 GitHub

在 `e:\Tools\B-视频整合解析析取` 目录下（注意是项目根，不是 android_app 子目录）：

```bash
git init
git add .
git commit -m "Android 视频悬浮嗅探原型"
git branch -M main
git remote add origin https://github.com/你的用户名/video-sniffer.git
git push -u origin main
```

如果你还没装 git：https://git-scm.com/download/win

## 触发构建

代码 push 到 main 后，GitHub Actions 会自动开始构建。
也可以在 GitHub 网页 → 你的仓库 → Actions → "Build Android APK" → Run workflow 手动触发。

## 拿 APK

构建约需 8-15 分钟（首次慢，后面有缓存约 5 分钟）。

1. 浏览器打开你的 GitHub 仓库
2. 切到 **Actions** 标签
3. 点最近一次 "Build Android APK" 运行
4. 拉到底部 **Artifacts** → 下载 `VideoSniffer-debug`
5. 解压得到 `app-debug.apk`
6. 手机上点开安装（需开启「未知来源应用」）

## 第一次失败怎么办

- **Chaquopy license 报错**：仓库必须是 Public，私有仓库需要付费 license
- **ffmpeg 下载失败**：BtbN 的 release 偶尔会改路径，看日志里的 curl 行
- **Android SDK 装不上**：setup-android action 应该会自动重试

## 后续更新代码

每次改了 android_app/ 或 core/ 下的文件后：

```bash
git add .
git commit -m "说明改了什么"
git push
```

push 完几分钟后，到 Actions 页面拿新的 APK。
