import sys
import inspect
import pywhispercpp
from pywhispercpp.model import Model
print("Model methods:")
for name, attr in inspect.getmembers(Model):
    if not name.startswith('_'):
        print(f"  {name}")
