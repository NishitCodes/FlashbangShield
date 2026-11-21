#define SHIELD_CORE_EXPORTS
#define NOMINMAX
#include "shield_core.h"

#include <windows.h>
#include <shellapi.h>
#include <d3d11.h>
#include <dxgi1_6.h>
#include <d3dcompiler.h>

#include <vector>
#include <memory>
#include <chrono>
#include <cmath>
#include <algorithm>
#include <iostream>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "dxguid.lib")
#pragma comment(lib, "d3dcompiler.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "shell32.lib")

// Precomputed sRGB to linear float LUT (IEC 61966-2-1)
static float s_srgb_to_linear[256];
static bool s_lut_initialized = false;

static void InitSRGBLUT() {
    if (s_lut_initialized) return;
    for (int i = 0; i < 256; ++i) {
        float c = (float)i / 255.0f;
        if (c <= 0.04045f) {
            s_srgb_to_linear[i] = c / 12.92f;
        } else {
            s_srgb_to_linear[i] = std::pow((c + 0.055f) / 1.055f, 2.4f);
        }
    }
    s_lut_initialized = true;
}

// Convert IEEE-754 16-bit half-precision float to single-precision float
static float HalfToFloat(uint16_t h) {
    uint32_t sign = (h >> 15) & 0x00000001;
    uint32_t exp  = (h >> 10) & 0x0000001F;
    uint32_t mant = h & 0x000003FF;

    if (exp == 0) {
        if (mant == 0) {
            uint32_t res = (sign << 31);
            return *(float*)&res;
        }
        while (!(mant & 0x00000400)) {
            mant <<= 1;
            exp -= 1;
        }
        exp += 1;
        mant &= ~0x00000400;
    } else if (exp == 31) {
        uint32_t res = (sign << 31) | 0x7F800000 | (mant << 13);
        return *(float*)&res;
    }

    exp = exp + (127 - 15);
    mant = mant << 13;
    uint32_t res = (sign << 31) | (exp << 23) | mant;
    return *(float*)&res;
}

// Minimal HLSL Shaders for GPU downsampling
static const char* s_hlsl_code = R"(
Texture2D g_Texture : register(t0);
SamplerState g_Sampler : register(s0);

struct VS_OUTPUT {
    float4 Pos : SV_POSITION;
    float2 Tex : TEXCOORD0;
};

VS_OUTPUT VS_Quad(uint id : SV_VertexID) {
    VS_OUTPUT output;
    output.Tex = float2((id << 1) & 2, id & 2);
    output.Pos = float4(output.Tex * float2(2.0f, -2.0f) + float2(-1.0f, 1.0f), 0.0f, 1.0f);
    return output;
}

float4 PS_Downsample(VS_OUTPUT input) : SV_Target {
    return g_Texture.Sample(g_Sampler, input.Tex);
}
)";

// Per-monitor DXGI capture pipeline context
struct MonitorCaptureContext {
    int monitor_id = 0;
    HMONITOR hmonitor = NULL;
    bool is_hdr = false;
    float sdr_white_nits = 200.0f;
    float max_nits = 1000.0f;
    DXGI_FORMAT capture_format = DXGI_FORMAT_B8G8R8A8_UNORM;

    ID3D11Device* pDevice = nullptr;
    ID3D11DeviceContext* pContext = nullptr;
    IDXGIOutputDuplication* pDuplication = nullptr;

    // GPU downsample resources (64x36)
    ID3D11Texture2D* pRenderTargetTex = nullptr;
    ID3D11RenderTargetView* pRTV = nullptr;
    ID3D11Texture2D* pStagingTex = nullptr;
    ID3D11SamplerState* pSampler = nullptr;
    ID3D11VertexShader* pVS = nullptr;
    ID3D11PixelShader* pPS = nullptr;

    // Cached telemetry
    float last_mean = 0.0f;
    float last_p90 = 0.0f;
    float last_eff = 0.0f;
    float last_mean_nits = 0.0f;
    float last_peak_nits = 0.0f;

    void Release() {
        if (pDuplication) { pDuplication->Release(); pDuplication = nullptr; }
        if (pStagingTex) { pStagingTex->Release(); pStagingTex = nullptr; }
        if (pRTV) { pRTV->Release(); pRTV = nullptr; }
        if (pRenderTargetTex) { pRenderTargetTex->Release(); pRenderTargetTex = nullptr; }
        if (pSampler) { pSampler->Release(); pSampler = nullptr; }
        if (pVS) { pVS->Release(); pVS = nullptr; }
        if (pPS) { pPS->Release(); pPS = nullptr; }
        if (pContext) { pContext->Release(); pContext = nullptr; }
        if (pDevice) { pDevice->Release(); pDevice = nullptr; }
    }
};

static std::vector<std::unique_ptr<MonitorCaptureContext>> s_monitors;
static bool s_initialized = false;

// Query Windows Advanced Color / HDR state and physical SDR white level
static void QueryMonitorHDRInfo(HMONITOR hMon, bool& out_is_hdr, float& out_sdr_white, float& out_max_nits) {
    out_is_hdr = false;
    out_sdr_white = 200.0f;
    out_max_nits = 1000.0f;

    UINT32 pathCount = 0, modeCount = 0;
    if (GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, &pathCount, &modeCount) != ERROR_SUCCESS) {
        return;
    }

    std::vector<DISPLAYCONFIG_PATH_INFO> paths(pathCount);
    std::vector<DISPLAYCONFIG_MODE_INFO> modes(modeCount);

    if (QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, &pathCount, paths.data(), &modeCount, modes.data(), NULL) != ERROR_SUCCESS) {
        return;
    }

    MONITORINFOEXW mi;
    mi.cbSize = sizeof(mi);
    if (!GetMonitorInfoW(hMon, &mi)) return;

    for (const auto& path : paths) {
        DISPLAYCONFIG_SOURCE_DEVICE_NAME sourceName = {};
        sourceName.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME;
        sourceName.header.size = sizeof(sourceName);
        sourceName.header.adapterId = path.sourceInfo.adapterId;
        sourceName.header.id = path.sourceInfo.id;

        if (DisplayConfigGetDeviceInfo(&sourceName.header) == ERROR_SUCCESS) {
            if (wcscmp(sourceName.viewGdiDeviceName, mi.szDevice) == 0) {
                // Check Advanced Color
                DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO advColor = {};
                advColor.header.type = DISPLAYCONFIG_DEVICE_INFO_TYPE(9); // GET_ADVANCED_COLOR_INFO
                advColor.header.size = sizeof(advColor);
                advColor.header.adapterId = path.targetInfo.adapterId;
                advColor.header.id = path.targetInfo.id;

                if (DisplayConfigGetDeviceInfo(&advColor.header) == ERROR_SUCCESS) {
                    out_is_hdr = (advColor.value & 0x1) != 0; // advancedColorEnabled
                }

                // Check SDR White Level
                DISPLAYCONFIG_SDR_WHITE_LEVEL sdrWhite = {};
                sdrWhite.header.type = DISPLAYCONFIG_DEVICE_INFO_TYPE(11); // GET_SDR_WHITE_LEVEL
                sdrWhite.header.size = sizeof(sdrWhite);
                sdrWhite.header.adapterId = path.targetInfo.adapterId;
                sdrWhite.header.id = path.targetInfo.id;

                if (DisplayConfigGetDeviceInfo(&sdrWhite.header) == ERROR_SUCCESS && sdrWhite.SDRWhiteLevel > 0) {
                    out_sdr_white = ((float)sdrWhite.SDRWhiteLevel / 1000.0f) * 80.0f;
                }
                break;
            }
        }
    }
}

// Monitor enumeration callback
static BOOL CALLBACK EnumMonitorsProc(HMONITOR hMon, HDC hdc, LPRECT rc, LPARAM lParam) {
    auto pCtx = std::make_unique<MonitorCaptureContext>();
    pCtx->monitor_id = (int)s_monitors.size();
    pCtx->hmonitor = hMon;
    QueryMonitorHDRInfo(hMon, pCtx->is_hdr, pCtx->sdr_white_nits, pCtx->max_nits);
    s_monitors.push_back(std::move(pCtx));
    return TRUE;
}

// ----------------------------------------------------------------------------
// Exported C API
// ----------------------------------------------------------------------------

SHIELD_API int ShieldCore_Init() {
    HDESK hDesk = OpenInputDesktop(0, FALSE, 0x01FF);
    if (hDesk) {
        SetThreadDesktop(hDesk);
        CloseDesktop(hDesk);
    }
    InitSRGBLUT();
    s_monitors.clear();
    EnumDisplayMonitors(NULL, NULL, EnumMonitorsProc, 0);
    s_initialized = true;
    return (int)s_monitors.size();
}

SHIELD_API void ShieldCore_Shutdown() {
    for (auto& ctx : s_monitors) {
        ctx->Release();
    }
    s_monitors.clear();
    s_initialized = false;
}

SHIELD_API int ShieldCore_GetMonitorCount() {
    return (int)s_monitors.size();
}

SHIELD_API int ShieldCore_GetMonitorHDRInfo(int monitor_index, int* out_is_hdr, float* out_sdr_white_nits, float* out_max_nits) {
    if (monitor_index < 0 || monitor_index >= (int)s_monitors.size()) return 0;
    const auto& ctx = s_monitors[monitor_index];
    if (out_is_hdr) *out_is_hdr = ctx->is_hdr ? 1 : 0;
    if (out_sdr_white_nits) *out_sdr_white_nits = ctx->sdr_white_nits;
    if (out_max_nits) *out_max_nits = ctx->max_nits;
    return 1;
}

SHIELD_API int ShieldCore_IsFullscreenGameActive() {
    QUERY_USER_NOTIFICATION_STATE state;
    if (SHQueryUserNotificationState(&state) == S_OK) {
        if (state == QUNS_RUNNING_D3D_FULL_SCREEN) {
            return 1;
        }
    }
    return 0;
}

SHIELD_API int ShieldCore_StartCapture(int monitor_index, int prefer_hdr) {
    if (monitor_index < 0 || monitor_index >= (int)s_monitors.size()) return 0;
    auto& ctx = s_monitors[monitor_index];
    ctx->Release();

    HDESK hDesk = OpenInputDesktop(0, FALSE, 0x01FF);
    if (hDesk) {
        SetThreadDesktop(hDesk);
        CloseDesktop(hDesk);
    }

    // Enumerate DXGI outputs to match HMONITOR
    IDXGIFactory1* pFactory = nullptr;
    if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&pFactory))) {
        return 0;
    }

    IDXGIAdapter1* pAdapter = nullptr;
    IDXGIOutput* pTargetOutput = nullptr;

    for (UINT aIdx = 0; pFactory->EnumAdapters1(aIdx, &pAdapter) != DXGI_ERROR_NOT_FOUND; ++aIdx) {
        IDXGIOutput* pOutput = nullptr;
        for (UINT oIdx = 0; pAdapter->EnumOutputs(oIdx, &pOutput) != DXGI_ERROR_NOT_FOUND; ++oIdx) {
            DXGI_OUTPUT_DESC oDesc;
            pOutput->GetDesc(&oDesc);
            if (oDesc.Monitor == ctx->hmonitor || s_monitors.size() == 1) {
                pTargetOutput = pOutput;
                break;
            }
            pOutput->Release();
        }
        if (pTargetOutput) break;
        pAdapter->Release();
        pAdapter = nullptr;
    }
    pFactory->Release();

    if (!pTargetOutput || !pAdapter) {
        if (pAdapter) pAdapter->Release();
        return 0;
    }

    // Create D3D11 Device on the adapter owning this output
    D3D_FEATURE_LEVEL featureLevels[] = { D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0 };
    D3D_FEATURE_LEVEL featureLevel;
    UINT createFlags = D3D11_CREATE_DEVICE_BGRA_SUPPORT;

    HRESULT hr = D3D11CreateDevice(
        pAdapter, D3D_DRIVER_TYPE_UNKNOWN, NULL, createFlags,
        featureLevels, 2, D3D11_SDK_VERSION,
        &ctx->pDevice, &featureLevel, &ctx->pContext
    );
    pAdapter->Release();

    if (FAILED(hr)) {
        pTargetOutput->Release();
        return 0;
    }

    // Try DuplicateOutput1 (Windows 10 1803+) for HDR FP16 scRGB capture
    IDXGIOutput5* pOutput5 = nullptr;
    ctx->capture_format = DXGI_FORMAT_B8G8R8A8_UNORM;

    if (prefer_hdr && ctx->is_hdr && SUCCEEDED(pTargetOutput->QueryInterface(__uuidof(IDXGIOutput5), (void**)&pOutput5))) {
        DXGI_FORMAT supportedFormats[] = { DXGI_FORMAT_R16G16B16A16_FLOAT, DXGI_FORMAT_B8G8R8A8_UNORM };
        hr = pOutput5->DuplicateOutput1(ctx->pDevice, 0, 2, supportedFormats, &ctx->pDuplication);
        if (SUCCEEDED(hr)) {
            ctx->capture_format = DXGI_FORMAT_R16G16B16A16_FLOAT;
        }
        pOutput5->Release();
    }

    // Fallback to standard DuplicateOutput
    if (!ctx->pDuplication) {
        IDXGIOutput1* pOutput1 = nullptr;
        if (SUCCEEDED(pTargetOutput->QueryInterface(__uuidof(IDXGIOutput1), (void**)&pOutput1))) {
            pOutput1->DuplicateOutput(ctx->pDevice, &ctx->pDuplication);
            pOutput1->Release();
        }
    }
    pTargetOutput->Release();

    if (!ctx->pDuplication) {
        ctx->Release();
        return 0;
    }

    // Compile downsample shaders
    ID3DBlob* pVSBlob = nullptr;
    ID3DBlob* pPSBlob = nullptr;
    ID3DBlob* pErrorBlob = nullptr;

    hr = D3DCompile(s_hlsl_code, strlen(s_hlsl_code), "VS", NULL, NULL, "VS_Quad", "vs_5_0", 0, 0, &pVSBlob, &pErrorBlob);
    if (FAILED(hr)) { ctx->Release(); return 0; }

    hr = D3DCompile(s_hlsl_code, strlen(s_hlsl_code), "PS", NULL, NULL, "PS_Downsample", "ps_5_0", 0, 0, &pPSBlob, &pErrorBlob);
    if (FAILED(hr)) { pVSBlob->Release(); ctx->Release(); return 0; }

    ctx->pDevice->CreateVertexShader(pVSBlob->GetBufferPointer(), pVSBlob->GetBufferSize(), NULL, &ctx->pVS);
    ctx->pDevice->CreatePixelShader(pPSBlob->GetBufferPointer(), pPSBlob->GetBufferSize(), NULL, &ctx->pPS);
    pVSBlob->Release();
    pPSBlob->Release();

    // Create 64x36 Render Target Texture
    D3D11_TEXTURE2D_DESC rtDesc = {};
    rtDesc.Width = 64;
    rtDesc.Height = 36;
    rtDesc.MipLevels = 1;
    rtDesc.ArraySize = 1;
    rtDesc.Format = ctx->capture_format;
    rtDesc.SampleDesc.Count = 1;
    rtDesc.Usage = D3D11_USAGE_DEFAULT;
    rtDesc.BindFlags = D3D11_BIND_RENDER_TARGET;

    ctx->pDevice->CreateTexture2D(&rtDesc, NULL, &ctx->pRenderTargetTex);
    ctx->pDevice->CreateRenderTargetView(ctx->pRenderTargetTex, NULL, &ctx->pRTV);

    // Create 64x36 Staging Texture for CPU Readback
    D3D11_TEXTURE2D_DESC stDesc = rtDesc;
    stDesc.BindFlags = 0;
    stDesc.Usage = D3D11_USAGE_STAGING;
    stDesc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    ctx->pDevice->CreateTexture2D(&stDesc, NULL, &ctx->pStagingTex);

    // Bilinear Sampler
    D3D11_SAMPLER_DESC sampDesc = {};
    sampDesc.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sampDesc.AddressU = D3D11_TEXTURE_ADDRESS_CLAMP;
    sampDesc.AddressV = D3D11_TEXTURE_ADDRESS_CLAMP;
    sampDesc.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    ctx->pDevice->CreateSamplerState(&sampDesc, &ctx->pSampler);

    return 1;
}

SHIELD_API int ShieldCore_Sample(int monitor_index, unsigned int timeout_ms, ShieldLuminanceResult* out_result) {
    if (!out_result || monitor_index < 0 || monitor_index >= (int)s_monitors.size()) return 0;
    auto& ctx = s_monitors[monitor_index];
    if (!ctx->pDuplication) return 0;

    auto t0 = std::chrono::high_resolution_clock::now();

    DXGI_OUTDUPL_FRAME_INFO frameInfo;
    IDXGIResource* pDesktopResource = nullptr;

    HRESULT hr = ctx->pDuplication->AcquireNextFrame(timeout_ms, &frameInfo, &pDesktopResource);

    if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
        // Screen is static! Zero CPU/GPU work needed, PSR preserved
        out_result->monitor_id = monitor_index;
        out_result->mean_linear_lum = ctx->last_mean;
        out_result->p90_linear_lum = ctx->last_p90;
        out_result->effective_lum = ctx->last_eff;
        out_result->mean_nits = ctx->last_mean_nits;
        out_result->peak_nits = ctx->last_peak_nits;
        out_result->sdr_white_nits = ctx->sdr_white_nits;
        out_result->is_hdr = (ctx->capture_format == DXGI_FORMAT_R16G16B16A16_FLOAT) ? 1 : 0;
        out_result->has_new_frame = 0;
        out_result->capture_latency_ms = 0.0f;
        return 1;
    }

    if (FAILED(hr) || !pDesktopResource) {
        if (hr == DXGI_ERROR_ACCESS_LOST) {
            // Mode switch, resolution change, or UAC desktop switch
            ShieldCore_StartCapture(monitor_index, ctx->is_hdr ? 1 : 0);
        }
        return 0;
    }

    ID3D11Texture2D* pDesktopTex = nullptr;
    pDesktopResource->QueryInterface(__uuidof(ID3D11Texture2D), (void**)&pDesktopTex);
    pDesktopResource->Release();

    if (!pDesktopTex) {
        ctx->pDuplication->ReleaseFrame();
        return 0;
    }

    // Create SRV for the desktop texture to sample into 64x36
    ID3D11ShaderResourceView* pDesktopSRV = nullptr;
    ctx->pDevice->CreateShaderResourceView(pDesktopTex, NULL, &pDesktopSRV);
    pDesktopTex->Release();

    if (pDesktopSRV) {
        // Set up 64x36 viewport
        D3D11_VIEWPORT vp = { 0.0f, 0.0f, 64.0f, 36.0f, 0.0f, 1.0f };
        ctx->pContext->RSSetViewports(1, &vp);
        ctx->pContext->OMSetRenderTargets(1, &ctx->pRTV, NULL);
        ctx->pContext->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        ctx->pContext->VSSetShader(ctx->pVS, NULL, 0);
        ctx->pContext->PSSetShader(ctx->pPS, NULL, 0);
        ctx->pContext->PSSetShaderResources(0, 1, &pDesktopSRV);
        ctx->pContext->PSSetSamplers(0, 1, &ctx->pSampler);

        // Execute GPU downsample draw
        ctx->pContext->Draw(3, 0);

        // Unbind SRV to allow frame release
        ID3D11ShaderResourceView* nullSRV = nullptr;
        ctx->pContext->PSSetShaderResources(0, 1, &nullSRV);
        pDesktopSRV->Release();

        // Copy 64x36 result to staging buffer for CPU readback
        ctx->pContext->CopyResource(ctx->pStagingTex, ctx->pRenderTargetTex);
    }

    ctx->pDuplication->ReleaseFrame();

    // Map staging texture to calculate luminance
    D3D11_MAPPED_SUBRESOURCE mapped;
    hr = ctx->pContext->Map(ctx->pStagingTex, 0, D3D11_MAP_READ, 0, &mapped);
    if (FAILED(hr)) return 0;

    const int total_pixels = 64 * 36;
    std::vector<float> lum_values;
    lum_values.reserve(total_pixels);

    float sum_lum = 0.0f;
    float peak_nits = 0.0f;
    bool is_fp16 = (ctx->capture_format == DXGI_FORMAT_R16G16B16A16_FLOAT);

    if (is_fp16) {
        // Linear scRGB FP16: 1.0 = 80 nits
        for (int y = 0; y < 36; ++y) {
            const uint16_t* pRow = (const uint16_t*)((const uint8_t*)mapped.pData + y * mapped.RowPitch);
            for (int x = 0; x < 64; ++x) {
                float r = HalfToFloat(pRow[x * 4 + 0]);
                float g = HalfToFloat(pRow[x * 4 + 1]);
                float b = HalfToFloat(pRow[x * 4 + 2]);
                float lin_y = (0.2126f * r + 0.7152f * g + 0.0722f * b);
                if (lin_y < 0.0f) lin_y = 0.0f;

                float nits = lin_y * 80.0f;
                if (nits > peak_nits) peak_nits = nits;

                // Normalize to [0..1] relative to monitor peak nits
                float norm_lum = std::min(1.0f, nits / std::max(100.0f, ctx->max_nits));
                lum_values.push_back(norm_lum);
                sum_lum += norm_lum;
            }
        }
    } else {
        // Standard 8-bit sRGB BGRA: convert through linear LUT
        for (int y = 0; y < 36; ++y) {
            const uint8_t* pRow = (const uint8_t*)mapped.pData + y * mapped.RowPitch;
            for (int x = 0; x < 64; ++x) {
                float b = s_srgb_to_linear[pRow[x * 4 + 0]];
                float g = s_srgb_to_linear[pRow[x * 4 + 1]];
                float r = s_srgb_to_linear[pRow[x * 4 + 2]];

                float lin_y = 0.2126f * r + 0.7152f * g + 0.0722f * b;
                lum_values.push_back(lin_y);
                sum_lum += lin_y;

                float nits = lin_y * ctx->sdr_white_nits;
                if (nits > peak_nits) peak_nits = nits;
            }
        }
    }

    ctx->pContext->Unmap(ctx->pStagingTex, 0);

    // Compute mean and p90
    float mean_lum = sum_lum / (float)total_pixels;
    std::nth_element(lum_values.begin(), lum_values.begin() + (int)(total_pixels * 0.90f), lum_values.end());
    float p90_lum = lum_values[(int)(total_pixels * 0.90f)];
    float effective_lum = 0.60f * mean_lum + 0.40f * p90_lum;

    ctx->last_mean = mean_lum;
    ctx->last_p90 = p90_lum;
    ctx->last_eff = effective_lum;
    ctx->last_mean_nits = effective_lum * (is_fp16 ? ctx->max_nits : ctx->sdr_white_nits);
    ctx->last_peak_nits = peak_nits;

    auto t1 = std::chrono::high_resolution_clock::now();
    float latency_ms = std::chrono::duration<float, std::milli>(t1 - t0).count();

    out_result->monitor_id = monitor_index;
    out_result->mean_linear_lum = mean_lum;
    out_result->p90_linear_lum = p90_lum;
    out_result->effective_lum = effective_lum;
    out_result->mean_nits = ctx->last_mean_nits;
    out_result->peak_nits = peak_nits;
    out_result->sdr_white_nits = ctx->sdr_white_nits;
    out_result->is_hdr = is_fp16 ? 1 : 0;
    out_result->has_new_frame = 1;
    out_result->capture_latency_ms = latency_ms;

    return 1;
}

SHIELD_API void ShieldCore_StopCapture(int monitor_index) {
    if (monitor_index >= 0 && monitor_index < (int)s_monitors.size()) {
        s_monitors[monitor_index]->Release();
    }
}
