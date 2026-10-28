import time
import traceback

from wcferry import Wcf

print("probe: creating Wcf", flush=True)
try:
    wcf = Wcf(debug=False, block=False)
    print("probe: created; login =", wcf.is_login(), flush=True)
    time.sleep(1)
    print("probe: self =", wcf.get_self_wxid(), flush=True)
    wcf.cleanup()
    print("probe: OK", flush=True)
except BaseException:
    traceback.print_exc()
    print("probe: FAILED", flush=True)
    raise
