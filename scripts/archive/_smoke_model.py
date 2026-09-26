# -*- coding: utf-8 -*-
"""验证 prediction_core 模型加载是否可用"""
import sys, time
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))
sys.path.insert(0, str(BASE_DIR / "collection"))
t0 = time.time()
from prediction_core import init_models, quick_predict
print(f"import ok ({time.time()-t0:.1f}s)")
t0 = time.time()
models = init_models()
print(f"init_models ok ({time.time()-t0:.1f}s)")
print("wdl status:", models['wdl']['status'])
print("t005 status:", models['t005']['status'])
print("epl status:", models.get('epl', {}).get('status', 'N/A'))