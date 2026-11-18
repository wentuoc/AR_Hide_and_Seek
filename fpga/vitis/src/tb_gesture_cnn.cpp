#include <cmath>
#include <cstdio>

#include "gesture_cnn.h"
#include "test_data.h"  // N_TEST, TEST_X, TEST_LOGITS, TEST_PRED (from export_weights_pt.py)

// Tolerances for fixed-point vs. float reference. Tighten once accuracy is confirmed.
constexpr double MAX_LOGIT_ERR = 0.25;
constexpr int MAX_PRED_MISMATCHES = N_TEST / 20;  // allow up to 5%

int main() {
    int mismatches = 0;
    double worst_err = 0.0;

    for (int n = 0; n < N_TEST; n++) {
        act_t x[T0 * C0];
        logit_t logits[NCLASS];
        int pred = -1;

        for (int i = 0; i < T0 * C0; i++) x[i] = act_t(TEST_X[n][i]);

        gesture_cnn(x, pred, logits);

        for (int k = 0; k < NCLASS; k++) {
            double err = std::fabs((double)logits[k] - (double)TEST_LOGITS[n][k]);
            if (err > worst_err) {
            	worst_err = err;
            }
        }
        std::printf("\n");
        if (pred != TEST_PRED[n]) {
            mismatches++;
            std::printf("window %d: HLS pred=%d, PyTorch pred=%d\n", n, pred, TEST_PRED[n]);
        }
    }

    std::printf("windows=%d  pred mismatches=%d  worst logit error=%.4f\n", N_TEST, mismatches,
                worst_err);

    // A nonzero return value makes C simulation and co-simulation report FAIL.
    bool ok = (mismatches <= MAX_PRED_MISMATCHES) && (worst_err <= MAX_LOGIT_ERR);
    std::printf(ok ? "TEST PASSED\n" : "TEST FAILED\n");
    return ok ? 0 : 1;
}
