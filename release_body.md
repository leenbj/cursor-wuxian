# Release v2.7.2 - 2026-09-07 - Explicit Wrap-up When Nobody Answers

## 🌟 Key Highlights
- On timeout or when the user closes the UI, the tool now returns an explicit "no user response — finish the task" instruction, and the tool description's usage rules allow stopping in that case, so clients no longer treat it as a generic error and retry forever (#125).
- Closing the feedback tab/window ends the wait after a 75-second grace period instead of blocking until the timeout (10 minutes by default) (#162).

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
uvx mcp-feedback-enhanced@v2.7.2
```

## 🔗 Links
- **Documentation**: [README.md](https://github.com/Minidoracat/mcp-feedback-enhanced/blob/main/README.md)
- **Full Changelog**: [CHANGELOG](https://github.com/Minidoracat/mcp-feedback-enhanced/blob/main/RELEASE_NOTES/)
- **Issues**: [GitHub Issues](https://github.com/Minidoracat/mcp-feedback-enhanced/issues)

---
**Release automatically generated from CHANGELOG system** 🤖
