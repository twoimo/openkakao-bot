import AVFoundation

// Measured, clipped source PCM indexed by the player's actual render clock.
// Only completed 20ms windows are exposed; a silent first window must not
// display the next window merely because lastRenderTime is its end cursor.
struct PlaybackEnvelope {
    let frames: Int
    static let windowFrames = 480
    private let levels: [Float]

    init(samples: UnsafePointer<Float>, frames: Int) {
        self.frames = frames
        var result = [Float](repeating: 0, count: (frames + Self.windowFrames - 1) / Self.windowFrames)
        for window in result.indices {
            let start = window * Self.windowFrames
            let end = min(frames, start + Self.windowFrames)
            var energy = 0.0
            for i in start..<end { let value = Double(samples[i]); energy += value * value }
            result[window] = Float((energy / Double(end - start)).squareRoot())
        }
        levels = result
    }

    func rms(afterPlayingFrames cursor: AVAudioFramePosition) -> Float {
        guard cursor > 0, cursor <= frames else { return 0 }
        let completed = cursor == frames ? levels.count : Int(cursor) / Self.windowFrames
        return completed > 0 ? levels[completed - 1] : 0
    }

    func rms(for player: AVAudioPlayerNode) -> Float {
        guard player.isPlaying, let nodeTime = player.lastRenderTime,
              nodeTime.isSampleTimeValid || nodeTime.isHostTimeValid,
              let time = player.playerTime(forNodeTime: nodeTime),
              time.isSampleTimeValid, time.sampleRate == 24_000 else { return 0 }
        return rms(afterPlayingFrames: time.sampleTime)
    }
}
