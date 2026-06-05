module.exports = {
    apps: [
      {
        name: "tool-fb-mobile",
        script: "main_merged.py",
        cwd: "C:/tool-fb-mobile/tool-fb-mobile",
        interpreter: "C:/tool-fb-mobile/tool-fb-mobile/venv/Scripts/python.exe",
        env: {
          PYTHONUTF8: "1",
          PYTHONIOENCODING: "utf-8"
        },
        merge_logs: true
      }
    ]
  }