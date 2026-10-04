// Actual AVAudioPlayerNode clock / PCM integration, with no device or microphone.
import AVFoundation
import Foundation

func render(_ input: AVAudioPCMBuffer, name: String) throws -> [String: Any] {
    let format = input.format
    precondition(format.sampleRate == 24_000 && format.channelCount == 1)
    let envelope = PlaybackEnvelope(samples: input.floatChannelData![0], frames: Int(input.frameLength))
    let engine = AVAudioEngine(), player = AVAudioPlayerNode()
    engine.attach(player); engine.connect(player, to: engine.mainMixerNode, format: format)
    try engine.enableManualRenderingMode(.offline, format: format, maximumFrameCount: 480)
    try engine.start(); player.scheduleBuffer(input); player.play()
    precondition(envelope.rms(for: player) == 0, "unrendered PCM must not become activity")
    let output = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 480)!
    var rows = [[String: Any]](), mismatches = 0
    for _ in 0..<(Int(input.frameLength) + 479) / 480 + 2 {
        let status = try engine.renderOffline(480, to: output)
        precondition(status == .success)
        let cursor = player.lastRenderTime.flatMap { player.playerTime(forNodeTime: $0) }!.sampleTime
        var energy = 0.0
        for i in 0..<Int(output.frameLength) { let v = Double(output.floatChannelData![0][i]); energy += v * v }
        let renderedRms = (energy / Double(output.frameLength)).squareRoot()
        let measuredRms = Double(envelope.rms(for: player))
        // The API is a completed-window envelope. A padded final partial
        // window is outside its frame range, and is deliberately excluded.
        if cursor <= input.frameLength && abs(renderedRms - measuredRms) > 0.000001 { mismatches += 1 }
        rows.append(["cursor": cursor, "renderedRms": renderedRms, "measuredRms": measuredRms])
    }
    player.stop()
    precondition(envelope.rms(for: player) == 0, "stop must clear the player timeline")
    // The previous render time must not be interpreted as the new buffer.
    player.scheduleBuffer(input); player.play()
    precondition(envelope.rms(for: player) == 0, "a replacement buffer has not rendered yet")
    player.stop(); engine.stop()
    precondition(mismatches == 0)
    return ["name": name, "frames": input.frameLength, "windows": rows.count, "mismatches": mismatches,
            "beforeRenderRms": 0, "afterStopRms": 0, "beforeReplacementRenderRms": 0, "rows": rows]
}

let format = AVAudioFormat(standardFormatWithSampleRate: 24_000, channels: 1)!
let tone = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 2400)!
tone.frameLength = 2400
for i in 0..<2400 { tone.floatChannelData![0][i] = i < 480 ? 0 : i < 1440 ? 0.25 : 0.5 }
var results = [try render(tone, name: "silence-quarter-half")]
let first = results[0]["rows"] as! [[String: Any]]
precondition((first[0]["measuredRms"] as! Double) == 0)
precondition((first[1]["measuredRms"] as! Double) == 0.25)
precondition((first[3]["measuredRms"] as! Double) == 0.5)
for path in CommandLine.arguments.dropFirst() {
    let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
    precondition(file.length > 0 && file.length <= 2_880_000)
    let input = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length))!
    try file.read(into: input)
    results.append(try render(input, name: URL(fileURLWithPath: path).lastPathComponent))
}
let data = try JSONSerialization.data(withJSONObject: ["schemaVersion": 1,
    "scope": "Actual AVAudioEngine offline DSP and player clock; no microphone, device or speaker playback",
    "cases": results], options: [.prettyPrinted, .sortedKeys])
FileHandle.standardOutput.write(data)
