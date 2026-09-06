# Release v2.8.0 - 2026-09-07 - Desktop Application Enters Maintenance-Only

## 🌟 Key Highlights
- The desktop application (Tauri shell) is **maintenance-only** from this release: no new features, only security fixes and "cannot launch at all" compatibility fixes, scheduled for removal in v3. The binaries still ship with this release; to keep using it, pin the version in your IDE's MCP configuration (see "Desktop application maintenance status" in the README).
- When the desktop shell cannot start (quarantined by antivirus, glibc too old, blocked by Gatekeeper, exits right after launch) the server no longer waits silently until the timeout: the process falls back to the browser and prints the URL to stderr.

## 🌐 Detailed Release Notes

### 🇺🇸 English
📖 **[View Complete English Release Notes](https://github.com/Minidoracat/mcp-feedback-enhanced/blob/main/RELEASE_NOTES/CHANGELOG.en.md)**

### 🇹🇼 繁體中文
📖 **[查看完整繁體中文發布說明](https://github.com/Minidoracat/mcp-feedback-enhanced/blob/main/RELEASE_NOTES/CHANGELOG.zh-TW.md)**

### 🇨🇳 简体中文
📖 **[查看完整简体中文发布说明](https://github.com/Minidoracat/mcp-feedback-enhanced/blob/main/RELEASE_NOTES/CHANGELOG.zh-CN.md)**

---

## 📦 Quick Installation / 快速安裝

```bash
# Latest version / 最新版本
uvx mcp-feedback-enhanced@latest

# This specific version / 此特定版本
uvx mcp-feedback-enhanced@v2.8.0
```

## 🔗 Links
- **Documentation**: [README.md](https://github.com/Minidoracat/mcp-feedback-enhanced/blob/main/README.md)
- **Full Changelog**: [CHANGELOG](https://github.com/Minidoracat/mcp-feedback-enhanced/blob/main/RELEASE_NOTES/)
- **Issues**: [GitHub Issues](https://github.com/Minidoracat/mcp-feedback-enhanced/issues)

---
**Release automatically generated from CHANGELOG system** 🤖
