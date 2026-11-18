#pragma once

// Project hardware reports 0x70. Set to 0 for standard MPU6050 devices only.
// This affects ID validation only, not the register map or I2C address.
#ifndef CAPSTONE_MPU_ALLOW_ID_0X70
#define CAPSTONE_MPU_ALLOW_ID_0X70 1
#endif

#if CAPSTONE_MPU_ALLOW_ID_0X70 != 0 && CAPSTONE_MPU_ALLOW_ID_0X70 != 1
#error "CAPSTONE_MPU_ALLOW_ID_0X70 must be 0 or 1"
#endif
