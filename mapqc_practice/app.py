"""v0.1仅验证程序启动和打包，业务功能按逐日计划加入。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MapQC综合实践")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args(argv)
    if args.smoke_test:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget

    app = QApplication([])
    app.setApplicationName("MapQC Practice")
    app.setApplicationVersion(__version__)
    font_name = "Microsoft YaHei UI"
    if args.smoke_test and os.name == "nt":
        # The offscreen platform needs an explicit Windows font registration.
        font_file = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
        if font_file.exists():
            font_id = QFontDatabase.addApplicationFont(str(font_file))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                font_name = families[0]
    app.setFont(QFont(font_name, 10))

    window = QMainWindow()
    window.setWindowTitle(f"MapQC 综合实践 v{__version__}")
    window.resize(900, 580)
    surface = QWidget()
    layout = QVBoxLayout(surface)
    layout.setContentsMargins(36, 28, 36, 24)
    title = QLabel("地图数据质量检查")
    title.setFont(QFont(font_name, 19, QFont.Weight.Bold))
    layout.addWidget(title)
    layout.addWidget(QLabel("综合实践 · 项目骨架"))
    layout.addSpacing(28)
    message = QLabel("尚未加载地图数据\n\n当前版本用于验证窗口启动与打包。")
    message.setAlignment(Qt.AlignmentFlag.AlignCenter)
    message.setStyleSheet("background:#f5f5f5; border:1px solid #dddddd; color:#555555;")
    layout.addWidget(message, 1)
    window.setCentralWidget(surface)
    window.statusBar().showMessage("v0.1 · 启动与打包验证")
    window.show()

    if args.smoke_test:
        app.processEvents()
        if args.screenshot:
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            if not window.grab().save(str(args.screenshot)):
                raise RuntimeError("窗口截图保存失败")
        result = {"status": "passed", "version": __version__,
                  "window_title": window.windowTitle(),
                  "window_size": [window.width(), window.height()],
                  "platform": app.platformName(),
                  "scope": "offscreen window creation and event processing; business functions are not implemented"}
        if args.result:
            args.result.parent.mkdir(parents=True, exist_ok=True)
            args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # A frozen Windows executable may inherit a non-UTF-8 console encoding.
        print(json.dumps(result, ensure_ascii=True))
        QTimer.singleShot(0, app.quit)
    return app.exec()
