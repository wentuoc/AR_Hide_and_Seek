# Capstone hardware compatibility

Base: Adafruit MPU6050 2.2.9, copied from the project's installed Arduino library.
Upstream: https://github.com/adafruit/Adafruit_MPU6050/tree/2.2.9
The upstream license and complete installed library contents are preserved.

The installed copy had replaced MPU6050_DEVICE_ID (0x68) with 0x70.
This vendored copy restores the standard ID and changes begin() to accept
0x70 additionally when CAPSTONE_MPU_ALLOW_ID_0X70 is 1. Other IDs are rejected.
The option lives in CapstoneMpuCompatibility.h and is enabled for this project.
Set it to 0 for standard devices only. A sketch-local #define cannot configure
the separately compiled driver; edit that header or pass a compiler-wide flag.

WHO_AM_I remains register 0x75; the default I2C address remains 0x68.
No register map, conversion scale, or acquisition changes were made.
The ID alone does not identify the device or prove full compatibility.

Hardware evidence: the project owner reports using the previous 0x70 patch
with their module. This revised dual-ID check has not been tested on hardware.
All three project sketches compile for esp32:esp32:firebeetle32 with ESP32
core 3.2.0 and the dependency versions pinned in scripts/setup-arduino.sh;
compiler output confirms it selects this vendored driver.

Use the repository build script to explicitly select this copy. Do not replace
it with a Library Manager update without reapplying and reviewing this patch.
