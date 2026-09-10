// N-API 绑定层：把 C++ 声纹门控 SDK（src/）暴露为 Node/Electron 可直接 require 的
// .node addon（NAPI_VERSION=8，ABI 稳定，Node 18+ / Electron 41 免重编译）。
//
// 模块导出两个入口（与 sdk/web 双入口一一对应，宿主可无感切换后端）：
//
// 1) createGate(config) -> Promise<Gate>          完整版（VAD 切段 + 门控）
//    Gate（ObjectWrap，持 shared_ptr<SpeakerGate>）：
//      beginEnroll()                 -> Promise<void>      建注册 VAD（含模型加载，异步）
//      enrollChunk(Float32Array)     -> Promise<{speechSeconds, enough}>
//      finishEnroll()                -> Promise<{speechSeconds}>
//      accept(Float32Array)          -> Promise<GateSegmentEvent[]>
//      flush()                       -> Promise<GateSegmentEvent[]>
//      reset()                       -> Promise<void>
//      dispose()                     -> Promise<void>      释放原生对象
//      enrolled / effectiveThreshold -> getter（同步）
//    事件字段与 sdk/web 的 GateSegmentEvent 一一对应（samples 为 float32 原始精度）。
//
// 2) createFilter(config) -> Promise<Filter>      core 版（无 VAD，内网场景）
//    Filter（ObjectWrap，持 shared_ptr<VoiceFilter>）：
//      enroll(Float32Array)          -> Promise<{speechSeconds}>
//      judge(Float32Array)           -> Promise<{similarity, accepted, durationSeconds}>
//      dispose()                     -> Promise<void>
//      enrolled / effectiveThreshold -> getter（同步）
//    （filter() 便捷方法在 index.js 由 judge 组合出，与 web API 同签名）
//
// 推理均在 libuv 工作线程执行（AsyncWorker），不阻塞 JS 线程；C++ SDK 要求串行
// 调用，index.js 包装层用 promise 链保证——直接使用本 addon 时调用方需自行串行。
#include <cstring>

#include <exception>
#include <functional>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include <napi.h>

#include "audiotse/core/voice_filter.hpp"
#include "audiotse/full/speaker_gate.hpp"

namespace {

using audiotse::EnrollProgress;
using audiotse::EnrollResult;
using audiotse::GateSegmentEvent;
using audiotse::JudgeResult;
using audiotse::SpeakerGate;
using audiotse::SpeakerGateConfig;
using audiotse::VoiceFilter;
using audiotse::VoiceFilterConfig;

// ── 配置解析（字段名与 sdk/web 的 SpeakerGateConfig 对齐）──────────────────

std::string RequireString(const Napi::Object &options, const char *key) {
    Napi::Value value = options.Get(key);
    if (!value.IsString()) {
        throw Napi::TypeError::New(options.Env(), std::string("config.") + key + " 必须是字符串（模型路径）");
    }
    return value.As<Napi::String>().Utf8Value();
}

template <typename T>
void OptionNumber(const Napi::Object &options, const char *key, T *out) {
    Napi::Value value = options.Get(key);
    if (value.IsUndefined()) return;
    if (!value.IsNumber()) {
        throw Napi::TypeError::New(options.Env(), std::string("config.") + key + " 必须是数字");
    }
    *out = static_cast<T>(value.As<Napi::Number>().DoubleValue());
}

SpeakerGateConfig ParseConfig(const Napi::Value &value) {
    if (!value.IsObject()) {
        throw Napi::TypeError::New(value.Env(), "createGate 需要 config 对象（vadModel / speakerModel 必填）");
    }
    Napi::Object options = value.As<Napi::Object>();
    SpeakerGateConfig config;
    config.vad_model = RequireString(options, "vadModel");
    config.speaker_model = RequireString(options, "speakerModel");
    OptionNumber(options, "threshold", &config.threshold);
    OptionNumber(options, "enrollTargetSpeechSeconds", &config.enroll_target_speech_seconds);
    OptionNumber(options, "enrollMinSpeechSeconds", &config.enroll_min_speech_seconds);
    OptionNumber(options, "shortEnrollThresholdFactor", &config.short_enroll_threshold_factor);
    Napi::Value vad = options.Get("vad");
    if (vad.IsObject()) {
        Napi::Object vad_options = vad.As<Napi::Object>();
        OptionNumber(vad_options, "threshold", &config.vad_threshold);
        OptionNumber(vad_options, "minSilenceDuration", &config.min_silence_duration);
        OptionNumber(vad_options, "minSpeechDuration", &config.min_speech_duration);
        OptionNumber(vad_options, "maxSpeechDuration", &config.max_speech_duration);
    }
    return config;
}

// ── 结果转 JS（字段名与 sdk/web 一致）──────────────────────────────────────

Napi::Value ProgressToJs(Napi::Env env, const EnrollProgress &progress) {
    Napi::Object out = Napi::Object::New(env);
    out.Set("speechSeconds", Napi::Number::New(env, progress.speech_seconds));
    out.Set("enough", Napi::Boolean::New(env, progress.enough));
    return out;
}

Napi::Value ResultToJs(Napi::Env env, const EnrollResult &result) {
    Napi::Object out = Napi::Object::New(env);
    out.Set("speechSeconds", Napi::Number::New(env, result.speech_seconds));
    return out;
}

Napi::Value JudgeToJs(Napi::Env env, const JudgeResult &result) {
    Napi::Object out = Napi::Object::New(env);
    out.Set("similarity", Napi::Number::New(env, result.similarity));
    out.Set("accepted", Napi::Boolean::New(env, result.accepted));
    out.Set("durationSeconds", Napi::Number::New(env, result.duration_seconds));
    return out;
}

Napi::Value EventsToJs(Napi::Env env, const std::vector<GateSegmentEvent> &events) {
    Napi::Array out = Napi::Array::New(env, events.size());
    uint32_t index = 0;
    for (const GateSegmentEvent &event : events) {
        Napi::Object obj = Napi::Object::New(env);
        obj.Set("type", Napi::String::New(env, "segment"));
        obj.Set("start", Napi::Number::New(env, static_cast<double>(event.start)));
        obj.Set("durationSeconds", Napi::Number::New(env, event.duration_seconds));
        obj.Set("similarity",
                event.has_similarity ? Napi::Value(Napi::Number::New(env, event.similarity))
                                     : Napi::Value(env.Null()));
        obj.Set("accepted", Napi::Boolean::New(env, event.accepted));
        Napi::Float32Array samples = Napi::Float32Array::New(env, event.samples.size());
        if (!event.samples.empty()) {
            std::memcpy(samples.Data(), event.samples.data(), event.samples.size() * sizeof(float));
        }
        obj.Set("samples", samples);
        out[index++] = obj;
    }
    return out;
}

// ── 通用 AsyncWorker：Execute 跑原生调用，OnOK/OnError 决议 Promise ─────────
// Result 需可默认构造（EnrollProgress / EnrollResult / vector 均满足）。

template <typename Result>
class OpWorker : public Napi::AsyncWorker {
public:
    using Run = std::function<Result()>;                                // 工作线程执行
    using Convert = std::function<Napi::Value(Napi::Env, const Result &)>;  // JS 线程转换

    OpWorker(Napi::Env env, Run run, Convert convert)
        : Napi::AsyncWorker(env),
          deferred_(Napi::Promise::Deferred::New(env)),
          run_(std::move(run)),
          convert_(std::move(convert)) {}

    Napi::Promise Promise() { return deferred_.Promise(); }

protected:
    void Execute() override {
        try {
            result_ = run_();
        } catch (const std::exception &e) {
            SetError(e.what());
        } catch (...) {
            SetError("unknown native error");
        }
    }

    void OnOK() override {
        Napi::HandleScope scope(Env());
        try {
            deferred_.Resolve(convert_(Env(), result_));
        } catch (const Napi::Error &e) {
            deferred_.Reject(e.Value());
        } catch (const std::exception &e) {
            deferred_.Reject(Napi::String::New(Env(), e.what()));
        }
    }

    void OnError(const Napi::Error &e) override { deferred_.Reject(e.Value()); }

private:
    Napi::Promise::Deferred deferred_;
    Run run_;
    Convert convert_;
    Result result_{};
};

using VoidWorker = OpWorker<bool>;

// ── Gate：SpeakerGate 的 ObjectWrap 包装 ────────────────────────────────────

class Gate : public Napi::ObjectWrap<Gate> {
public:
    static Napi::Object Init(Napi::Env env, Napi::Object exports) {
        Napi::Function func = DefineClass(
            env, "Gate",
            {
                InstanceMethod("beginEnroll", &Gate::BeginEnroll),
                InstanceMethod("enrollChunk", &Gate::EnrollChunk),
                InstanceMethod("finishEnroll", &Gate::FinishEnroll),
                InstanceMethod("accept", &Gate::Accept),
                InstanceMethod("flush", &Gate::Flush),
                InstanceMethod("reset", &Gate::Reset),
                InstanceMethod("dispose", &Gate::Dispose),
                InstanceAccessor("enrolled", &Gate::Enrolled, nullptr),
                InstanceAccessor("effectiveThreshold", &Gate::EffectiveThreshold, nullptr),
            });
        constructor_ = new Napi::FunctionReference();
        *constructor_ = Napi::Persistent(func);
        exports.Set("Gate", func);
        return exports;
    }

    explicit Gate(const Napi::CallbackInfo &info) : Napi::ObjectWrap<Gate>(info) {}

    // createGate 的 worker 在 JS 线程把加载好的原生对象注入新实例（见 CreateGate）
    static Napi::Object WrapNative(Napi::Env env, std::shared_ptr<SpeakerGate> native) {
        Napi::Object obj = constructor_->New({});
        Gate::Unwrap(obj)->native_ = std::move(native);
        return obj;
    }

private:
    std::shared_ptr<SpeakerGate> native_;

    void RequireAlive() const {
        if (!native_) {
            throw Napi::Error::New(Env(), "gate 已 dispose 或未完成初始化");
        }
    }

    // Float32Array → 拷贝进 vector（worker 执行期间 JS 可能改动原 buffer，必须复制）
    std::vector<float> RequireSamples(const Napi::CallbackInfo &info) const {
        RequireAlive();
        if (info.Length() < 1 || !info[0].IsTypedArray() ||
            info[0].As<Napi::TypedArray>().TypedArrayType() != napi_float32_array) {
            throw Napi::TypeError::New(Env(), "参数必须是 Float32Array（[-1,1] @16k）");
        }
        Napi::Float32Array array = info[0].As<Napi::Float32Array>();
        const float *data = array.Data();
        const size_t n = array.ElementLength();
        return std::vector<float>(data, data + n);
    }

    Napi::Value BeginEnroll(const Napi::CallbackInfo &info) {
        RequireAlive();
        auto gate = native_;
        auto *worker = new VoidWorker(
            info.Env(), [gate] { gate->BeginEnroll(); return true; },
            [](Napi::Env env, const bool &) { return env.Undefined(); });
        worker->Queue();
        return worker->Promise();
    }

    Napi::Value EnrollChunk(const Napi::CallbackInfo &info) {
        std::vector<float> samples = RequireSamples(info);
        auto gate = native_;
        auto *worker = new OpWorker<EnrollProgress>(
            info.Env(), [gate, samples = std::move(samples)]() mutable {
                return gate->EnrollChunk(samples.data(), samples.size());
            },
            ProgressToJs);
        worker->Queue();
        return worker->Promise();
    }

    Napi::Value FinishEnroll(const Napi::CallbackInfo &info) {
        RequireAlive();
        auto gate = native_;
        auto *worker = new OpWorker<EnrollResult>(info.Env(), [gate] { return gate->FinishEnroll(); },
                                                  ResultToJs);
        worker->Queue();
        return worker->Promise();
    }

    Napi::Value Accept(const Napi::CallbackInfo &info) {
        std::vector<float> samples = RequireSamples(info);
        auto gate = native_;
        auto *worker = new OpWorker<std::vector<GateSegmentEvent>>(
            info.Env(), [gate, samples = std::move(samples)]() mutable {
                return gate->AcceptWaveform(samples.data(), samples.size());
            },
            EventsToJs);
        worker->Queue();
        return worker->Promise();
    }

    Napi::Value Flush(const Napi::CallbackInfo &info) {
        RequireAlive();
        auto gate = native_;
        auto *worker = new OpWorker<std::vector<GateSegmentEvent>>(
            info.Env(), [gate] { return gate->Flush(); }, EventsToJs);
        worker->Queue();
        return worker->Promise();
    }

    Napi::Value Reset(const Napi::CallbackInfo &info) {
        RequireAlive();
        auto gate = native_;
        auto *worker = new VoidWorker(info.Env(), [gate] { gate->Reset(); return true; },
                                      [](Napi::Env env, const bool &) { return env.Undefined(); });
        worker->Queue();
        return worker->Promise();
    }

    Napi::Value Dispose(const Napi::CallbackInfo &info) {
        auto gate = std::move(native_);
        if (!gate) {
            Napi::Promise::Deferred deferred = Napi::Promise::Deferred::New(info.Env());
            deferred.Resolve(info.Env().Undefined());
            return deferred.Promise();
        }
        // mutable + reset：让 SpeakerGate 在工作线程析构（ORT session 释放较重）
        auto *worker = new VoidWorker(
            info.Env(), [gate]() mutable { gate.reset(); return true; },
            [](Napi::Env env, const bool &) { return env.Undefined(); });
        worker->Queue();
        return worker->Promise();
    }

    Napi::Value Enrolled(const Napi::CallbackInfo &info) {
        RequireAlive();
        return Napi::Boolean::New(info.Env(), native_->Enrolled());
    }

    Napi::Value EffectiveThreshold(const Napi::CallbackInfo &info) {
        RequireAlive();
        return Napi::Number::New(info.Env(), native_->EffectiveThreshold());
    }

    static Napi::FunctionReference *constructor_;
};

Napi::FunctionReference *Gate::constructor_ = nullptr;

// ── Filter：VoiceFilter（core，无 VAD）的 ObjectWrap 包装 ───────────────────
// API 与 sdk/web 的 core/voice-filter.ts 一一对应（filter() 由 index.js 组合）。

VoiceFilterConfig ParseFilterConfig(const Napi::Value &value) {
    if (!value.IsObject()) {
        throw Napi::TypeError::New(value.Env(), "createFilter 需要 config 对象（speakerModel 必填）");
    }
    Napi::Object options = value.As<Napi::Object>();
    VoiceFilterConfig config;
    config.speaker_model = RequireString(options, "speakerModel");
    OptionNumber(options, "threshold", &config.threshold);
    OptionNumber(options, "shortEnrollThresholdFactor", &config.short_enroll_threshold_factor);
    return config;
}

class Filter : public Napi::ObjectWrap<Filter> {
public:
    static Napi::Object Init(Napi::Env env, Napi::Object exports) {
        Napi::Function func = DefineClass(
            env, "Filter",
            {
                InstanceMethod("enroll", &Filter::Enroll),
                InstanceMethod("judge", &Filter::Judge),
                InstanceMethod("dispose", &Filter::Dispose),
                InstanceAccessor("enrolled", &Filter::Enrolled, nullptr),
                InstanceAccessor("effectiveThreshold", &Filter::EffectiveThreshold, nullptr),
            });
        constructor_ = new Napi::FunctionReference();
        *constructor_ = Napi::Persistent(func);
        exports.Set("Filter", func);
        return exports;
    }

    explicit Filter(const Napi::CallbackInfo &info) : Napi::ObjectWrap<Filter>(info) {}

    static Napi::Object WrapNative(Napi::Env env, std::shared_ptr<VoiceFilter> native) {
        Napi::Object obj = constructor_->New({});
        Filter::Unwrap(obj)->native_ = std::move(native);
        return obj;
    }

private:
    std::shared_ptr<VoiceFilter> native_;

    void RequireAlive() const {
        if (!native_) {
            throw Napi::Error::New(Env(), "filter 已 dispose 或未完成初始化");
        }
    }

    std::vector<float> RequireSamples(const Napi::CallbackInfo &info) const {
        RequireAlive();
        if (info.Length() < 1 || !info[0].IsTypedArray() ||
            info[0].As<Napi::TypedArray>().TypedArrayType() != napi_float32_array) {
            throw Napi::TypeError::New(Env(), "参数必须是 Float32Array（[-1,1] @16k）");
        }
        Napi::Float32Array array = info[0].As<Napi::Float32Array>();
        const float *data = array.Data();
        const size_t n = array.ElementLength();
        return std::vector<float>(data, data + n);
    }

    Napi::Value Enroll(const Napi::CallbackInfo &info) {
        std::vector<float> samples = RequireSamples(info);
        auto filter = native_;
        auto *worker = new OpWorker<EnrollResult>(
            info.Env(), [filter, samples = std::move(samples)]() mutable {
                return filter->Enroll(samples.data(), samples.size());
            },
            ResultToJs);
        worker->Queue();
        return worker->Promise();
    }

    Napi::Value Judge(const Napi::CallbackInfo &info) {
        std::vector<float> samples = RequireSamples(info);
        auto filter = native_;
        auto *worker = new OpWorker<JudgeResult>(
            info.Env(), [filter, samples = std::move(samples)]() mutable {
                return filter->Judge(samples.data(), samples.size());
            },
            JudgeToJs);
        worker->Queue();
        return worker->Promise();
    }

    Napi::Value Dispose(const Napi::CallbackInfo &info) {
        auto filter = std::move(native_);
        if (!filter) {
            Napi::Promise::Deferred deferred = Napi::Promise::Deferred::New(info.Env());
            deferred.Resolve(info.Env().Undefined());
            return deferred.Promise();
        }
        // 与 Gate::Dispose 同理：让 VoiceFilter 在工作线程析构
        auto *worker = new VoidWorker(
            info.Env(), [filter]() mutable { filter.reset(); return true; },
            [](Napi::Env env, const bool &) { return env.Undefined(); });
        worker->Queue();
        return worker->Promise();
    }

    Napi::Value Enrolled(const Napi::CallbackInfo &info) {
        RequireAlive();
        return Napi::Boolean::New(info.Env(), native_->Enrolled());
    }

    Napi::Value EffectiveThreshold(const Napi::CallbackInfo &info) {
        RequireAlive();
        return Napi::Number::New(info.Env(), native_->EffectiveThreshold());
    }

    static Napi::FunctionReference *constructor_;
};

Napi::FunctionReference *Filter::constructor_ = nullptr;

// createGate(config)：模型加载在工作线程执行，完成后把 shared_ptr 注入新 Gate 实例
Napi::Value CreateGate(const Napi::CallbackInfo &info) {
    SpeakerGateConfig config = ParseConfig(info[0]);  // 参数错误同步抛 TypeError

    auto *worker = new OpWorker<std::shared_ptr<SpeakerGate>>(
        info.Env(), [config] { return std::make_shared<SpeakerGate>(config); },
        [](Napi::Env env, const std::shared_ptr<SpeakerGate> &gate) {
            return Napi::Value(Gate::WrapNative(env, gate));
        });
    worker->Queue();
    return worker->Promise();
}

// createFilter(config)：core 版（无 VAD），模型加载在工作线程执行
Napi::Value CreateFilter(const Napi::CallbackInfo &info) {
    VoiceFilterConfig config = ParseFilterConfig(info[0]);

    auto *worker = new OpWorker<std::shared_ptr<VoiceFilter>>(
        info.Env(), [config] { return std::make_shared<VoiceFilter>(config); },
        [](Napi::Env env, const std::shared_ptr<VoiceFilter> &filter) {
            return Napi::Value(Filter::WrapNative(env, filter));
        });
    worker->Queue();
    return worker->Promise();
}

Napi::Object InitModule(Napi::Env env, Napi::Object exports) {
    Gate::Init(env, exports);
    Filter::Init(env, exports);
    exports.Set("createGate", Napi::Function::New(env, CreateGate));
    exports.Set("createFilter", Napi::Function::New(env, CreateFilter));
    return exports;
}

}  // namespace

NODE_API_MODULE(audiotse_gate_napi, InitModule)
