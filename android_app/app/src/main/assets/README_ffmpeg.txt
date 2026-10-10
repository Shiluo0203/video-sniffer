Android 版需要 ffmpeg 的 Android 预编译二进制，放到此目录。

文件名对应 ABI：
  ffmpeg_arm64   → arm64-v8a（现代手机，必放）
  ffmpeg_x86_64  → x86_64（模拟器用，可选）
  ffmpeg_arm      → armeabi-v7a（老手机，可选）

下载源（任选其一）：

1. BtbN 自动构建（推荐，单文件静态链接）：
   https://github.com/BtbN/FFmpeg-Builds/releases
   选 ffmpeg-master-android-clang-aarch64.tar.xz
   解压后取 bin/ffmpeg 重命名为 ffmpeg_arm64 放此目录

2. ffbinaries：
   https://ffbinaries.com/downloads
   选 Android arm64 版本

3. 自行编译（NDK + clang）：
   https://github.com/FFmpeg/FFmpeg
   ./configure --target-os=android --arch=aarch64 --enable-cross-compile
   --cross-prefix=aarch64-linux-android- --sysroot=$NDK/sysroot
   --enable-static --disable-shared --disable-doc --disable-ffmpeg
   --disable-ffplay --disable-ffprobe --enable-protocol=file,http,https

注意：
  - 文件不要带 .exe 后缀
  - 打包后 app 会自动把它拷到 filesDir/bin/ffmpeg 并 chmod 755
  - 不放也能跑，只是 yt-dlp 无法合并视频流（解析仍可用）
