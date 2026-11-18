#ifndef GESTURE_CNN_H
#define GESTURE_CNN_H

#include <ap_fixed.h>
#include <ap_int.h>

// ---------------------------------------------------------------------------
// Model dimensions (must match the trained network)
// ---------------------------------------------------------------------------
constexpr int T0 = 20;          // input window length (samples)
constexpr int C0 = 11;          // 6 IMU + 5 flex channels
constexpr int C1 = 32, K1 = 5;  // conv1
constexpr int C2 = 64, K2 = 3;  // conv2
constexpr int C3 = 64, K3 = 3;  // conv3
constexpr int T1 = T0 / 2;      // length after pool 1
constexpr int T2 = T1 / 2;      // length after pool 2
constexpr int NFEAT = 2 * C3;   // [global avg | global max]
constexpr int FC1 = 64;
constexpr int NCLASS = 7;
static_assert(T0 % 4 == 0, "T0 must be divisible by 4 (two MaxPool(2) layers)");

// ---------------------------------------------------------------------------
// Fixed-point types. Start here, then run a precision sweep in C simulation.
// AP_SAT matters: without it, overflow wraps around and silently corrupts results.
// ---------------------------------------------------------------------------
typedef ap_fixed<16, 6, AP_RND, AP_SAT> act_t;  	// activations
typedef ap_fixed<16, 4, AP_RND, AP_SAT> wgt_t;  	// weights (check range with export script)
typedef ap_fixed<40, 18> acc_t;                 	// accumulators (wide enough for 192 MACs)
typedef ap_fixed<16, 7, AP_RND, AP_SAT> logit_t;	// output logits (wider to prevent saturation)

// Parallelism knobs: initiation interval of the output-channel loops.
// II=1 gives the most parallel (and most DSP-hungry) hardware; higher II shares multipliers.
#define CONV_II 4
#define DENSE_II 4

// ---------------------------------------------------------------------------
// Layers
// ---------------------------------------------------------------------------

// 1D convolution ("same" padding, stride 1) with bias and fused ReLU.
// BatchNorm is already folded into w and b by the export script.
// Weight layout matches Keras: w[K][CIN][COUT].
template <int T, int CIN, int COUT, int K>
void conv1d_relu(const act_t in[T][CIN], const wgt_t w[K][CIN][COUT],
                 const wgt_t b[COUT], act_t out[T][COUT]) {
#pragma HLS ARRAY_PARTITION variable = in complete dim = 2
#pragma HLS ARRAY_PARTITION variable = w complete dim = 1
#pragma HLS ARRAY_PARTITION variable = w complete dim = 2
    constexpr int PAD = K / 2;
    for (int t = 0; t < T; t++) {
        for (int co = 0; co < COUT; co++) {
#pragma HLS PIPELINE II = CONV_II
            acc_t acc = b[co];
            for (int k = 0; k < K; k++) {
                int ti = t + k - PAD;
                if (ti >= 0 && ti < T) {  // zero padding at the borders
                    for (int ci = 0; ci < CIN; ci++) {
                        acc += in[ti][ci] * w[k][ci][co];
                    }
                }
            }
            out[t][co] = (acc > 0) ? act_t(acc) : act_t(0);
        }
    }
}

// MaxPool(2), stride 2 along time.
template <int T, int C>
void maxpool2(const act_t in[T][C], act_t out[T / 2][C]) {
#pragma HLS ARRAY_PARTITION variable = in complete dim = 1
    for (int t = 0; t < T / 2; t++) {
        for (int c = 0; c < C; c++) {
#pragma HLS PIPELINE II = 1
            act_t a = in[2 * t][c];
            act_t b = in[2 * t + 1][c];
            out[t][c] = (a > b) ? a : b;
        }
    }
}

// Global average pool and global max pool over time, concatenated: [avg | max].
template <int T, int C>
void global_pool(const act_t in[T][C], act_t feat[2 * C]) {
#pragma HLS ARRAY_PARTITION variable = in complete dim = 1
    const acc_t inv = acc_t(1.0 / T);  // multiply by constant instead of dividing
    for (int c = 0; c < C; c++) {
#pragma HLS PIPELINE II = 1
        acc_t sum = 0;
        act_t mx = in[0][c];
        for (int t = 0; t < T; t++) {
            sum += in[t][c];
            if (in[t][c] > mx) mx = in[t][c];
        }
        feat[c] = act_t(sum * inv);
        feat[C + c] = mx;
    }
}

// Fully connected layer, optional ReLU. Weight layout matches Keras: w[NIN][NOUT].
template <int NIN, int NOUT, bool RELU, typename OUT_T = act_t>
void dense(const act_t in[NIN], const wgt_t w[NIN][NOUT], const wgt_t b[NOUT],
           OUT_T out[NOUT]) {
#pragma HLS ARRAY_PARTITION variable = in complete
#pragma HLS ARRAY_PARTITION variable = w complete dim = 1
    for (int o = 0; o < NOUT; o++) {
#pragma HLS PIPELINE II = DENSE_II
        acc_t acc = b[o];
        for (int i = 0; i < NIN; i++) {
            acc += in[i] * w[i][o];
        }
        out[o] = (RELU && acc < 0) ? OUT_T(0) : OUT_T(acc);
    }
}


// Top-level function (defined in gesture_cnn.cpp).
// x_in: normalized window, row-major [T0][C0] flattened. pred: class index.
void gesture_cnn(const act_t x_in[T0 * C0], int &pred, logit_t logits[NCLASS]);

#endif  // GESTURE_CNN_H
