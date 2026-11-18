import argparse
import csv
import os
import socket
import time
import numpy as np
from scipy.signal import butter, lfilter
import open3d as o3d

from libs.receive_wifi import receive


class PureIMUTracker:
    def __init__(self, sample_rate_hz=100.0):
        self.dt = 1.0 / sample_rate_hz
        
        # --- 3D Open3D Environment Setup ---
        self.vis = o3d.visualization.Visualizer()
        self.vis.create_window(window_name="Pure IMU Relative Pose", width=800, height=600)
        self.mesh = o3d.geometry.TriangleMesh.create_coordinate_frame(size=0.4, origin=[0, 0, 0])
        self.vis.add_geometry(self.mesh)
        
        # --- State Tracking Matrices ---
        self.position = np.array([0.0, 0.0, 0.0])
        self.velocity = np.array([0.0, 0.0, 0.0])
        self.rotation_matrix = np.eye(3)
        self.angles = np.array([0.0, 0.0, 0.0]) # Roll, Pitch, Yaw
        self.mesh_transform = np.eye(4)
        
        # --- Filtering Window Buffers ---
        self.accel_buffer = []
        self.window_size = 10  # Window for variance checks
        
        # --- Butter High-Pass Filter Design (To strip creeping bias) ---
        # Cutoff at 0.5Hz to remove slow sensor drift while keeping hand movements
        b, a = butter(1, 0.5 / (sample_rate_hz / 2.0), btype='high')
        self.filter_b = b
        self.filter_a = a
        self.filter_state_x = np.zeros(1)
        self.filter_state_y = np.zeros(1)
        self.filter_state_z = np.zeros(1)

    def filter_bias(self, accel_xyz):
        """Applies a real-time high-pass filter component-by-component."""
        ax, ay, az = accel_xyz
        ax_f, self.filter_state_x = lfilter(self.filter_b, self.filter_a, [ax], zi=self.filter_state_x)
        ay_f, self.filter_state_y = lfilter(self.filter_b, self.filter_a, [ay], zi=self.filter_state_y)
        az_f, self.filter_state_z = lfilter(self.filter_b, self.filter_a, [az], zi=self.filter_state_z)
        return np.array([ax_f[0], ay_f[0], az_f[0]])

    def update_pose(self, raw_accel, raw_gyro):
        """
        Processes a single IMU frame. 
        Expects calibrated inputs: accel (m/s^2), gyro (rad/s)
        """
        # 1. Orientation Integration (Complementary / Gyro Dead-Reckoning)
        pitch_acc = np.arctan2(raw_accel[0], np.sqrt(raw_accel[1]**2 + raw_accel[2]**2))
        roll_acc = np.arctan2(-raw_accel[1], np.sqrt(raw_accel[0]**2 + raw_accel[2]**2))
        
        alpha = 0.98
        self.angles[0] = alpha * (self.angles[0] + raw_gyro[0] * self.dt) + (1 - alpha) * roll_acc # Roll
        self.angles[1] = alpha * (self.angles[1] + raw_gyro[1] * self.dt) + (1 - alpha) * pitch_acc # Pitch
        self.angles[2] += raw_gyro[2] * self.dt # Yaw remains unanchored
        
        # Generate 3D Rotation Matrix from Euler Angles
        r, p, y = self.angles
        Rx = np.array([[1, 0, 0], [0, np.cos(r), -np.sin(r)], [0, np.sin(r), np.cos(r)]])
        Ry = np.array([[np.cos(p), 0, np.sin(p)], [0, 1, 0], [-np.sin(p), 0, np.cos(p)]])
        Rz = np.array([[np.cos(y), -np.sin(y), 0], [np.sin(y), np.cos(y), 0], [0, 0, 1]])
        self.rotation_matrix = Rz @ Ry @ Rx

        # 2. Isolate & Transform Linear Acceleration
        accel_global = self.rotation_matrix @ np.array(raw_accel)
        linear_accel_global = accel_global - np.array([0.0, 0.0, 9.88])

        # 3. High-Pass Filter & Non-linear Deadzone
        clean_accel = self.filter_bias(linear_accel_global)
        
        # Dynamic Deadzone: Ignore values beneath the sensor noise floor
        mag = np.linalg.norm(clean_accel)
        if mag < 0.15:
            clean_accel = np.array([0.0, 0.0, 0.0])

        # 4. Adaptive Zero-Velocity Update (A-ZUPT) via Variance Check
        self.accel_buffer.append(clean_accel)
        if len(self.accel_buffer) > self.window_size:
            self.accel_buffer.pop(0)
            
        if len(self.accel_buffer) == self.window_size:
            variance = np.var(self.accel_buffer, axis=0)
            # If the window variance is exceptionally flat, force velocity reset
            if np.all(variance < 0.02) and mag < 0.2:
                self.velocity = np.array([0.0, 0.0, 0.0])
                clean_accel = np.array([0.0, 0.0, 0.0])

        # 5. Kinematic Integration Steps
        self.velocity += clean_accel * self.dt
        self.position += self.velocity * self.dt

        self.position = np.array([0.0, 0.0, 0.0])

        # 6. Render Transformation updates
        T = np.eye(4)
        T[0:3, 0:3] = self.rotation_matrix
        T[0:3, 3] = self.position
        
        self.mesh.transform(np.linalg.inv(self.mesh_transform))
        self.mesh.transform(T)
        self.mesh_transform = T
        
        self.vis.update_geometry(self.mesh)
        self.vis.poll_events()
        self.vis.update_renderer()

    def close(self):
        self.vis.destroy_window()

def main():
    parser = argparse.ArgumentParser(description="Visualize live IMU data over Wi-Fi.")
    parser.add_argument("--host", default="192.168.4.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    tracker = PureIMUTracker()

    def on_record(fields):
        accel = [round(float(value), 1) for value in fields[0:3]]
        print(accel)
        print((accel[0]**2 + accel[1]**2 + accel[2]**2)**0.5)
        gyro = [float(value) for value in fields[3:6]]
        tracker.update_pose(accel, gyro)

    print("Pure IMU engine running. Close window to exit.")
    try:
        with open(os.devnull, "w", newline="") as output:
            writer = csv.writer(output)
            while True:
                try:
                    connection = socket.create_connection(
                        (args.host, args.port), timeout=5
                    )
                except OSError as error:
                    print(
                        f"Connection unavailable: {error}; retrying in 2 seconds.",
                        flush=True,
                    )
                    time.sleep(2)
                    continue

                print(f"Connected to {args.host}:{args.port}", flush=True)
                with connection:
                    connection.settimeout(10)
                    receive(
                        connection,
                        writer,
                        output,
                        quiet=True,
                        on_record=on_record,
                    )
                print("Disconnected; reconnecting in 2 seconds.", flush=True)
                time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        tracker.close()


if __name__ == "__main__":
    main()
