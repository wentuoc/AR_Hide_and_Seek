"""Read live status via the SSH control forward."""
import json
from calibration_web import request
if __name__=='__main__':
    print(json.dumps(request({'type':'STATUS'}),indent=2))
