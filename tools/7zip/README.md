# 7-Zip RAR backend

Windows发布包若需要在用户未安装解压软件时读取RAR，请将官方64位独立控制台程序命名为
`7zz.exe` 放在此目录。两个PyInstaller配置会自动把它打包到
`tools/7zip/7zz.exe`，运行时 `archive_scanner.py` 会优先使用该内置程序。

建议使用7-Zip官网发布的最新安全版本。分发时需要在产品文档中说明使用了7-Zip的部分
程序、其GNU LGPL许可，并提供 https://www.7-zip.org/ 源代码链接；同时遵守其中RAR
代码的许可限制。
