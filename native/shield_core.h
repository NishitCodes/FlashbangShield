#ifndef SHIELD_CORE_H
#define SHIELD_CORE_H

#ifdef __cplusplus
extern "C" {
#endif

#ifdef SHIELD_CORE_EXPORTS
#define SHIELD_API __declspec(dllexport)
#else
#define SHIELD_API __declspec(dllimport)
#endif

// Telemetry & measurement snapshot returned per frame
typedef struct {
    int monitor_id;             // Physical monitor index
    float mean_linear_lum;      // Normalized linear luminance [0.0..1.0]
    float p90_linear_lum;       // 90th percentile linear luminance [0.0..1.0]
    float effective_lum;        // Blended 0.60*mean + 0.40*p90
    float mean_nits;            // Physical estimated luminance in nits
    float peak_nits;            // Peak sampled luminance in nits
    float sdr_white_nits;       // Display SDR white level in nits (e.g. 200.0)
    int is_hdr;                 // 1 if HDR / Advanced Color is active, 0 for SDR
    int has_new_frame;          // 1 if screen changed, 0 if static (timeout / PSR active)
    float capture_latency_ms;   // GPU downsample + readback time in milliseconds
} ShieldLuminanceResult;

// Lifecycle management
SHIELD_API int ShieldCore_Init();
SHIELD_API void ShieldCore_Shutdown();

// Monitor enumeration & HDR inspection
SHIELD_API int ShieldCore_GetMonitorCount();
SHIELD_API int ShieldCore_GetMonitorHDRInfo(int monitor_index, int* out_is_hdr, float* out_sdr_white_nits, float* out_max_nits);

// Capture session management
SHIELD_API int ShieldCore_StartCapture(int monitor_index, int prefer_hdr);
SHIELD_API int ShieldCore_Sample(int monitor_index, unsigned int timeout_ms, ShieldLuminanceResult* out_result);
SHIELD_API void ShieldCore_StopCapture(int monitor_index);

// Query full-screen 3D gaming state (QUNS_RUNNING_D3D_FULL_SCREEN)
SHIELD_API int ShieldCore_IsFullscreenGameActive();

#ifdef __cplusplus
}
#endif

#endif // SHIELD_CORE_H
