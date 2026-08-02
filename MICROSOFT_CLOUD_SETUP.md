# SharePoint / OneDrive 云端资料配置

步骤 2 现在保留原有“本地文件夹”扫描，并新增 SharePoint / OneDrive 文件夹网址扫描。
云端目录会递归扫描，文件夹层级会原样镜像到应用缓存，因此文件名匹配、父目录公司识别、
内容识别、预览和文件整理继续使用现有逻辑。

## 一次性管理员配置

1. 在 Microsoft Entra 管理中心注册一个应用。
2. 将应用设置为“允许公共客户端流”（移动和桌面应用）。
3. 添加 Microsoft Graph 的委托权限：
   - `User.Read`
   - `Files.Read.All`
   - `Sites.Read.All`
4. 根据组织策略完成管理员同意。
5. 启动程序前设置：

```text
PBC_MICROSOFT_CLIENT_ID=<应用程序（客户端）ID>
PBC_MICROSOFT_TENANT_ID=<目录（租户）ID>
```

`PBC_MICROSOFT_TENANT_ID` 可省略；省略时使用 `organizations`，允许任意组织账户登录。

## 使用方法

1. 在步骤 2 的第二行粘贴 SharePoint 或 OneDrive 文件夹网址。
2. 点击“登录 Microsoft”，在微软页面输入工具显示的设备代码。
3. 回到工具完成登录，然后点击“扫描云端”。

建议在 SharePoint/OneDrive 中对目标文件夹选择“复制链接”后粘贴。普通文档库网址也会尝试解析。

## 数据与权限

- 工具只申请读取权限，不会修改或删除 SharePoint/OneDrive 中的文件。
- 云端文件自动下载到应用数据目录的 `cloud_cache` 中，用户无需手工下载。
- 删除或重命名云端项目后，再次扫描会同步更新缓存和增量匹配状态。
- 本地文件夹扫描不经过 Microsoft Graph，行为与原版本一致。
