// One owned AVAudioEngine supplies echo-processed microphone input and TTS output.
// The C ABI is loaded by the existing Python voice process; no audio service is added.
import AVFoundation
import Foundation
import Darwin

private final class VoiceAudio {
    let engine = AVAudioEngine()
    let player = AVAudioPlayerNode()
    let state = NSLock()
    let commands = NSLock()
    var queue = [UInt8](repeating: 0, count: 64_000)
    var readIndex = 0
    var count = 0
    var dropped: UInt64 = 0
    var failed = false
    var closed = false
    var tapped = false
    var generation: UInt64 = 0
    var playing: UInt64 = 0
    var playbackEnvelope: PlaybackEnvelope?

    func start() -> Int32 {
        // Do not request permission or show an OS dialog from a background probe.
        guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else { return -3 }
        do {
            // Establish the render graph before switching the duplex I/O unit.
            // Switching first makes output-node initialization fail on this Mac.
            engine.attach(player)
            engine.connect(player, to: engine.mainMixerNode,
                           format: AVAudioFormat(standardFormatWithSampleRate: 24_000, channels: 1))
            engine.connect(engine.mainMixerNode, to: engine.outputNode,
                           format: engine.outputNode.inputFormat(forBus: 0))
            let input = engine.inputNode
            try input.setVoiceProcessingEnabled(true)
            if #available(macOS 14.0, *) {
                input.voiceProcessingOtherAudioDuckingConfiguration = .init(
                    enableAdvancedDucking: true, duckingLevel: .min)
            }
            guard input.isVoiceProcessingEnabled else { return -41 }
            guard engine.outputNode.isVoiceProcessingEnabled else { return -42 }
            guard !input.isVoiceProcessingBypassed else { return -43 }
            guard !input.isVoiceProcessingInputMuted else { return -44 }
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0,
                  let target = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000,
                                             channels: 1, interleaved: true),
                  let converter = AVAudioConverter(from: format, to: target),
                  let output = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: 4096) else {
                fputs("alden_audio: unsupported input format rate=\(format.sampleRate) channels=\(format.channelCount)\n", stderr)
                return -51
            }
            // Keep first-channel selection explicit for multichannel I/O.
            // Do not blend additional channels into speech recognition.
            converter.channelMap = [0]
            input.installTap(onBus: 0, bufferSize: 960, format: format) { [weak self] buffer, _ in
                guard let self else { return }
                self.state.lock()
                let unavailable = self.closed || self.failed
                self.state.unlock()
                if unavailable { return }
                // Route changes invalidate the converter rather than silently changing provenance.
                guard buffer.format.sampleRate == format.sampleRate,
                      buffer.format.channelCount == format.channelCount,
                      buffer.format.commonFormat == format.commonFormat,
                      buffer.format.isInterleaved == format.isInterleaved else {
                    self.state.lock(); self.failed = true; self.state.unlock(); return
                }
                var supplied = false
                var conversionError: NSError?
                output.frameLength = 0
                let result = converter.convert(to: output, error: &conversionError) { _, status in
                    if supplied { status.pointee = .noDataNow; return nil }
                    supplied = true
                    status.pointee = .haveData
                    return buffer
                }
                guard conversionError == nil, result != .error else {
                    self.state.lock(); self.failed = true; self.state.unlock(); return
                }
                let length = Int(output.frameLength) * 2
                if length == 0 { return }
                guard let samples = output.int16ChannelData?[0], length <= self.queue.count else {
                    self.state.lock(); self.failed = true; self.state.unlock(); return
                }
                self.state.lock()
                defer { self.state.unlock() }
                if self.closed { return }
                let overflow = max(0, self.count + length - self.queue.count)
                if overflow > 0 {
                    // Drop whole 20ms packets; never splice half of an old sample/frame into a turn.
                    let discard = min(self.count, ((overflow + 639) / 640) * 640)
                    self.readIndex = (self.readIndex + discard) % self.queue.count
                    self.count -= discard
                    self.dropped += UInt64(discard / 640)
                }
                let bytes = UnsafeRawPointer(samples).assumingMemoryBound(to: UInt8.self)
                for i in 0..<length {
                    self.queue[(self.readIndex + self.count + i) % self.queue.count] = bytes[i]
                }
                self.count += length
            }
            tapped = true
            engine.prepare()
            try engine.start()
            return echoProcessed() ? 0 : -4
        } catch let error as NSError {
            fputs("alden_audio: start error domain=\(error.domain) code=\(error.code)\n", stderr)
            return Int32(clamping: error.code)
        }
    }

    func echoProcessed() -> Bool {
        state.lock(); let ready = !closed && !failed; state.unlock()
        return ready && engine.isRunning && engine.inputNode.isVoiceProcessingEnabled
            && engine.outputNode.isVoiceProcessingEnabled
            && !engine.inputNode.isVoiceProcessingBypassed && !engine.inputNode.isVoiceProcessingInputMuted
    }

    func play(_ samples: UnsafePointer<Float>, frames: Int, rate: Double) -> UInt64 {
        guard frames > 0, rate == 24_000, frames <= 2_880_000,
              let format = AVAudioFormat(standardFormatWithSampleRate: rate, channels: 1),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)),
              let destination = buffer.floatChannelData?[0] else { return 0 }
        for i in 0..<frames {
            guard samples[i].isFinite else { return 0 }
            destination[i] = max(-1, min(1, samples[i]))
        }
        buffer.frameLength = AVAudioFrameCount(frames)
        commands.lock(); defer { commands.unlock() }
        guard echoProcessed() else { return 0 }
        player.stop()
        playbackEnvelope = PlaybackEnvelope(samples: destination, frames: frames)
        state.lock()
        generation += 1
        let ticket = generation
        playing = ticket
        state.unlock()
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            guard let self else { return }
            self.state.lock(); defer { self.state.unlock() }
            if self.playing == ticket { self.playing = 0 }
        }
        player.play()
        return ticket
    }

    func outputRms(_ ticket: UInt64) -> Float {
        commands.lock(); defer { commands.unlock() }
        state.lock()
        let valid = ticket > 0 && playing == ticket && !closed && !failed
        state.unlock()
        guard valid, echoProcessed(), let envelope = playbackEnvelope,
              let nodeTime = player.lastRenderTime, nodeTime.isHostTimeValid else { return 0 }
        // A stalled device must not keep publishing an old rendered window.
        let age = AVAudioTime.seconds(forHostTime: mach_absolute_time())
            - AVAudioTime.seconds(forHostTime: nodeTime.hostTime)
        guard age >= -0.05, age <= 0.2 else { return 0 }
        return envelope.rms(for: player)
    }

    func cancel(_ ticket: UInt64) {
        commands.lock(); defer { commands.unlock() }
        state.lock()
        let matches = ticket > 0 && playing == ticket
        let ownsEnvelope = ticket > 0 && generation == ticket
        if matches { playing = 0 }
        state.unlock()
        if ownsEnvelope { playbackEnvelope = nil }
        if matches { player.stop() }
    }

    func close() {
        commands.lock(); defer { commands.unlock() }
        state.lock()
        let wasClosed = closed
        closed = true; playing = 0; count = 0
        state.unlock()
        if wasClosed { return }
        playbackEnvelope = nil
        player.stop()
        engine.stop()
        if tapped { engine.inputNode.removeTap(onBus: 0); tapped = false }
    }
}

private func audio(_ pointer: UnsafeMutableRawPointer) -> VoiceAudio {
    Unmanaged<VoiceAudio>.fromOpaque(pointer).takeUnretainedValue()
}

@_cdecl("alden_audio_abi") public func audioABI() -> Int32 { 2 }
@_cdecl("alden_audio_permission") public func audioPermission() -> Int32 {
    Int32(AVCaptureDevice.authorizationStatus(for: .audio).rawValue)
}
@_cdecl("alden_audio_request_permission") public func audioRequestPermission() {
    // Only the foreground voice-start path requests access. Read-only probes do not.
    AVCaptureDevice.requestAccess(for: .audio) { _ in }
}
@_cdecl("alden_audio_create") public func audioCreate() -> UnsafeMutableRawPointer {
    Unmanaged.passRetained(VoiceAudio()).toOpaque()
}
@_cdecl("alden_audio_start") public func audioStart(_ pointer: UnsafeMutableRawPointer) -> Int32 {
    audio(pointer).start()
}
@_cdecl("alden_audio_processed") public func audioProcessed(_ pointer: UnsafeMutableRawPointer) -> Int32 {
    audio(pointer).echoProcessed() ? 1 : 0
}
@_cdecl("alden_audio_available") public func audioAvailable(_ pointer: UnsafeMutableRawPointer) -> Int32 {
    let a = audio(pointer)
    a.state.lock(); defer { a.state.unlock() }
    return a.failed || a.closed ? -1 : Int32(a.count)
}
@_cdecl("alden_audio_dropped") public func audioDropped(_ pointer: UnsafeMutableRawPointer) -> UInt64 {
    let a = audio(pointer)
    a.state.lock(); defer { a.state.unlock() }
    return a.dropped
}
@_cdecl("alden_audio_read") public func audioRead(_ pointer: UnsafeMutableRawPointer,
    _ destination: UnsafeMutablePointer<UInt8>, _ capacity: Int32) -> Int32 {
    guard capacity == 640 else { return -1 }
    let a = audio(pointer)
    a.state.lock(); defer { a.state.unlock() }
    guard !a.closed, !a.failed else { return -1 }
    guard a.count >= 640 else { return 0 }
    for i in 0..<640 { destination[i] = a.queue[(a.readIndex + i) % a.queue.count] }
    a.readIndex = (a.readIndex + 640) % a.queue.count
    a.count -= 640
    return 640
}
@_cdecl("alden_audio_play") public func audioPlay(_ pointer: UnsafeMutableRawPointer,
    _ samples: UnsafePointer<Float>, _ frames: Int32, _ rate: Double) -> UInt64 {
    audio(pointer).play(samples, frames: Int(frames), rate: rate)
}
@_cdecl("alden_audio_playing") public func audioPlaying(_ pointer: UnsafeMutableRawPointer,
    _ ticket: UInt64) -> Int32 {
    let a = audio(pointer)
    a.state.lock(); defer { a.state.unlock() }
    return ticket > 0 && a.playing == ticket && !a.closed && !a.failed ? 1 : 0
}
@_cdecl("alden_audio_cancel") public func audioCancel(_ pointer: UnsafeMutableRawPointer, _ ticket: UInt64) {
    audio(pointer).cancel(ticket)
}
@_cdecl("alden_audio_output_rms") public func audioOutputRms(_ pointer: UnsafeMutableRawPointer,
    _ ticket: UInt64) -> Float { audio(pointer).outputRms(ticket) }
@_cdecl("alden_audio_destroy") public func audioDestroy(_ pointer: UnsafeMutableRawPointer) {
    let a = Unmanaged<VoiceAudio>.fromOpaque(pointer).takeRetainedValue()
    a.close()
}
