"""Общий загрузчик модулей для тестов. Скрипты живут в папках с дефисами
(`.claude/skills/listing-fin-offer/...`) — обычный `import` там невозможен
(дефис не валиден в имени пакета Python), поэтому грузим по пути через importlib.
Один шов на файл, не пересобирать логику загрузки в каждом тесте отдельно."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_module(rel_path: str, module_name: str):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
