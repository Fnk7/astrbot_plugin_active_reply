"""将插件目录加入 sys.path，使 tests 可直接 import main。"""

import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))
