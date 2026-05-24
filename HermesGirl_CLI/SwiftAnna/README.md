# Anna Swift Shell

Native macOS SwiftUI shell for Anna.

The Swift app keeps the main Hermes/RAG/account logic in Python by launching:

```bash
python3 Webversion/swift_bridge.py
```

Run the app from this folder:

```bash
swift run AnnaSwift
```

Current Swift coverage:

- Chat shell
- Learning workspace sidebar
- Web Mode settings
- Pre-app login and registration gate
- Logout from settings
- Memory and skills text views in Web Mode
- Local/Web gateway health and startup through the Python bridge
- Uploaded file list, upload picker, delete, and reindex actions
- Upload progress popup
- Original RAG source list and chunk browser

Still to migrate from PySide:

- Memory import/export/recovery UI
- Native file reupload prompt
