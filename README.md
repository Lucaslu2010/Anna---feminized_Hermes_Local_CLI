# Anna

Anna is a desktop GUI wrapper for Hermes Agent. It provides a split interface with a terminal-style Hermes workspace on the left and an avatar panel on the right.
The app is designed to make Hermes feel more like an interactive desktop assistant while still preserving command-line control.

Anna is also the name of the fictional character that's created for this project. 
<img width="1254" height="1254" alt="happy" src="https://github.com/user-attachments/assets/14bfc6a1-a036-4905-9b27-69a03d94e701" />


## Features

- Embedded terminal interface powered by xterm.js
- Hermes Agent runs inside a pseudo-terminal
- Avatar panel with state-based image switching
- Avatar states such as idle, listening, thinking, talking, searching, coding, warning, and success
- Local assets for avatar images and terminal frontend files
- macOS `.app` packaging support
- Optional Hermes auto-detection from common install paths
- RAG File memory support, using keyword summary

## Requirements

- macOS
- Python environment bundled with the app or available in the project folder
- Hermes Agent installed on the system
- PySide6
- PySide6-Addons / QtWebEngine
- Seperate Embedding Model, API settable in Rag menu --> Settings --> Embedding Settings

Hermes must be installed separately.

You can check whether Hermes is available by running:

```bash
which hermes
```
## Future Changes And Author's Notes
- 3D Modeled Anna controlled by a secondary (hopefully free) AI (Such as Qwen 3.5 4B, which is free on sillicon flow)
- Please Note that this project is vibe coded with GPT 5.5 Thinking
- The character of Anna is drawn with help from ChatGPT Image 2, with background removal from Canva
- You can find releases on our release page.
