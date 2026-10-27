"""让 pytest 在项目根目录也能 import app 包（Windows / macOS 通用）。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
