"""Wire validation and per-device continuous windows. No PYNQ dependency."""
import math
from collections import deque

FEATURES = ('ax','ay','az','gx','gy','gz','flex0','flex1','flex2','flex3','flex4')

def uint32(v):
    return type(v) is int and 0 <= v <= 0xffffffff

def values(packet):
    if packet.get('sensor_schema') != 'si_flex_v1':
        raise ValueError('Real sensor_schema=si_flex_v1 required; demo IMU is not accepted')
    if not uint32(packet.get('sample_seq')) or not uint32(packet.get('ts_ms')):
        raise ValueError('Invalid sample counter/time')
    flex = packet.get('flex')
    if not isinstance(flex, list) or len(flex) != 5:
        raise ValueError('Expected five normalized flex readings')
    row = [packet['imu'][k] for k in FEATURES[:6]] + flex
    if any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>1e6 for v in row):
        raise ValueError('Nonfinite/invalid sensor value')
    return row

class Windows:
    def __init__(self, period=50, tolerance=15, stride=5):
        if type(period) is not int or period <= 0 or not 0 <= tolerance < period:
            raise ValueError('Invalid timing parameters')
        if type(stride) is not int or not 1 <= stride <= 20:
            raise ValueError('stride must be 1..20')
        self.period, self.tolerance, self.stride = period, tolerance, stride
        self.rows = deque(maxlen=20)
        self.previous = None
        self.since = 0
        self.resets = 0

    def add(self, packet):
        row = values(packet)
        # latest_samples already retains the complete packet for the visualizer.
        # Invalid flex prevents inference, while its IMU remains available.
        if any(v < 0 or v > 1 for v in packet['flex']):
            self.rows.clear()
            self.previous = None
            self.since = 0
            self.resets += 1
            return None
        current = (packet['sample_seq'], packet['ts_ms'])
        if self.previous is not None:
            ds = (current[0]-self.previous[0]) & 0xffffffff
            dt = (current[1]-self.previous[1]) & 0xffffffff
            if ds != 1 or abs(dt-self.period)>self.tolerance:
                self.rows.clear(); self.since=0; self.resets+=1
        self.previous=current
        self.rows.append(row); self.since+=1
        if len(self.rows)==20 and self.since>=self.stride:
            self.since=0
            return list(self.rows)
        return None
