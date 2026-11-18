# Machine Learning Pipeline

This subfolder contains scripts for collecting sensor data with the Firebeetle, preprocessing and augmenting the dataset, training the machine learning model, evaluating its performance, and exporting it for use in hardware acceleration.

## Requirements and Installation

- Python 3.11.16
- Required Python packages listed in `requirements.txt`

The commands below assume they are run from the repository root. `uv` is
recommended for Python package management.

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then, create the virtual environment and install the packages

**Windows (PowerShell):**
```powershell
cd .\ml
uv venv --python 3.11.16
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
```

**macOS/Linux:**
```bash
cd ml
uv venv --python 3.11.16
source .venv/bin/activate
uv pip install -r requirements.txt
```

If `uv` is not found after installation, open a new terminal so that the
updated `PATH` is loaded.

## record_data.py

This script can be used to record sensor data from the FireBeetle. The data can then be cleaned and pre-processed in subsequent steps for machine learning. 

This script automatically runs through the list of gestures and prompts the user to perform the required gesture at 3s time intervals. Each trial consists of approximately 1.5s of sampling, and the raw data received is saved as separate csv files to facilitate subsequent steps.

Each CSV file contains the header row, as well as the sample_index and label columns.

### Usage

0. The FireBeetle should be sending the 11 sensor data points over serial at
   115200 baud as comma-separated values:

   `accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z, flex_1, flex_2, flex_3, flex_4, flex_0`

1. Connect the FireBeetle to the laptop, run the sensor code from Arduino, and
   close the Serial Monitor/Serial Plotter.

2. From the repo root, run the script using the port for the connected board:

   **Windows (PowerShell):**

   ```powershell
   python -m ml.record_data COM5 -n 10
   ```

   **macOS/Linux:**

   ```bash
   python -m ml.record_data /dev/cu.usbmodemXXXX -n 10
   ```

   Replace `COM5` or `/dev/cu.usbmodemXXXX` with the actual serial port. The
   default is `COM5`, so the port must be supplied explicitly on macOS/Linux.
   Omit `-n 10` to use the default of 10 trials per gesture.

3. Follow the instructions in the terminal to record gestures.

## record_data_wifi.py

This script allows for the recording of sensor data sent from the FireBeetle over WiFi.

### Usage

1. Join the FireBeetle's Wi-Fi network and run the sensor code from Arduino.

2. From the repo root, run the script using:

   ```bash
   python -m ml.record_data_wifi -n 10 --host 192.168.4.1 --port 5000
   ```

   `--host` and `--port` default to `192.168.4.1` and `5000`, respectively.
   Omit `-n 10` to use the default of 10 trials per gesture.

## visualise_data.py

This script is primarily used for debugging and visualisation. A specified csv file (generated using `record_data.py`) is read and separate subplots show the accelerometer, gyroscope, and flex sensor readings.

### Usage

From the repository root, provide a path relative to the `ml` directory. For
example, a recording saved by `record_data.py` can be visualised with:

```text
python -m ml.visualise_data data/raw/d01/d01_0000.csv
```

## preprocess_data.py

This script validates and cleans the recordings in `data/raw`, splits them into
training and test datasets, normalizes the IMU channels, and creates processed
CSV manifests.

The accelerometer and gyroscope channels are normalized using means and standard
deviations calculated from the training split. Flex sensor values remain
unchanged. The normalization metadata is saved to
`data/metadata/normalisation.json`.

### Usage

To split recordings randomly:

```bash
python -m ml.preprocess_data --seed 42
```

To reserve specific dataset folders for testing, pass them as a comma-separated
list:

```bash
python -m ml.preprocess_data --seed 42 --test-set d20,d21
```

The processed recordings are written to `data/processed/train` and
`data/processed/test`. Dataset manifests are written to
`data/processed/train.csv` and `data/processed/test.csv`.

## augment_data.py

This script converts processed recordings into fixed-length windows and saves
each window as a `.pt` file. It can generate conservative augmented copies of
the original motion data.

By default, each recording is split into overlapping 20-sample windows with a
stride of 10. The script also produces a manifest containing the path, source
recording, label, window index, and augmentation variant of every example.

### Usage

Create augmented training windows:

```bash
python -m ml.augment_data --copies 2 --stride 10 --seed 42
```

Create test windows without augmentation:

```bash
python -m ml.augment_data \
  --input data/processed/test \
  --output data/augmented/test \
  --copies 0 \
  --window-start 5 \
  --stride 20
```

The default training output is `data/augmented/train`, with
`data/augmented/train/manifest.csv` as the manifest.

## optimise.py

This script uses Optuna and grouped k-fold cross-validation to search for
gesture CNN architecture and training hyperparameters.

The searched options include batch size, model type, optimiser, learning rate,
and weight decay.

### Usage

```bash
python -m ml.optimise --trials 30 --epochs 30 --folds 4 --seed 42
```

Use `--train-manifest` to select a different training manifest or `--output`
to choose a different JSON output location. The default output is a timestamped
directory under `ml/outputs/optuna`.

The best trial and all trial summaries are saved to a JSON file with the
following structure:

```json
{
  "best_value": 0.95,
  "best_params": {},
  "trials": []
}
```

## train.py

This script trains a gesture CNN using the augmented training manifest. TensorBoard
logs, periodic checkpoints, training parameters, and final weights are saved
under the selected output directory.

### Usage

Train with the default hyperparameters:

```bash
python -m ml.train --epochs 50 --seed 42
```

Train using parameters from an Optuna study:

```bash
python -m ml.train \
  --epochs 50 \
  --optuna-study ml/outputs/optuna_study.json \
  --output-directory ml/outputs/final \
  --log-directory ml/runs/final
```

The output includes:

- `final_weights.pt`
- `training_parameters.json`
- `checkpoint_epoch_005.pt`, `checkpoint_epoch_010.pt`, and subsequent checkpoints
- TensorBoard logs under `--log-directory`

## test.py

This script evaluates an in-distribution trained checkpoint and generates
printed window/recording metrics, per-class results, a confusion matrix, and
`test_statistics.png`.

### Usage

```bash
python -m ml.test \
  --weights ml/outputs/final/final_weights.pt \
  --manifest ml/data/augmented/test/manifest.csv \
  --batch-size 32
```

The plot is saved next to the supplied weights file.

## optimise_energy.py

This script fine-tunes an existing classifier using an energy-regularized loss
and searches for its learning rate, weight decay, and energy threshold using
Optuna.

The training manifest must include OOD/resting recordings, represented internally
by label `-1`.

### Usage

```bash
python -m ml.optimise_energy \
  --weights ml/outputs/final_weights.pt \
  --train-manifest ml/data/augmented/train/manifest.csv \
  --trials 20 \
  --epochs 20 \
  --folds 2
```

The study is saved as JSON under `ml/outputs/optuna_energy` by default.

## finetune_energy.py

This script applies energy-based OOD training to an existing trained checkpoint.
It treats `resting` as the OOD label, trains for the requested number of epochs,
and saves energy-finetuned checkpoints and weights.

### Usage

```bash
python -m ml.finetune_energy \
  --weights ml/outputs/final_weights.pt \
  --train-manifest ml/data/augmented/train/manifest.csv \
  --epochs 20 \
  --output-directory ml/outputs/energy
```

Hyperparameters can be supplied through `--optuna-study`. The checkpoint
mapping preserves the original ID class labels; the OOD label is used only when
loading data for energy training.

## test_energy.py

This script evaluates a checkpoint with energy-based OOD rejection. It prints
ID/OOD energy statistics, per-class results, and the confusion matrix, and saves
`test_statistics.png`.

In the confusion matrix and per-class results, `resting` represents the OOD
class with internal label `-1`.

### Usage

```bash
python -m ml.test_energy \
  --weights ml/outputs/energy/final_weights.pt \
  --manifest ml/data/augmented/test/manifest.csv \
  --energy-threshold -17
```

Energy scores above the threshold are classified as OOD. The threshold, ID/OOD
score distributions, predictions, and recording-level metrics are included in
the output.

## test_live.py

This script connects to the FireBeetle Wi-Fi sensor stream and runs live
inference using a trained checkpoint. Predictions with confidence below 0.90 are
displayed as `Ignore`.

### Usage

```bash
python -m ml.test_live \
  --weights ml/outputs/final_weights.pt \
  --host 192.168.4.1 \
  --port 5000
```

The script normalizes incoming samples using `data/metadata/normalisation.json`
and maintains a 20-sample window for inference.

## export_model_onnx.py

This script exports a trained PyTorch checkpoint to ONNX.

### Usage

```bash
python -m ml.export_model_onnx \
  --weights ml/outputs/final_weights.pt \
  --output ml/exports/gesture_cnn \
  --opset 17
```

The exported model accepts float32 input with shape `[batch, 20, 11]` and
produces float32 logits with shape `[batch, number_of_classes]`.
The script also verifies the ONNX graph and, when ONNX Runtime is available,
compares one ONNX prediction against PyTorch.

## export_npy_test_data_ref_logits.py

This script exports normalized test windows as a NumPy array and creates matching
PyTorch reference logits for FPGA or bitstream validation.

### Usage

```bash
python -m ml.export_npy_test_data_ref_logits \
  --manifest ml/data/augmented/test/manifest.csv \
  --weights ml/outputs/final_weights.pt \
  --output ml/exports/fpga_test
```

The output includes:

- `test_data.npy` with shape `[N, 11, 20]`
- `ref_logits.npy` with one row of logits per test window

## export_c_weights_test_data.py

This script exports BatchNorm-folded PyTorch weights and test vectors as C/C++
headers for Vitis HLS. It also validates that the folded float implementation
matches the PyTorch model.

### Usage

```bash
python -m ml.export_c_weights_test_data \
  --weights ml/outputs/final_weights.pt \
  --test-data ml/exports/fpga_test/test_data.npy \
  --export ml/exports/fpga_headers
```

The output includes:

- `weights.h`
- `test_data.h`

Before deployment, verify fixed-point types and tensor ranges in
`gesture_cnn.h` and the exporter output.
